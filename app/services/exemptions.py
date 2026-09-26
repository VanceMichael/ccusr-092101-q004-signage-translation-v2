"""限时豁免：申请/批准/到期/撤销全部是 append-only 决定事件，互不覆盖。

原批准决定永久保留在链上；到期与撤销以新事件叠加，当前状态由事件链推导。
"""

import sqlite3
from typing import Any, Literal

from app import clock
from app.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError

ExemptionStatus = Literal["none", "pending", "active", "expired", "revoked"]


def request_exemption(conn: sqlite3.Connection, unit_id: str, req_id: str,
                      reason: str, valid_until: str, kind: str) -> dict[str, Any]:
    from app.services import diffs as diff_service
    requirement = diff_service.get_requirement(conn, req_id)
    if requirement["unit_id"] != unit_id:
        raise ForbiddenError("只能对本单位的更新要求申请豁免")
    if requirement["completed_at"]:
        raise ConflictError("更新要求已完成，无需豁免")
    until = clock.parse(valid_until)
    if until <= clock.now():
        raise ValidationError("豁免截止时间必须晚于当前时间")
    recorded = clock.now_iso()
    conn.execute(
        "INSERT INTO exemptions (req_id, action, actor, reason, valid_until, recorded_at)"
        " VALUES (?, 'request', ?, ?, ?, ?)",
        (req_id, unit_id, f"[{kind}] {reason}", clock.to_iso(until), recorded),
    )
    conn.commit()
    return current_state(conn, req_id)


def _history(conn: sqlite3.Connection, req_id: str) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM exemptions WHERE req_id = ? ORDER BY id", (req_id,)))


def current_state(conn: sqlite3.Connection, req_id: str, at: str | None = None) -> dict[str, Any]:
    """从事件链推导当前豁免状态；任何历史决定都不被改写。"""
    at_iso = at or clock.now_iso()
    events = _history(conn, req_id)
    if not events:
        return {"status": "none", "history": []}

    latest_approval = None
    latest_action = None
    for event in events:
        if event["recorded_at"] > at_iso:
            break
        latest_action = event
        if event["action"] == "approve":
            latest_approval = event

    history = [dict(e) for e in events]
    if latest_action is None:
        return {"status": "none", "history": history}

    if latest_action["action"] == "request":
        status: ExemptionStatus = "pending"
    elif latest_action["action"] == "revoke":
        status = "revoked"
    elif latest_action["action"] == "expire":
        status = "expired"
    elif latest_action["action"] == "approve" and latest_approval is not None:
        status = "expired" if latest_approval["valid_until"] <= at_iso else "active"
    else:
        status = "none"

    result = {"status": status, "history": history}
    if latest_approval is not None:
        result["approved_at"] = latest_approval["recorded_at"]
        result["valid_until"] = latest_approval["valid_until"]
        result["approver"] = latest_approval["actor"]
    return result


def approve(conn: sqlite3.Connection, req_id: str, approver: str, valid_until: str,
            reason: str) -> dict[str, Any]:
    from app.services import diffs as diff_service
    diff_service.get_requirement(conn, req_id)  # 存在性
    state = current_state(conn, req_id)
    if state["status"] not in ("pending", "expired", "revoked"):
        raise ConflictError(f"当前豁免状态 {state['status']} 不可批准")
    until = clock.parse(valid_until)
    if until <= clock.now():
        raise ValidationError("批准的截止时间必须晚于当前时间")
    recorded = clock.now_iso()
    conn.execute(
        "INSERT INTO exemptions (req_id, action, actor, reason, valid_until, recorded_at)"
        " VALUES (?, 'approve', ?, ?, ?, ?)",
        (req_id, approver, reason, clock.to_iso(until), recorded),
    )
    conn.commit()
    return current_state(conn, req_id)


def revoke(conn: sqlite3.Connection, req_id: str, actor: str, reason: str) -> dict[str, Any]:
    from app.services import diffs as diff_service
    diff_service.get_requirement(conn, req_id)
    state = current_state(conn, req_id)
    if state["status"] != "active":
        raise ConflictError("仅生效中的豁免可以撤销")
    conn.execute(
        "INSERT INTO exemptions (req_id, action, actor, reason, valid_until, recorded_at)"
        " VALUES (?, 'revoke', ?, ?, NULL, ?)",
        (req_id, actor, reason, clock.now_iso()),
    )
    conn.commit()
    return current_state(conn, req_id)


def sweep_expirations(conn: sqlite3.Connection) -> list[str]:
    """把已过有效期但尚无到期事件的豁免追加 expire 决定（不覆盖批准记录）。"""
    now_iso = clock.now_iso()
    expired_reqs: list[str] = []
    rows = conn.execute(
        "SELECT DISTINCT req_id FROM exemptions"
    ).fetchall()
    for row in rows:
        req_id = row["req_id"]
        state = current_state(conn, req_id, now_iso)
        if state["status"] == "expired":
            latest = state["history"][-1]
            if latest["action"] == "approve":  # 仅时间已到、尚未登记到期事件
                conn.execute(
                    "INSERT INTO exemptions (req_id, action, actor, reason, valid_until, recorded_at)"
                    " VALUES (?, 'expire', 'system', '有效期届满', NULL, ?)",
                    (req_id, now_iso),
                )
                expired_reqs.append(req_id)
    if expired_reqs:
        conn.commit()
    return expired_reqs
