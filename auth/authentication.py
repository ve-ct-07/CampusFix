"""Registration, login, logout and administrator bootstrap for CampusFix.

Security properties
-------------------
* Only ``@campus.ac.in`` addresses are accepted, for students and for the
  administrator bootstrap. Emails are trimmed and lower-cased before
  validation, storage and lookup, and are unique.
* ``register_student`` has no role parameter: public registration can only
  create student accounts. Administrators are created exclusively by the
  one-time terminal command ``scripts/create_admin.py``.
* Passwords are hashed with Argon2id (``auth.password_utils``) and are never
  stored in plaintext; the database also rejects non-Argon2id hashes.
* Login returns a safe user dict (no password hash) or None. Every failure
  (unknown email, wrong password, inactive account) produces the same None
  and the same generic message, and unknown emails still cost a full hash
  verification.
* There is deliberately no per-account lockout (it would let anyone lock
  another student out). Repeated failures are throttled per client by
  ``auth.rate_limit`` instead.
* ``logout`` clears the authenticated user from the session state.
"""

import re
import sqlite3
import time
import unicodedata

from auth.password_utils import (
    hash_password,
    needs_rehash,
    verify_dummy,
    verify_password,
)
from database import db

CAMPUS_EMAIL_DOMAIN = "campus.ac.in"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 100

# Streamlit session-state keys holding the authenticated identity. Only the
# user ID is stored; role and status are re-read from the database.
SESSION_USER_KEY = "auth_user_id"
SESSION_LOGIN_AT_KEY = "auth_login_at"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

GENERIC_LOGIN_ERROR = "Invalid email or password."

_LOCAL_PART = r"[a-z0-9_%+\-]+(?:\.[a-z0-9_%+\-]+)*"
_EMAIL_RE = re.compile(
    rf"(?P<local>{_LOCAL_PART})@(?P<domain>[a-z0-9.\-]+)"
)


class AuthError(Exception):
    """Base class for authentication errors with user-safe messages."""


class RegistrationError(AuthError):
    pass


class AdminBootstrapError(AuthError):
    pass


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

normalize_email = db.normalize_email


def validate_name(name):
    """Return a trimmed display name or raise RegistrationError."""
    if not isinstance(name, str):
        raise RegistrationError("Enter your name.")

    cleaned = " ".join(name.split())
    if not cleaned:
        raise RegistrationError("Enter your name.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise RegistrationError(
            f"Name must be at most {MAX_NAME_LENGTH} characters."
        )
    if any(unicodedata.category(ch).startswith("C") for ch in cleaned):
        raise RegistrationError("Name contains invalid characters.")

    return cleaned


def validate_campus_email(email):
    """Return the normalized @campus.ac.in address or raise."""
    normalized = normalize_email(email)

    if not normalized or len(normalized) > MAX_EMAIL_LENGTH:
        raise RegistrationError("Enter a valid email address.")

    match = _EMAIL_RE.fullmatch(normalized)
    if not match or len(match.group("local")) > 64:
        raise RegistrationError("Enter a valid email address.")

    # Exact domain match: subdomains and look-alike suffixes are rejected.
    if match.group("domain") != CAMPUS_EMAIL_DOMAIN:
        raise RegistrationError(
            "A university email address ending in "
            f"@{CAMPUS_EMAIL_DOMAIN} is required."
        )

    return normalized


def validate_password(password, email=None):
    """Raise RegistrationError if the password does not meet the policy."""
    if not isinstance(password, str):
        raise RegistrationError("Enter a password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise RegistrationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise RegistrationError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
        )
    if not password.strip():
        raise RegistrationError("Password cannot be only whitespace.")
    if email and password.strip().lower() == normalize_email(email):
        raise RegistrationError("Password must not be your email address.")


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------

def get_active_user(user_id):
    """Return the user's public record only if the account is active."""
    user = db.get_user_by_id(user_id)
    if user and user["is_active"] and user["role"] in db.USER_ROLES:
        return user
    return None


def register_student(name, email, password):
    """Create a student account and return its safe public record.

    There is deliberately no role parameter.
    """
    name = validate_name(name)
    normalized = validate_campus_email(email)
    validate_password(password, normalized)

    try:
        user_id = db.create_user(
            name, normalized, hash_password(password), role="student",
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE" in str(error).upper():
            raise RegistrationError(
                "An account with this email address already exists."
            ) from None
        raise

    return db.get_user_by_id(user_id)


def authenticate(email, password):
    """Return the safe user record on success, otherwise None."""
    if (
        not isinstance(password, str)
        or not password
        or len(password) > MAX_PASSWORD_LENGTH
    ):
        verify_dummy(password)
        return None

    credentials = db.get_credentials_by_email(email)

    if credentials is None:
        # Unknown email: do the same work as a real verification.
        verify_dummy(password)
        return None

    if not verify_password(credentials["password_hash"], password):
        return None

    if not credentials["is_active"]:
        return None

    new_hash = (
        hash_password(password)
        if needs_rehash(credentials["password_hash"])
        else None
    )
    db.record_successful_login(credentials["user_id"], new_hash)

    return get_active_user(credentials["user_id"])


# ----------------------------------------------------------------------
# Session handling (works on st.session_state or any mutable mapping)
# ----------------------------------------------------------------------

def login(session_state, user):
    """Start a fresh authenticated session for ``user``.

    Any previous session data is discarded first so nothing carries over
    from an earlier user of the same browser session.
    """
    logout(session_state)
    session_state[SESSION_USER_KEY] = int(user["user_id"])
    session_state[SESSION_LOGIN_AT_KEY] = time.time()


def logout(session_state):
    """Clear the authenticated user (and all other session data)."""
    for key in list(session_state.keys()):
        del session_state[key]


def get_session_user(session_state, now=None):
    """Return the active signed-in user, or None.

    Returns None (after clearing the session) when nobody is signed in, the
    session is older than SESSION_MAX_AGE_SECONDS, or the account is no
    longer active. The role always comes from the database.
    """
    user_id = session_state.get(SESSION_USER_KEY)
    if user_id is None:
        return None

    now = time.time() if now is None else now
    login_at = session_state.get(SESSION_LOGIN_AT_KEY)

    if (
        not isinstance(login_at, (int, float))
        or now - login_at > SESSION_MAX_AGE_SECONDS
        or now < login_at
    ):
        logout(session_state)
        return None

    user = get_active_user(user_id)
    if user is None:
        logout(session_state)
    return user


# ----------------------------------------------------------------------
# Administrator bootstrap (terminal only)
# ----------------------------------------------------------------------

def admin_exists():
    return db.admin_exists()


def bootstrap_admin(name, email, password):
    """Create the first administrator; refuses if one already exists.

    Called only by ``scripts/create_admin.py``. The administrator email must
    also be an @campus.ac.in address.
    """
    name = validate_name(name)
    normalized = validate_campus_email(email)
    validate_password(password, normalized)

    try:
        user_id = db.create_first_admin(
            name, normalized, hash_password(password),
        )
    except (db.AdminAlreadyExistsError, db.EmailAlreadyRegisteredError) as e:
        raise AdminBootstrapError(str(e)) from None

    return db.get_user_by_id(user_id)
