"""Tests for role permissions, report ownership and audit identity."""

import inspect
import sqlite3

import pytest

import database.access as access
import database.db as db
import auth

PASSWORD = "correct horse battery"


@pytest.fixture
def users(fresh_db):
    admin = auth.bootstrap_admin("Admin User", "admin@campus.ac.in", PASSWORD)
    alice = auth.register_student("Alice", "alice@campus.ac.in", PASSWORD)
    bob = auth.register_student("Bob", "bob@campus.ac.in", PASSWORD)
    return {"admin": admin, "alice": alice, "bob": bob}


def submit(actor, title="Leaking tap", location="Hostel A", **kwargs):
    return access.submit_report(
        actor,
        title=title,
        description=f"{title} description",
        location=location,
        **kwargs,
    )


def table_dump():
    with db.get_connection() as conn:
        dump = {}
        for table in (
            "issues", "issue_groups", "issue_group_members",
            "incident_activity", "safety_response_log",
            "duplicate_review_candidates",
        ):
            dump[table] = [
                tuple(row) for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
        return dump


def seed_admin_scenario(users):
    """Create data that every admin operation can act on."""
    safety = submit(
        users["alice"], "Water near switchboard",
        ai_assessment={"safety_risk": True, "safety_risk_reason": "Shock"},
    )
    safety_dup = submit(
        users["bob"], "Wet switchboard",
        ai_assessment={"safety_risk": True, "safety_risk_reason": "Shock"},
    )
    plain = submit(users["bob"], "Broken chair")
    group_id = db.attach_duplicate_report(safety_dup, safety)
    pending = submit(users["alice"], "Another leak")
    db.save_duplicate_candidates(
        pending, [{"issue_id": plain, "similarity": 0.9}]
    )
    candidate_id = db.list_pending_duplicate_reviews()[0]["candidate_id"]
    return {
        "safety": safety, "plain": plain, "group_id": group_id,
        "candidate_id": candidate_id,
    }


def admin_operations(ids):
    """(name, callable(actor)) for every administrator-only operation."""
    g, c, s, p = ids["group_id"], ids["candidate_id"], ids["safety"], ids["plain"]
    return [
        ("list_issues", lambda a: access.list_issues(a)),
        ("get_issue_activity", lambda a: access.get_issue_activity(a, p)),
        ("update_issue_status",
         lambda a: access.update_issue_status(a, p, "Resolved", "Civil")),
        ("list_incident_groups", lambda a: access.list_incident_groups(a)),
        ("list_group_members", lambda a: access.list_group_members(a, g)),
        ("get_incident_group", lambda a: access.get_incident_group(a, g)),
        ("get_issue_group", lambda a: access.get_issue_group(a, s)),
        ("get_incident_activity", lambda a: access.get_incident_activity(a, g)),
        ("get_incident_analytics", lambda a: access.get_incident_analytics(a)),
        ("get_sla_incidents", lambda a: access.get_sla_incidents(a)),
        ("get_safety_response_history",
         lambda a: access.get_safety_response_history(a, group_id=g)),
        ("list_pending_duplicate_reviews",
         lambda a: access.list_pending_duplicate_reviews(a)),
        ("list_duplicate_review_history",
         lambda a: access.list_duplicate_review_history(a)),
        ("update_incident_group",
         lambda a: access.update_incident_group(a, g, "Resolved", "High", "IT")),
        ("record_safety_response",
         lambda a: access.record_safety_response(
             a, "Restricted access", group_id=g)),
        ("review_duplicate_candidate",
         lambda a: access.review_duplicate_candidate(a, c, "Rejected")),
        ("confirm_duplicate_candidate",
         lambda a: access.confirm_duplicate_candidate(a, c)),
    ]


# ----------------------------------------------------------------------
# Role permissions
# ----------------------------------------------------------------------

def test_every_admin_operation_is_covered_by_this_test(users):
    """Guard: a newly added admin wrapper must be added to the list above."""
    ids = seed_admin_scenario(users)
    tested = {name for name, _ in admin_operations(ids)}

    student_or_shared = {
        "require_user", "require_admin", "is_admin", "submit_report",
        "list_my_reports", "get_report_tracking",
        "list_reports_for_duplicate_check", "record_duplicate_candidates",
    }
    public = {
        name for name, obj in vars(access).items()
        if callable(obj) and not name.startswith("_")
        and getattr(obj, "__module__", None) == access.__name__
        and not inspect.isclass(obj)
    }

    assert public - student_or_shared == tested


def test_student_cannot_perform_any_admin_operation(users):
    ids = seed_admin_scenario(users)
    before = table_dump()

    for name, operation in admin_operations(ids):
        with pytest.raises(access.AuthorizationError):
            operation(users["alice"])

    assert table_dump() == before


@pytest.mark.parametrize(
    "actor",
    [None, 0, -1, "abc", True, {}, {"user_id": None}, 999_999],
)
def test_unauthenticated_actor_is_rejected_everywhere(users, actor):
    ids = seed_admin_scenario(users)
    before = table_dump()

    for name, operation in admin_operations(ids):
        with pytest.raises(access.AuthorizationError):
            operation(actor)

    with pytest.raises(access.AuthorizationError):
        submit(actor)
    with pytest.raises(access.AuthorizationError):
        access.list_my_reports(actor)
    with pytest.raises(access.AuthorizationError):
        access.get_report_tracking(actor, ids["plain"])
    with pytest.raises(access.AuthorizationError):
        access.list_reports_for_duplicate_check(actor)

    assert table_dump() == before


def test_role_in_session_data_is_never_trusted(users):
    ids = seed_admin_scenario(users)
    forged = {**users["alice"], "role": "admin"}

    with pytest.raises(access.AuthorizationError):
        access.list_issues(forged)
    with pytest.raises(access.AuthorizationError):
        access.confirm_duplicate_candidate(forged, ids["candidate_id"])
    assert access.get_report_tracking(forged, ids["plain"]) is None


def test_deactivated_admin_loses_access_immediately(users):
    seed_admin_scenario(users)
    assert access.list_issues(users["admin"])

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?",
            (users["admin"]["user_id"],),
        )

    with pytest.raises(access.AuthorizationError):
        access.list_issues(users["admin"])


def test_admin_can_perform_every_admin_operation(users):
    ids = seed_admin_scenario(users)

    for name, operation in admin_operations(ids):
        if name == "confirm_duplicate_candidate":
            continue  # candidate already reviewed by the rejection above
        operation(users["admin"])  # must not raise

    # Confirmation exercised separately with a fresh candidate.
    new = submit(users["bob"], "Leak again")
    db.save_duplicate_candidates(new, [{"issue_id": ids["plain"], "similarity": 0.95}])
    candidate = access.list_pending_duplicate_reviews(users["admin"])[0]
    group_id = access.confirm_duplicate_candidate(
        users["admin"], candidate["candidate_id"], reopen_resolved=True
    )
    assert db.get_issue_group(new)["group_id"] == group_id


def test_student_and_admin_can_submit_reports(users):
    student_issue = submit(users["alice"])
    admin_issue = submit(users["admin"])

    assert db.get_issue(student_issue)["submitted_by"] == users["alice"]["user_id"]
    assert db.get_issue(admin_issue)["submitted_by"] == users["admin"]["user_id"]


def test_submitter_cannot_be_chosen_by_caller(users):
    assert "submitted_by" not in inspect.signature(access.submit_report).parameters
    with pytest.raises(TypeError):
        submit(users["alice"], submitted_by=users["bob"]["user_id"])


# ----------------------------------------------------------------------
# Report ownership
# ----------------------------------------------------------------------

def test_students_only_see_their_own_reports(users):
    a1 = submit(users["alice"], "Alice private issue", "Alice Room 101")
    a2 = submit(users["alice"], "Alice second issue")
    b1 = submit(users["bob"], "Bob private issue", "Bob Room 202")

    alice_reports = access.list_my_reports(users["alice"])
    bob_reports = access.list_my_reports(users["bob"])

    assert [r["issue_id"] for r in alice_reports] == [a2, a1]
    assert [r["issue_id"] for r in bob_reports] == [b1]
    assert "Bob" not in str(alice_reports)
    assert "Alice" not in str(bob_reports)

    assert access.get_report_tracking(users["alice"], a1)["issue"]["title"] == (
        "Alice private issue"
    )
    assert access.get_report_tracking(users["alice"], b1) is None
    assert access.get_report_tracking(users["bob"], a1) is None


def test_students_can_only_queue_duplicates_for_their_own_report(users):
    alice_issue = submit(users["alice"])
    bob_issue = submit(users["bob"])
    match = [{"issue_id": alice_issue, "similarity": 0.9}]

    with pytest.raises(access.AuthorizationError):
        access.record_duplicate_candidates(users["alice"], bob_issue, match)
    assert db.list_pending_duplicate_reviews() == []

    saved = access.record_duplicate_candidates(
        users["bob"], bob_issue, match
    )
    assert saved == 1
    assert db.list_pending_duplicate_reviews()[0]["new_issue_id"] == bob_issue


def test_not_owned_and_nonexistent_reports_are_indistinguishable(users):
    bob_issue = submit(users["bob"])

    for issue_id in (bob_issue, 999_999, 0, -5, "1 OR 1=1", None):
        assert access.get_report_tracking(users["alice"], issue_id) is None


def test_legacy_unowned_reports_are_admin_only(users):
    legacy = db.create_issue("Legacy report", "Before auth", "Old Block")
    assert db.get_issue(legacy)["submitted_by"] is None

    assert access.get_report_tracking(users["alice"], legacy) is None
    assert legacy not in [r["issue_id"] for r in access.list_my_reports(users["alice"])]

    admin_view = access.get_report_tracking(users["admin"], legacy)
    assert admin_view["issue"]["title"] == "Legacy report"


def test_student_tracking_view_hides_internal_fields(users):
    issue_id = submit(
        users["alice"],
        image_path="/srv/uploads/secret.png",
        ai_assessment={"summary": "INTERNAL_AI_SUMMARY"},
    )

    student_view = access.get_report_tracking(users["alice"], issue_id)
    admin_view = access.get_report_tracking(users["admin"], issue_id)

    assert set(student_view["issue"]) == set(access._STUDENT_ISSUE_FIELDS)
    assert "secret.png" not in str(student_view)
    assert "INTERNAL_AI_SUMMARY" not in str(student_view)
    assert admin_view["issue"]["image_path"] == "/srv/uploads/secret.png"


def test_grouped_report_tracking_does_not_reveal_other_students(users):
    alice_issue = submit(users["alice"], "Alice leak", "Hostel A")
    bob_issue = submit(users["bob"], "BOB_SECRET_TITLE", "Hostel A")
    group_id = db.attach_duplicate_report(alice_issue, bob_issue)

    access.update_incident_group(
        users["admin"], group_id, "Assigned", "High", "Plumbing"
    )

    view = access.get_report_tracking(users["alice"], alice_issue)

    assert view["incident_ref"] == f"GF-{group_id:04d}"
    assert view["status"] == "Assigned"
    assert "BOB_SECRET_TITLE" not in str(view)
    assert "bob@campus.ac.in" not in str(view)
    assert "admin@campus.ac.in" not in str(view)


# ----------------------------------------------------------------------
# Administrator identity in audit records
# ----------------------------------------------------------------------

def test_audit_records_use_authenticated_admin_identity(users):
    ids = seed_admin_scenario(users)
    admin = users["admin"]

    # Typed names can no longer be supplied.
    for func, param in (
        (access.review_duplicate_candidate, "reviewed_by"),
        (access.confirm_duplicate_candidate, "reviewed_by"),
        (access.record_safety_response, "acknowledged_by"),
        (access.update_incident_group, "changed_by"),
    ):
        assert param not in inspect.signature(func).parameters

    access.record_safety_response(
        admin, "Restricted access", group_id=ids["group_id"]
    )
    access.update_incident_group(
        admin, ids["group_id"], "In Progress", "Critical", "Electrical"
    )
    access.review_duplicate_candidate(admin, ids["candidate_id"], "Rejected")

    history = access.get_safety_response_history(admin, group_id=ids["group_id"])
    assert history[0]["acknowledged_by"] == "admin@campus.ac.in"

    activity = access.get_incident_activity(admin, ids["group_id"])
    assert activity and all(
        event["changed_by"] == "admin@campus.ac.in" for event in activity
    )

    reviews = access.list_duplicate_review_history(admin)
    assert reviews[0]["reviewed_by"] == "admin@campus.ac.in"

    # Admin identity never reaches the student-facing timeline.
    student_view = access.get_report_tracking(users["alice"], ids["safety"])
    assert "admin@campus.ac.in" not in str(student_view)


def test_parameterized_queries_resist_injection_in_email(fresh_db):
    hostile = "x'); DROP TABLE users; --@campus.ac.in"

    with pytest.raises(auth.RegistrationError):
        auth.register_student("Hostile", hostile, PASSWORD)
    assert auth.authenticate(hostile, PASSWORD) is None

    with db.get_connection() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'users'"
        ).fetchone() is not None


def test_existing_db_functions_still_work_without_actor(fresh_db):
    """The internal data layer keeps its original behaviour."""
    issue_id = db.create_issue("Tap", "Leaking", "Hostel A")
    db.update_issue_status(issue_id, "Assigned", "Plumbing")

    assert db.get_issue(issue_id)["status"] == "Assigned"
    assert db.get_issue(issue_id)["submitted_by"] is None

    with pytest.raises(sqlite3.IntegrityError):
        db.create_issue("Tap", "Leaking", "Hostel A", submitted_by=12345)


def test_duplicate_detection_still_compares_against_all_reports(
    users, monkeypatch,
):
    """Detection runs server-side over every report (other students' and
    legacy unowned ones), even though students never see those reports."""
    import numpy as np
    import modules.duplicate_detector as detector

    def fake_embedding(text):
        text = text.lower()
        return np.array(
            [float("leak" in text), float("chair" in text), 1.0],
            dtype=np.float32,
        )

    monkeypatch.setattr(detector, "get_embedding", fake_embedding)

    bob_leak = submit(users["bob"], "Pipe leak", "Hostel A")
    legacy_leak = db.create_issue("Old leak", "leak description", "Hostel A")
    submit(users["bob"], "Broken chair", "Hostel A")
    submit(users["bob"], "Pipe leak", "Library")  # different location

    pool = access.list_reports_for_duplicate_check(users["alice"])
    assert {r["issue_id"] for r in pool} >= {bob_leak, legacy_leak}

    matches = detector.find_similar_issues(
        "Tap leak", "Water leak", pool, location="Hostel A", threshold=0.9,
    )

    assert {m["issue_id"] for m in matches} == {bob_leak, legacy_leak}



# ----------------------------------------------------------------------
# Individual report changes record the administrator
# ----------------------------------------------------------------------

def test_individual_status_change_records_admin_identity(users):
    issue_id = submit(users["alice"], "Flickering light", "Library")

    access.update_issue_status(users["admin"], issue_id, "Assigned", "Electrical")
    access.update_issue_status(users["admin"], issue_id, "Resolved")

    history = access.get_issue_activity(users["admin"], issue_id)
    assert [(e["event_type"], e["old_value"], e["new_value"], e["changed_by"])
            for e in reversed(history)] == [
        ("Status", "Submitted", "Assigned", "admin@campus.ac.in"),
        ("Assigned team", None, "Electrical", "admin@campus.ac.in"),
        ("Status", "Assigned", "Resolved", "admin@campus.ac.in"),
    ]

    assert "changed_by" not in inspect.signature(
        access.update_issue_status
    ).parameters

    # Students cannot read the log, and their tracking view never shows it.
    with pytest.raises(access.AuthorizationError):
        access.get_issue_activity(users["alice"], issue_id)
    assert "admin@campus.ac.in" not in str(
        access.get_report_tracking(users["alice"], issue_id)
    )


def test_unchanged_fields_are_not_logged(users):
    issue_id = submit(users["alice"])
    access.update_issue_status(users["admin"], issue_id, "Assigned", "Civil")
    access.update_issue_status(users["admin"], issue_id, "Assigned")  # no-op
    access.update_issue_status(users["admin"], issue_id, "Assigned", "Civil")

    assert len(access.get_issue_activity(users["admin"], issue_id)) == 2


def test_individual_status_behaviour_is_unchanged(users):
    """Same transitions as before, compared field by field with a report
    updated through the original call signature (no identity)."""
    with_identity = submit(users["alice"])
    without_identity = db.create_issue("Tap", "Leak", "Hostel A")

    steps = [
        ("In Progress", "Plumbing"),
        ("Resolved", None),
        ("Resolved", "Civil"),   # stays resolved; assignment updates
        ("Under Review", None),  # reopened: resolved_at cleared
    ]
    for status, team in steps:
        access.update_issue_status(users["admin"], with_identity, status, team)
        db.update_issue_status(without_identity, status, team)

        a, b = db.get_issue(with_identity), db.get_issue(without_identity)
        assert (a["status"], a["assigned_to"], a["resolved_at"] is None) == (
            b["status"], b["assigned_to"], b["resolved_at"] is None
        )

    final = db.get_issue(with_identity)
    assert final["status"] == "Under Review"
    assert final["assigned_to"] == "Civil"
    assert final["resolved_at"] is None

    # Original-signature calls still log, with no identity attached.
    assert {e["changed_by"] for e in db.get_issue_activity(without_identity)} == {None}

    with pytest.raises(ValueError):
        access.update_issue_status(users["admin"], with_identity, "Bogus")
    with pytest.raises(ValueError):
        access.update_issue_status(users["admin"], 999_999, "Resolved")
