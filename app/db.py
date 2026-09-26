"""SQLite 连接管理。每次连接都确保迁移已应用（迁移本身幂等）。"""

import os
import sqlite3
from pathlib import Path

from scripts.migrate import migrate


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "data/app.sqlite3"))


def connect() -> sqlite3.Connection:
    path = database_path()
    migrate(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
