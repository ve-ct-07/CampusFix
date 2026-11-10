"""Tests for the additive authentication schema migration."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import database.db as db
import database.migrations as migrations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = PROJECT_ROOT / "database" / "schema.sql"

LEGACY_TABLES = [
    "issues",
    "issue_groups",
    "issue_group_members",
    "issue_embeddings",
    "incident_activity",
    "safety_response_log",
    "duplicate_review_candidates",
]


def build_legacy_database(path):
    """Create a pre-authentication database populated in every table."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executescript(
        """
        INSERT INTO issues (title, description, location, priority,
                            status, safety_risk, ai_method)
        VALUES
            ('Leaking tap', 'Water leaking', 'Hostel A', 'High',
             'Submitted', 0, 'keyword'),
            ('Water leakage', 'Leaking near washroom', 'Hostel A',
             'High', 'Submitted', 0, 'keyword'),
            ('Sparking socket', 'Socket sparks', 'Library', 'Critical',
             'In Progress', 1, 'ollama_local');

        INSERT INTO issue_groups (primary_issue_id, duplicate_count,
                                  status, priority, assigned_team)
        VALUES (1, 1, 'Assigned', 'High', 'Plumbing');

        INSERT INTO issue_group_members (group_id, issue_id)
        VALUES (1, 1), (1, 2);

        INSERT INTO issue_embeddings (issue_id, model_name, embedding_json)
        VALUES (1, 'test-model', '[0.1, 0.2]');

        INSERT INTO incident_activity (group_id, event_type,
                                       old_value, new_value)
        VALUES (1, 'Status', 'Submitted', 'Assigned');

        INSERT INTO safety_response_log (issue_id, acknowledged_by,
                                         action_taken)
        VALUES (3, 'Legacy Admin', 'Restricted access to affected area');

        INSERT INTO duplicate_review_candidates (new_issue_id,
                                                 matched_issue_id,
                                                 similarity, decision)
        VALUES (2, 1, 0.93, 'Confirmed');
        """
    )
    conn.commit()
    conn.close()


def snapshot(path):
    """Return every legacy row, keyed by table, excluding new columns."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    data = {}
    for table in LEGACY_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        data[table] = [
            {
                key: row[key] for key in row.keys()
                if key not in ("submitted_by", "changed_by")
            }
            for row in rows
        ]
    conn.close()
    return data


def columns(path, table):
    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def user_version(path):
    conn = sqlite3.connect(path)
    value = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    return value


def test_migration_preserves_all_existing_records(monkeypatch, tmp_path):
    legacy = tmp_path / "campusfix.db"
    build_legacy_database(legacy)
    before = snapshot(legacy)

    assert "submitted_by" not in columns(legacy, "issues")

    monkeypatch.setattr(db, "DB_PATH", legacy)
    db.initialize_database()

    assert snapshot(legacy) == before
    assert "submitted_by" in columns(legacy, "issues")
    assert "changed_by" in columns(legacy, "incident_activity")
    assert user_version(legacy) == migrations.SCHEMA_VERSION

    # Existing reports remain valid and unowned.
    conn = sqlite3.connect(legacy)
    owners = [r[0] for r in conn.execute("SELECT submitted_by FROM issues")]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    assert owners == [None, None, None]
    assert integrity == "ok"
    assert fk_errors == []

    # Existing application functions still read legacy data correctly.
    assert len(db.list_issues()) == 3
    assert db.get_issue_group(2)["group_id"] == 1
    assert db.get_student_tracking_data(2)["incident_ref"] == "GF-0001"


def test_migration_writes_backup_of_populated_database(tmp_path):
    legacy = tmp_path / "campusfix.db"
    build_legacy_database(legacy)
    before = snapshot(legacy)

    changes, backup_path = migrations.run_migrations(legacy)

    assert "created table users" in changes
    assert "added column issues.submitted_by" in changes
    assert backup_path is not None and backup_path.exists()
    assert backup_path.parent == legacy.parent

    # The backup is an untouched pre-migration copy.
    assert snapshot(backup_path) == before
    assert "submitted_by" not in columns(backup_path, "issues")
    assert user_version(backup_path) == 0


def test_migration_is_repeatable(tmp_path):
    legacy = tmp_path / "campusfix.db"
    build_legacy_database(legacy)

    first_changes, first_backup = migrations.run_migrations(legacy)
    after_first = snapshot(legacy)

    second_changes, second_backup = migrations.run_migrations(legacy)
    third_changes, _ = migrations.run_migrations(legacy)

    assert first_changes
    assert second_changes == [] and third_changes == []
    assert second_backup is None
    assert snapshot(legacy) == after_first
    assert len(list(tmp_path.glob("campusfix_pre_auth_*.db"))) == 1
    assert first_backup.exists()


def test_fresh_database_is_migrated_without_backup(monkeypatch, tmp_path):
    fresh = tmp_path / "new.db"
    monkeypatch.setattr(db, "DB_PATH", fresh)

    db.initialize_database()
    db.initialize_database()  # second start-up is a no-op

    assert "submitted_by" in columns(fresh, "issues")
    assert user_version(fresh) == migrations.SCHEMA_VERSION
    assert list(tmp_path.glob("*_pre_auth_*.db")) == []


def test_failed_migration_rolls_back_completely(monkeypatch, tmp_path):
    legacy = tmp_path / "campusfix.db"
    build_legacy_database(legacy)
    before = snapshot(legacy)

    broken_steps = migrations._ADDED_COLUMNS + (
        ("issues", "broken", "THIS IS NOT VALID SQL ((("),
    )
    monkeypatch.setattr(migrations, "_ADDED_COLUMNS", broken_steps)

    with pytest.raises(sqlite3.OperationalError):
        migrations.run_migrations(legacy, backup=False)

    # Nothing from the failed transaction is left behind.
    assert "submitted_by" not in columns(legacy, "issues")
    assert "changed_by" not in columns(legacy, "incident_activity")
    conn = sqlite3.connect(legacy)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'issue_activity'"
    ).fetchone() is None
    users = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'users'"
    ).fetchone()
    conn.close()
    assert users is None
    assert user_version(legacy) == 0
    assert snapshot(legacy) == before


def test_submitted_by_foreign_key_is_enforced(fresh_db):
    hash_ = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"

    with db.get_connection() as conn:
        user_id = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES ('T', ?, ?)",
            ("owner@campus.ac.in", hash_),
        ).lastrowid

    issue_id = db.create_issue(
        "Broken light", "Light flickers", "Library", submitted_by=user_id
    )
    assert db.get_issue(issue_id)["submitted_by"] == user_id

    # A report cannot reference a user that does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        db.create_issue("X", "Y", "Z", submitted_by=999_999)

    # Deleting a user keeps the report and clears ownership.
    with db.get_connection() as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

    assert db.get_issue(issue_id)["submitted_by"] is None


@pytest.mark.parametrize(
    "email, password_hash, role",
    [
        ("a@campus.ac.in", "$argon2id$v=19$m=1,t=1,p=1$x$y", "superuser"),
        ("a@gmail.com", "$argon2id$v=19$m=1,t=1,p=1$x$y", "student"),
        ("a@campus.ac.in", "plaintext-password", "student"),
        ("a@campus.ac.in", "$argon2i$v=19$m=1,t=1,p=1$x$y", "student"),
        ("A@Campus.ac.in", "$argon2id$v=19$m=1,t=1,p=1$x$y", "student"),
        (" a@campus.ac.in", "$argon2id$v=19$m=1,t=1,p=1$x$y", "student"),
    ],
)
def test_users_table_constraints_reject_invalid_rows(
    fresh_db, email, password_hash, role,
):
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role) "
                "VALUES ('T', ?, ?, ?)",
                (email, password_hash, role),
            )


def test_users_email_is_unique(fresh_db):
    hash_ = "$argon2id$v=19$m=1,t=1,p=1$x$y"
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES ('T', ?, ?)",
            ("dup@campus.ac.in", hash_),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES ('T', ?, ?)",
                ("dup@campus.ac.in", hash_),
            )


def test_migration_script_on_legacy_copy(tmp_path):
    legacy = tmp_path / "copy.db"
    build_legacy_database(legacy)
    before = snapshot(legacy)

    script = PROJECT_ROOT / "scripts" / "migrate_auth.py"
    first = subprocess.run(
        [sys.executable, str(script), "--db", str(legacy)],
        capture_output=True, text=True, check=True,
    )
    second = subprocess.run(
        [sys.executable, str(script), "--db", str(legacy)],
        capture_output=True, text=True, check=True,
    )

    assert "Migration completed successfully." in first.stdout
    assert "Existing reports with submitted_by = NULL: 3" in first.stdout
    assert "No migration pending" in second.stdout
    assert snapshot(legacy) == before
    assert len(list(tmp_path.glob("copy_pre_auth_*.db"))) == 1


# ----------------------------------------------------------------------
# Version 2: users.name, and upgrade paths
# ----------------------------------------------------------------------

V1_USERS_SQL = """
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT
);
ALTER TABLE issues ADD COLUMN submitted_by INTEGER
    REFERENCES users(user_id) ON DELETE SET NULL;
ALTER TABLE incident_activity ADD COLUMN changed_by TEXT;
PRAGMA user_version = 1;
"""


def test_users_table_has_required_columns(fresh_db):
    assert {
        "user_id", "name", "email", "password_hash", "role",
        "is_active", "created_at",
    } <= columns(fresh_db, "users")
    assert "submitted_by" in columns(fresh_db, "issues")
    assert {
        "issue_id", "event_type", "old_value", "new_value", "changed_by",
    } <= columns(fresh_db, "issue_activity")
    assert user_version(fresh_db) == migrations.SCHEMA_VERSION == 3


def test_upgrade_from_version_1_adds_name_and_keeps_users(tmp_path):
    path = tmp_path / "v1.db"
    build_legacy_database(path)
    conn = sqlite3.connect(path)
    conn.executescript(V1_USERS_SQL)
    conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        ("early@campus.ac.in", "$argon2id$v=19$m=1,t=1,p=1$x$y"),
    )
    conn.execute("UPDATE issues SET submitted_by = 1 WHERE issue_id = 3")
    conn.commit()
    conn.close()
    before = snapshot(path)

    changes, _ = migrations.run_migrations(path)

    assert "added column users.name" in changes
    assert "created table issue_activity" in changes
    assert user_version(path) == 3
    assert snapshot(path) == before
    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT email, name FROM users"
    ).fetchall() == [("early@campus.ac.in", "")]
    assert conn.execute(
        "SELECT submitted_by FROM issues ORDER BY issue_id"
    ).fetchall() == [(None,), (None,), (1,)]
    conn.close()

    assert migrations.run_migrations(path) == ([], None)


def test_old_schema_reports_and_groups_preserved_and_unassigned(
    monkeypatch, tmp_path,
):
    legacy = tmp_path / "campusfix.db"
    build_legacy_database(legacy)
    monkeypatch.setattr(db, "DB_PATH", legacy)

    db.initialize_database()

    assert [i["submitted_by"] for i in db.list_issues()] == [None] * 3
    groups = db.list_incident_groups()
    assert len(groups) == 1 and groups[0]["assigned_team"] == "Plumbing"
    assert {m["issue_id"] for m in db.list_group_members(1)} == {1, 2}
    assert len(db.get_safety_response_history(issue_id=3)) == 1


def test_connections_enforce_foreign_keys(fresh_db):
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize("name", ["", "   ", None])
def test_users_name_is_required(fresh_db, name):
    with pytest.raises(sqlite3.IntegrityError):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO users (name, email, password_hash) "
                "VALUES (?, ?, ?)",
                (name, "n@campus.ac.in", "$argon2id$v=19$m=1,t=1,p=1$x$y"),
            )


def test_upgrade_from_version_2_adds_issue_activity(tmp_path):
    path = tmp_path / "v2.db"
    build_legacy_database(path)
    conn = sqlite3.connect(path)
    conn.executescript(V1_USERS_SQL)
    conn.executescript(
        "ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT '';"
        "PRAGMA user_version = 2;"
    )
    conn.close()
    before = snapshot(path)

    changes, backup = migrations.run_migrations(path)

    assert changes == [
        "created table issue_activity", "schema version set to 3",
    ]
    assert backup is not None and user_version(backup) == 2
    assert snapshot(path) == before
    assert migrations.run_migrations(path) == ([], None)
