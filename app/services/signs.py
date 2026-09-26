"""标识目录上报：幂等批次、合法引用校验、采用进度只进不退、更新要求自动完成。"""

import json
import sqlite3
from typing import Any

from app import clock
from app.errors import ValidationError
from app.models import CatalogReport
from app.services import packages as pkg_service
from app.services import units as unit_service


def _package_seq(conn: sqlite3.Connection, package_id: str) -> int:
    row = conn.execute("SELECT sequence_no FROM packages WHERE package_id = ?",
                       (package_id,)).fetchone()
    return row["sequence_no"] if row else 0


def _receipt_from_batch(conn: sqlite3.Connection, unit_id: str, batch_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT receipt_json FROM sync_batches WHERE unit_id = ? AND batch_id = ?",
        (unit_id, batch_id),
    ).fetchone()
    return json.loads(row["receipt_json"]) if row else None


def report_catalog(conn: sqlite3.Connection, unit_id: str, report: CatalogReport) -> dict[str, Any]:
    # 离线回执：同批次重放必须返回首次处理结果，绝不二次推进状态
    existing = _receipt_from_batch(conn, unit_id, report.batch_id)
    if existing is not None:
        existing["replayed"] = True
        return existing

    if not report.items:
        raise ValidationError("目录批次至少包含一条标识记录")

    results: list[dict[str, Any]] = []
    referenced_packages: set[str] = set()
    for item in report.items:
        results.append(_ingest_item(conn, unit_id, item, report.batch_id, referenced_packages))

    for package_id in referenced_packages:
        # 回执所引用包，以及同 类别+语种 中序号不大于它的更早投递（已采用新版即视为知悉旧版）
        conn.execute(
            """
            UPDATE notifications
               SET acked_at = ?
             WHERE unit_id = ? AND acked_at IS NULL
               AND package_id IN (
                   SELECT p.package_id FROM packages p
                   JOIN packages q ON q.category = p.category
                        AND q.language_code = p.language_code
                  WHERE q.package_id = ? AND p.sequence_no <= q.sequence_no
               )
            """,
            (clock.now_iso(), unit_id, package_id),
        )

    accepted = [r for r in results if r["accepted"]]
    receipt = {
        "batch_id": report.batch_id,
        "unit_id": unit_id,
        "received_at": clock.now_iso(),
        "accepted": len(accepted) > 0 and all(r["accepted"] for r in results),
        "replayed": False,
        "summary": {
            "total": len(results),
            "accepted": len(accepted),
            "rejected": len(results) - len(accepted),
        },
        "items": results,
    }
    conn.execute(
        "INSERT INTO sync_batches (unit_id, batch_id, received_at, accepted, report_json,"
        " receipt_json) VALUES (?,?,?,?,?,?)",
        (unit_id, report.batch_id, receipt["received_at"], 1 if receipt["accepted"] else 0,
         report.model_dump_json(), json.dumps(receipt, ensure_ascii=False)),
    )
    conn.commit()
    return receipt


def _ingest_item(conn: sqlite3.Connection, unit_id: str, item: Any, batch_id: str,
                 referenced_packages: set[str]) -> dict[str, Any]:
    base: dict[str, Any] = {"sign_ref": item.sign_ref, "term_key": item.term_key,
                            "language_code": item.language_code}
    try:
        unit_service.assert_manages(conn, unit_id, item.category, item.location_code)
    except Exception as exc:  # 越权记录到条目级回执
        return {**base, "accepted": False, "reason": str(getattr(exc, "message", exc))}

    pkg_row = conn.execute(
        "SELECT * FROM packages WHERE package_id = ?", (item.package_id,)
    ).fetchone()
    if not pkg_row:
        return {**base, "accepted": False, "reason": "发布包不存在"}
    if pkg_row["category"] != item.category or pkg_row["language_code"] != item.language_code:
        return {**base, "accepted": False, "reason": "发布包的场所类别或语种与标识不匹配"}

    status = pkg_service.status_at(conn, item.package_id)
    sign_row = conn.execute(
        "SELECT location_code, category FROM signs WHERE sign_ref = ?", (item.sign_ref,)
    ).fetchone()
    is_new_sign = sign_row is None

    # 废止包任何时候都不能被合法引用；未生效包同样不能
    if status == "withdrawn":
        return {**base, "accepted": False, "reason": "发布包已废止，不得引用"}
    if status == "pending":
        return {**base, "accepted": False, "reason": "发布包尚未生效，不得引用"}

    entries = json.loads(pkg_row["entries_json"])
    if item.term_key not in entries:
        return {**base, "accepted": False, "reason": "词条不在所引用发布包中"}

    recommended = pkg_service.current_recommended(conn, item.category, item.language_code)
    # 新建标识只能下载/引用当前推荐包；旧包仅供历史查询
    if is_new_sign and (not recommended or recommended["package_id"] != item.package_id):
        rec_id = recommended["package_id"] if recommended else None
        return {**base, "accepted": False, "reason": "新建标识只能引用当前推荐发布包",
                "recommended_package_id": rec_id}

    if sign_row and (sign_row["category"] != item.category
                     or sign_row["location_code"] != item.location_code):
        return {**base, "accepted": False, "reason": "标识的点位或场所类别与既有登记冲突"}

    # 水位校验：同一 标识+语种+词条 的引用包序号不得倒退（迟到/重复同步不回退进度）
    current = conn.execute(
        "SELECT vid, package_id, observed_text FROM sign_versions"
        " WHERE sign_ref = ? AND language_code = ? AND term_key = ?"
        " ORDER BY vid DESC LIMIT 1",
        (item.sign_ref, item.language_code, item.term_key),
    ).fetchone()
    new_seq = pkg_row["sequence_no"]
    if current:
        cur_seq = _package_seq(conn, current["package_id"])
        if new_seq < cur_seq:
            return {**base, "accepted": False, "reason": "采用进度倒退：引用包版本早于现场已上报版本",
                    "current_package_id": current["package_id"]}
        if current["package_id"] == item.package_id and current["observed_text"] == item.observed_text:
            return {**base, "accepted": True, "duplicate": True,
                    "vid": current["vid"], "package_id": item.package_id}

    if is_new_sign:
        conn.execute(
            "INSERT INTO signs (sign_ref, location_code, category, created_at) VALUES (?,?,?,?)",
            (item.sign_ref, item.location_code, item.category, clock.now_iso()),
        )

    cur = conn.execute(
        "SELECT MAX(vid) AS vid FROM sign_versions WHERE sign_ref = ? AND language_code = ?",
        (item.sign_ref, item.language_code),
    ).fetchone()
    reported_at = clock.now_iso()
    cur_vid = conn.execute(
        "INSERT INTO sign_versions (sign_ref, language_code, term_key, package_id,"
        " observed_text, reported_by, reported_at, batch_id, superseded_vid)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (item.sign_ref, item.language_code, item.term_key, item.package_id,
         item.observed_text, unit_id, reported_at, batch_id, cur["vid"]),
    )
    referenced_packages.add(item.package_id)
    _complete_requirements(conn, item, pkg_row["sequence_no"], reported_at)
    return {**base, "accepted": True, "vid": cur_vid.lastrowid, "package_id": item.package_id,
            "is_new_sign": is_new_sign}


def _complete_requirements(conn: sqlite3.Connection, item: Any, new_seq: int,
                           completed_at: str) -> None:
    """现场版本推进到目标包（或更新包）时，自动回填真实完成时间。"""
    rows = conn.execute(
        "SELECT r.req_id, p.sequence_no AS target_seq FROM update_requirements r"
        " JOIN packages p ON p.package_id = r.target_package_id"
        " WHERE r.sign_ref = ? AND r.language_code = ? AND r.term_key = ?"
        " AND r.completed_at IS NULL",
        (item.sign_ref, item.language_code, item.term_key),
    ).fetchall()
    for row in rows:
        if new_seq >= row["target_seq"]:
            conn.execute(
                "UPDATE update_requirements SET completed_at = ? WHERE req_id = ?",
                (completed_at, row["req_id"]),
            )


def latest_versions(conn: sqlite3.Connection, sign_ref: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT v.* FROM sign_versions v"
        " JOIN (SELECT language_code, term_key, MAX(vid) AS vid FROM sign_versions"
        " WHERE sign_ref = ? GROUP BY language_code, term_key) m"
        " ON m.vid = v.vid WHERE v.sign_ref = ? ORDER BY v.language_code, v.term_key",
        (sign_ref, sign_ref),
    )]


def get_sign(conn: sqlite3.Connection, sign_ref: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM signs WHERE sign_ref = ?", (sign_ref,)).fetchone()
    if not row:
        return None
    sign = dict(row)
    sign["versions"] = [dict(v) for v in conn.execute(
        "SELECT * FROM sign_versions WHERE sign_ref = ? ORDER BY vid", (sign_ref,))]
    return sign
