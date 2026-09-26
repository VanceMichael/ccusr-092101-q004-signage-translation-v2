"""路由层依赖：管理端密钥与单位令牌鉴权。"""

import sqlite3

from fastapi import Depends, Header

from app import security
from app.db import get_db
from app.errors import AuthError
from app.services import units as unit_service


def require_admin(x_admin_key: str | None = Header(default=None)) -> str:
    if not x_admin_key or x_admin_key != security.admin_key():
        raise AuthError("管理端密钥缺失或无效")
    return "admin"


def require_unit(
    x_unit_id: str | None = Header(default=None),
    x_unit_token: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not x_unit_id or not x_unit_token:
        raise AuthError("缺少单位身份头 X-Unit-Id / X-Unit-Token")
    return unit_service.authenticate(conn, x_unit_id, x_unit_token)
