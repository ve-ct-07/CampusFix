"""Versioned, idempotent schema migrations for CampusFix.

The base tables are still created by ``database/schema.sql``. Changes to
existing tables cannot be expressed with ``CREATE TABLE IF NOT EXISTS``, so
they live here and are applied after the base schema on every start-up.

Migration guarantees
--------------------
* Additive only: no table is dropped, recreated or rewritten, and no row is
  deleted or modified. Existing reports keep ``submitted_by = NULL``.
* Idempotent: every step checks the live schema *inside* the transaction, so
  re-running (or two processes starting at once) is harmless.
* Atomic: all steps run in a single ``BEGIN IMMEDIATE`` transaction and are
  rolled back together on any error.
* Backed up: before a pending migration touches a database that already holds
  reports, a consistent copy is written next to it using SQLite's online
  backup API.
"""

from datetime import datetime
from pathlib import Path
import sqlite3

# PRAGMA user_version value after all migrations in this module.
SCHEMA_VERSION = 3

# Version history:
#   1 - users table, issues.submitted_by, incident_activity.changed_by
#   2 - users.name (added to v1 databases with ALTER TABLE)
#   3 - issue_activity: audit log for individual report changes

USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL
        CHECK (length(trim(name)) BETWEEN 1 AND 100),

    -- Stored normalized (trimmed, lower-case) and unique.
    email TEXT NOT NULL UNIQUE
        CHECK (
            length(email) BETWEEN 3 AND 254
            AND email = lower(trim(email))
            AND email LIKE '%_@_%'
        ),

    -- Only Argon2id PHC strings are accepted; plaintext cannot be stored.
    password_hash TEXT NOT NULL
        CHECK (password_hash LIKE '$argon2id$%'),

    role TEXT NOT NULL DEFAULT 'student'
        CHECK (role IN ('student', 'admin')),

    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),

    -- Legacy, unused: per-account lockout was removed because it allowed
    -- denial of service against other users. Kept to avoid rebuilding
    -- the table; login throttling is per client (auth/rate_limit.py).
    failed_login_attempts INTEGER NOT NULL DEFAULT 0
        CHECK (failed_login_attempts >= 0),
    locked_until TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT,

    -- Database-level backstop: student accounts must use the campus domain.
    CHECK (role <> 'student' OR email LIKE '%_@campus.ac.in')
)
"""

# (table, column, column definition). Table and column names are fixed
# constants from this module, never user input.
_ADDED_COLUMNS = (
    (
        # Only needed for databases migrated to version 1, whose users
        # table predates the name column. Fresh tables already have it.
        "users",
        "name",
        "TEXT NOT NULL DEFAULT ''",
    ),
    (
        "issues",
        "submitted_by",
        "INTEGER REFERENCES users(user_id) ON DELETE SET NULL",
    ),
    (
        "incident_activity",
        "changed_by",
        "TEXT",
    ),
)

ISSUE_ACTIVITY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS issue_activity (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    issue_id INTEGER NOT NULL,

    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,

    -- Administrator responsible (email); NULL for system/script changes.
    changed_by TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE
)
"""

# (table name, CREATE TABLE statement) for tables added after version 1.
_NEW_TABLES = (
    ("issue_activity", ISSUE_ACTIVITY_TABLE_SQL),
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_issues_submitted_by "
    "ON issues(submitted_by)",
    "CREATE INDEX IF NOT EXISTS idx_issue_activity_issue "
    "ON issue_activity(issue_id, created_at DESC)",
)


def _columns(conn, table):
    if table not in {t for t, _, _ in _ADDED_COLUMNS}:
        raise ValueError(f"Unexpected table: {table}")
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def get_schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def is_migration_pending(conn):
    """Return True when any migration step still has work to do."""

    if not _table_exists(conn, "users"):
        return True

    for table, _ in _NEW_TABLES:
        if not _table_exists(conn, table):
            return True

    for table, column, _ in _ADDED_COLUMNS:
        if column not in _columns(conn, table):
            return True

    return get_schema_version(conn) < SCHEMA_VERSION


def apply_migrations(conn):
    """Apply all pending steps atomically. Returns a list of changes made.

    ``conn`` must not have an open transaction. The base schema
    (``schema.sql``) must already have been applied.
    """

    changes = []
    conn.execute("BEGIN IMMEDIATE")

    try:
        if not _table_exists(conn, "users"):
            conn.execute(USERS_TABLE_SQL)
            changes.append("created table users")

        for table, statement in _NEW_TABLES:
            if not _table_exists(conn, table):
                conn.execute(statement)
                changes.append(f"created table {table}")

        for table, column, definition in _ADDED_COLUMNS:
            if column not in _columns(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
                changes.append(f"added column {table}.{column}")

        for statement in _INDEXES:
            conn.execute(statement)

        if get_schema_version(conn) < SCHEMA_VERSION:
            # PRAGMA does not accept bound parameters; value is a constant.
            conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
            changes.append(f"schema version set to {SCHEMA_VERSION}")

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    return changes


def backup_database(db_path, label="pre_auth"):
    """Write a consistent copy of ``db_path`` beside it and return its path."""

    db_path = Path(db_path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = db_path.with_name(f"{db_path.stem}_{label}_{stamp}.db")

    counter = 1
    while target.exists():
        target = db_path.with_name(
            f"{db_path.stem}_{label}_{stamp}_{counter}.db"
        )
        counter += 1

    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(target)

    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    return target


def run_migrations(db_path, backup=True):
    """Migrate the database at ``db_path``.

    Returns ``(changes, backup_path)``. A backup is only taken when work is
    pending and the database already contains reports.
    """

    db_path = Path(db_path)
    backup_path = None

    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        if not is_migration_pending(conn):
            return [], None

        if backup and _table_exists(conn, "issues"):
            has_reports = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM issues)"
            ).fetchone()[0]

            if has_reports:
                backup_path = backup_database(db_path)

        changes = apply_migrations(conn)
        return changes, backup_path

    finally:
        conn.close()
