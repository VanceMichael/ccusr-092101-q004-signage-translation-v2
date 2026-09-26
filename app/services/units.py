"""责任单位注册、按管理范围订阅、令牌校验。"""

import json
import sqlite3
from typing import Any

from app import clock, security
from app.errors import AuthError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models import SubscribeRequest, UnitRegisterRequest


def register_unit(conn: sqlite3.Connection, req: UnitRegisterRequest) -> dict[str, Any]:
    if conn.execute("SELECT 1 FROM units WHERE unit_id = ?", (req.unit_id,)).fetchone():
        raise ConflictError(f"单位已存在：{req.unit_id}", code="unit_exists")
    if not req.managed_categories:
        raise ValidationError("单位至少需要一个可管理的场所类别")
    conn.execute(
        "INSERT INTO units (unit_id, name, managed_categories, managed_locations, token, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (req.unit_id, req.name, json.dumps(req.managed_categories, ensure_ascii=False),
         json.dumps(req.managed_locations, ensure_ascii=False),
         security.unit_token(req.unit_id), clock.now_iso()),
    )
    conn.commit()
    return get_unit(conn, req.unit_id)


def get_unit(conn: sqlite3.Connection, unit_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM units WHERE unit_id = ?", (unit_id,)).fetchone()
    if not row:
        raise NotFoundError(f"单位不存在：{unit_id}", code="unit_not_found")
    return {
        "unit_id": row["unit_id"],
        "name": row["name"],
        "managed_categories": json.loads(row["managed_categories"]),
        "managed_locations": json.loads(row["managed_locations"]),
        "token": row["token"],
        "created_at": row["created_at"],
    }


def authenticate(conn: sqlite3.Connection, unit_id: str, token: str) -> dict[str, Any]:
    unit = get_unit(conn, unit_id)
    if not security.verify(f"unit-token:{unit_id}".encode("utf-8"), token):
        raise AuthError("单位令牌无效")
    return unit


def subscribe(conn: sqlite3.Connection, unit_id: str, req: SubscribeRequest) -> dict[str, Any]:
    unit = get_unit(conn, unit_id)
    managed = set(unit["managed_categories"])
    created: list[str] = []
    for category in req.categories:
        if category not in managed:
            raise ForbiddenError(f"单位管理范围不含场所类别：{category}")
        exists = conn.execute(
            "SELECT 1 FROM subscriptions WHERE unit_id = ? AND category = ?",
            (unit_id, category),
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO subscriptions (unit_id, category, created_at) VALUES (?,?,?)",
                (unit_id, category, clock.now_iso()),
            )
            created.append(category)
            # 订阅时按语种补投当前 active 的推荐包（跳过 pending/withdrawn），离线也不丢
            from app.services import packages as pkg_service
            rows = conn.execute(
                "SELECT DISTINCT language_code FROM packages WHERE category = ?", (category,)
            ).fetchall()
            for r in rows:
                recommended = pkg_service.current_recommended(conn, category, r["language_code"])
                if not recommended:
                    continue
                conn.execute(
                    "INSERT INTO notifications (unit_id, package_id, category, notified_at)"
                    " VALUES (?,?,?,?)",
                    (unit_id, recommended["package_id"], category, clock.now_iso()),
                )
    conn.commit()
    return {"unit_id": unit_id, "subscribed": req.categories, "created": created}


def assert_manages(conn: sqlite3.Connection, unit_id: str, category: str,
                   location_code: str) -> None:
    unit = get_unit(conn, unit_id)
    if category not in unit["managed_categories"]:
        raise ForbiddenError(f"单位 {unit_id} 无权管理类别 {category}")
    managed_locations = unit["managed_locations"]
    if managed_locations and location_code not in managed_locations:
        raise ForbiddenError(f"单位 {unit_id} 无权管理点位 {location_code}")


def list_units(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [get_unit(conn, r["unit_id"]) for r in conn.execute("SELECT unit_id FROM units")]
