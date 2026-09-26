"""发布包：签发（签名+版本链）、生命周期推导、现行推荐、历史状态。"""

import json
import sqlite3
from typing import Any, Literal

from app import clock, security
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import PackageIssueRequest

LifecycleStatus = Literal["pending", "active", "superseded", "withdrawn"]


def _signed_payload(req: PackageIssueRequest, sequence_no: int, prev_package_id: str | None,
                    issued_at: str) -> dict[str, Any]:
    return {
        "category": req.category,
        "language_code": req.language_code,
        "sequence_no": sequence_no,
        "prev_package_id": prev_package_id,
        "issued_at": issued_at,
        "issued_by": req.issued_by,
        "effective_at": req.effective_at,
        "basis_docs": [d.model_dump() for d in req.basis_docs],
        "entries": {k: v.model_dump() for k, v in req.entries.items()},
        "exceptions": [e.model_dump() for e in req.exceptions],
    }


def _derive_id(canonical: bytes) -> str:
    return "PKG-" + security.sha256_hex(canonical)[:16].upper()


def issue_package(conn: sqlite3.Connection, req: PackageIssueRequest) -> dict[str, Any]:
    effective = clock.parse(req.effective_at)  # 校验带偏移量
    issued_at = clock.now_iso()
    if not req.entries:
        raise ValidationError("发布包至少包含一个标准词条")

    row = conn.execute(
        "SELECT package_id, sequence_no FROM packages WHERE category = ? AND language_code = ?"
        " ORDER BY sequence_no DESC LIMIT 1",
        (req.category, req.language_code),
    ).fetchone()
    prev_package_id = row["package_id"] if row else None
    sequence_no = (row["sequence_no"] + 1) if row else 1

    payload = _signed_payload(req, sequence_no, prev_package_id, issued_at)
    canonical = security.canonical_bytes(payload)
    package_id = _derive_id(canonical)
    if conn.execute("SELECT 1 FROM packages WHERE package_id = ?", (package_id,)).fetchone():
        raise ConflictError("内容完全相同的发布包已存在", code="duplicate_package")

    conn.execute(
        "INSERT INTO packages (package_id, prev_package_id, category, language_code, sequence_no,"
        " issued_at, issued_by, effective_at, basis_docs, entries_json, exceptions_json,"
        " canonical_bytes, signature, chain_ok)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (package_id, prev_package_id, req.category, req.language_code, sequence_no,
         issued_at, req.issued_by, clock.to_iso(effective),
         json.dumps(payload["basis_docs"], ensure_ascii=False),
         json.dumps(payload["entries"], ensure_ascii=False),
         json.dumps(payload["exceptions"], ensure_ascii=False),
         canonical, security.sign(canonical)),
    )
    conn.execute(
        "INSERT INTO package_events (package_id, event_type, actor, reason, recorded_at,"
        " effective_at) VALUES (?, 'issued', ?, ?, ?, ?)",
        (package_id, req.issued_by, "", issued_at, clock.to_iso(effective)),
    )
    # 旧包自新包生效之时起被取代：决定在签发时做出(recorded_at)，效力在新包生效时发生
    if prev_package_id:
        conn.execute(
            "INSERT INTO package_events (package_id, event_type, actor, reason, recorded_at,"
            " effective_at) VALUES (?, 'supersede', ?, ?, ?, ?)",
            (prev_package_id, req.issued_by, f"被 {package_id} 取代",
             issued_at, clock.to_iso(effective)),
        )
    # 向订阅该类别的单位投递
    for sub in conn.execute(
        "SELECT unit_id FROM subscriptions WHERE category = ?", (req.category,)
    ):
        conn.execute(
            "INSERT INTO notifications (unit_id, package_id, category, notified_at)"
            " VALUES (?,?,?,?)",
            (sub["unit_id"], package_id, req.category, issued_at),
        )
    conn.commit()
    return get_package(conn, package_id)


def withdraw_package(conn: sqlite3.Connection, package_id: str, actor: str, reason: str) -> dict[str, Any]:
    pkg = get_package(conn, package_id)
    if pkg["status"] == "withdrawn":
        raise ConflictError("发布包已废止")
    conn.execute(
        "INSERT INTO package_events (package_id, event_type, actor, reason, recorded_at,"
        " effective_at) VALUES (?, 'withdraw', ?, ?, ?, ?)",
        (package_id, actor, reason, clock.now_iso(), clock.now_iso()),
    )
    conn.commit()
    return get_package(conn, package_id)


def get_package(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM packages WHERE package_id = ?", (package_id,)).fetchone()
    if not row:
        raise NotFoundError(f"发布包不存在：{package_id}", code="package_not_found")
    pkg = _row_to_dict(row)
    pkg["status"] = status_at(conn, package_id)
    pkg["signature_valid"] = security.verify(row["canonical_bytes"], row["signature"])
    return pkg


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "package_id": row["package_id"],
        "prev_package_id": row["prev_package_id"],
        "category": row["category"],
        "language_code": row["language_code"],
        "sequence_no": row["sequence_no"],
        "issued_at": row["issued_at"],
        "issued_by": row["issued_by"],
        "effective_at": row["effective_at"],
        "basis_docs": json.loads(row["basis_docs"]),
        "entries": json.loads(row["entries_json"]),
        "exceptions": json.loads(row["exceptions_json"]),
        "signature": row["signature"],
        "chain_ok": bool(row["chain_ok"]),
    }


def _effective_event_before(conn: sqlite3.Connection, package_id: str,
                            at_iso: str) -> str | None:
    """时刻 T 已生效的最后一个生命周期事件：决定时刻与生效时刻都必须不晚于 T。"""
    row = conn.execute(
        "SELECT event_type FROM package_events"
        " WHERE package_id = ? AND recorded_at <= ? AND effective_at <= ?"
        " ORDER BY eid DESC LIMIT 1",
        (package_id, at_iso, at_iso),
    ).fetchone()
    return row["event_type"] if row else None


def status_at(conn: sqlite3.Connection, package_id: str, at: str | None = None) -> LifecycleStatus:
    """某时刻（默认现在）的生命周期状态，完全由已生效的事件链推导。

    历史时点 T 的状态只计入“在 T 时既已决定且已生效”的事件，因此一个在上位包发布前
    上报的旧版本，反查时仍能判定为当时 active、合法可引用。
    """
    row = conn.execute(
        "SELECT effective_at FROM packages WHERE package_id = ?", (package_id,)
    ).fetchone()
    if not row:
        raise NotFoundError(f"发布包不存在：{package_id}", code="package_not_found")
    at_iso = at or clock.now_iso()
    event = _effective_event_before(conn, package_id, at_iso)
    if event == "withdraw":
        return "withdrawn"
    if event == "supersede":
        return "superseded"
    return "active" if at_iso >= row["effective_at"] else "pending"


def current_recommended(conn: sqlite3.Connection, category: str, language_code: str,
                        at: str | None = None) -> dict[str, Any] | None:
    """现行推荐：沿版本链序号最大、在给定时刻处于 active 的包。"""
    at_iso = at or clock.now_iso()
    row = conn.execute(
        "SELECT package_id FROM packages WHERE category = ? AND language_code = ?"
        " ORDER BY sequence_no DESC",
        (category, language_code),
    ).fetchall()
    for candidate in row:
        if status_at(conn, candidate["package_id"], at_iso) == "active":
            return get_package(conn, candidate["package_id"])
    return None


def list_packages(conn: sqlite3.Connection, category: str | None = None,
                  language_code: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT package_id FROM packages WHERE 1=1"
    params: list[Any] = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if language_code:
        sql += " AND language_code = ?"
        params.append(language_code)
    sql += " ORDER BY category, language_code, sequence_no"
    return [get_package(conn, r["package_id"]) for r in conn.execute(sql, params)]
