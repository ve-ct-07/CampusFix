"""Argon2id password hashing for CampusFix.

Uses argon2-cffi's ``PasswordHasher`` with ``Type.ID`` (Argon2id) and the
library's default RFC 9106 low-memory parameters. Plaintext passwords are
never stored or logged; only PHC-format hash strings leave this module.
"""

from argon2 import PasswordHasher, Type
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_hasher = PasswordHasher(type=Type.ID)
_dummy_hash = None


def hash_password(password):
    """Return an Argon2id PHC-format hash of ``password``."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")
    return _hasher.hash(password)


def verify_password(password_hash, password):
    """Return True only if ``password`` matches ``password_hash``."""
    if not isinstance(password_hash, str) or not isinstance(password, str):
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash):
    """True if the hash was made with weaker or different parameters."""
    return _hasher.check_needs_rehash(password_hash)


def is_argon2id_hash(value):
    return isinstance(value, str) and value.startswith("$argon2id$")


def verify_dummy(password):
    """Spend the same work as a real verification and return False.

    Used when an account does not exist so that response
    time does not reveal which email addresses are registered.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("campusfix-timing-equalisation-dummy")
    verify_password(_dummy_hash, password if isinstance(password, str) else "")
    return False
