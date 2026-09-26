"""数据库迁移：按文件名顺序执行 migrations/*.sql，并记录迁移水位。"""

import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(database_path: str | os.PathLike[str] | None = None) -> Path:
    database_path = Path(database_path or os.getenv("DATABASE_PATH", "data/app.sqlite3"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem
            if version in applied:
                continue
            connection.executescript(sql_file.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,)
            )
            applied.add(version)
            print(f"已应用迁移：{version}")
    return database_path


if __name__ == "__main__":
    path = migrate()
    print(f"数据库迁移完成：{path}")
