"""规范发布与采用追踪的领域逻辑。

不变量：
- 事实表（上报、完成、豁免决定、包生命周期）只追加，不更新、不删除。
- 同一来源的同一事件幂等；同一来源同一标识同一语种的序列号只许前进。
- 候选由系统自动生成，整改要求只能由专业人员确认候选后产生。
- 豁免的批准、到期、撤销以新事件追加，原决定永远保留。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app import security


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 时间

def parse_time(value: str, field: str) -> datetime:
    """解析带偏移量的 ISO 8601 时间；缺少偏移量一律拒绝。"""
    if not isinstance(value, str):
        raise DomainError(422, "invalid_time", f"{field} 必须是 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainError(422, "invalid_time", f"{field} 不是合法的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise DomainError(422, "invalid_time", f"{field} 必须带时区偏移量")
    return parsed


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------- 基础档案

def create_unit(conn: sqlite3.Connection, unit_ref: str, name: str) -> dict[str, Any]:
    try:
        with conn:
            conn.execute(
                "INSERT INTO units(unit_ref, name, created_at) VALUES (?, ?, ?)",
                (unit_ref, name, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "unit_exists", f"责任单位 {unit_ref} 已存在") from exc
    return {"unit_ref": unit_ref, "name": name}


def subscribe(
    conn: sqlite3.Connection,
    subscription_id: str,
    unit_ref: str,
    scope_type: str,
    scope_value: str,
) -> dict[str, Any]:
    if scope_type not in ("place_category", "language", "location_prefix"):
        raise DomainError(422, "invalid_scope", f"不支持的订阅范围 {scope_type}")
    _require_unit(conn, unit_ref)
    try:
        with conn:
            conn.execute(
                "INSERT INTO subscriptions(subscription_id, unit_ref, scope_type, scope_value, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (subscription_id, unit_ref, scope_type, scope_value, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "subscription_exists", "订阅编号或范围组合已存在") from exc
    return {
        "subscription_id": subscription_id,
        "unit_ref": unit_ref,
        "scope_type": scope_type,
        "scope_value": scope_value,
    }


def register_sign(
    conn: sqlite3.Connection,
    sign_ref: str,
    unit_ref: str,
    location_code: str,
    place_category: str,
    carrier_type: str,
) -> dict[str, Any]:
    _require_unit(conn, unit_ref)
    try:
        with conn:
            conn.execute(
                "INSERT INTO signs(sign_ref, unit_ref, location_code, place_category, carrier_type, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sign_ref, unit_ref, location_code, place_category, carrier_type, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "sign_exists", f"标识 {sign_ref} 已登记") from exc
    return {
        "sign_ref": sign_ref,
        "unit_ref": unit_ref,
        "location_code": location_code,
        "place_category": place_category,
        "carrier_type": carrier_type,
    }


def _require_unit(conn: sqlite3.Connection, unit_ref: str) -> None:
    if not conn.execute("SELECT 1 FROM units WHERE unit_ref = ?", (unit_ref,)).fetchone():
        raise DomainError(404, "unit_not_found", f"责任单位 {unit_ref} 不存在")


def _require_sign(conn: sqlite3.Connection, sign_ref: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM signs WHERE sign_ref = ?", (sign_ref,)).fetchone()
    if not row:
        raise DomainError(404, "sign_not_found", f"标识 {sign_ref} 未登记")
    return row


def _subscription_covers(
    conn: sqlite3.Connection, unit_ref: str, sign: sqlite3.Row, language_code: str
) -> bool:
    """单位至少一条订阅覆盖该标识的场所类别、语种或地点前缀，才允许上报。"""
    rows = conn.execute(
        "SELECT scope_type, scope_value FROM subscriptions WHERE unit_ref = ?", (unit_ref,)
    ).fetchall()
    for row in rows:
        if row["scope_type"] == "place_category" and row["scope_value"] == sign["place_category"]:
            return True
        if row["scope_type"] == "language" and row["scope_value"] == language_code:
            return True
        if row["scope_type"] == "location_prefix" and sign["location_code"].startswith(
            row["scope_value"]
        ):
            return True
    return False


# ---------------------------------------------------------------- 发布包

def _package_payload(body: dict[str, Any]) -> dict[str, Any]:
    """从发布请求中提取被签名的规范负载，并规范化为落库后的形态。"""
    entries = [
        {
            "entry_key": e["entry_key"],
            "language_code": e["language_code"],
            "place_category": e["place_category"],
            "source_text": e["source_text"],
            "target_text": e["target_text"],
            "note": e.get("note", ""),
        }
        for e in body.get("entries", [])
    ]
    exceptions = [
        {
            "exception_key": x["exception_key"],
            "language_code": x["language_code"],
            "place_category": x["place_category"],
            "description": x["description"],
        }
        for x in body.get("exceptions", [])
    ]
    basis = [
        {
            "document_key": d["document_key"],
            "title": d["title"],
            "reference": d["reference"],
        }
        for d in body.get("basis_documents", [])
    ]
    return {
        "package_id": body["package_id"],
        "revision": body["revision"],
        "title": body["title"],
        "effective_from": body["effective_from"],
        "supersedes_package_id": body.get("supersedes_package_id"),
        "entries": entries,
        "exceptions": exceptions,
        "basis_documents": basis,
    }


def publish_package(conn: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    for field in ("package_id", "title"):
        if not body.get(field):
            raise DomainError(422, "missing_field", f"缺少字段 {field}")
    revision = body.get("revision")
    if not isinstance(revision, int) or revision < 1:
        raise DomainError(422, "invalid_revision", "revision 必须是正整数")
    parse_time(body.get("effective_from", ""), "effective_from")
    entries = body.get("entries") or []
    if not entries:
        raise DomainError(422, "empty_entries", "发布包至少包含一条标准词条")
    entry_keys = [e.get("entry_key") for e in entries]
    if len(set(entry_keys)) != len(entry_keys) or not all(entry_keys):
        raise DomainError(422, "duplicate_entry", "entry_key 重复或缺失")
    for entry in entries:
        for field in ("language_code", "place_category", "source_text", "target_text"):
            if not entry.get(field):
                raise DomainError(422, "invalid_entry", f"词条 {entry.get('entry_key')} 缺少 {field}")

    supersedes = body.get("supersedes_package_id")
    if supersedes:
        parent = _package_row(conn, supersedes)
        if parent["revision"] >= revision:
            raise DomainError(422, "invalid_revision", "新包修订号必须大于被替代的发布包")

    payload = _package_payload(body)
    digest, signature = security.sign(payload)
    recorded = now_iso()
    try:
        with conn:
            conn.execute(
                "INSERT INTO release_packages(package_id, revision, title, status, effective_from,"
                " supersedes_package_id, payload_hash, signature, published_at)"
                " VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?)",
                (
                    payload["package_id"],
                    revision,
                    payload["title"],
                    payload["effective_from"],
                    supersedes,
                    digest,
                    signature,
                    recorded,
                ),
            )
            for entry in entries:
                conn.execute(
                    "INSERT INTO release_entries(package_id, entry_key, language_code, place_category,"
                    " source_text, target_text, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        payload["package_id"],
                        entry["entry_key"],
                        entry["language_code"],
                        entry["place_category"],
                        entry["source_text"],
                        entry["target_text"],
                        entry.get("note", ""),
                    ),
                )
            for exc in body.get("exceptions") or []:
                conn.execute(
                    "INSERT INTO release_exceptions(package_id, exception_key, language_code,"
                    " place_category, description) VALUES (?, ?, ?, ?, ?)",
                    (
                        payload["package_id"],
                        exc["exception_key"],
                        exc["language_code"],
                        exc["place_category"],
                        exc["description"],
                    ),
                )
            for doc in body.get("basis_documents") or []:
                conn.execute(
                    "INSERT INTO release_basis_documents(package_id, document_key, title, reference)"
                    " VALUES (?, ?, ?, ?)",
                    (payload["package_id"], doc["document_key"], doc["title"], doc["reference"]),
                )
            conn.execute(
                "INSERT INTO package_events(package_id, action, effective_at, recorded_at)"
                " VALUES (?, 'published', ?, ?)",
                (payload["package_id"], recorded, recorded),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "package_exists", "发布包编号或修订号已存在") from exc

    candidates = generate_candidates(conn, payload["package_id"])
    view = get_package_view(conn, payload["package_id"])
    view["candidates_generated"] = candidates
    return view


def deprecate_package(
    conn: sqlite3.Connection, package_id: str, effective_at: str, note: str = ""
) -> dict[str, Any]:
    package = _package_row(conn, package_id)
    parse_time(effective_at, "effective_at")
    try:
        with conn:
            conn.execute(
                "INSERT INTO package_events(package_id, action, effective_at, note, recorded_at)"
                " VALUES (?, 'deprecated', ?, ?, ?)",
                (package_id, effective_at, note, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "already_deprecated", f"发布包 {package_id} 已记录废止") from exc
    return get_package_view(conn, package["package_id"])


def _package_row(conn: sqlite3.Connection, package_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM release_packages WHERE package_id = ?", (package_id,)
    ).fetchone()
    if not row:
        raise DomainError(404, "package_not_found", f"发布包 {package_id} 不存在")
    return row


def _deprecated_at(conn: sqlite3.Connection, package_id: str) -> str | None:
    row = conn.execute(
        "SELECT effective_at FROM package_events WHERE package_id = ? AND action = 'deprecated'",
        (package_id,),
    ).fetchone()
    return row["effective_at"] if row else None


def package_status_at(conn: sqlite3.Connection, package: sqlite3.Row, at: datetime) -> str:
    """发布包在某一时刻的状态：announced / effective / deprecated。"""
    deprecated = _deprecated_at(conn, package["package_id"])
    if deprecated and parse_time(deprecated, "deprecated_at") <= at:
        return "deprecated"
    if parse_time(package["effective_from"], "effective_from") <= at:
        return "effective"
    return "announced"


def recommended_package(conn: sqlite3.Connection, at: datetime) -> sqlite3.Row | None:
    """当前推荐：该时刻已生效且未废止的发布包中修订号最大者。"""
    rows = conn.execute("SELECT * FROM release_packages ORDER BY revision").fetchall()
    best = None
    for row in rows:
        if package_status_at(conn, row, at) == "effective":
            if best is None or row["revision"] > best["revision"]:
                best = row
    return best


def _package_content(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    entries = [
        dict(row)
        for row in conn.execute(
            "SELECT entry_key, language_code, place_category, source_text, target_text, note"
            " FROM release_entries WHERE package_id = ? ORDER BY entry_key",
            (package_id,),
        )
    ]
    exceptions = [
        dict(row)
        for row in conn.execute(
            "SELECT exception_key, language_code, place_category, description"
            " FROM release_exceptions WHERE package_id = ? ORDER BY exception_key",
            (package_id,),
        )
    ]
    basis = [
        dict(row)
        for row in conn.execute(
            "SELECT document_key, title, reference"
            " FROM release_basis_documents WHERE package_id = ? ORDER BY document_key",
            (package_id,),
        )
    ]
    return {"entries": entries, "exceptions": exceptions, "basis_documents": basis}


def get_package_view(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    package = _package_row(conn, package_id)
    content = _package_content(conn, package_id)
    payload = {
        "package_id": package["package_id"],
        "revision": package["revision"],
        "title": package["title"],
        "effective_from": package["effective_from"],
        "supersedes_package_id": package["supersedes_package_id"],
        **content,
    }
    now = datetime.now(timezone.utc)
    return {
        **payload,
        "payload_hash": package["payload_hash"],
        "signature": package["signature"],
        "signature_valid": security.verify(payload, package["payload_hash"], package["signature"]),
        "published_at": package["published_at"],
        "deprecated_at": _deprecated_at(conn, package_id),
        "status": package_status_at(conn, package, now),
    }


def list_packages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT package_id FROM release_packages ORDER BY revision").fetchall()
    return [get_package_view(conn, row["package_id"]) for row in rows]


def download_package(conn: sqlite3.Connection, package_id: str, purpose: str) -> dict[str, Any]:
    """下载发布包。history_query 任意已发布包可用；new_sign 仅限当前推荐包。"""
    package = _package_row(conn, package_id)
    now = datetime.now(timezone.utc)
    if purpose == "new_sign":
        current = recommended_package(conn, now)
        if not current or current["package_id"] != package_id:
            raise DomainError(
                409,
                "not_current_package",
                f"发布包 {package_id} 不是当前推荐版本，新建标识不得引用",
            )
    elif purpose != "history_query":
        raise DomainError(422, "invalid_purpose", "purpose 只能是 history_query 或 new_sign")
    view = get_package_view(conn, package_id)
    view["download_purpose"] = purpose
    return view


# ---------------------------------------------------------------- 差异与候选

def _entries_map(conn: sqlite3.Connection, package_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM release_entries WHERE package_id = ?", (package_id,)
    ).fetchall()
    return {row["entry_key"]: row for row in rows}


def compute_diffs(
    conn: sqlite3.Connection, from_package_id: str, to_package_id: str
) -> list[dict[str, Any]]:
    """计算两个发布包之间的词条差异并落库（幂等），返回差异清单。"""
    _package_row(conn, from_package_id)
    _package_row(conn, to_package_id)
    old = _entries_map(conn, from_package_id)
    new = _entries_map(conn, to_package_id)
    recorded = now_iso()
    with conn:
        for entry_key in sorted(set(old) | set(new)):
            before = old.get(entry_key)
            after = new.get(entry_key)
            if before and not after:
                change_type, old_target, new_target = "removed", before["target_text"], None
                language, category = before["language_code"], before["place_category"]
            elif after and not before:
                change_type, old_target, new_target = "added", None, after["target_text"]
                language, category = after["language_code"], after["place_category"]
            elif (
                before["target_text"] != after["target_text"]
                or before["note"] != after["note"]
                or before["language_code"] != after["language_code"]
                or before["place_category"] != after["place_category"]
            ):
                change_type, old_target, new_target = "modified", before["target_text"], after["target_text"]
                language, category = after["language_code"], after["place_category"]
            else:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO entry_diffs(diff_id, from_package_id, to_package_id, entry_key,"
                " language_code, place_category, change_type, old_target, new_target, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{from_package_id}--{to_package_id}--{entry_key}",
                    from_package_id,
                    to_package_id,
                    entry_key,
                    language,
                    category,
                    change_type,
                    old_target,
                    new_target,
                    recorded,
                ),
            )
    rows = conn.execute(
        "SELECT * FROM entry_diffs WHERE from_package_id = ? AND to_package_id = ? ORDER BY entry_key",
        (from_package_id, to_package_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _ancestry(conn: sqlite3.Connection, package_id: str) -> set[str]:
    """发布包沿 supersedes 链向上的全部祖先编号。"""
    seen: set[str] = set()
    cursor = package_id
    while True:
        row = conn.execute(
            "SELECT supersedes_package_id FROM release_packages WHERE package_id = ?", (cursor,)
        ).fetchone()
        if not row or not row["supersedes_package_id"] or row["supersedes_package_id"] in seen:
            return seen
        seen.add(row["supersedes_package_id"])
        cursor = row["supersedes_package_id"]


def _current_adoptions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """每个标识每个语种最新一条上报记录（后写入者优先）。"""
    return conn.execute(
        "SELECT r.*, s.place_category, s.unit_ref FROM report_events r"
        " JOIN signs s ON s.sign_ref = r.sign_ref"
        " WHERE r.id IN (SELECT MAX(id) FROM report_events GROUP BY sign_ref, language_code)"
    ).fetchall()


def generate_candidates(conn: sqlite3.Connection, to_package_id: str) -> int:
    """针对新发布包，为仍引用其祖先包的现行标识生成整改候选。幂等。"""
    _package_row(conn, to_package_id)
    ancestors = _ancestry(conn, to_package_id)
    if not ancestors:
        return 0
    created = 0
    adoptions = _current_adoptions(conn)
    packages_in_use = {row["package_id"] for row in adoptions} & ancestors
    for old_package in packages_in_use:
        compute_diffs(conn, old_package, to_package_id)
    recorded = now_iso()
    with conn:
        for adoption in adoptions:
            if adoption["package_id"] not in ancestors:
                continue
            diff = conn.execute(
                "SELECT * FROM entry_diffs WHERE from_package_id = ? AND to_package_id = ?"
                " AND entry_key = ? AND language_code = ?",
                (
                    adoption["package_id"],
                    to_package_id,
                    adoption["entry_key"],
                    adoption["language_code"],
                ),
            ).fetchone()
            if not diff or diff["change_type"] == "added":
                continue
            candidate_id = f"{diff['diff_id']}--{adoption['sign_ref']}--{adoption['language_code']}"
            cursor = conn.execute(
                "INSERT OR IGNORE INTO update_candidates(candidate_id, diff_id, sign_ref, language_code,"
                " place_category, current_translation, suggested_translation, based_on_revision,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    candidate_id,
                    diff["diff_id"],
                    adoption["sign_ref"],
                    adoption["language_code"],
                    adoption["place_category"],
                    adoption["translation"],
                    diff["new_target"],
                    adoption["translation_revision"],
                    recorded,
                ),
            )
            created += cursor.rowcount
    return created


def list_diffs(
    conn: sqlite3.Connection, from_package_id: str, to_package_id: str
) -> dict[str, Any]:
    """可复核的差异与影响清单：词条差异 + 受影响标识。"""
    diffs = compute_diffs(conn, from_package_id, to_package_id)
    impacts = []
    for diff in diffs:
        if diff["change_type"] == "added":
            continue
        rows = conn.execute(
            "SELECT candidate_id, sign_ref, language_code, status FROM update_candidates"
            " WHERE diff_id = ? ORDER BY sign_ref",
            (diff["diff_id"],),
        ).fetchall()
        impacts.append({**diff, "affected": [dict(row) for row in rows]})
    return {
        "from_package_id": from_package_id,
        "to_package_id": to_package_id,
        "diffs": diffs,
        "impacts": impacts,
    }


# ---------------------------------------------------------------- 候选与整改要求

def _candidate_row(conn: sqlite3.Connection, candidate_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM update_candidates WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    if not row:
        raise DomainError(404, "candidate_not_found", f"候选 {candidate_id} 不存在")
    return row


def decide_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    action: str,
    decided_by: str,
    note: str = "",
) -> dict[str, Any]:
    """专业人员确认或驳回候选；确认后生成整改要求。决定不可更改。"""
    if action not in ("confirm", "dismiss"):
        raise DomainError(422, "invalid_action", "action 只能是 confirm 或 dismiss")
    if not decided_by:
        raise DomainError(422, "missing_decider", "必须提供决定人引用")
    candidate = _candidate_row(conn, candidate_id)
    if candidate["status"] != "pending":
        raise DomainError(409, "already_decided", f"候选 {candidate_id} 已被处理，决定不可更改")
    new_status = "confirmed" if action == "confirm" else "dismissed"
    recorded = now_iso()
    requirement_id = None
    with conn:
        conn.execute(
            "UPDATE update_candidates SET status = ?, decided_by = ?, decided_at = ?,"
            " decision_note = ? WHERE candidate_id = ?",
            (new_status, decided_by, recorded, note, candidate_id),
        )
        if action == "confirm":
            if candidate["suggested_translation"] is None:
                raise DomainError(409, "entry_removed", "词条已废止的候选需以驳回处理")
            requirement_id = f"REQ--{candidate_id}"
            sign = _require_sign(conn, candidate["sign_ref"])
            diff = conn.execute(
                "SELECT * FROM entry_diffs WHERE diff_id = ?", (candidate["diff_id"],)
            ).fetchone()
            conn.execute(
                "INSERT INTO requirements(requirement_id, candidate_id, sign_ref, unit_ref,"
                " language_code, entry_key, target_package_id, required_translation,"
                " based_on_revision, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    requirement_id,
                    candidate_id,
                    candidate["sign_ref"],
                    sign["unit_ref"],
                    candidate["language_code"],
                    diff["entry_key"],
                    diff["to_package_id"],
                    candidate["suggested_translation"],
                    candidate["based_on_revision"],
                    recorded,
                ),
            )
    result = dict(
        conn.execute(
            "SELECT * FROM update_candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
    )
    result["requirement_id"] = requirement_id
    return result


def commit_requirement(
    conn: sqlite3.Connection, requirement_id: str, committed_deadline: str
) -> dict[str, Any]:
    parse_time(committed_deadline, "committed_deadline")
    row = _requirement_row(conn, requirement_id)
    if row["status"] != "open":
        raise DomainError(409, "requirement_closed", "已完成的整改要求不能再承诺期限")
    with conn:
        conn.execute(
            "UPDATE requirements SET committed_deadline = ?, committed_at = ? WHERE requirement_id = ?",
            (committed_deadline, now_iso(), requirement_id),
        )
    return dict(_requirement_row(conn, requirement_id))


def _requirement_row(conn: sqlite3.Connection, requirement_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM requirements WHERE requirement_id = ?", (requirement_id,)
    ).fetchone()
    if not row:
        raise DomainError(404, "requirement_not_found", f"整改要求 {requirement_id} 不存在")
    return row


def list_candidates(
    conn: sqlite3.Connection, status: str | None = None, sign_ref: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM update_candidates WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if sign_ref:
        sql += " AND sign_ref = ?"
        params.append(sign_ref)
    sql += " ORDER BY created_at, candidate_id"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def list_requirements(
    conn: sqlite3.Connection,
    unit_ref: str | None = None,
    status: str | None = None,
    target_package_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM requirements WHERE 1=1"
    params: list[Any] = []
    if unit_ref:
        sql += " AND unit_ref = ?"
        params.append(unit_ref)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if target_package_id:
        sql += " AND target_package_id = ?"
        params.append(target_package_id)
    sql += " ORDER BY created_at, requirement_id"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------- 同步与完成

def ingest_batch(
    conn: sqlite3.Connection, source_system: str, batch_key: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """接收一批上报事件。批次幂等、事件幂等、序列号只前进。"""
    if not source_system or not batch_key:
        raise DomainError(422, "missing_field", "source_system 与 batch_key 必填")
    existing = conn.execute(
        "SELECT * FROM sync_batches WHERE batch_key = ?", (batch_key,)
    ).fetchone()
    if existing:
        return {
            "batch_key": batch_key,
            "duplicate_batch": True,
            "accepted": existing["accepted"],
            "duplicates": existing["duplicates"],
            "rejected": existing["rejected"],
            "results": [],
        }

    accepted = duplicates = 0
    rejected: list[dict[str, Any]] = []
    accepted_packages: set[str] = set()
    with conn:
        for event in events:
            outcome = _ingest_event(conn, source_system, event)
            if outcome["result"] == "accepted":
                accepted += 1
                if event.get("package_id"):
                    accepted_packages.add(event["package_id"])
            elif outcome["result"] == "duplicate":
                duplicates += 1
            else:
                rejected.append(outcome)
        conn.execute(
            "INSERT INTO sync_batches(batch_key, source_system, accepted, duplicates, rejected,"
            " first_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_key, source_system, accepted, duplicates, len(rejected), now_iso()),
        )
    # 迟到的上报可能落在某个新包的祖先上：为受影响的新包补齐候选（幂等）。
    if accepted_packages:
        for row in conn.execute("SELECT package_id FROM release_packages").fetchall():
            newer = row["package_id"]
            if newer not in accepted_packages and _ancestry(conn, newer) & accepted_packages:
                generate_candidates(conn, newer)
    return {
        "batch_key": batch_key,
        "duplicate_batch": False,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": len(rejected),
        "results": rejected,
    }


def _ingest_event(conn: sqlite3.Connection, source_system: str, event: dict[str, Any]) -> dict[str, Any]:
    event_id = event.get("event_id")
    sign_ref = event.get("sign_ref")
    language = event.get("language_code")

    def reject(reason: str) -> dict[str, Any]:
        return {"result": "rejected", "event_id": event_id, "sign_ref": sign_ref, "reason": reason}

    if not event_id or not sign_ref or not language:
        return reject("缺少 event_id / sign_ref / language_code")
    try:
        seq = int(event.get("seq"))
        revision = int(event.get("translation_revision"))
    except (TypeError, ValueError):
        return reject("seq 与 translation_revision 必须是整数")
    try:
        reported_at = parse_time(event.get("reported_at", ""), "reported_at")
    except DomainError:
        return reject("reported_at 必须是带偏移量的 ISO 8601 时间")
    entry_key = event.get("entry_key")
    package_id = event.get("package_id")
    translation = event.get("translation")
    if not entry_key or not package_id or translation is None:
        return reject("缺少 entry_key / package_id / translation")

    if conn.execute(
        "SELECT 1 FROM report_events WHERE source_system = ? AND event_id = ?",
        (source_system, event_id),
    ).fetchone():
        return {"result": "duplicate", "event_id": event_id, "sign_ref": sign_ref}

    sign = conn.execute("SELECT * FROM signs WHERE sign_ref = ?", (sign_ref,)).fetchone()
    if not sign:
        return reject("标识未登记")
    if not conn.execute(
        "SELECT 1 FROM release_packages WHERE package_id = ?", (package_id,)
    ).fetchone():
        return reject("引用的发布包不存在")
    if not conn.execute(
        "SELECT 1 FROM release_entries WHERE package_id = ? AND entry_key = ? AND language_code = ?",
        (package_id, entry_key, language),
    ).fetchone():
        return reject("发布包中不存在对应词条")
    if not _subscription_covers(conn, sign["unit_ref"], sign, language):
        return reject("责任单位未订阅该标识的管理范围")

    cursor = conn.execute(
        "SELECT last_seq FROM sync_cursors WHERE source_system = ? AND sign_ref = ? AND language_code = ?",
        (source_system, sign_ref, language),
    ).fetchone()
    if cursor and seq <= cursor["last_seq"]:
        return reject(f"序列号回退（当前游标 {cursor['last_seq']}），事件已过期")

    recorded = now_iso()
    conn.execute(
        "INSERT INTO report_events(source_system, event_id, seq, reported_at, sign_ref,"
        " language_code, entry_key, translation_revision, package_id, translation, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_system,
            event_id,
            seq,
            iso(reported_at),
            sign_ref,
            language,
            entry_key,
            revision,
            package_id,
            translation,
            recorded,
        ),
    )
    conn.execute(
        "INSERT INTO sync_cursors(source_system, sign_ref, language_code, last_seq) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(source_system, sign_ref, language_code) DO UPDATE SET last_seq = excluded.last_seq",
        (source_system, sign_ref, language, seq),
    )
    _check_completions(conn, source_system, event_id, sign_ref, language, entry_key, revision,
                       package_id, translation, iso(reported_at), recorded)
    return {"result": "accepted", "event_id": event_id, "sign_ref": sign_ref}


def _is_descendant(conn: sqlite3.Connection, package_id: str, ancestor_id: str) -> bool:
    return package_id == ancestor_id or ancestor_id in _ancestry(conn, package_id)


def _check_completions(
    conn: sqlite3.Connection,
    source_system: str,
    event_id: str,
    sign_ref: str,
    language: str,
    entry_key: str,
    revision: int,
    package_id: str,
    translation: str,
    reported_at: str,
    recorded: str,
) -> None:
    """上报事件若满足某个未结整改要求，则记录完成事件并结转。完成只前进不后退。"""
    rows = conn.execute(
        "SELECT * FROM requirements WHERE sign_ref = ? AND language_code = ? AND entry_key = ?"
        " AND status = 'open'",
        (sign_ref, language, entry_key),
    ).fetchall()
    for requirement in rows:
        if revision <= requirement["based_on_revision"]:
            continue
        if translation != requirement["required_translation"]:
            continue
        if not _is_descendant(conn, package_id, requirement["target_package_id"]):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO completion_events(source_system, event_id, requirement_id,"
            " sign_ref, language_code, translation_revision, package_id, translation,"
            " reported_at, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_system,
                event_id,
                requirement["requirement_id"],
                sign_ref,
                language,
                revision,
                package_id,
                translation,
                reported_at,
                recorded,
            ),
        )
        conn.execute(
            "UPDATE requirements SET status = 'completed', completed_at = ? WHERE requirement_id = ?",
            (reported_at, requirement["requirement_id"]),
        )


# ---------------------------------------------------------------- 限时豁免

def apply_exemption(
    conn: sqlite3.Connection,
    exemption_id: str,
    sign_ref: str,
    reason: str,
    valid_from: str,
    valid_until: str,
    decided_by: str,
    requirement_id: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    _require_sign(conn, sign_ref)
    if requirement_id:
        requirement = _requirement_row(conn, requirement_id)
        if requirement["sign_ref"] != sign_ref:
            raise DomainError(422, "mismatch", "豁免的标识与整改要求不一致")
    start = parse_time(valid_from, "valid_from")
    end = parse_time(valid_until, "valid_until")
    if end <= start:
        raise DomainError(422, "invalid_window", "valid_until 必须晚于 valid_from")
    if not reason or not decided_by:
        raise DomainError(422, "missing_field", "reason 与 decided_by 必填")
    try:
        with conn:
            conn.execute(
                "INSERT INTO exemption_events(exemption_id, action, sign_ref, requirement_id, reason,"
                " valid_from, valid_until, decided_by, note, recorded_at)"
                " VALUES (?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?)",
                # 决定原文照存（含原始时区偏移），只追加不修改
                (exemption_id, sign_ref, requirement_id, reason, valid_from, valid_until,
                 decided_by, note, now_iso()),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "exemption_exists", f"豁免 {exemption_id} 已申请") from exc
    return exemption_state(conn, exemption_id)


def _exemption_action(
    conn: sqlite3.Connection,
    exemption_id: str,
    action: str,
    decided_by: str,
    note: str = "",
) -> dict[str, Any]:
    events = conn.execute(
        "SELECT * FROM exemption_events WHERE exemption_id = ? ORDER BY id", (exemption_id,)
    ).fetchall()
    if not events:
        raise DomainError(404, "exemption_not_found", f"豁免 {exemption_id} 不存在")
    actions = {event["action"] for event in events}
    state = exemption_state(conn, exemption_id)
    if action == "approved":
        if state["status"] != "applied":
            raise DomainError(409, "invalid_transition", "只有待批准的豁免可以批准")
    elif action == "revoked":
        if state["status"] != "active":
            raise DomainError(409, "invalid_transition", "只有生效中的豁免可以撤销")
    elif action == "expired":
        if "approved" not in actions or "expired" in actions or "revoked" in actions:
            raise DomainError(409, "invalid_transition", "只有已批准且未到期未撤销的豁免可以登记到期")
    applied = events[0]
    try:
        with conn:
            conn.execute(
                "INSERT INTO exemption_events(exemption_id, action, sign_ref, requirement_id, reason,"
                " valid_from, valid_until, decided_by, note, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    exemption_id,
                    action,
                    applied["sign_ref"],
                    applied["requirement_id"],
                    applied["reason"],
                    applied["valid_from"],
                    applied["valid_until"],
                    decided_by,
                    note,
                    now_iso(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise DomainError(409, "decision_exists", f"豁免 {exemption_id} 的 {action} 决定已存在") from exc
    return exemption_state(conn, exemption_id)


def approve_exemption(conn: sqlite3.Connection, exemption_id: str, decided_by: str, note: str = "") -> dict[str, Any]:
    return _exemption_action(conn, exemption_id, "approved", decided_by, note)


def revoke_exemption(conn: sqlite3.Connection, exemption_id: str, decided_by: str, note: str = "") -> dict[str, Any]:
    return _exemption_action(conn, exemption_id, "revoked", decided_by, note)


def expire_exemption(conn: sqlite3.Connection, exemption_id: str, decided_by: str = "system") -> dict[str, Any]:
    return _exemption_action(conn, exemption_id, "expired", decided_by)


def exemption_state(conn: sqlite3.Connection, exemption_id: str) -> dict[str, Any]:
    events = conn.execute(
        "SELECT * FROM exemption_events WHERE exemption_id = ? ORDER BY id", (exemption_id,)
    ).fetchall()
    if not events:
        raise DomainError(404, "exemption_not_found", f"豁免 {exemption_id} 不存在")
    actions = {event["action"] for event in events}
    applied = events[0]
    now = datetime.now(timezone.utc)
    if "revoked" in actions:
        status = "revoked"
    elif "expired" in actions:
        status = "expired"
    elif "approved" not in actions:
        status = "applied"
    elif parse_time(applied["valid_until"], "valid_until") < now:
        status = "expired"  # 派生到期；原批准决定仍在事件流中
    elif parse_time(applied["valid_from"], "valid_from") <= now:
        status = "active"
    else:
        status = "approved"
    return {
        "exemption_id": exemption_id,
        "sign_ref": applied["sign_ref"],
        "requirement_id": applied["requirement_id"],
        "reason": applied["reason"],
        "valid_from": applied["valid_from"],
        "valid_until": applied["valid_until"],
        "status": status,
        "history": [dict(event) for event in events],
    }


def active_exemption_signs(conn: sqlite3.Connection, at: datetime | None = None) -> set[str]:
    """当前处于生效豁免中的标识集合。"""
    at = at or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT exemption_id FROM exemption_events GROUP BY exemption_id"
    ).fetchall()
    active: set[str] = set()
    for row in rows:
        state = exemption_state(conn, row["exemption_id"])
        if state["status"] == "active":
            active.add(state["sign_ref"])
    return active


def list_exemptions(conn: sqlite3.Connection, sign_ref: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT DISTINCT exemption_id FROM exemption_events"
    params: list[Any] = []
    if sign_ref:
        sql += " WHERE sign_ref = ?"
        params.append(sign_ref)
    return [exemption_state(conn, row["exemption_id"]) for row in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------- 查询与分析

def sign_trace(conn: sqlite3.Connection, sign_ref: str) -> dict[str, Any]:
    """标识全量版本轨迹：每次上报引用的发布包及其当时的合法性。"""
    sign = _require_sign(conn, sign_ref)
    events = conn.execute(
        "SELECT * FROM report_events WHERE sign_ref = ? ORDER BY id", (sign_ref,)
    ).fetchall()
    versions = []
    for event in events:
        package = _package_row(conn, event["package_id"])
        reported_at = parse_time(event["reported_at"], "reported_at")
        status_then = package_status_at(conn, package, reported_at)
        versions.append(
            {
                "source_system": event["source_system"],
                "event_id": event["event_id"],
                "seq": event["seq"],
                "language_code": event["language_code"],
                "entry_key": event["entry_key"],
                "translation_revision": event["translation_revision"],
                "translation": event["translation"],
                "package_id": event["package_id"],
                "package_revision": package["revision"],
                "package_signature": package["signature"],
                "reported_at": event["reported_at"],
                "package_status_then": status_then,
                "reference_legitimate_then": status_then == "effective",
            }
        )
    return {"sign": dict(sign), "versions": versions}


def version_package_lookup(
    conn: sqlite3.Connection, sign_ref: str, translation_revision: int
) -> dict[str, Any]:
    """从任一标识版本反查它当时合法引用的发布包。"""
    _require_sign(conn, sign_ref)
    events = conn.execute(
        "SELECT * FROM report_events WHERE sign_ref = ? AND translation_revision = ? ORDER BY id",
        (sign_ref, translation_revision),
    ).fetchall()
    if not events:
        raise DomainError(404, "version_not_found", "该标识不存在此译文版本")
    answers = []
    for event in events:
        view = get_package_view(conn, event["package_id"])
        reported_at = parse_time(event["reported_at"], "reported_at")
        package = _package_row(conn, event["package_id"])
        answers.append(
            {
                "language_code": event["language_code"],
                "entry_key": event["entry_key"],
                "translation": event["translation"],
                "reported_at": event["reported_at"],
                "package": view,
                "package_status_then": package_status_at(conn, package, reported_at),
            }
        )
    return {"sign_ref": sign_ref, "translation_revision": translation_revision, "references": answers}


def public_sign_view(conn: sqlite3.Connection, sign_ref: str) -> dict[str, Any]:
    """公开查询：当前推荐译文与现场实际译文并排，换版期间差异显式标注。"""
    sign = _require_sign(conn, sign_ref)
    now = datetime.now(timezone.utc)
    current = recommended_package(conn, now)
    adoptions = conn.execute(
        "SELECT * FROM report_events WHERE sign_ref = ? AND id IN"
        " (SELECT MAX(id) FROM report_events WHERE sign_ref = ? GROUP BY language_code)",
        (sign_ref, sign_ref),
    ).fetchall()
    languages = []
    for adoption in adoptions:
        recommended_entry = None
        if current:
            recommended_entry = conn.execute(
                "SELECT * FROM release_entries WHERE package_id = ? AND entry_key = ?"
                " AND language_code = ?",
                (current["package_id"], adoption["entry_key"], adoption["language_code"]),
            ).fetchone()
        on_site_package = _package_row(conn, adoption["package_id"])
        matches = bool(
            recommended_entry and adoption["translation"] == recommended_entry["target_text"]
        )
        languages.append(
            {
                "language_code": adoption["language_code"],
                "entry_key": adoption["entry_key"],
                "on_site_translation": adoption["translation"],
                "on_site_package_id": adoption["package_id"],
                "on_site_package_revision": on_site_package["revision"],
                "recommended_translation": recommended_entry["target_text"] if recommended_entry else None,
                "recommended_package_id": current["package_id"] if current else None,
                "recommended_package_revision": current["revision"] if current else None,
                "matches_recommendation": matches,
                "transitioning": bool(current and adoption["package_id"] != current["package_id"]),
            }
        )
    return {
        "sign_ref": sign["sign_ref"],
        "location_code": sign["location_code"],
        "place_category": sign["place_category"],
        "carrier_type": sign["carrier_type"],
        "languages": languages,
    }


def package_adoption_analytics(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    """按一条规范修订汇总：受影响载体、责任单位、承诺期限、真实完成比例。"""
    package = _package_row(conn, package_id)
    now = datetime.now(timezone.utc)
    exempted_signs = active_exemption_signs(conn, now)

    candidates = conn.execute(
        "SELECT c.* FROM update_candidates c JOIN entry_diffs d ON d.diff_id = c.diff_id"
        " WHERE d.to_package_id = ?",
        (package_id,),
    ).fetchall()
    requirements = conn.execute(
        "SELECT * FROM requirements WHERE target_package_id = ? ORDER BY unit_ref, requirement_id",
        (package_id,),
    ).fetchall()

    req_views = []
    completed = open_count = exempted_open = overdue_open = 0
    for requirement in requirements:
        sign = conn.execute(
            "SELECT * FROM signs WHERE sign_ref = ?", (requirement["sign_ref"],)
        ).fetchone()
        exempted = requirement["status"] == "open" and requirement["sign_ref"] in exempted_signs
        overdue = (
            requirement["status"] == "open"
            and not exempted
            and requirement["committed_deadline"] is not None
            and parse_time(requirement["committed_deadline"], "committed_deadline") < now
        )
        if requirement["status"] == "completed":
            completed += 1
        elif exempted:
            exempted_open += 1
        else:
            open_count += 1
            if overdue:
                overdue_open += 1
        req_views.append(
            {
                **dict(requirement),
                "carrier_type": sign["carrier_type"],
                "place_category": sign["place_category"],
                "exempted": exempted,
                "overdue": overdue,
            }
        )

    total = len(requirements)
    affected_signs = {candidate["sign_ref"] for candidate in candidates}
    return {
        "package_id": package_id,
        "revision": package["revision"],
        "title": package["title"],
        "affected_signs": sorted(affected_signs),
        "units": sorted({requirement["unit_ref"] for requirement in requirements}),
        "candidates": {
            "total": len(candidates),
            "pending": sum(1 for c in candidates if c["status"] == "pending"),
            "confirmed": sum(1 for c in candidates if c["status"] == "confirmed"),
            "dismissed": sum(1 for c in candidates if c["status"] == "dismissed"),
        },
        "requirements": req_views,
        "summary": {
            "total": total,
            "completed": completed,
            "open": open_count,
            "exempted_open": exempted_open,
            "overdue_open": overdue_open,
            "completion_ratio": (completed / total) if total else None,
            "effective_denominator": total - exempted_open,
            "adjusted_completion_ratio": (
                completed / (total - exempted_open) if total - exempted_open > 0 else None
            ),
        },
    }
