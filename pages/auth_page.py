"""Sign-in, student registration and session UI for CampusFix.

Session rules live in ``auth.authentication``: only the signed-in user's ID
and sign-in time are kept in ``st.session_state`` (server-side), and the
role and active status are re-read from the database on every run.
"""

import secrets

import streamlit as st

from auth import (
    AuthError,
    CAMPUS_EMAIL_DOMAIN,
    GENERIC_LOGIN_ERROR,
    MIN_PASSWORD_LENGTH,
    authenticate,
    get_session_user,
    login,
    logout,
    register_student,
)
from auth.authentication import SESSION_USER_KEY
from auth.rate_limit import login_limiter

# Identifies this browser session for login throttling. It is never tied to
# an account, so failures here cannot slow down anyone else's sign-in.
CLIENT_KEY = "_login_client_key"

ROLE_LABELS = {"admin": "Administrator", "student": "Student"}


def start_session(user):
    login(st.session_state, user)


def end_session():
    logout(st.session_state)


def get_current_user():
    """Return the active signed-in user, or None (clearing stale sessions)."""
    had_session = SESSION_USER_KEY in st.session_state
    user = get_session_user(st.session_state)

    if user is None and had_session:
        st.session_state["auth_notice"] = (
            "Your session expired or was ended. Please sign in again."
        )
    return user


def _client_key():
    if CLIENT_KEY not in st.session_state:
        st.session_state[CLIENT_KEY] = secrets.token_hex(16)
    return st.session_state[CLIENT_KEY]


def display_name(user):
    # Accounts migrated from schema version 1 may have an empty name.
    return user.get("name") or user["email"]


def render_account_sidebar(user):
    st.sidebar.markdown(f"**Signed in as**  \n{display_name(user)}")
    st.sidebar.caption(
        f"{user['email']} · Role: {ROLE_LABELS.get(user['role'], user['role'])}"
    )

    if st.sidebar.button("Sign out", key="sign_out", width="stretch"):
        end_session()
        st.rerun()


def render_auth_page():
    st.header("Welcome to CampusFix")

    notice = st.session_state.pop("auth_notice", None)
    if notice:
        st.info(notice)

    sign_in_tab, register_tab = st.tabs(
        ["Sign in", "Create student account"]
    )

    with sign_in_tab:
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input(
                "University email",
                key="login_email",
                placeholder=f"name@{CAMPUS_EMAIL_DOMAIN}",
            )
            password = st.text_input(
                "Password", type="password", key="login_password",
            )
            submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            client = _client_key()
            wait = login_limiter.retry_after(client)

            if wait:
                st.error(
                    "Too many failed sign-in attempts from this browser "
                    f"session. Please wait {wait} seconds and try again."
                )
            else:
                user = authenticate(email, password)
                if user is None:
                    login_limiter.record_failure(client)
                    st.error(GENERIC_LOGIN_ERROR)
                else:
                    login_limiter.reset(client)
                    start_session(user)
                    st.rerun()

    with register_tab:
        st.caption(
            f"Student accounts require a university email ending in "
            f"@{CAMPUS_EMAIL_DOMAIN}. Administrator accounts cannot be "
            "created here."
        )
        with st.form("register_form", clear_on_submit=False):
            reg_name = st.text_input("Full name", key="register_name")
            reg_email = st.text_input(
                "University email",
                key="register_email",
                placeholder=f"name@{CAMPUS_EMAIL_DOMAIN}",
            )
            reg_password = st.text_input(
                f"Password (at least {MIN_PASSWORD_LENGTH} characters)",
                type="password",
                key="register_password",
            )
            reg_confirm = st.text_input(
                "Confirm password",
                type="password",
                key="register_confirm",
            )
            registered = st.form_submit_button(
                "Create account", type="primary",
            )

        if registered:
            if reg_password != reg_confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    user = register_student(
                        reg_name, reg_email, reg_password,
                    )
                except AuthError as error:
                    st.error(str(error))
                else:
                    start_session(user)
                    st.rerun()
