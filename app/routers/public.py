"""公开查询端（无需鉴权）：现场实际 vs 当前推荐、历史发布包、版本反查。"""

import sqlite3

from fastapi import APIRouter, Depends

from app.db import get_db
from app.services import public as public_service

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/signs/{sign_ref}")
def sign_view(sign_ref: str, conn: sqlite3.Connection = Depends(get_db)):
    """分批换版期间，明确展示当前推荐与现场实际的差异。"""
    return public_service.public_sign_view(conn, sign_ref)


@router.get("/packages/{package_id}")
def package_snapshot(package_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """旧版仍支持历史查询；usable_for_new_signs=False 时不得用于新建标识。"""
    return public_service.package_snapshot(conn, package_id)


@router.get("/versions/{vid}")
def trace_version(vid: int, conn: sqlite3.Connection = Depends(get_db)):
    """从任一标识版本反查它当时合法引用的发布包。"""
    return public_service.trace_version(conn, vid)
