import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "campusfix.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(issue_groups)")
        }

        if "created_at" not in columns:
            conn.execute("""
                ALTER TABLE issue_groups
                ADD COLUMN created_at TEXT
            """)
            print("Added issue_groups.created_at")
        else:
            print("issue_groups.created_at already exists")

        if "resolved_at" not in columns:
            conn.execute("""
                ALTER TABLE issue_groups
                ADD COLUMN resolved_at TEXT
            """)
            print("Added issue_groups.resolved_at")
        else:
            print("issue_groups.resolved_at already exists")

        # For existing groups, use the earliest report timestamp.
        conn.execute("""
            UPDATE issue_groups
            SET created_at = (
                SELECT MIN(i.created_at)
                FROM issue_group_members m
                JOIN issues i ON i.issue_id = m.issue_id
                WHERE m.group_id = issue_groups.group_id
            )
            WHERE created_at IS NULL
        """)

        conn.commit()

        print("\\nResolution tracking migration completed.")

        rows = conn.execute("""
            SELECT group_id, created_at, resolved_at, status
            FROM issue_groups
        """).fetchall()

        for row in rows:
            print(dict(row))

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
