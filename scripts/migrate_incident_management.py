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

        additions = {
            "status": """
                ALTER TABLE issue_groups
                ADD COLUMN status TEXT NOT NULL DEFAULT 'Submitted'
                CHECK(status IN (
                    'Submitted', 'Under Review', 'Assigned',
                    'In Progress', 'Resolved', 'Rejected'
                ))
            """,
            "priority": """
                ALTER TABLE issue_groups
                ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium'
                CHECK(priority IN ('Low', 'Medium', 'High', 'Critical'))
            """,
            "assigned_team": """
                ALTER TABLE issue_groups
                ADD COLUMN assigned_team TEXT
            """,
        }

        for column, statement in additions.items():
            if column not in columns:
                conn.execute(statement)
                print(f"Added column: {column}")
            else:
                print(f"Already exists: {column}")

        # Initialize existing incidents from their primary reports.
        conn.execute("""
            UPDATE issue_groups
            SET
                status = (
                    SELECT status FROM issues
                    WHERE issues.issue_id = issue_groups.primary_issue_id
                ),
                priority = (
                    SELECT priority FROM issues
                    WHERE issues.issue_id = issue_groups.primary_issue_id
                ),
                assigned_team = (
                    SELECT assigned_to FROM issues
                    WHERE issues.issue_id = issue_groups.primary_issue_id
                )
        """)

        conn.commit()
        print("\\nIncident management migration completed.")

        rows = conn.execute("""
            SELECT group_id, primary_issue_id, duplicate_count,
                   status, priority, assigned_team
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
