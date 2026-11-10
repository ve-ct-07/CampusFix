from pathlib import Path

import database.db as db
import pages.student_portal as student_portal

from streamlit.testing.v1 import AppTest


def test_student_can_submit_report_end_to_end(monkeypatch, tmp_path):
    # Use an isolated database. Never touch campusfix.db.
    test_db = tmp_path / "campusfix_e2e.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(
        db,
        "SCHEMA_PATH",
        Path(__file__).parents[1] / "database" / "schema.sql",
    )

    # Mock local AI classification.
    prediction = {
        "category": "Plumbing",
        "severity": "Medium",
        "secondary_hazards": [],
        "safety_risk": False,
        "safety_risk_reason": "No immediate safety hazard identified.",
        "summary": "A leaking tap needs maintenance.",
        "reason": "Water leakage detected.",
        "method": "test_mock",
        "confidence": None,
    }

    monkeypatch.setattr(
        student_portal,
        "classify_issue",
        lambda description, image_path=None: prediction.copy(),
    )

    # Mock duplicate detection: no similar reports found.
    monkeypatch.setattr(
        student_portal,
        "find_similar_issues",
        lambda *args, **kwargs: [],
    )

    # Start the actual Streamlit application.
    app_path = Path(__file__).parents[1] / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.run()

    assert len(at.exception) == 0

    # Select the Student portal.
    at.radio[0].set_value("Student portal")

    # Fill in the actual student form.
    at.text_input[0].set_value("Leaking tap near hostel washroom")
    at.text_area[0].set_value(
        "Water is continuously leaking from the tap."
    )
    at.text_input[1].set_value("Hostel A, Ground Floor")

    # Submit the form.
    at.button[0].click().run()

    # The application should not crash.
    assert len(at.exception) == 0, [
        exception.message for exception in at.exception
    ]

    # Verify the success message.
    success_messages = [item.value for item in at.success]
    assert any(
        "CF-0001" in message
        for message in success_messages
    )

    # Verify the report was actually stored.
    issue = db.get_issue(1)

    assert issue is not None
    assert issue["title"] == "Leaking tap near hostel washroom"
    assert issue["description"] == (
        "Water is continuously leaking from the tap."
    )
    assert issue["location"] == "Hostel A, Ground Floor"

    # Verify AI assessment persistence.
    assert issue["category"] == "Plumbing"
    assert issue["severity"] == "Medium"
    assert issue["ai_method"] == "test_mock"
    assert issue["safety_risk"] == 0

    # No duplicate candidates should be created.
    with db.get_connection() as conn:
        candidate_count = conn.execute(
            "SELECT COUNT(*) FROM duplicate_review_candidates"
        ).fetchone()[0]

        issue_count = conn.execute(
            "SELECT COUNT(*) FROM issues"
        ).fetchone()[0]

    assert candidate_count == 0
    assert issue_count == 1


def test_duplicate_suggestion_requires_admin_confirmation(
    monkeypatch, tmp_path
):
    # Isolated database.
    test_db = tmp_path / "campusfix_duplicate_e2e.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(
        db,
        "SCHEMA_PATH",
        Path(__file__).parents[1] / "database" / "schema.sql",
    )

    db.initialize_database()

    # Seed an existing report.
    existing_id = db.create_issue(
        title="Water leakage in Hostel B",
        description="A tap is leaking continuously near the washroom.",
        location="Hostel B, First Floor",
        category="Plumbing",
        severity="Medium",
        priority="Medium",
    )

    # Mock classification of the new report.
    prediction = {
        "category": "Plumbing",
        "severity": "Medium",
        "secondary_hazards": [],
        "safety_risk": False,
        "safety_risk_reason": "No immediate safety hazard identified.",
        "summary": "Water leakage near the washroom.",
        "reason": "Plumbing issue detected.",
        "method": "test_mock",
        "confidence": None,
    }

    monkeypatch.setattr(
        student_portal,
        "classify_issue",
        lambda description, image_path=None: prediction.copy(),
    )

    # Simulate a semantic match from the duplicate detector.
    monkeypatch.setattr(
        student_portal,
        "find_similar_issues",
        lambda *args, **kwargs: [
            {
                "issue_id": existing_id,
                "title": "Water leakage in Hostel B",
                "location": "Hostel B, First Floor",
                "status": "Submitted",
                "similarity": 0.94,
            }
        ],
    )

    # Open the actual application.
    app_path = Path(__file__).parents[1] / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.run()

    assert len(at.exception) == 0

    # Submit the new report through the student portal.
    at.radio[0].set_value("Student portal")
    at.run()

    at.text_input[0].set_value("Tap leaking near Hostel B washroom")
    at.text_area[0].set_value(
        "Water is leaking continuously from the tap."
    )
    at.text_input[1].set_value("Hostel B, First Floor")

    at.button[0].click().run()

    assert len(at.exception) == 0, [
        exception.message for exception in at.exception
    ]

    # The new report must exist.
    issues = db.list_issues()
    assert len(issues) == 2

    new_issue = next(
        issue for issue in issues
        if issue["issue_id"] != existing_id
    )

    # The duplicate suggestion must be persisted.
    pending = db.list_pending_duplicate_reviews()

    assert len(pending) == 1
    assert pending[0]["new_issue_id"] == new_issue["issue_id"]
    assert pending[0]["matched_issue_id"] == existing_id
    assert abs(pending[0]["similarity"] - 0.94) < 0.0001

    # Reports must remain separate until administrator confirmation.
    assert db.get_issue_group(existing_id) is None
    assert db.get_issue_group(new_issue["issue_id"]) is None

    with db.get_connection() as conn:
        group_count = conn.execute(
            "SELECT COUNT(*) FROM issue_groups"
        ).fetchone()[0]

    assert group_count == 0


def test_admin_confirms_duplicate_through_dashboard(
    monkeypatch, tmp_path
):
    # Use an isolated database. Never touch campusfix.db.
    test_db = tmp_path / "campusfix_admin_e2e.db"

    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(
        db,
        "SCHEMA_PATH",
        Path(__file__).parents[1] / "database" / "schema.sql",
    )

    db.initialize_database()

    # Create two existing reports that could match the new report.
    first_id = db.create_issue(
        title="Water leakage in Hostel C",
        description="Water leaking continuously from a washroom tap.",
        location="Hostel C, Ground Floor",
        category="Plumbing",
        severity="Medium",
        priority="Medium",
    )

    second_id = db.create_issue(
        title="Broken tap in Hostel C",
        description="Tap near the washroom is leaking water.",
        location="Hostel C, Ground Floor",
        category="Plumbing",
        severity="Medium",
        priority="Medium",
    )

    # Mock the local AI classifier.
    prediction = {
        "category": "Plumbing",
        "severity": "Medium",
        "secondary_hazards": [],
        "safety_risk": False,
        "safety_risk_reason": "No immediate safety hazard identified.",
        "summary": "Water leakage near the washroom.",
        "reason": "Plumbing issue detected.",
        "method": "test_mock",
        "confidence": None,
    }

    monkeypatch.setattr(
        student_portal,
        "classify_issue",
        lambda description, image_path=None: prediction.copy(),
    )

    # Simulate two possible duplicate matches.
    monkeypatch.setattr(
        student_portal,
        "find_similar_issues",
        lambda *args, **kwargs: [
            {
                "issue_id": first_id,
                "title": "Water leakage in Hostel C",
                "location": "Hostel C, Ground Floor",
                "status": "Submitted",
                "similarity": 0.96,
            },
            {
                "issue_id": second_id,
                "title": "Broken tap in Hostel C",
                "location": "Hostel C, Ground Floor",
                "status": "Submitted",
                "similarity": 0.91,
            },
        ],
    )

    app_path = Path(__file__).parents[1] / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=30)
    at.run()

    assert len(at.exception) == 0

    # Submit a new report through the actual Student Portal.
    at.radio[0].set_value("Student portal")
    at.run()

    at.text_input[0].set_value(
        "Water leaking near Hostel C washroom"
    )
    at.text_area[0].set_value(
        "Water is continuously leaking from the tap."
    )
    at.text_input[1].set_value("Hostel C, Ground Floor")

    at.button[0].click().run()

    assert len(at.exception) == 0, [
        exception.message for exception in at.exception
    ]

    # Identify the newly created report.
    issues = db.list_issues()
    assert len(issues) == 3

    new_issue = next(
        issue for issue in issues
        if issue["issue_id"] not in (first_id, second_id)
    )
    new_id = new_issue["issue_id"]

    # Both suggestions must exist and remain pending.
    pending = db.list_pending_duplicate_reviews()

    assert len(pending) == 2
    assert {item["matched_issue_id"] for item in pending} == {
        first_id,
        second_id,
    }

    assert db.get_issue_group(new_id) is None
    assert db.get_issue_group(first_id) is None
    assert db.get_issue_group(second_id) is None

    # Open the actual Admin Dashboard.
    at.radio[0].set_value("Admin dashboard")
    at.run()

    assert len(at.exception) == 0, [
        exception.message for exception in at.exception
    ]

    # Confirm the suggestion matching the first existing report.
    selected = next(
        item for item in pending
        if item["matched_issue_id"] == first_id
    )
    candidate_id = selected["candidate_id"]

    admin_input = next(
        widget for widget in at.text_input
        if widget.key == f"duplicate_admin_{candidate_id}"
    )

    decision_input = next(
        widget for widget in at.selectbox
        if widget.key == f"duplicate_decision_{candidate_id}"
    )

    admin_input.set_value("Test Administrator")
    decision_input.set_value("Confirm duplicate")

    submit_button = next(
        widget for widget in at.button
        if widget.key
        == (
            f"FormSubmitter:duplicate_review_{candidate_id}"
            "-Save review decision"
        )
    )

    submit_button.click().run()

    # The dashboard must complete without an exception.
    assert len(at.exception) == 0, [
        exception.message for exception in at.exception
    ]

    # Verify that the selected reports now belong to one incident.
    first_group = db.get_issue_group(first_id)
    new_group = db.get_issue_group(new_id)

    assert first_group is not None
    assert new_group is not None
    assert first_group["group_id"] == new_group["group_id"]

    # The alternative report must remain outside this incident.
    assert db.get_issue_group(second_id) is None

    # Verify the candidate decisions in the database.
    with db.get_connection() as conn:
        decisions = conn.execute(
            """
            SELECT matched_issue_id, decision, reviewed_by
            FROM duplicate_review_candidates
            WHERE new_issue_id = ?
            """,
            (new_id,),
        ).fetchall()

    decision_map = {
        row["matched_issue_id"]: (
            row["decision"],
            row["reviewed_by"],
        )
        for row in decisions
    }

    assert decision_map[first_id] == (
        "Confirmed",
        "Test Administrator",
    )

    assert decision_map[second_id] == (
        "Rejected",
        "Test Administrator",
    )

    # Verify that the incident contains exactly two reports.
    members = db.list_group_members(first_group["group_id"])

    member_ids = {
        member["issue_id"] for member in members
    }

    assert member_ids == {first_id, new_id}

    # Verify the student-facing tracking reference.
    tracking = db.get_student_tracking_data(new_id)

    assert tracking is not None
    assert tracking["incident_ref"] == "GF-0001"
