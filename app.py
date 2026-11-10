import streamlit as st

from database.access import AuthorizationError
from database.db import initialize_database
from pages.auth_page import (
    end_session,
    get_current_user,
    render_account_sidebar,
    render_auth_page,
)
from pages.student_portal import render_student_portal
from pages.admin_dashboard import render_admin_dashboard
from pages.issue_tracking import render_issue_tracking

STUDENT_PAGES = ["Student portal", "Track a report"]
ADMIN_PAGES = STUDENT_PAGES + ["Admin dashboard"]

st.set_page_config(
    page_title="CampusFix",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

initialize_database()


def main():
    st.sidebar.title("🏫 CampusFix")
    st.sidebar.caption("University maintenance management")

    st.title("CampusFix")
    st.caption("A centralized platform for reporting and resolving university maintenance issues.")

    user = get_current_user()

    if user is None:
        render_auth_page()
        st.sidebar.divider()
        st.sidebar.caption("Prototype build • Local SQLite storage")
        return

    # Navigation reflects the role stored in the database. Every page and
    # data operation re-checks authorization independently.
    options = ADMIN_PAGES if user["role"] == "admin" else STUDENT_PAGES
    page = st.sidebar.radio("Navigate", options)

    try:
        if page == "Student portal":
            render_student_portal(user)
        elif page == "Track a report":
            render_issue_tracking(user)
        elif page == "Admin dashboard":
            render_admin_dashboard(user)
    except AuthorizationError:
        # The account was deactivated or its role changed mid-session.
        end_session()
        st.error("Your session is no longer authorized. Please sign in again.")
        return

    st.sidebar.divider()
    render_account_sidebar(user)
    st.sidebar.divider()
    st.sidebar.caption("Prototype build • Local SQLite storage")


# Route every request through this script. Calling st.navigation disables
# Streamlit's automatic pages/ directory routing, which would otherwise let
# a browser open files in pages/ directly and skip the sign-in check above.
st.navigation(
    [st.Page(main, title="CampusFix", url_path="campusfix", default=True)],
    position="hidden",
).run()
