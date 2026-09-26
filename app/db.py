"""SQLite 连接管理：每请求一个连接，外键开启，行以字典式 Row 返回。"""

import os
import sqlite3
from collections.abc import Iterator

from fastapi import Request


def connect(database_path: str | None = None) -> sqlite3.Connection:
    path = database_path or os.getenv("DATABASE_PATH", "data/app.sqlite3")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI 依赖：连接存于 app.state，便于测试整体替换。"""
    if app_state_db := getattr(request.app.state, "db", None):
        yield app_state_db
        return
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
