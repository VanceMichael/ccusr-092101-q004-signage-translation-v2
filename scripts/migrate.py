
import os
import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(database_path: Path) -> list[str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, "
            "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem
            row = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if row:
                continue
            connection.executescript(sql_file.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,)
            )
            applied.append(version)
    return applied


if __name__ == "__main__":
    path = Path(os.getenv("DATABASE_PATH", "data/app.sqlite3"))
    done = migrate(path)
    print(f"数据库迁移完成：{path}（本次应用：{', '.join(done) or '无'}）")
