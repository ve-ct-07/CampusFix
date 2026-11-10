import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "campusfix.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)

    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(issues)")
        }

        if "resolved_at" not in columns:
            conn.execute("""
                ALTER TABLE issues ADD COLUMN resolved_at TEXT
            """)
            print("Added issues.resolved_at")
        else:
            print("issues.resolved_at already exists")

        conn.commit()
        print("Individual report resolution migration completed.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
