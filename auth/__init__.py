"""CampusFix authentication package.

* ``auth.password_utils``  - Argon2id hashing and verification
* ``auth.authentication``  - registration, login, logout, sessions and the
  one-time administrator bootstrap
"""

from auth.authentication import (
    AdminBootstrapError,
    AuthError,
    CAMPUS_EMAIL_DOMAIN,
    GENERIC_LOGIN_ERROR,
    MIN_PASSWORD_LENGTH,
    RegistrationError,
    authenticate,
    bootstrap_admin,
    get_active_user,
    get_session_user,
    login,
    logout,
    register_student,
)
from auth.password_utils import hash_password, verify_password

__all__ = [
    "AdminBootstrapError",
    "AuthError",
    "CAMPUS_EMAIL_DOMAIN",
    "GENERIC_LOGIN_ERROR",
    "MIN_PASSWORD_LENGTH",
    "RegistrationError",
    "authenticate",
    "bootstrap_admin",
    "get_active_user",
    "get_session_user",
    "hash_password",
    "login",
    "logout",
    "register_student",
    "verify_password",
]
