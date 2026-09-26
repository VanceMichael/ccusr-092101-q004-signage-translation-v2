"""新旧发布包差异、影响清单、候选与专业确认后的更新要求。

自动分析只能产生 proposed 候选；只有专业人员 confirm 才会生成 update_requirements，
进而“要求某个载体更新”。reject 同样留痕。
"""

import json
import sqlite3
from typing import Any

from app import clock
from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import CandidateDecisionRequest
from app.services import packages as pkg_service


def _entries(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT entries_json FROM packages WHERE package_id = ?",
                       (package_id,)).fetchone()
    if not row:
        raise NotFoundError(f"发布包不存在：{package_id}", code="package_not_found")
    return json.loads(row["entries_json"])


def compute_changes(old_entries: dict[str, Any], new_entries: dict[str, Any]) -> dict[str, list]:
    added, removed, changed = [], [], []
    for key, value in new_entries.items():
        if key not in old_entries:
            added.append({"term_key": key, **value})
        elif old_entries[key].get("translation") != value.get("translation") \
                or old_entries[key].get("term") != value.get("term"):
            changed.append({
                "term_key": key,
                "old": old_entries[key],
                "new": value,
            })
    for key, value in old_entries.items():
        if key not in new_entries:
            removed.append({"term_key": key, **value})
    return {"added": added, "removed": removed, "changed": changed}


def generate_diff(conn: sqlite3.Connection, new_package_id: str,
                  old_package_id: str | None = None) -> dict[str, Any]:
    new_pkg = pkg_service.get_package(conn, new_package_id)
    old_package_id = old_package_id or new_pkg["prev_package_id"]
    if not old_package_id:
        raise ValidationError("首个发布包没有可比较的旧版本")
    old_pkg = pkg_service.get_package(conn, old_package_id)
    if old_pkg["category"] != new_pkg["category"] or old_pkg["language_code"] != new_pkg["language_code"]:
        raise ValidationError("只能比较同一场所类别与语种的发布包")

    diff_id = f"DIFF-{old_package_id[-8:]}-{new_package_id[-8:]}"
    existing = conn.execute("SELECT diff_id FROM diffs WHERE diff_id = ?", (diff_id,)).fetchone()
    if existing:
        return get_diff(conn, diff_id)

    changes = compute_changes(_entries(conn, old_package_id), _entries(conn, new_package_id))
    conn.execute(
        "INSERT INTO diffs (diff_id, old_package_id, new_package_id, generated_at, changes_json)"
        " VALUES (?,?,?,?,?)",
        (diff_id, old_package_id, new_package_id, clock.now_iso(),
         json.dumps(changes, ensure_ascii=False)),
    )

    # 影响清单：取仍停留在旧序号包上的现场最新版本（词条被修改或删除）
    affected_keys = {c["term_key"]: "changed" for c in changes["changed"]}
    affected_keys.update({c["term_key"]: "removed" for c in changes["removed"]})
    _build_impact(conn, diff_id, new_pkg, affected_keys)
    conn.commit()
    return get_diff(conn, diff_id)


def _build_impact(conn: sqlite3.Connection, diff_id: str, new_pkg: dict[str, Any],
                  affected_keys: dict[str, str]) -> None:
    if not affected_keys:
        return
    placeholders = ",".join("?" for _ in affected_keys)
    rows = conn.execute(
        f"""
        SELECT v.sign_ref, v.language_code, v.term_key, v.package_id, v.reported_by AS unit_id,
               p.sequence_no
        FROM sign_versions v
        JOIN packages p ON p.package_id = v.package_id
        JOIN (SELECT sign_ref, language_code, term_key, MAX(vid) AS vid
              FROM sign_versions GROUP BY sign_ref, language_code, term_key) m
          ON m.sign_ref = v.sign_ref AND m.language_code = v.language_code
         AND m.term_key = v.term_key AND m.vid = v.vid
        WHERE v.language_code = ? AND p.category = ? AND p.sequence_no < ?
          AND v.term_key IN ({placeholders})
        """,
        [new_pkg["language_code"], new_pkg["category"], new_pkg["sequence_no"],
         *affected_keys.keys()],
    ).fetchall()
    for row in rows:
        kind = affected_keys[row["term_key"]]
        conn.execute(
            "INSERT INTO impact_items (diff_id, term_key, sign_ref, language_code, unit_id,"
            " current_package_id, kind) VALUES (?,?,?,?,?,?,?)",
            (diff_id, row["term_key"], row["sign_ref"], row["language_code"], row["unit_id"],
             row["package_id"], kind),
        )
        # 自动判断只能提出候选
        conn.execute(
            "INSERT OR IGNORE INTO candidates (diff_id, sign_ref, language_code, term_key,"
            " unit_id, target_package_id, status) VALUES (?,?,?,?,?,?,'proposed')",
            (diff_id, row["sign_ref"], row["language_code"], row["term_key"], row["unit_id"],
             new_pkg["package_id"]),
        )


def get_diff(conn: sqlite3.Connection, diff_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM diffs WHERE diff_id = ?", (diff_id,)).fetchone()
    if not row:
        raise NotFoundError(f"差异不存在：{diff_id}", code="diff_not_found")
    impact = [dict(r) for r in conn.execute(
        "SELECT * FROM impact_items WHERE diff_id = ? ORDER BY id", (diff_id,))]
    candidates = [_candidate_dict(r) for r in conn.execute(
        "SELECT c.*, (SELECT req_id FROM update_requirements r WHERE r.candidate_id = c.id)"
        " AS req_id FROM candidates c WHERE diff_id = ? ORDER BY id", (diff_id,))]
    return {
        "diff_id": diff_id,
        "old_package_id": row["old_package_id"],
        "new_package_id": row["new_package_id"],
        "generated_at": row["generated_at"],
        "changes": json.loads(row["changes_json"]),
        "impact": impact,
        "candidates": candidates,
    }


def _candidate_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_candidates(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT c.*, d.diff_id AS diff_id FROM candidates c JOIN diffs d ON d.diff_id = c.diff_id"
           " WHERE 1=1")
    params: list[Any] = []
    if status:
        sql += " AND c.status = ?"
        params.append(status)
    sql += " ORDER BY c.id"
    return [dict(r) for r in conn.execute(sql, params)]


def decide_candidate(conn: sqlite3.Connection, candidate_id: int, actor: str,
                     req: CandidateDecisionRequest) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    if not row:
        raise NotFoundError(f"候选不存在：{candidate_id}", code="candidate_not_found")
    if row["status"] != "proposed":
        raise ConflictError(f"候选已做出决定：{row['status']}")

    decided_at = clock.now_iso()
    if req.action == "reject":
        conn.execute(
            "UPDATE candidates SET status = 'rejected', reason = ?, decided_by = ?, decided_at = ?"
            " WHERE id = ?",
            (req.reason, actor, decided_at, candidate_id),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM candidates WHERE id = ?",
                                 (candidate_id,)).fetchone())

    promised = clock.parse(req.promised_by) if req.promised_by else None
    # 专业确认：幂等地生成更新要求（不同差异可能指向同一目标版本）
    existing_req = conn.execute(
        "SELECT req_id FROM update_requirements WHERE sign_ref = ? AND language_code = ?"
        " AND term_key = ? AND target_package_id = ?",
        (row["sign_ref"], row["language_code"], row["term_key"], row["target_package_id"]),
    ).fetchone()
    if existing_req:
        req_id = existing_req["req_id"]
    else:
        req_id = f"REQ-{candidate_id:06d}"
        conn.execute(
            "INSERT INTO update_requirements (req_id, candidate_id, sign_ref, language_code,"
            " term_key, unit_id, target_package_id, created_at, promised_by)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (req_id, candidate_id, row["sign_ref"], row["language_code"], row["term_key"],
             row["unit_id"], row["target_package_id"], decided_at,
             clock.to_iso(promised) if promised else None),
        )
    conn.execute(
        "UPDATE candidates SET status = 'confirmed', reason = ?, decided_by = ?, decided_at = ?"
        " WHERE id = ?",
        (req.reason, actor, decided_at, candidate_id),
    )
    _maybe_complete(conn, req_id, row["sign_ref"], row["language_code"], row["term_key"],
                    row["target_package_id"], decided_at)
    conn.commit()
    return get_requirement(conn, req_id)


def _maybe_complete(conn: sqlite3.Connection, req_id: str, sign_ref: str, language_code: str,
                    term_key: str, target_package_id: str, at_iso: str) -> None:
    """确认时现场若已在目标版本（或更新）上，立即回填真实完成。"""
    row = conn.execute(
        "SELECT p.sequence_no AS seq FROM sign_versions v JOIN packages p ON p.package_id = v.package_id"
        " WHERE v.sign_ref = ? AND v.language_code = ? AND v.term_key = ?"
        " ORDER BY v.vid DESC LIMIT 1",
        (sign_ref, language_code, term_key),
    ).fetchone()
    target = conn.execute("SELECT sequence_no FROM packages WHERE package_id = ?",
                          (target_package_id,)).fetchone()
    if row and target and row["seq"] >= target["sequence_no"]:
        conn.execute("UPDATE update_requirements SET completed_at = ? WHERE req_id = ?",
                     (at_iso, req_id))


def get_requirement(conn: sqlite3.Connection, req_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM update_requirements WHERE req_id = ?",
                       (req_id,)).fetchone()
    if not row:
        raise NotFoundError(f"更新要求不存在：{req_id}", code="requirement_not_found")
    from app.services import exemptions as ex_service
    data = dict(row)
    data["exemption"] = ex_service.current_state(conn, req_id)
    return data


def list_requirements(conn: sqlite3.Connection, unit_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT req_id FROM update_requirements WHERE 1=1"
    params: list[Any] = []
    if unit_id:
        sql += " AND unit_id = ?"
        params.append(unit_id)
    return [get_requirement(conn, r["req_id"]) for r in conn.execute(sql, params)]


def set_promise(conn: sqlite3.Connection, unit_id: str, req_id: str, promised_by: str) -> dict[str, Any]:
    req = get_requirement(conn, req_id)
    if req["unit_id"] != unit_id:
        from app.errors import ForbiddenError
        raise ForbiddenError("只能为本单位的更新要求上报承诺期限")
    promised = clock.parse(promised_by)
    conn.execute("UPDATE update_requirements SET promised_by = ? WHERE req_id = ?",
                 (clock.to_iso(promised), req_id))
    conn.commit()
    return get_requirement(conn, req_id)


def revision_impact(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    """管理者视角：一条规范修订 → 受影响载体、单位、承诺期限、真实完成比例。"""
    pkg = pkg_service.get_package(conn, package_id)
    rows = conn.execute(
        "SELECT * FROM update_requirements WHERE target_package_id = ? ORDER BY created_at",
        (package_id,),
    ).fetchall()
    from app.services import exemptions as ex_service
    now_iso = clock.now_iso()
    requirements = []
    completed = 0
    exempt = 0
    overdue = 0
    units: set[str] = set()
    signs: set[str] = set()
    for row in rows:
        data = dict(row)
        data["exemption"] = ex_service.current_state(conn, row["req_id"])
        is_exempt = data["exemption"]["status"] == "active"
        is_done = bool(row["completed_at"])
        if is_done:
            completed += 1
        if is_exempt:
            exempt += 1
        if row["promised_by"] and not is_done and not is_exempt and row["promised_by"] < now_iso:
            overdue += 1
            data["overdue"] = True
        else:
            data["overdue"] = False
        requirements.append(data)
        units.add(row["unit_id"])
        signs.add(row["sign_ref"])

    total = len(rows)
    # 影响清单中尚未形成要求的候选（仍待专业判断）
    pending_candidates = conn.execute(
        "SELECT COUNT(*) AS n FROM candidates WHERE target_package_id = ? AND status = 'proposed'",
        (package_id,),
    ).fetchone()["n"]
    return {
        "package_id": package_id,
        "category": pkg["category"],
        "language_code": pkg["language_code"],
        "effective_at": pkg["effective_at"],
        "affected_signs": sorted(signs),
        "affected_units": sorted(units),
        "candidate_count": conn.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE target_package_id = ?", (package_id,)
        ).fetchone()["n"],
        "pending_candidates": pending_candidates,
        "requirement_total": total,
        "completed": completed,
        "exempt_active": exempt,
        "overdue": overdue,
        "completion_rate": (completed / total) if total else None,
        "requirements": requirements,
    }
