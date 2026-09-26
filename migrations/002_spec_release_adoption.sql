-- 规范发布与采用追踪：发布包、订阅、标识目录、差异候选、整改要求、限时豁免、幂等同步
-- 约定：时间一律为带偏移量的 ISO 8601 文本；外部主体只用引用编号；事实表只追加不修改。

CREATE TABLE IF NOT EXISTS units (
    unit_ref TEXT PRIMARY KEY,          -- 责任单位引用编号，不含身份信息
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id TEXT PRIMARY KEY,
    unit_ref TEXT NOT NULL REFERENCES units(unit_ref),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('place_category', 'language', 'location_prefix')),
    scope_value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (unit_ref, scope_type, scope_value)
);

CREATE TABLE IF NOT EXISTS signs (
    sign_ref TEXT PRIMARY KEY,          -- 标识引用编号，不含公众身份信息
    unit_ref TEXT NOT NULL REFERENCES units(unit_ref),
    location_code TEXT NOT NULL,
    place_category TEXT NOT NULL,
    carrier_type TEXT NOT NULL,         -- 载体：机场/医院/景区/道路标牌/政务网页等
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_packages (
    package_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,          -- 同一规范线内的修订序号
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'published')),
    effective_from TEXT NOT NULL,       -- 生效时间
    supersedes_package_id TEXT REFERENCES release_packages(package_id),
    payload_hash TEXT NOT NULL,         -- 规范化负载的 sha256
    signature TEXT NOT NULL,            -- HMAC-SHA256 签名
    published_at TEXT NOT NULL,
    UNIQUE (revision)
);

-- 发布包生命周期：废止等动作只追加事件，签名负载永不改写。
CREATE TABLE IF NOT EXISTS package_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    action TEXT NOT NULL CHECK (action IN ('published', 'deprecated')),
    effective_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL,
    UNIQUE (package_id, action)
);

CREATE TABLE IF NOT EXISTS release_entries (
    package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    entry_key TEXT NOT NULL,            -- 词条键，跨版本稳定
    language_code TEXT NOT NULL,
    place_category TEXT NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (package_id, entry_key)
);

CREATE TABLE IF NOT EXISTS release_exceptions (
    package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    exception_key TEXT NOT NULL,
    language_code TEXT NOT NULL,
    place_category TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (package_id, exception_key)
);

CREATE TABLE IF NOT EXISTS release_basis_documents (
    package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    document_key TEXT NOT NULL,
    title TEXT NOT NULL,
    reference TEXT NOT NULL,            -- 受控引用或 sha256 摘要
    PRIMARY KEY (package_id, document_key)
);

-- 上报事件：只追加。同一 (source_system, event_id) 幂等；seq 单调防回退。
CREATE TABLE IF NOT EXISTS report_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL,
    event_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    sign_ref TEXT NOT NULL REFERENCES signs(sign_ref),
    language_code TEXT NOT NULL,
    entry_key TEXT NOT NULL,            -- 该语种译文引用的标准词条
    translation_revision INTEGER NOT NULL,
    package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    translation TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (source_system, event_id),
    UNIQUE (source_system, sign_ref, language_code, seq)
);

-- 每来源系统每标识每语种的同步游标，只许前进。
CREATE TABLE IF NOT EXISTS sync_cursors (
    source_system TEXT NOT NULL,
    sign_ref TEXT NOT NULL,
    language_code TEXT NOT NULL,
    last_seq INTEGER NOT NULL,
    PRIMARY KEY (source_system, sign_ref, language_code)
);

-- 同步批次：离线回执与重复同步按批次键幂等。
CREATE TABLE IF NOT EXISTS sync_batches (
    batch_key TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_diffs (
    diff_id TEXT PRIMARY KEY,
    from_package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    to_package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    entry_key TEXT NOT NULL,
    language_code TEXT NOT NULL,
    place_category TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('added', 'removed', 'modified')),
    old_target TEXT,
    new_target TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (from_package_id, to_package_id, entry_key)
);

-- 整改候选：自动判断只能提出候选，专业人员确认后才升级为整改要求。
CREATE TABLE IF NOT EXISTS update_candidates (
    candidate_id TEXT PRIMARY KEY,
    diff_id TEXT NOT NULL REFERENCES entry_diffs(diff_id),
    sign_ref TEXT NOT NULL REFERENCES signs(sign_ref),
    language_code TEXT NOT NULL,
    place_category TEXT NOT NULL,
    current_translation TEXT NOT NULL,
    suggested_translation TEXT NOT NULL,
    based_on_revision INTEGER NOT NULL,     -- 生成候选时该标识的译文版本
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'dismissed')),
    decided_by TEXT,                        -- 内部工号引用，不含身份信息
    decided_at TEXT,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (diff_id, sign_ref, language_code)   -- 候选生成幂等
);
CREATE INDEX IF NOT EXISTS idx_candidates_sign ON update_candidates(sign_ref, status);

CREATE TABLE IF NOT EXISTS requirements (
    requirement_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES update_candidates(candidate_id),
    sign_ref TEXT NOT NULL REFERENCES signs(sign_ref),
    unit_ref TEXT NOT NULL REFERENCES units(unit_ref),
    language_code TEXT NOT NULL,
    entry_key TEXT NOT NULL,
    target_package_id TEXT NOT NULL REFERENCES release_packages(package_id),
    required_translation TEXT NOT NULL,
    based_on_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
    created_at TEXT NOT NULL,
    committed_deadline TEXT,                -- 责任单位承诺期限
    committed_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requirements_unit ON requirements(unit_ref, status);
CREATE INDEX IF NOT EXISTS idx_requirements_target ON requirements(target_package_id, status);

-- 完成事件：只追加；只有校验通过且状态前进时才影响要求状态。
CREATE TABLE IF NOT EXISTS completion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_system TEXT NOT NULL,
    event_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL REFERENCES requirements(requirement_id),
    sign_ref TEXT NOT NULL,
    language_code TEXT NOT NULL,
    translation_revision INTEGER NOT NULL,
    package_id TEXT NOT NULL,
    translation TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (source_system, event_id, requirement_id)
);

-- 豁免决定：批准/到期/撤销一律追加新事件，原决定永不覆盖。
CREATE TABLE IF NOT EXISTS exemption_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exemption_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('applied', 'approved', 'expired', 'revoked')),
    sign_ref TEXT NOT NULL REFERENCES signs(sign_ref),
    requirement_id TEXT REFERENCES requirements(requirement_id),
    reason TEXT NOT NULL,                   -- 施工周期 / 历史建筑等
    valid_from TEXT,
    valid_until TEXT,
    decided_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL,
    UNIQUE (exemption_id, action)           -- 同一豁免同一动作只记录一次
);
CREATE INDEX IF NOT EXISTS idx_exemption_events_sign ON exemption_events(sign_ref);
