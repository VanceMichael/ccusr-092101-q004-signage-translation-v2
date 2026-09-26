-- 规范发布与采用追踪：核心结构
-- 约定：
--  * 时间字段一律 TEXT，存带偏移量的 ISO 8601 字符串（UTC 归一化）
--  * “事实”表 append-only：历史行不改写；状态推进通过新事件行实现
--  * 引用编号不含真实身份信息；附件只存 sha256 摘要或受控引用
--  * 状态/进度的“当前值”由水位规则从历史行推导，更新只能前进不能倒退

-- ---------------------------------------------------------------------------
-- 1. 责任单位与订阅
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS units (
    unit_id            TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    managed_categories TEXT NOT NULL DEFAULT '[]',  -- 场所类别白名单 JSON 数组
    managed_locations  TEXT NOT NULL DEFAULT '[]',  -- 空数组表示类别内全部点位可管
    token              TEXT NOT NULL,               -- 单位上报令牌（不含身份信息）
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    unit_id     TEXT NOT NULL REFERENCES units(unit_id),
    category    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (unit_id, category)
) WITHOUT ROWID;

-- 发布包向订阅单位的投递通知（离线单位重连后据此拉取）
CREATE TABLE IF NOT EXISTS notifications (
    nid          INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id      TEXT NOT NULL REFERENCES units(unit_id),
    package_id   TEXT NOT NULL,
    category     TEXT NOT NULL,
    notified_at  TEXT NOT NULL,
    acked_at     TEXT                                -- 单位重连后回执；为空表示尚待送达
);

-- ---------------------------------------------------------------------------
-- 2. 签名发布包（不可变内容 + append-only 生命周期事件）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS packages (
    package_id       TEXT PRIMARY KEY,
    prev_package_id  TEXT,                            -- 同 类别+语种 上一包，构成版本链
    category         TEXT NOT NULL,
    language_code    TEXT NOT NULL,
    sequence_no      INTEGER NOT NULL,                -- 同 类别+语种 内递增
    issued_at        TEXT NOT NULL,
    issued_by        TEXT NOT NULL,
    effective_at     TEXT NOT NULL,                   -- 生效时间（可未来生效）
    basis_docs       TEXT NOT NULL DEFAULT '[]',      -- 依据文件 [{doc_ref,title,sha256}]
    entries_json     TEXT NOT NULL,                   -- 标准词条 {term_key: {term, translation, note}}
    exceptions_json  TEXT NOT NULL DEFAULT '[]',      -- 例外说明 [{term_key, scope, reason}]
    canonical_bytes  BLOB NOT NULL,                   -- 签名覆盖的规范化字节
    signature        TEXT NOT NULL,                   -- 主密钥 HMAC，可离线复核
    chain_ok         INTEGER NOT NULL DEFAULT 1       -- 版本链完整标志
);
-- 当前生命周期状态（active/superseded/withdrawn/pending）一律从 package_events
-- 与 effective_at 推导，不在此行就地改写，历史行保持不可变。

CREATE TABLE IF NOT EXISTS package_events (
    eid          INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id   TEXT NOT NULL REFERENCES packages(package_id),
    event_type   TEXT NOT NULL,                       -- issued | supersede | withdraw
    actor        TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    recorded_at  TEXT NOT NULL,                       -- 决定做出的时刻（不可变）
    effective_at TEXT NOT NULL                        -- 该决定产生效力的时刻（可晚于 recorded_at）
);

-- ---------------------------------------------------------------------------
-- 3. 标识目录与 append-only 采用版本
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signs (
    sign_ref       TEXT PRIMARY KEY,                  -- 不含公众身份信息
    location_code  TEXT NOT NULL,
    category       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sign_versions (
    vid             INTEGER PRIMARY KEY AUTOINCREMENT,
    sign_ref        TEXT NOT NULL REFERENCES signs(sign_ref),
    language_code   TEXT NOT NULL,
    term_key        TEXT NOT NULL,
    package_id      TEXT NOT NULL,                    -- 该版本自报合法引用的发布包
    observed_text   TEXT NOT NULL,                    -- 现场实际译写
    reported_by     TEXT NOT NULL REFERENCES units(unit_id),
    reported_at     TEXT NOT NULL,
    batch_id        TEXT NOT NULL,                    -- 幂等：同批次重放不产生新版本
    superseded_vid  INTEGER                           -- 该版本替换的上一现场版本
);

CREATE INDEX IF NOT EXISTS idx_sign_versions_sign ON sign_versions(sign_ref, language_code, vid);
CREATE INDEX IF NOT EXISTS idx_sign_versions_pkg ON sign_versions(package_id);

-- 离线回执 / 重复同步：每个单位+批次只接受一次，且结果可重放
CREATE TABLE IF NOT EXISTS sync_batches (
    unit_id      TEXT NOT NULL REFERENCES units(unit_id),
    batch_id     TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    accepted     INTEGER NOT NULL,
    report_json  TEXT NOT NULL,                       -- 首次接受的载荷
    receipt_json TEXT NOT NULL DEFAULT '[]',          -- 首次处理结果，重放时原样返回
    PRIMARY KEY (unit_id, batch_id)
) WITHOUT ROWID;

-- ---------------------------------------------------------------------------
-- 4. 差异、候选与（专业确认后的）更新要求
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diffs (
    diff_id        TEXT PRIMARY KEY,
    old_package_id TEXT NOT NULL,
    new_package_id TEXT NOT NULL,
    generated_at   TEXT NOT NULL,
    changes_json   TEXT NOT NULL                       -- added/removed/changed 明细
);

CREATE TABLE IF NOT EXISTS impact_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    diff_id       TEXT NOT NULL REFERENCES diffs(diff_id),
    term_key      TEXT NOT NULL,
    sign_ref      TEXT NOT NULL,
    language_code TEXT NOT NULL,
    unit_id       TEXT NOT NULL,
    current_package_id TEXT NOT NULL,
    kind          TEXT NOT NULL                        -- added|removed|changed
);

CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    diff_id      TEXT NOT NULL REFERENCES diffs(diff_id),
    sign_ref     TEXT NOT NULL,
    language_code TEXT NOT NULL,
    term_key     TEXT NOT NULL,
    unit_id      TEXT NOT NULL,
    target_package_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'proposed',     -- proposed|confirmed|rejected
    reason       TEXT NOT NULL DEFAULT '',
    decided_by   TEXT NOT NULL DEFAULT '',
    decided_at   TEXT,
    UNIQUE (diff_id, sign_ref, language_code, term_key)
);

-- 专业确认后才能生成；承诺期限来自单位，真实完成由 sign_versions 推进自动判定
CREATE TABLE IF NOT EXISTS update_requirements (
    req_id           TEXT PRIMARY KEY,
    candidate_id     INTEGER NOT NULL REFERENCES candidates(id),
    sign_ref         TEXT NOT NULL,
    language_code    TEXT NOT NULL,
    term_key         TEXT NOT NULL,
    unit_id          TEXT NOT NULL,
    target_package_id TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    promised_by      TEXT,                            -- 单位上报的承诺期限
    completed_at     TEXT                             -- 现场版本推进到目标包时回填
);
-- 同一标识+语种+词条对同一目标包只产生一条要求；候选确认幂等。
-- 豁免是否“当前生效”由 exemptions 事件链（request/approve/expire/revoke）推导，
-- 不在此行上就地改写，保证批准、到期、撤销都留痕且互不覆盖。

CREATE INDEX IF NOT EXISTS idx_req_open ON update_requirements(unit_id, completed_at);

-- ---------------------------------------------------------------------------
-- 5. 限时豁免：批准 / 到期 / 撤销均为不可覆盖的独立决定
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exemptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    req_id       TEXT NOT NULL REFERENCES update_requirements(req_id),
    action       TEXT NOT NULL,                       -- request|approve|expire|revoke
    actor        TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    valid_until  TEXT,                                -- 仅批准决定携带
    recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exemptions_req ON exemptions(req_id, id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_tracking');
