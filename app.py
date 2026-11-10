import streamlit as st

from database.db import initialize_database
from pages.student_portal import render_student_portal
from pages.admin_dashboard import render_admin_dashboard
from pages.issue_tracking import render_issue_tracking

st.set_page_config(
    page_title="CampusFix",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

initialize_database()

st.sidebar.title("🏫 CampusFix")
st.sidebar.caption("University maintenance management")
page = st.sidebar.radio(
    "Navigate",
    ["Student portal", "Track a report", "Admin dashboard"],
)

st.title("CampusFix")
st.caption("A centralized platform for reporting and resolving university maintenance issues.")

if page == "Student portal":
    render_student_portal()
elif page == "Track a report":
    render_issue_tracking()
else:
    render_admin_dashboard()

st.sidebar.divider()
st.sidebar.caption("Prototype build • Local SQLite storage")
