"""Apply the authentication schema migration to an existing CampusFix database.

Usage (from the project root):

    python scripts/migrate_auth.py                 # migrates ./campusfix.db
    python scripts/migrate_auth.py --db other.db   # migrates another file

The script never deletes or recreates the database. It:
  1. takes a backup copy (SQLite online backup API),
  2. records the row count of every existing table,
  3. applies the idempotent migration in one transaction,
  4. verifies row counts, integrity and foreign keys afterwards.

Running it again is safe; it reports that nothing is pending.
The application also applies the same migration automatically on start-up.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from database.migrations import (  # noqa: E402
    apply_migrations,
    backup_database,
    get_schema_version,
    is_migration_pending,
)

SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"


def _row_counts(conn):
    tables = [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    # Table names come from sqlite_master, not user input.
    return {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tables
    }


def migrate(db_path):
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        raise SystemExit(
            f"Database not found: {db_path}\n"
            "Nothing to migrate. Start the app to create a new database."
        )

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        if not is_migration_pending(conn):
            print(
                "No migration pending. "
                f"Schema version: {get_schema_version(conn)}"
            )
            return 0

        before = _row_counts(conn)
        backup_path = backup_database(db_path)
        print(f"Backup written: {backup_path}")

        # Ensure base tables exist exactly as the application expects.
        # schema.sql only contains CREATE ... IF NOT EXISTS statements.
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

        for change in apply_migrations(conn):
            print(f"  - {change}")

        after = _row_counts(conn)

        for table, count in before.items():
            if after.get(table) != count:
                raise SystemExit(
                    f"Row count changed for {table}: {count} -> "
                    f"{after.get(table)}. Restore from {backup_path}."
                )

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

        if integrity != "ok" or fk_errors:
            raise SystemExit(
                f"Verification failed (integrity={integrity}, "
                f"foreign key errors={len(fk_errors)}). "
                f"Restore from {backup_path}."
            )

        unowned = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE submitted_by IS NULL"
        ).fetchone()[0]

        print("Row counts preserved:")
        for table, count in before.items():
            print(f"  {table}: {count}")
        print(f"Existing reports with submitted_by = NULL: {unowned}")
        print(f"Schema version: {get_schema_version(conn)}")
        print("Migration completed successfully.")
        return 0

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "campusfix.db"),
        help="Path to the SQLite database (default: ./campusfix.db)",
    )
    args = parser.parse_args()
    return migrate(args.db)


if __name__ == "__main__":
    sys.exit(main())
