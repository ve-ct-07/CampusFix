import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "campusfix.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS safety_response_log (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER,
    group_id INTEGER,
    acknowledged_by TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    notified_party TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        (issue_id IS NOT NULL AND group_id IS NULL)
        OR
        (issue_id IS NULL AND group_id IS NOT NULL)
    ),

    FOREIGN KEY (issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE,

    FOREIGN KEY (group_id)
        REFERENCES issue_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_safety_response_issue
ON safety_response_log(issue_id);

CREATE INDEX IF NOT EXISTS idx_safety_response_group
ON safety_response_log(group_id);

CREATE INDEX IF NOT EXISTS idx_safety_response_created
ON safety_response_log(created_at DESC);
"""


def main():
    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(CREATE_SQL)
        conn.commit()

        print("Safety response log migration completed.")

        row = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'safety_response_log'
        """).fetchone()

        print("Table verified:", row[0] if row else "NOT FOUND")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
