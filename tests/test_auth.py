"""Tests for registration, login, logout, hashing, user DB functions and
the administrator bootstrap."""

import inspect
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from argon2 import PasswordHasher, Type

import auth
import auth.authentication as authn
import auth.password_utils as pw
import database.db as db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"


def user_row(email):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def count_users():
    with db.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ----------------------------------------------------------------------
# Database user functions
# ----------------------------------------------------------------------

def test_db_user_functions_round_trip(fresh_db):
    hash_ = pw.hash_password(PASSWORD)
    user_id = db.create_user("  Asha  ", " Asha@Campus.AC.IN ", hash_)

    by_id = db.get_user_by_id(user_id)
    by_email = db.get_user_by_email("ASHA@campus.ac.in ")

    assert by_id == by_email
    assert by_id["name"] == "Asha"
    assert by_id["email"] == "asha@campus.ac.in"
    assert by_id["role"] == "student"
    assert "password_hash" not in by_id

    creds = db.get_credentials_by_email("asha@CAMPUS.ac.in")
    assert creds["password_hash"] == hash_
    assert creds == {
        "user_id": user_id, "password_hash": hash_, "is_active": True,
    }

    assert db.get_user_by_email("nobody@campus.ac.in") is None
    assert db.get_credentials_by_email("") is None
    assert db.get_user_by_id("1 OR 1=1") is None


@pytest.mark.parametrize(
    "role, password_hash",
    [
        ("superuser", "$argon2id$v=19$m=1,t=1,p=1$x$y"),
        ("Admin", "$argon2id$v=19$m=1,t=1,p=1$x$y"),
        ("student", "plaintext"),
        ("student", "$2b$12$bcrypthash"),
    ],
)
def test_create_user_rejects_bad_role_or_hash(fresh_db, role, password_hash):
    with pytest.raises(ValueError):
        db.create_user("X", "x@campus.ac.in", password_hash, role)
    assert count_users() == 0


def test_create_user_rejects_duplicate_email(fresh_db):
    hash_ = pw.hash_password(PASSWORD)
    db.create_user("A", "dup@campus.ac.in", hash_)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("B", "DUP@campus.ac.in", hash_)


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

def test_student_registration(fresh_db):
    user = auth.register_student(" Asha  Rao ", "  Asha.R@Campus.AC.IN ", PASSWORD)

    assert user["name"] == "Asha Rao"
    assert user["email"] == "asha.r@campus.ac.in"
    assert user["role"] == "student"
    assert user["is_active"] is True
    assert "password_hash" not in user


@pytest.mark.parametrize(
    "email",
    [
        "student@gmail.com",
        "student@campus.ac.in.evil.com",
        "student@sub.campus.ac.in",
        "student@campus.ac.inx",
        "student@xcampus.ac.in",
        "student@campus-ac.in",
        "student@@campus.ac.in",
        "@campus.ac.in",
        "stu dent@campus.ac.in",
        ".student@campus.ac.in",
        "stu..dent@campus.ac.in",
        "student.@campus.ac.in",
        "a@b@campus.ac.in",
        "ѕtudent@campus.ac.in",  # Cyrillic look-alike "s"
        "student@campus.ac.in\nx@campus.ac.in",
        "",
        None,
        "x" * 250 + "@campus.ac.in",
    ],
)
def test_registration_rejects_non_campus_or_malformed_email(fresh_db, email):
    with pytest.raises(auth.RegistrationError):
        auth.register_student("Name", email, PASSWORD)
    assert count_users() == 0


@pytest.mark.parametrize("name", ["", "   ", None, "x" * 101, "Bad\x00Name"])
def test_registration_requires_valid_name(fresh_db, name):
    with pytest.raises(auth.RegistrationError):
        auth.register_student(name, "n@campus.ac.in", PASSWORD)
    assert count_users() == 0


def test_duplicate_email_is_rejected_case_insensitively(fresh_db):
    auth.register_student("First", "dup@campus.ac.in", PASSWORD)

    with pytest.raises(auth.RegistrationError, match="already exists"):
        auth.register_student("Second", "  DUP@Campus.ac.in", "another pw 1234")

    assert count_users() == 1


@pytest.mark.parametrize(
    "password",
    ["short", "123456789", " " * 12, "x" * 257, None],
)
def test_registration_enforces_password_policy(fresh_db, password):
    with pytest.raises(auth.RegistrationError):
        auth.register_student("Name", "policy@campus.ac.in", password)
    assert count_users() == 0


def test_password_cannot_equal_email(fresh_db):
    with pytest.raises(auth.RegistrationError, match="email"):
        auth.register_student(
            "Name", "longname1@campus.ac.in", "LongName1@campus.ac.in"
        )


def test_public_registration_cannot_select_admin_role(fresh_db):
    params = set(inspect.signature(auth.register_student).parameters)
    assert params == {"name", "email", "password"}

    with pytest.raises(TypeError):
        auth.register_student(
            "Sneaky", "sneaky@campus.ac.in", PASSWORD, role="admin"
        )

    user = auth.register_student("Sneaky", "sneaky@campus.ac.in", PASSWORD)
    assert user["role"] == "student"
    assert not db.admin_exists()


# ----------------------------------------------------------------------
# Password hashing
# ----------------------------------------------------------------------

def test_password_is_stored_as_argon2id_never_plaintext(fresh_db):
    auth.register_student("Hash", "hash@campus.ac.in", PASSWORD)
    row = user_row("hash@campus.ac.in")

    assert row["password_hash"].startswith("$argon2id$")
    assert all(PASSWORD not in str(value) for value in row.values())


def test_hash_and_verify_password():
    first = pw.hash_password(PASSWORD)
    second = pw.hash_password(PASSWORD)

    assert first.startswith("$argon2id$")
    assert first != second  # unique salt per hash
    assert pw.verify_password(first, PASSWORD)
    assert not pw.verify_password(first, PASSWORD + "x")
    assert not pw.verify_password(first, "")
    assert not pw.verify_password(first, None)
    assert not pw.verify_password("not-a-hash", PASSWORD)


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------

def test_login_returns_safe_user(fresh_db):
    auth.register_student("Login", "login@campus.ac.in", PASSWORD)

    user = auth.authenticate("  LOGIN@campus.AC.in ", PASSWORD)

    assert user["email"] == "login@campus.ac.in"
    assert user["name"] == "Login"
    assert user["role"] == "student"
    assert "password_hash" not in user
    assert not any(str(v).startswith("$argon2") for v in user.values())
    assert user_row("login@campus.ac.in")["last_login_at"] is not None


@pytest.mark.parametrize(
    "email, password",
    [
        ("login@campus.ac.in", "wrong password 123"),
        ("login@campus.ac.in", ""),
        ("login@campus.ac.in", None),
        ("login@campus.ac.in", "x" * 1000),
        ("nobody@campus.ac.in", PASSWORD),
        ("", PASSWORD),
        (None, None),
    ],
)
def test_invalid_credentials_fail_generically(fresh_db, email, password):
    auth.register_student("Login", "login@campus.ac.in", PASSWORD)
    assert auth.authenticate(email, password) is None


def test_generic_error_message_reveals_nothing():
    message = auth.GENERIC_LOGIN_ERROR.lower()
    assert "invalid email or password" in message
    for leak in ("not found", "no account", "inactive", "does not exist"):
        assert leak not in message


def test_unknown_email_still_performs_a_hash_verification(
    fresh_db, monkeypatch,
):
    calls = []
    original = pw.verify_password
    monkeypatch.setattr(
        pw, "verify_password",
        lambda h, p: calls.append(h) or original(h, p),
    )

    assert auth.authenticate("ghost@campus.ac.in", PASSWORD) is None
    assert len(calls) == 1 and calls[0].startswith("$argon2id$")


def test_inactive_account_cannot_log_in(fresh_db):
    user = auth.register_student("Inactive", "inactive@campus.ac.in", PASSWORD)
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?",
            (user["user_id"],),
        )

    assert auth.authenticate("inactive@campus.ac.in", PASSWORD) is None
    assert auth.get_active_user(user["user_id"]) is None


def test_failed_logins_never_lock_the_account(fresh_db):
    """Someone who knows a student's email must not be able to lock them
    out: per-account lockout was removed."""
    auth.register_student("Victim", "victim@campus.ac.in", PASSWORD)
    before = user_row("victim@campus.ac.in")

    for _ in range(25):
        assert auth.authenticate("victim@campus.ac.in", "bad guess 12345") is None

    after_failures = user_row("victim@campus.ac.in")
    assert after_failures["failed_login_attempts"] == 0
    assert after_failures["locked_until"] is None
    assert after_failures == before  # failures write nothing to the account

    assert auth.authenticate("victim@campus.ac.in", PASSWORD) is not None


def test_outdated_hash_is_upgraded_on_login(fresh_db):
    weak = PasswordHasher(
        time_cost=1, memory_cost=8, parallelism=1, type=Type.ID
    ).hash(PASSWORD)
    db.create_user("Legacy", "legacy@campus.ac.in", weak)

    assert auth.authenticate("legacy@campus.ac.in", PASSWORD) is not None

    upgraded = user_row("legacy@campus.ac.in")["password_hash"]
    assert upgraded != weak and not pw.needs_rehash(upgraded)
    assert auth.authenticate("legacy@campus.ac.in", PASSWORD) is not None


# ----------------------------------------------------------------------
# Session login / logout (plain dict stands in for st.session_state)
# ----------------------------------------------------------------------

def test_login_and_logout_session(fresh_db):
    user = auth.register_student("Sess", "sess@campus.ac.in", PASSWORD)
    state = {"stale_widget": "previous user's data"}

    auth.login(state, user)
    assert state == {
        authn.SESSION_USER_KEY: user["user_id"],
        authn.SESSION_LOGIN_AT_KEY: state[authn.SESSION_LOGIN_AT_KEY],
    }
    assert auth.get_session_user(state)["email"] == "sess@campus.ac.in"

    auth.logout(state)
    assert state == {}
    assert auth.get_session_user(state) is None


def test_session_stores_no_role_or_secret(fresh_db):
    user = auth.register_student("Sess", "sess@campus.ac.in", PASSWORD)
    state = {}
    auth.login(state, user)

    assert "student" not in map(str, state.values())
    assert not any("argon2" in str(v) for v in state.values())


def test_expired_or_deactivated_session_is_cleared(fresh_db):
    user = auth.register_student("Sess", "sess@campus.ac.in", PASSWORD)

    state = {}
    auth.login(state, user)
    later = time.time() + authn.SESSION_MAX_AGE_SECONDS + 1
    assert auth.get_session_user(state, now=later) is None
    assert state == {}

    auth.login(state, user)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET is_active = 0")
    assert auth.get_session_user(state) is None
    assert state == {}


# ----------------------------------------------------------------------
# Administrator bootstrap
# ----------------------------------------------------------------------

def test_bootstrap_admin_is_one_time(fresh_db):
    admin = auth.bootstrap_admin("Chief Admin", "Admin@Campus.ac.in", PASSWORD)

    assert admin["role"] == "admin"
    assert admin["name"] == "Chief Admin"
    assert admin["email"] == "admin@campus.ac.in"
    assert auth.authenticate("admin@campus.ac.in", PASSWORD)["role"] == "admin"

    with pytest.raises(auth.AdminBootstrapError, match="already exists"):
        auth.bootstrap_admin("Other", "second@campus.ac.in", PASSWORD)


def test_bootstrap_requires_campus_email(fresh_db):
    for email in ("admin@gmail.com", "admin@sub.campus.ac.in"):
        with pytest.raises(auth.RegistrationError, match="campus.ac.in"):
            auth.bootstrap_admin("Admin", email, PASSWORD)
    assert not db.admin_exists()


def test_bootstrap_never_promotes_existing_student(fresh_db):
    auth.register_student("Student", "student@campus.ac.in", PASSWORD)

    with pytest.raises(auth.AdminBootstrapError, match="never promoted"):
        auth.bootstrap_admin("Admin", "student@campus.ac.in", PASSWORD)

    assert user_row("student@campus.ac.in")["role"] == "student"
    assert not db.admin_exists()


def test_bootstrap_enforces_password_policy(fresh_db):
    with pytest.raises(auth.RegistrationError):
        auth.bootstrap_admin("Admin", "admin@campus.ac.in", "short")
    assert not db.admin_exists()


def run_create_admin(db_path, stdin_text, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "create_admin.py"),
            "--db", str(db_path), "--non-interactive", *extra,
        ],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_create_admin_command(tmp_path):
    db_path = tmp_path / "bootstrap.db"

    first = run_create_admin(
        db_path, f"Chief Admin\nadmin@campus.ac.in\n{PASSWORD}\n"
    )
    assert first.returncode == 0, first.stderr
    assert "Chief Admin <admin@campus.ac.in>" in first.stdout

    output = first.stdout + first.stderr
    assert PASSWORD not in output
    assert "$argon2" not in output

    second = run_create_admin(
        db_path, f"Other\nother@campus.ac.in\n{PASSWORD}\n"
    )
    assert second.returncode == 1
    assert "already exists" in second.stderr

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, email, role, password_hash FROM users"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][:3] == ("Chief Admin", "admin@campus.ac.in", "admin")
    assert rows[0][3].startswith("$argon2id$")


@pytest.mark.parametrize(
    "stdin_text, message",
    [
        (f"Admin\nadmin@gmail.com\n{PASSWORD}\n", "campus.ac.in"),
        ("Admin\nadmin@campus.ac.in\nweak\n", "at least"),
        (f"\nadmin@campus.ac.in\n{PASSWORD}\n", "name"),
    ],
)
def test_create_admin_command_rejects_invalid_input(
    tmp_path, stdin_text, message,
):
    db_path = tmp_path / "bootstrap.db"
    result = run_create_admin(db_path, stdin_text)

    assert result.returncode == 1
    assert message in result.stderr

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    conn.close()


def test_create_admin_has_no_password_argument():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "create_admin.py"),
         "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert "--password" not in result.stdout
    assert "--email" not in result.stdout


# ----------------------------------------------------------------------
# Per-client login rate limiter
# ----------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_rate_limiter_blocks_only_the_failing_client():
    from auth.rate_limit import LoginRateLimiter

    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=3, window_seconds=60, clock=clock)

    for _ in range(3):
        assert limiter.retry_after("attacker") == 0
        limiter.record_failure("attacker")

    assert limiter.retry_after("attacker") == 60
    assert limiter.retry_after("victim") == 0  # other clients unaffected

    clock.now += 30
    assert limiter.retry_after("attacker") == 30

    clock.now += 30
    assert limiter.retry_after("attacker") == 0  # window has slid past


def test_rate_limiter_reset_and_sliding_window():
    from auth.rate_limit import LoginRateLimiter

    clock = FakeClock()
    limiter = LoginRateLimiter(max_failures=2, window_seconds=10, clock=clock)

    limiter.record_failure("c")
    clock.now += 6
    limiter.record_failure("c")
    assert limiter.retry_after("c") == 4

    clock.now += 4  # first failure expires
    assert limiter.retry_after("c") == 0

    limiter.record_failure("c")
    assert limiter.retry_after("c") > 0
    limiter.reset("c")
    assert limiter.retry_after("c") == 0
