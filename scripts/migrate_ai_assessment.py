import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "campusfix.db"

COLUMNS = {
    "ai_category": "TEXT",
    "ai_severity": "TEXT",
    "secondary_hazards": "TEXT NOT NULL DEFAULT '[]'",
    "ai_summary": "TEXT",
    "ai_reason": "TEXT",
    "safety_risk": "INTEGER NOT NULL DEFAULT 0",
    "safety_risk_reason": "TEXT",
    "ai_method": "TEXT",
}


def migrate():
    conn = sqlite3.connect(DB_PATH)

    try:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(issues)")
        }

        for name, sql_type in COLUMNS.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE issues ADD COLUMN {name} {sql_type}"
                )
                print(f"Added issues.{name}")
            else:
                print(f"Already exists: issues.{name}")

        conn.commit()
        print("\\nAI assessment migration completed.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
