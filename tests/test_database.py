import tempfile
from pathlib import Path

import database.db as db


def test_create_read_update_issue(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(db, "SCHEMA_PATH", Path(__file__).parents[1] / "database" / "schema.sql")
        db.initialize_database()

        issue_id = db.create_issue(
            "Leaking tap", "Water is leaking continuously", "Hostel A"
        )
        issue = db.get_issue(issue_id)
        assert issue["title"] == "Leaking tap"
        assert issue["status"] == "Submitted"

        db.update_issue_status(issue_id, "In Progress", "Plumbing team")
        updated = db.get_issue(issue_id)
        assert updated["status"] == "In Progress"
        assert updated["assigned_to"] == "Plumbing team"



def test_duplicate_confirmation_preserves_incident_state(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        matched_id = db.create_issue(
            "Leaking tap",
            "Water leaking near the washroom",
            "Hostel A",
            priority="High",
        )

        new_id = db.create_issue(
            "Water leakage",
            "Water leaking continuously",
            "Hostel A",
        )

        candidate_id = None

        db.save_duplicate_candidates(
            new_id,
            [{"issue_id": matched_id, "similarity": 0.94}],
        )

        candidate = db.list_pending_duplicate_reviews()[0]
        candidate_id = candidate["candidate_id"]

        group_id = db.confirm_duplicate_candidate(
            candidate_id,
            "Test Administrator",
        )

        group = db.get_incident_group(group_id)
        members = db.list_group_members(group_id)

        assert group["status"] == "Submitted"
        assert group["priority"] == "High"
        assert len(members) == 2

        assert all(issue["priority"] == "High" for issue in members)


def test_resolved_incident_requires_explicit_reopening(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        matched_id = db.create_issue(
            "Leaking tap",
            "Water leaking near the washroom",
            "Hostel A",
        )

        new_id = db.create_issue(
            "Water leakage",
            "Water leaking continuously",
            "Hostel A",
        )

        db.update_issue_status(matched_id, "Resolved")

        db.save_duplicate_candidates(
            new_id,
            [{"issue_id": matched_id, "similarity": 0.94}],
        )

        candidate = db.list_pending_duplicate_reviews()[0]

        try:
            db.confirm_duplicate_candidate(
                candidate["candidate_id"],
                "Test Administrator",
            )
            assert False, "Resolved incident should require approval"
        except ValueError as error:
            assert "reopening approval" in str(error)

        group_id = db.confirm_duplicate_candidate(
            candidate["candidate_id"],
            "Test Administrator",
            reopen_resolved=True,
        )

        group = db.get_incident_group(group_id)

        assert group["status"] == "Under Review"
        assert len(db.list_group_members(group_id)) == 2


def test_safety_risk_mismatch_cannot_be_confirmed(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        safe_issue = db.create_issue(
            "Water leakage",
            "Water leaking near washroom",
            "Hostel A",
        )

        safety_issue = db.create_issue(
            "Water near electrical switchboard",
            "Water leaking beside electrical equipment",
            "Hostel A",
            priority="Critical",
            ai_assessment={
                "safety_risk": True,
                "safety_risk_reason": "Potential electrical shock hazard.",
            },
        )

        db.save_duplicate_candidates(
            safe_issue,
            [{"issue_id": safety_issue, "similarity": 0.95}],
        )

        candidate = db.list_pending_duplicate_reviews()[0]

        try:
            db.confirm_duplicate_candidate(
                candidate["candidate_id"],
                "Test Administrator",
            )
            assert False, "Safety mismatch should not be confirmed."
        except ValueError as error:
            assert "safety-risk report" in str(error)

        # The failed confirmation must leave the candidate pending.
        pending = db.list_pending_duplicate_reviews()
        assert len(pending) == 1


def test_confirmation_closes_alternative_candidates_and_records_history(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        matched_a = db.create_issue(
            "Leaking tap",
            "Water leaking near washroom",
            "Hostel A",
        )
        matched_b = db.create_issue(
            "Pipe leakage",
            "Pipe leaking near washroom",
            "Hostel A",
        )
        new_issue = db.create_issue(
            "Water leakage",
            "Continuous leakage near washroom",
            "Hostel A",
        )

        db.save_duplicate_candidates(
            new_issue,
            [
                {"issue_id": matched_a, "similarity": 0.96},
                {"issue_id": matched_b, "similarity": 0.91},
            ],
        )

        pending = db.list_pending_duplicate_reviews()
        assert len(pending) == 2

        selected = next(
            item for item in pending
            if item["matched_issue_id"] == matched_a
        )

        group_id = db.confirm_duplicate_candidate(
            selected["candidate_id"],
            "Lifecycle Tester",
        )

        # Confirmation must close all alternative suggestions.
        assert db.list_pending_duplicate_reviews() == []

        with db.get_connection() as conn:
            decisions = conn.execute(
                """
                SELECT matched_issue_id, decision, reviewed_by, reviewed_at
                FROM duplicate_review_candidates
                WHERE new_issue_id = ?
                """,
                (new_issue,),
            ).fetchall()

        assert len(decisions) == 2
        assert all(row["reviewed_by"] == "Lifecycle Tester" for row in decisions)
        assert all(row["reviewed_at"] for row in decisions)

        decision_by_match = {
            row["matched_issue_id"]: row["decision"]
            for row in decisions
        }

        assert decision_by_match[matched_a] == "Confirmed"
        assert decision_by_match[matched_b] == "Rejected"

        history = db.list_duplicate_review_history()
        assert len(history) == 2
        assert any(
            row["decision"] == "Confirmed"
            and row["group_id"] == group_id
            for row in history
        )


def test_rejection_does_not_create_incident_group(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        matched_id = db.create_issue(
            "Leaking tap",
            "Water leaking near washroom",
            "Hostel A",
        )
        new_id = db.create_issue(
            "Broken chair",
            "A chair is damaged",
            "Hostel A",
        )

        db.save_duplicate_candidates(
            new_id,
            [{"issue_id": matched_id, "similarity": 0.84}],
        )

        candidate = db.list_pending_duplicate_reviews()[0]

        db.review_duplicate_candidate(
            candidate["candidate_id"],
            "Rejected",
            "Lifecycle Tester",
        )

        assert db.list_pending_duplicate_reviews() == []

        with db.get_connection() as conn:
            group_count = conn.execute(
                "SELECT COUNT(*) FROM issue_groups"
            ).fetchone()[0]

            history = conn.execute(
                """
                SELECT decision, reviewed_by, reviewed_at
                FROM duplicate_review_candidates
                WHERE candidate_id = ?
                """,
                (candidate["candidate_id"],),
            ).fetchone()

        assert group_count == 0
        assert history["decision"] == "Rejected"
        assert history["reviewed_by"] == "Lifecycle Tester"
        assert history["reviewed_at"]


def test_new_incident_closes_all_pending_candidates(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        matched_a = db.create_issue(
            "Leaking tap",
            "Water leaking near washroom",
            "Hostel A",
        )
        matched_b = db.create_issue(
            "Pipe leakage",
            "Pipe leaking near washroom",
            "Hostel A",
        )
        new_id = db.create_issue(
            "Damaged furniture",
            "A table is broken",
            "Hostel A",
        )

        db.save_duplicate_candidates(
            new_id,
            [
                {"issue_id": matched_a, "similarity": 0.86},
                {"issue_id": matched_b, "similarity": 0.83},
            ],
        )

        pending = db.list_pending_duplicate_reviews()
        assert len(pending) == 2

        db.review_duplicate_candidate(
            pending[0]["candidate_id"],
            "New Incident",
            "Lifecycle Tester",
        )

        assert db.list_pending_duplicate_reviews() == []

        with db.get_connection() as conn:
            decisions = conn.execute(
                """
                SELECT decision, reviewed_by, reviewed_at
                FROM duplicate_review_candidates
                WHERE new_issue_id = ?
                """,
                (new_id,),
            ).fetchall()

            group_count = conn.execute(
                "SELECT COUNT(*) FROM issue_groups"
            ).fetchone()[0]

        assert len(decisions) == 2
        assert all(row["decision"] == "New Incident" for row in decisions)
        assert all(row["reviewed_by"] == "Lifecycle Tester" for row in decisions)
        assert all(row["reviewed_at"] for row in decisions)
        assert group_count == 0


def test_group_safety_history_includes_member_report_responses(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        safety_issue = db.create_issue(
            "Water near electrical switchboard",
            "Water leaking beside electrical equipment",
            "Hostel A",
            priority="Critical",
            ai_assessment={
                "safety_risk": True,
                "safety_risk_reason": "Potential electrical shock hazard.",
            },
        )

        other_issue = db.create_issue(
            "Water leakage",
            "Water leaking near washroom",
            "Hostel A",
        )

        group_id = db.attach_duplicate_report(
            other_issue,
            safety_issue,
        )

        db.record_safety_response(
            acknowledged_by="Safety Administrator",
            action_taken="Restricted access to affected area",
            issue_id=safety_issue,
            notes="PRIVATE_INTERNAL_NOTE",
        )

        db.record_safety_response(
            acknowledged_by="Safety Administrator",
            action_taken="Requested urgent inspection",
            group_id=group_id,
        )

        history = db.get_safety_response_history(group_id=group_id)

        assert len(history) == 2
        assert {row["issue_id"] for row in history} == {safety_issue, None}
        assert {row["group_id"] for row in history} == {group_id, None}


def test_student_timeline_shows_member_safety_response_without_private_data(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        safety_issue = db.create_issue(
            "Water near electrical switchboard",
            "Water leaking beside electrical equipment",
            "Hostel A",
            priority="Critical",
            ai_assessment={
                "safety_risk": True,
                "safety_risk_reason": "Potential electrical shock hazard.",
            },
        )

        other_issue = db.create_issue(
            "Water leakage",
            "Water leaking near washroom",
            "Hostel A",
        )

        db.attach_duplicate_report(other_issue, safety_issue)

        db.record_safety_response(
            acknowledged_by="Private Administrator",
            action_taken="Contacted campus safety/security",
            issue_id=safety_issue,
            notified_party="Electrical Maintenance Team",
            notes="PRIVATE_INTERNAL_NOTE",
        )

        data = db.get_student_tracking_data(other_issue)
        timeline_text = str(data["timeline"])

        safety_events = [
            event for event in data["timeline"]
            if event["event"] == "Safety response recorded"
        ]

        assert len(safety_events) == 1
        assert "PRIVATE_INTERNAL_NOTE" not in timeline_text
        assert "Private Administrator" not in timeline_text
        assert "Electrical Maintenance Team" not in timeline_text


def test_individual_safety_history_remains_available_after_grouping(
    monkeypatch,
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        monkeypatch.setattr(
            db,
            "SCHEMA_PATH",
            Path(__file__).parents[1] / "database" / "schema.sql",
        )
        db.initialize_database()

        safety_issue = db.create_issue(
            "Water near electrical switchboard",
            "Water leaking beside electrical equipment",
            "Hostel A",
            priority="Critical",
            ai_assessment={
                "safety_risk": True,
                "safety_risk_reason": "Potential electrical shock hazard.",
            },
        )

        other_issue = db.create_issue(
            "Water leakage",
            "Water leaking near washroom",
            "Hostel A",
        )

        db.attach_duplicate_report(other_issue, safety_issue)

        response_id = db.record_safety_response(
            acknowledged_by="Safety Administrator",
            action_taken="Requested urgent inspection",
            issue_id=safety_issue,
        )

        history = db.get_safety_response_history(issue_id=safety_issue)

        assert len(history) == 1
        assert history[0]["response_id"] == response_id
