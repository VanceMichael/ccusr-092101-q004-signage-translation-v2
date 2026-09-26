"""公开查询：现场实际与当前推荐的差异、历史发布包、任一标识版本的合法引用反查。"""

import json
import sqlite3
from typing import Any

from app import clock
from app.errors import NotFoundError
from app.services import packages as pkg_service


def _latest_per_term(conn: sqlite3.Connection, sign_ref: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT v.* FROM sign_versions v JOIN ("
        " SELECT language_code, term_key, MAX(vid) AS vid FROM sign_versions"
        " WHERE sign_ref = ? GROUP BY language_code, term_key) m"
        " ON m.vid = v.vid ORDER BY v.language_code, v.term_key",
        (sign_ref,),
    ).fetchall()


def public_sign_view(conn: sqlite3.Connection, sign_ref: str) -> dict[str, Any]:
    sign = conn.execute("SELECT * FROM signs WHERE sign_ref = ?", (sign_ref,)).fetchone()
    if not sign:
        raise NotFoundError(f"标识不存在：{sign_ref}", code="sign_not_found")

    latest = _latest_per_term(conn, sign_ref)
    items: list[dict[str, Any]] = []
    aligned = outdated = 0
    for v in latest:
        referenced = pkg_service.get_package(conn, v["package_id"])
        recommended = pkg_service.current_recommended(
            conn, sign["category"], v["language_code"])
        actual_entry = referenced["entries"].get(v["term_key"], {})
        rec_entry = (recommended or {}).get("entries", {}).get(v["term_key"])
        rec_status = referenced["status"]

        is_aligned = bool(
            recommended
            and recommended["package_id"] == v["package_id"]
            and rec_entry is not None
            and rec_entry.get("translation") == v["observed_text"]
        )
        if is_aligned:
            aligned += 1
        else:
            outdated += 1
        items.append({
            "language_code": v["language_code"],
            "term_key": v["term_key"],
            "actual": {
                "observed_text": v["observed_text"],
                "package_id": v["package_id"],
                "package_status_now": rec_status,
                "reported_at": v["reported_at"],
            },
            "recommended": None if not recommended or rec_entry is None else {
                "translation": rec_entry.get("translation"),
                "package_id": recommended["package_id"],
                "effective_at": recommended["effective_at"],
            },
            "state": "aligned" if is_aligned else "differs",
        })

    return {
        "sign_ref": sign_ref,
        "location_code": sign["location_code"],
        "category": sign["category"],
        "queried_at": clock.now_iso(),
        "summary": {"terms": len(items), "aligned": aligned, "differs": outdated},
        "items": items,
    }


def package_snapshot(conn: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    """历史查询：任何已发布包（含已废止/被取代）都可查看；显式标注能否用于新建。"""
    pkg = pkg_service.get_package(conn, package_id)
    recommended = pkg_service.current_recommended(conn, pkg["category"], pkg["language_code"])
    pkg["usable_for_new_signs"] = bool(
        recommended and recommended["package_id"] == package_id and pkg["status"] == "active"
    )
    return pkg


def trace_version(conn: sqlite3.Connection, vid: int) -> dict[str, Any]:
    """从任一标识版本反查它当时合法引用的发布包及其当时状态。"""
    v = conn.execute("SELECT * FROM sign_versions WHERE vid = ?", (vid,)).fetchone()
    if not v:
        raise NotFoundError(f"标识版本不存在：{vid}", code="version_not_found")
    pkg_row = conn.execute("SELECT * FROM packages WHERE package_id = ?",
                           (v["package_id"],)).fetchone()
    status_then = pkg_service.status_at(conn, v["package_id"], v["reported_at"])
    return {
        "vid": vid,
        "sign_ref": v["sign_ref"],
        "language_code": v["language_code"],
        "term_key": v["term_key"],
        "observed_text": v["observed_text"],
        "reported_at": v["reported_at"],
        "reported_by": v["reported_by"],
        "referenced_package": {
            "package_id": v["package_id"],
            "category": pkg_row["category"],
            "language_code": pkg_row["language_code"],
            "sequence_no": pkg_row["sequence_no"],
            "status_at_report_time": status_then,
            "signature_valid": pkg_service.get_package(conn, v["package_id"])["signature_valid"],
            "entry": json.loads(pkg_row["entries_json"]).get(v["term_key"]),
        },
        "was_legally_referenceable": status_then == "active",
    }
