"""Shared test safety net and fixtures.

Every test runs with ``database.db.DB_PATH`` pointed at a temporary file, so
no test can read or write the real ``campusfix.db`` even if it forgets to
isolate itself. A session-level check also fails the run if the real
database file changes while the tests execute.
"""

import hashlib
from pathlib import Path

import pytest

import database.db as db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = PROJECT_ROOT / "campusfix.db"
SCHEMA = PROJECT_ROOT / "database" / "schema.sql"


def _fingerprint(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def real_database_is_never_modified():
    before = _fingerprint(REAL_DB)
    yield
    assert _fingerprint(REAL_DB) == before, (
        "The real campusfix.db was modified during the test run."
    )


@pytest.fixture(autouse=True)
def isolate_database(monkeypatch, tmp_path):
    """Default every test to a throwaway database file."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "isolated_default.db")
    monkeypatch.setattr(db, "SCHEMA_PATH", SCHEMA)


@pytest.fixture(autouse=True)
def reset_login_rate_limiter():
    """The limiter is process-wide; give every test a clean slate."""
    from auth.rate_limit import login_limiter
    login_limiter._failures.clear()
    yield
    login_limiter._failures.clear()


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    """An initialized, empty, migrated temporary database."""
    path = tmp_path / "campusfix_test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize_database()
    return path
