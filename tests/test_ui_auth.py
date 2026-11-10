"""Streamlit-level tests for sign-in, navigation and student privacy."""

import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import database.db as db
import auth
import pages.student_portal as student_portal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP = str(PROJECT_ROOT / "app.py")
PASSWORD = "ui test password"


def rendered_text(at):
    """Concatenate all visible text produced by a script run."""
    parts = []
    for kind in (
        "title", "header", "subheader", "markdown", "caption", "text",
        "success", "info", "warning", "error",
    ):
        parts.extend(str(element.value) for element in getattr(at, kind))
    for metric in at.metric:
        parts.extend([str(metric.label), str(metric.value)])
    for frame in at.dataframe:
        parts.append(frame.value.to_string())
    for expander in at.expander:
        parts.append(str(expander.label))
    return "\n".join(parts)


def new_app():
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def sign_in(at, email, password=PASSWORD):
    at.text_input(key="login_email").set_value(email)
    at.text_input(key="login_password").set_value(password)
    at.button(key="FormSubmitter:login_form-Sign in").click().run()
    assert not at.exception, [e.message for e in at.exception]


@pytest.fixture
def accounts(fresh_db):
    return {
        "admin": auth.bootstrap_admin("Admin User", "admin@campus.ac.in", PASSWORD),
        "alice": auth.register_student("Alice Rao", "alice@campus.ac.in", PASSWORD),
        "bob": auth.register_student("Bob", "bob@campus.ac.in", PASSWORD),
    }


@pytest.fixture
def mock_ai(monkeypatch):
    prediction = {
        "category": "Plumbing", "severity": "Medium",
        "secondary_hazards": [], "safety_risk": False,
        "safety_risk_reason": "None", "summary": "Leak.",
        "reason": "Leak.", "method": "test_mock", "confidence": None,
    }
    monkeypatch.setattr(
        student_portal, "classify_issue",
        lambda description, image_path=None: dict(prediction),
    )
    monkeypatch.setattr(
        student_portal, "find_similar_issues", lambda *a, **k: [],
    )


# ----------------------------------------------------------------------
# Sign-in gate and registration
# ----------------------------------------------------------------------

def test_unauthenticated_visitor_only_sees_sign_in(accounts):
    at = new_app()
    text = rendered_text(at)

    assert "Welcome to CampusFix" in text
    assert len(at.radio) == 0
    assert "Report a campus issue" not in text
    assert "Maintenance Administration" not in text
    assert "Track Your Campus Issue" not in text


def test_registration_form_offers_no_role_choice(accounts):
    at = new_app()
    assert len(at.selectbox) == 0
    assert len(at.radio) == 0
    assert {w.key for w in at.text_input} == {
        "login_email", "login_password",
        "register_name", "register_email", "register_password",
        "register_confirm",
    }


def register_via_ui(at, email, password=PASSWORD, confirm=None,
                    name="New Student"):
    at.text_input(key="register_name").set_value(name)
    at.text_input(key="register_email").set_value(email)
    at.text_input(key="register_password").set_value(password)
    at.text_input(key="register_confirm").set_value(
        password if confirm is None else confirm
    )
    at.button(key="FormSubmitter:register_form-Create account").click().run()
    assert not at.exception, [e.message for e in at.exception]


def test_registration_through_ui(fresh_db):
    at = new_app()

    register_via_ui(at, "outsider@gmail.com")
    assert any("@campus.ac.in" in e.value for e in at.error)

    register_via_ui(at, "newbie@campus.ac.in", confirm="something else 1")
    assert any("do not match" in e.value for e in at.error)

    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    register_via_ui(at, "  NewBie@Campus.ac.in ")

    # Registration signs the student in with student navigation only.
    assert at.radio[0].options == ["Student portal", "Track a report"]
    assert "New Student" in rendered_text(at)

    with db.get_connection() as conn:
        row = conn.execute("SELECT name, email, role FROM users").fetchone()
    assert tuple(row) == ("New Student", "newbie@campus.ac.in", "student")


def test_wrong_password_shows_generic_error(accounts):
    at = new_app()
    sign_in(at, "alice@campus.ac.in", "wrong password 1")

    assert len(at.radio) == 0
    assert any("Invalid email or password" in e.value for e in at.error)

    at2 = new_app()
    sign_in(at2, "nobody@campus.ac.in", "wrong password 1")
    assert [e.value for e in at2.error] == [e.value for e in at.error]


# ----------------------------------------------------------------------
# Role-based navigation and sessions
# ----------------------------------------------------------------------

def test_navigation_depends_on_role(accounts):
    student = new_app()
    sign_in(student, "alice@campus.ac.in")
    assert student.radio[0].options == ["Student portal", "Track a report"]
    sidebar = "\n".join(
        str(e.value) for e in student.sidebar.markdown
    ) + "\n".join(str(e.value) for e in student.sidebar.caption)
    assert "Alice Rao" in sidebar and "Role: Student" in sidebar

    admin = new_app()
    sign_in(admin, "admin@campus.ac.in")
    assert admin.radio[0].options == [
        "Student portal", "Track a report", "Admin dashboard",
    ]
    assert any(
        "Role: Administrator" in str(e.value) for e in admin.sidebar.caption
    )

    admin.radio[0].set_value("Admin dashboard").run()
    assert not admin.exception
    assert "Maintenance Administration" in rendered_text(admin)


def _render_dashboard_for_probe_user():
    import streamlit as st
    from pages.admin_dashboard import render_admin_dashboard
    render_admin_dashboard(st.session_state["probe_user"])


@pytest.mark.parametrize("forge_role", [False, True])
def test_admin_dashboard_refuses_students_even_if_called_directly(
    accounts, forge_role,
):
    db.create_issue("Visible only to admins", "Secret", "Admin Block")
    probe = dict(accounts["alice"])
    if forge_role:
        probe["role"] = "admin"

    at = AppTest.from_function(_render_dashboard_for_probe_user)
    at.session_state["probe_user"] = probe
    at.run()

    assert not at.exception
    text = rendered_text(at)
    assert "Administrator access is required" in text
    assert "Maintenance Administration" not in text
    assert "Visible only to admins" not in text


def test_sign_out_clears_the_session(accounts):
    at = new_app()
    sign_in(at, "alice@campus.ac.in")
    assert len(at.radio) == 1

    at.button(key="sign_out").click().run()

    assert len(at.radio) == 0
    assert "Welcome to CampusFix" in rendered_text(at)
    assert "auth_user_id" not in at.session_state


def test_expired_session_requires_sign_in_again(accounts):
    at = new_app()
    sign_in(at, "alice@campus.ac.in")

    at.session_state["auth_login_at"] = time.time() - 13 * 60 * 60
    at.run()

    assert len(at.radio) == 0
    assert "session expired" in rendered_text(at)


def test_deactivated_account_is_signed_out_on_next_action(accounts):
    at = new_app()
    sign_in(at, "alice@campus.ac.in")

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_active = 0 WHERE email = 'alice@campus.ac.in'"
        )

    at.run()
    assert len(at.radio) == 0


def test_authorization_failure_during_render_ends_session_cleanly(
    accounts, monkeypatch,
):
    """If access is revoked between the session check and a data call
    (e.g. a concurrent role change), the user sees a clean message, not a
    traceback, and the session ends."""
    import database.access as access
    import pages.issue_tracking as issue_tracking

    at = new_app()
    sign_in(at, "alice@campus.ac.in")

    def revoked(actor):
        raise access.AuthorizationError("revoked")

    monkeypatch.setattr(issue_tracking, "list_my_reports", revoked)
    at.radio[0].set_value("Track a report").run()

    assert not at.exception, [e.message for e in at.exception]
    assert any("no longer authorized" in e.value for e in at.error)
    assert "auth_user_id" not in at.session_state


# ----------------------------------------------------------------------
# Student privacy
# ----------------------------------------------------------------------

def track(at, reference):
    at.radio[0].set_value("Track a report").run()
    at.text_input[0].set_value(reference)
    at.button(key="FormSubmitter:track_issue_form-Track Report").click().run()
    assert not at.exception, [e.message for e in at.exception]
    return rendered_text(at)


def test_student_cannot_track_another_students_report(accounts):
    bob_issue = db.create_issue(
        "BOB_PRIVATE_TITLE", "Bob only", "BOB_ROOM_LOCATION",
        submitted_by=accounts["bob"]["user_id"],
    )
    alice_issue = db.create_issue(
        "Alice tap", "Leak", "Hostel A",
        submitted_by=accounts["alice"]["user_id"],
    )
    legacy_issue = db.create_issue("LEGACY_TITLE", "Old", "Old Block")

    at = new_app()
    sign_in(at, "alice@campus.ac.in")

    for other in (bob_issue, legacy_issue):
        text = track(at, f"CF-{other:04d}")
        assert "No report found for that reference" in text
        assert "BOB_PRIVATE_TITLE" not in text
        assert "BOB_ROOM_LOCATION" not in text
        assert "LEGACY_TITLE" not in text

    text = track(at, f"CF-{alice_issue:04d}")
    assert f"CF-{alice_issue:04d} | Alice tap" in text

    # The "Your reports" list only contains Alice's own report.
    assert len(at.dataframe) == 1
    assert list(at.dataframe[0].value["Title"]) == ["Alice tap"]


def test_admin_can_track_any_report(accounts):
    legacy_issue = db.create_issue("LEGACY_TITLE", "Old", "Old Block")
    at = new_app()
    sign_in(at, "admin@campus.ac.in")
    assert "LEGACY_TITLE" in track(at, f"CF-{legacy_issue:04d}")


def test_duplicate_suggestions_do_not_reveal_other_reports(
    accounts, mock_ai, monkeypatch,
):
    other = db.create_issue(
        "SECRET_OTHER_TITLE", "Bob's report", "Hostel A, Room 12",
        submitted_by=accounts["bob"]["user_id"],
    )
    monkeypatch.setattr(
        student_portal, "find_similar_issues",
        lambda *a, **k: [{
            "issue_id": other, "title": "SECRET_OTHER_TITLE",
            "location": "Hostel A, Room 12", "status": "Submitted",
            "similarity": 0.97,
        }],
    )

    at = new_app()
    sign_in(at, "alice@campus.ac.in")
    at.radio[0].set_value("Student portal").run()
    at.text_input[0].set_value("Leaking tap")
    at.text_area[0].set_value("Water leaking from the tap.")
    at.text_input[1].set_value("Hostel A, Room 12 corridor")
    at.button[0].click().run()

    assert not at.exception, [e.message for e in at.exception]
    text = rendered_text(at)

    assert "may already have been reported" in text
    assert "SECRET_OTHER_TITLE" not in text
    assert "Room 12" not in text.replace("Room 12 corridor", "")
    assert f"CF-{other:04d}" not in text

    # The suggestion is still queued for administrator review.
    pending = db.list_pending_duplicate_reviews()
    assert [p["matched_issue_id"] for p in pending] == [other]
    assert db.get_issue(pending[0]["new_issue_id"])["submitted_by"] == (
        accounts["alice"]["user_id"]
    )


# ----------------------------------------------------------------------
# Administrator identity in the dashboard
# ----------------------------------------------------------------------

def test_safety_response_records_signed_in_admin(accounts):
    issue_id = db.create_issue(
        "Water near switchboard", "Sparks", "Library",
        priority="Critical",
        ai_assessment={"safety_risk": True, "method": "test_mock"},
        submitted_by=accounts["alice"]["user_id"],
    )

    at = new_app()
    sign_in(at, "admin@campus.ac.in")
    at.radio[0].set_value("Admin dashboard").run()

    assert not any("Administrator name" in w.label for w in at.text_input)
    assert "Recorded as: admin@campus.ac.in" in rendered_text(at)

    at.button(
        key=f"FormSubmitter:safety_response_form_{issue_id}"
        "-Record Safety Response"
    ).click().run()
    assert not at.exception, [e.message for e in at.exception]

    history = db.get_safety_response_history(issue_id=issue_id)
    assert len(history) == 1
    assert history[0]["acknowledged_by"] == "admin@campus.ac.in"


# ----------------------------------------------------------------------
# Streamlit pages/ directory cannot bypass the sign-in gate
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "page",
    sorted(
        p.name for p in (PROJECT_ROOT / "pages").glob("*.py")
        if p.name != "__init__.py"
    ),
)
def test_page_modules_render_nothing_when_opened_directly(fresh_db, page):
    """Streamlit can serve files in pages/ directly (without app.py).

    app.py disables that routing with st.navigation, but as a second line of
    defence every page module must only define functions: executed on its
    own it must render nothing and expose no data.
    """
    db.create_issue("PAGE_LEAK_CHECK", "x", "y")

    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / page), default_timeout=30)
    at.run()

    assert not at.exception, [e.message for e in at.exception]
    assert len(at.main.children) == 0
    assert len(at.sidebar.children) == 0
    assert "PAGE_LEAK_CHECK" not in rendered_text(at)



# ----------------------------------------------------------------------
# Login throttling is per browser session, never per account
# ----------------------------------------------------------------------

def test_failed_logins_throttle_only_the_offending_session(accounts):
    from auth.rate_limit import MAX_FAILURES

    attacker = new_app()
    for _ in range(MAX_FAILURES):
        sign_in(attacker, "alice@campus.ac.in", "wrong guess 12345")
        assert any("Invalid email or password" in e.value for e in attacker.error)

    # The attacker's session is now throttled, even with the right password.
    sign_in(attacker, "alice@campus.ac.in")
    assert len(attacker.radio) == 0
    assert any("Too many failed sign-in attempts" in e.value
               for e in attacker.error)

    # The real account owner, in their own session, is not affected.
    victim = new_app()
    sign_in(victim, "alice@campus.ac.in")
    assert len(victim.radio) == 1

    # And nothing about the account itself was changed.
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT failed_login_attempts, locked_until FROM users "
            "WHERE email = 'alice@campus.ac.in'"
        ).fetchone()
    assert tuple(row) == (0, None)


def test_individual_report_update_in_dashboard_logs_admin(accounts):
    issue_id = db.create_issue(
        "Broken window", "Cracked glass", "Block C",
        submitted_by=accounts["bob"]["user_id"],
    )

    at = new_app()
    sign_in(at, "admin@campus.ac.in")
    at.radio[0].set_value("Admin dashboard").run()

    status_box = next(s for s in at.selectbox if s.label == "Update status")
    status_box.set_value("In Progress")
    next(t for t in at.text_input
         if t.label == "Assigned maintenance team").set_value("Civil")
    next(b for b in at.button
         if b.label == "Save Individual Report Changes").click().run()
    assert not at.exception, [e.message for e in at.exception]

    issue = db.get_issue(issue_id)
    assert (issue["status"], issue["assigned_to"]) == ("In Progress", "Civil")
    assert {e["changed_by"] for e in db.get_issue_activity(issue_id)} == {
        "admin@campus.ac.in"
    }
    assert "by admin@campus.ac.in" in rendered_text(at)
