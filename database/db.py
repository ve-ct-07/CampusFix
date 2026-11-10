from pathlib import Path
import sqlite3
from contextlib import contextmanager
import json

from database.migrations import run_migrations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "campusfix.db"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def initialize_database():
    """Create all CampusFix tables and apply pending schema migrations.

    The base schema only uses CREATE ... IF NOT EXISTS, so it never alters
    existing data. Additive migrations (users table, issues.submitted_by,
    incident_activity.changed_by) are then applied idempotently; a backup is
    written first if the database already holds reports.
    """
    with get_connection() as conn:
        conn.executescript(
            SCHEMA_PATH.read_text(encoding="utf-8")
        )

    run_migrations(DB_PATH)


def create_issue(
    title,
    description,
    location,
    category="Unclassified",
    severity="Medium",
    priority="Medium",
    image_path=None,
    ai_assessment=None,
    submitted_by=None,
):
    """Create a report and preserve its original AI triage assessment.

    ``submitted_by`` is the owning user's ID. It is optional so that
    existing callers keep working; the authenticated UI always sets it via
    ``database.access.submit_report``.
    """

    assessment = ai_assessment or {}

    secondary_hazards = assessment.get("secondary_hazards", [])
    if not isinstance(secondary_hazards, list):
        secondary_hazards = []

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO issues (
                title, description, location, category, severity,
                priority, image_path, ai_category, ai_severity,
                secondary_hazards, ai_summary, ai_reason,
                safety_risk, safety_risk_reason, ai_method,
                submitted_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                location,
                category,
                severity,
                priority,
                image_path,
                assessment.get("category", category),
                assessment.get("severity", severity),
                json.dumps(secondary_hazards),
                assessment.get("summary"),
                assessment.get("reason"),
                int(bool(assessment.get("safety_risk", False))),
                assessment.get("safety_risk_reason"),
                assessment.get("method", "unknown"),
                submitted_by,
            ),
        )

        return cursor.lastrowid


def list_issues():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM issues ORDER BY created_at DESC, issue_id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_issue(issue_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM issues WHERE issue_id = ?", (issue_id,)
        ).fetchone()
        return dict(row) if row else None


def update_issue_status(issue_id, status, assigned_to=None, changed_by=None):
    """Update one report's status/assignment and log what changed.

    Status and assignment behaviour is unchanged. Each field that actually
    changes is recorded in ``issue_activity`` in the same transaction, with
    ``changed_by`` (the administrator's identity; optional so existing
    callers keep working).
    """
    allowed = {
        "Submitted",
        "Under Review",
        "Assigned",
        "In Progress",
        "Resolved",
        "Rejected",
    }

    if status not in allowed:
        raise ValueError(f"Unsupported status: {status}")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, assigned_to FROM issues WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()

        if not row:
            raise ValueError(f"Issue {issue_id} does not exist")

        previous_status = row["status"]
        previous_assignee = row["assigned_to"]

        if status == "Resolved" and previous_status != "Resolved":
            conn.execute(
                """
                UPDATE issues
                SET status = ?,
                    assigned_to = COALESCE(?, assigned_to),
                    resolved_at = CURRENT_TIMESTAMP
                WHERE issue_id = ?
                """,
                (status, assigned_to, issue_id),
            )

        elif status != "Resolved":
            conn.execute(
                """
                UPDATE issues
                SET status = ?,
                    assigned_to = COALESCE(?, assigned_to),
                    resolved_at = NULL
                WHERE issue_id = ?
                """,
                (status, assigned_to, issue_id),
            )

        else:
            conn.execute(
                """
                UPDATE issues
                SET assigned_to = COALESCE(?, assigned_to)
                WHERE issue_id = ?
                """,
                (assigned_to, issue_id),
            )

        # Record only the fields that actually changed.
        new_assignee = (
            assigned_to if assigned_to is not None else previous_assignee
        )
        changes = (
            ("Status", previous_status, status),
            ("Assigned team", previous_assignee, new_assignee),
        )
        for event_type, old_value, new_value in changes:
            if old_value != new_value:
                conn.execute(
                    """
                    INSERT INTO issue_activity
                        (issue_id, event_type, old_value, new_value,
                         changed_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (issue_id, event_type, old_value, new_value, changed_by),
                )


def get_issue_activity(issue_id):
    """Return the administrative change history of one report, newest first."""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT activity_id, issue_id, event_type,
                   old_value, new_value, changed_by, created_at
            FROM issue_activity
            WHERE issue_id = ?
            ORDER BY created_at DESC, activity_id DESC
            """,
            (issue_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def save_issue_embedding(issue_id, model_name, embedding):
    payload = json.dumps([float(value) for value in embedding])
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO issue_embeddings (issue_id, model_name, embedding_json)
               VALUES (?, ?, ?)
               ON CONFLICT(issue_id) DO UPDATE SET
                   model_name = excluded.model_name,
                   embedding_json = excluded.embedding_json,
                   created_at = CURRENT_TIMESTAMP""",
            (issue_id, model_name, payload),
        )


def get_issue_embedding(issue_id, model_name):
    with get_connection() as conn:
        row = conn.execute(
            """SELECT embedding_json FROM issue_embeddings
               WHERE issue_id = ? AND model_name = ?""",
            (issue_id, model_name),
        ).fetchone()
        return json.loads(row["embedding_json"]) if row else None


def attach_duplicate_report(new_issue_id, matched_issue_id):
    """Link two existing reports without deleting or merging either report."""
    if new_issue_id == matched_issue_id:
        raise ValueError("A report cannot be grouped with itself")

    with get_connection() as conn:
        # If the matched report already belongs to a group, reuse that group.
        row = conn.execute(
            "SELECT group_id FROM issue_group_members WHERE issue_id = ?",
            (matched_issue_id,),
        ).fetchone()

        if row:
            group_id = row["group_id"]
        else:
            cursor = conn.execute(
                "INSERT INTO issue_groups (primary_issue_id, duplicate_count) VALUES (?, 0)",
                (matched_issue_id,),
            )
            group_id = cursor.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO issue_group_members (group_id, issue_id) VALUES (?, ?)",
                (group_id, matched_issue_id),
            )

        # Do not silently move a report out of a different group.
        existing = conn.execute(
            "SELECT group_id FROM issue_group_members WHERE issue_id = ?",
            (new_issue_id,),
        ).fetchone()
        if existing and existing["group_id"] != group_id:
            raise ValueError("The new report already belongs to another incident group")

        conn.execute(
            "INSERT OR IGNORE INTO issue_group_members (group_id, issue_id) VALUES (?, ?)",
            (group_id, new_issue_id),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM issue_group_members WHERE group_id = ?",
            (group_id,),
        ).fetchone()["n"]
        conn.execute(
            "UPDATE issue_groups SET duplicate_count = ? WHERE group_id = ?",
            (max(0, count - 1), group_id),
        )
        return group_id


def get_issue_group(issue_id):
    with get_connection() as conn:
        row = conn.execute(
            """SELECT g.group_id, g.primary_issue_id, g.duplicate_count
               FROM issue_groups g
               JOIN issue_group_members m ON m.group_id = g.group_id
               WHERE m.issue_id = ?""",
            (issue_id,),
        ).fetchone()
        return dict(row) if row else None


def list_incident_groups():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                g.group_id,
                g.primary_issue_id,
                g.duplicate_count,
                i.title AS primary_title,
                i.location,
                g.status,
                g.priority,
                g.assigned_team
            FROM issue_groups g
            JOIN issues i ON i.issue_id = g.primary_issue_id
            ORDER BY g.group_id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def list_group_members(group_id):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT i.* FROM issues i
               JOIN issue_group_members m ON m.issue_id = i.issue_id
               WHERE m.group_id = ?
               ORDER BY CASE WHEN i.issue_id = (
                   SELECT primary_issue_id FROM issue_groups WHERE group_id = ?
               ) THEN 0 ELSE 1 END, i.issue_id""",
            (group_id, group_id),
        ).fetchall()
        return [dict(row) for row in rows]


def update_incident_group(
    group_id, status, priority, assigned_team, changed_by=None,
):
    """Update an incident, synchronize reports, and record actual changes.

    ``changed_by`` (optional) records the administrator responsible for the
    change in the activity log. It is never shown to students.
    """

    allowed_statuses = {
        "Submitted", "Under Review", "Assigned",
        "In Progress", "Resolved", "Rejected",
    }
    allowed_priorities = {"Low", "Medium", "High", "Critical"}

    if status not in allowed_statuses:
        raise ValueError(f"Unsupported incident status: {status}")

    if priority not in allowed_priorities:
        raise ValueError(f"Unsupported incident priority: {priority}")

    if assigned_team is not None:
        assigned_team = assigned_team.strip() or None

    with get_connection() as conn:
        group = conn.execute(
            """
            SELECT group_id, status, priority, assigned_team
            FROM issue_groups
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()

        if not group:
            raise ValueError(f"Incident group {group_id} does not exist")

        old_values = {
            "Status": group["status"],
            "Priority": group["priority"],
            "Assigned team": group["assigned_team"],
        }

        new_values = {
            "Status": status,
            "Priority": priority,
            "Assigned team": assigned_team,
        }

        # Update the incident and maintain its resolution timestamp.
        if status == "Resolved" and group["status"] != "Resolved":
            conn.execute(
                """
                UPDATE issue_groups
                SET status = ?, priority = ?, assigned_team = ?,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE group_id = ?
                """,
                (status, priority, assigned_team, group_id),
            )

        elif status != "Resolved":
            conn.execute(
                """
                UPDATE issue_groups
                SET status = ?, priority = ?, assigned_team = ?,
                    resolved_at = NULL
                WHERE group_id = ?
                """,
                (status, priority, assigned_team, group_id),
            )

        else:
            conn.execute(
                """
                UPDATE issue_groups
                SET priority = ?, assigned_team = ?
                WHERE group_id = ?
                """,
                (priority, assigned_team, group_id),
            )

        # Synchronize every report belonging to this incident.
        conn.execute(
            """
            UPDATE issues
            SET status = ?, priority = ?, assigned_to = ?,
                resolved_at = CASE
                    WHEN ? = 'Resolved'
                         AND status != 'Resolved'
                    THEN CURRENT_TIMESTAMP
                    WHEN ? != 'Resolved'
                    THEN NULL
                    ELSE resolved_at
                END
            WHERE issue_id IN (
                SELECT issue_id
                FROM issue_group_members
                WHERE group_id = ?
            )
            """,
            (status, priority, assigned_team, status, status, group_id),
        )

        # Record only fields that actually changed.
        for event_type, old_value in old_values.items():
            new_value = new_values[event_type]

            if old_value != new_value:
                conn.execute(
                    """
                    INSERT INTO incident_activity
                        (group_id, event_type, old_value, new_value,
                         changed_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        event_type,
                        old_value,
                        new_value,
                        changed_by,
                    ),
                )


def get_incident_activity(group_id):
    """Return the activity history of one incident, newest first."""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT activity_id, group_id, event_type,
                   old_value, new_value, changed_by, created_at
            FROM incident_activity
            WHERE group_id = ?
            ORDER BY created_at DESC, activity_id DESC
            """,
            (group_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def get_incident_group(group_id):
    """Return the management details of one incident group."""

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                group_id,
                primary_issue_id,
                duplicate_count,
                status,
                priority,
                assigned_team
            FROM issue_groups
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()

        return dict(row) if row else None


def get_incident_analytics():
    """Calculate analytics using unique incidents, not raw submissions."""

    with get_connection() as conn:

        rows = conn.execute("""
            WITH incidents AS (

                -- Grouped reports count as one incident.
                SELECT
                    g.group_id AS incident_id,
                    i.category,
                    g.status,
                    g.priority,
                    g.assigned_team,
                    g.created_at,
                    g.resolved_at
                FROM issue_groups g
                JOIN issues i
                    ON i.issue_id = g.primary_issue_id

                UNION ALL

                -- Ungrouped reports each count as one incident.
                SELECT
                    -i.issue_id AS incident_id,
                    i.category,
                    i.status,
                    i.priority,
                    i.assigned_to AS assigned_team,
                    i.created_at,
                    i.resolved_at
                FROM issues i
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM issue_group_members m
                    WHERE m.issue_id = i.issue_id
                )
            )
            SELECT * FROM incidents
        """).fetchall()

        incidents = [dict(row) for row in rows]

        total = len(incidents)
        resolved = sum(
            incident["status"] == "Resolved"
            for incident in incidents
        )

        resolution_hours = []

        for incident in incidents:
            if incident["status"] == "Resolved" and incident["resolved_at"] and incident["created_at"]:
                duration = conn.execute(
                    """
                    SELECT
                        (julianday(?) - julianday(?)) * 24
                    """,
                    (
                        incident["resolved_at"],
                        incident["created_at"],
                    ),
                ).fetchone()[0]

                if duration is not None and duration >= 0:
                    resolution_hours.append(duration)

        average_resolution_hours = (
            sum(resolution_hours) / len(resolution_hours)
            if resolution_hours else None
        )

        def count_by(field):
            counts = {}

            for incident in incidents:
                value = incident[field] or "Unassigned"
                counts[value] = counts.get(value, 0) + 1

            return [
                {"name": name, "count": count}
                for name, count in sorted(
                    counts.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]

        team_workload = {}

        for incident in incidents:
            if incident["status"] != "Resolved":
                team = incident["assigned_team"] or "Unassigned"
                team_workload[team] = team_workload.get(team, 0) + 1

        return {
            "total_incidents": total,
            "open_incidents": total - resolved,
            "resolved_incidents": resolved,
            "resolution_rate": (
                resolved / total * 100 if total else 0
            ),
            "total_submissions": conn.execute(
                "SELECT COUNT(*) FROM issues"
            ).fetchone()[0],
            "duplicate_submissions": conn.execute(
                """
                SELECT COALESCE(SUM(duplicate_count), 0)
                FROM issue_groups
                """
            ).fetchone()[0],
            "average_resolution_hours": average_resolution_hours,
            "by_category": count_by("category"),
            "by_priority": count_by("priority"),
            "team_workload": [
                {"name": name, "count": count}
                for name, count in sorted(team_workload.items())
            ],
        }


def get_sla_incidents():
    """Return open incidents with SLA deadlines and current waiting times."""

    sla_targets = {
        "Critical": 2,
        "High": 8,
        "Medium": 24,
        "Low": 72,
    }

    with get_connection() as conn:
        rows = conn.execute("""
            WITH incidents AS (

                SELECT
                    'GF-' || printf('%04d', g.group_id) AS incident_ref,
                    i.title,
                    i.location,
                    g.status,
                    g.priority,
                    g.assigned_team,
                    g.created_at
                FROM issue_groups g
                JOIN issues i ON i.issue_id = g.primary_issue_id

                UNION ALL

                SELECT
                    'CF-' || printf('%04d', i.issue_id) AS incident_ref,
                    i.title,
                    i.location,
                    i.status,
                    i.priority,
                    i.assigned_to AS assigned_team,
                    i.created_at
                FROM issues i
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM issue_group_members m
                    WHERE m.issue_id = i.issue_id
                )
            )
            SELECT
                *,
                (julianday('now') - julianday(created_at)) * 24
                    AS age_hours
            FROM incidents
            WHERE status NOT IN ('Resolved', 'Rejected')
            ORDER BY age_hours DESC
        """).fetchall()

        result = []

        for row in rows:
            incident = dict(row)

            priority = incident["priority"] or "Medium"
            target = sla_targets.get(priority, 24)
            age = max(0, incident["age_hours"] or 0)

            incident["target_hours"] = target
            incident["remaining_hours"] = target - age
            incident["sla_breached"] = age > target
            incident["sla_status"] = (
                "Breached" if age > target else "Within SLA"
            )

            result.append(incident)

        return result


def get_student_tracking_data(issue_id):
    """Return report details and student-safe incident updates."""

    with get_connection() as conn:
        issue_row = conn.execute(
            "SELECT * FROM issues WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()

        if not issue_row:
            return None

        issue = dict(issue_row)

        group_row = conn.execute(
            """
            SELECT g.group_id, g.status, g.priority,
                   g.assigned_team, g.created_at, g.resolved_at
            FROM issue_groups g
            JOIN issue_group_members m ON m.group_id = g.group_id
            WHERE m.issue_id = ?
            """,
            (issue_id,),
        ).fetchone()

        if group_row:
            incident = dict(group_row)

            activity_rows = conn.execute(
                """
                SELECT event_type, old_value, new_value, created_at
                FROM incident_activity
                WHERE group_id = ?
                  AND event_type IN ('Status', 'Assigned team')
                ORDER BY created_at, activity_id
                """,
                (incident["group_id"],),
            ).fetchall()

            incident_ref = f"GF-{incident['group_id']:04d}"
            response_query = """
                SELECT created_at, response_id
                FROM safety_response_log
                WHERE group_id = ?
                   OR issue_id IN (
                       SELECT issue_id
                       FROM issue_group_members
                       WHERE group_id = ?
                   )
                ORDER BY created_at, response_id
            """
            response_parameters = (
                incident["group_id"],
                incident["group_id"],
            )

        else:
            incident = {
                "status": issue["status"],
                "priority": issue["priority"],
                "assigned_team": issue["assigned_to"],
                "created_at": issue["created_at"],
                "resolved_at": issue.get("resolved_at"),
            }

            activity_rows = []
            incident_ref = None
            response_query = """
                SELECT created_at, response_id
                FROM safety_response_log
                WHERE issue_id = ?
                ORDER BY created_at, response_id
            """
            response_parameters = (issue_id,)

        timeline = [{
            "event": "Report submitted",
            "details": "Your report was received by CampusFix.",
            "created_at": issue["created_at"],
        }]

        for event in activity_rows:
            if event["event_type"] == "Status":
                details = (
                    f"Status changed from {event['old_value']} "
                    f"to {event['new_value']}."
                )
                title = "Status updated"
            else:
                new_team = event["new_value"] or "Unassigned"
                details = (
                    f"Maintenance team assignment updated to {new_team}."
                )
                title = "Maintenance team updated"

            timeline.append({
                "event": title,
                "details": details,
                "created_at": event["created_at"],
            })

        # Display a generic public update for each recorded safety response.
        # Administrator identities, notified parties and internal notes
        # are deliberately excluded from student-facing tracking.
        safety_responses = conn.execute(
            response_query,
            response_parameters,
        ).fetchall()

        for response in safety_responses:
            timeline.append({
                "event": "Safety response recorded",
                "details": (
                    "The campus team has reviewed the safety alert "
                    "and recorded a follow-up response."
                ),
                "created_at": response["created_at"],
            })

        if incident["status"] == "Resolved" and incident.get("resolved_at"):
            timeline.append({
                "event": "Incident resolved",
                "details": "The maintenance incident has been marked resolved.",
                "created_at": incident["resolved_at"],
            })

        timeline.sort(
            key=lambda item: item["created_at"]
        )

        return {
            "issue": issue,
            "incident_ref": incident_ref,
            "status": incident["status"],
            "priority": incident["priority"],
            "assigned_team": incident["assigned_team"],
            "resolved_at": incident.get("resolved_at"),
            "timeline": timeline,
        }

def record_safety_response(
    acknowledged_by,
    action_taken,
    issue_id=None,
    group_id=None,
    notified_party=None,
    notes=None,
):
    """Record an administrator's response to a safety alert."""

    acknowledged_by = (acknowledged_by or "").strip()
    action_taken = (action_taken or "").strip()

    if not acknowledged_by:
        raise ValueError("Administrator name is required.")

    if not action_taken:
        raise ValueError("Action taken is required.")

    if (issue_id is None) == (group_id is None):
        raise ValueError(
            "Provide exactly one of issue_id or group_id."
        )

    notified_party = (notified_party or "").strip() or None
    notes = (notes or "").strip() or None

    with get_connection() as conn:

        if issue_id is not None:
            target = conn.execute(
                """
                SELECT issue_id
                FROM issues
                WHERE issue_id = ? AND safety_risk = 1
                """,
                (issue_id,),
            ).fetchone()

            if not target:
                raise ValueError(
                    "Report does not exist or has no recorded AI safety risk."
                )

        else:
            target = conn.execute(
                """
                SELECT g.group_id
                FROM issue_groups g
                JOIN issue_group_members m
                    ON m.group_id = g.group_id
                JOIN issues i
                    ON i.issue_id = m.issue_id
                WHERE g.group_id = ?
                GROUP BY g.group_id
                HAVING MAX(i.safety_risk) = 1
                """,
                (group_id,),
            ).fetchone()

            if not target:
                raise ValueError(
                    "Incident does not exist or has no recorded AI safety risk."
                )

        cursor = conn.execute(
            """
            INSERT INTO safety_response_log (
                issue_id,
                group_id,
                acknowledged_by,
                action_taken,
                notified_party,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id,
                group_id,
                acknowledged_by,
                action_taken,
                notified_party,
                notes,
            ),
        )

        return cursor.lastrowid


def get_safety_response_history(issue_id=None, group_id=None):
    """Return safety responses for a report or an entire incident."""

    if (issue_id is None) == (group_id is None):
        raise ValueError(
            "Provide exactly one of issue_id or group_id."
        )

    with get_connection() as conn:
        if issue_id is not None:
            rows = conn.execute(
                """
                SELECT *
                FROM safety_response_log
                WHERE issue_id = ?
                ORDER BY created_at DESC, response_id DESC
                """,
                (issue_id,),
            ).fetchall()

        else:
            rows = conn.execute(
                """
                SELECT DISTINCT sr.*
                FROM safety_response_log sr
                WHERE sr.group_id = ?
                   OR sr.issue_id IN (
                       SELECT issue_id
                       FROM issue_group_members
                       WHERE group_id = ?
                   )
                ORDER BY sr.created_at DESC, sr.response_id DESC
                """,
                (group_id, group_id),
            ).fetchall()

        return [dict(row) for row in rows]


def save_duplicate_candidates(new_issue_id, candidates):
    """Persist potential duplicate matches for administrator review."""

    with get_connection() as conn:
        issue = conn.execute(
            "SELECT issue_id FROM issues WHERE issue_id = ?",
            (new_issue_id,),
        ).fetchone()

        if not issue:
            raise ValueError(f"Issue {new_issue_id} does not exist")

        saved = 0

        for candidate in candidates:
            matched_issue_id = int(candidate["issue_id"])
            similarity = float(candidate["similarity"])

            if matched_issue_id == new_issue_id:
                continue

            if not -1.0 <= similarity <= 1.0:
                raise ValueError("Similarity must be between -1 and 1")

            matched = conn.execute(
                "SELECT issue_id FROM issues WHERE issue_id = ?",
                (matched_issue_id,),
            ).fetchone()

            if not matched:
                continue

            cursor = conn.execute(
                """
                INSERT INTO duplicate_review_candidates (
                    new_issue_id,
                    matched_issue_id,
                    similarity
                )
                VALUES (?, ?, ?)
                ON CONFLICT(new_issue_id, matched_issue_id)
                DO NOTHING
                """,
                (new_issue_id, matched_issue_id, similarity),
            )

            saved += cursor.rowcount

        return saved


def list_pending_duplicate_reviews():
    """Return pending candidate matches with report and incident details."""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                d.candidate_id,
                d.new_issue_id,
                d.matched_issue_id,
                d.similarity,
                d.created_at,

                n.title AS new_title,
                n.description AS new_description,
                n.location AS new_location,
                n.status AS new_status,
                n.priority AS new_priority,
                n.safety_risk AS new_safety_risk,

                m.title AS matched_title,
                m.description AS matched_description,
                m.location AS matched_location,
                m.status AS matched_status,
                m.priority AS matched_priority,
                m.safety_risk AS matched_safety_risk,
                m.safety_risk_reason AS matched_safety_reason,

                g.group_id,
                g.status AS group_status,
                g.priority AS group_priority,
                g.assigned_team

            FROM duplicate_review_candidates d

            JOIN issues n
                ON n.issue_id = d.new_issue_id

            JOIN issues m
                ON m.issue_id = d.matched_issue_id

            LEFT JOIN issue_group_members gm
                ON gm.issue_id = d.matched_issue_id

            LEFT JOIN issue_groups g
                ON g.group_id = gm.group_id

            WHERE d.decision = 'Pending'

            ORDER BY
                n.safety_risk DESC,
                d.created_at ASC,
                d.similarity DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def review_duplicate_candidate(
    candidate_id,
    decision,
    reviewed_by,
):
    """Record an administrator's duplicate-review decision."""

    allowed_decisions = {"Rejected", "New Incident"}

    if decision not in allowed_decisions:
        raise ValueError(
            "This function currently supports Rejected or New Incident."
        )

    reviewed_by = (reviewed_by or "").strip()

    if not reviewed_by:
        raise ValueError("Administrator name is required.")

    with get_connection() as conn:
        candidate = conn.execute(
            """
            SELECT *
            FROM duplicate_review_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if not candidate:
            raise ValueError("Duplicate candidate does not exist.")

        if candidate["decision"] != "Pending":
            raise ValueError("This candidate has already been reviewed.")

        conn.execute(
            """
            UPDATE duplicate_review_candidates
            SET decision = ?,
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE candidate_id = ?
              AND decision = 'Pending'
            """,
            (decision, reviewed_by, candidate_id),
        )

        # Choosing New Incident means the administrator has decided
        # that this report should remain separate. Resolve all its
        # other pending suggestions as well.
        if decision == "New Incident":
            conn.execute(
                """
                UPDATE duplicate_review_candidates
                SET decision = 'New Incident',
                    reviewed_by = ?,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE new_issue_id = ?
                  AND decision = 'Pending'
                """,
                (reviewed_by, candidate["new_issue_id"]),
            )

        return True



def confirm_duplicate_candidate(
    candidate_id,
    reviewed_by,
    reopen_resolved=False,
):
    """
    Confirm a potential duplicate and attach the new report to the
    matched report's incident.

    A resolved incident can only be used when the administrator
    explicitly requests reopening it.
    """

    reviewed_by = (reviewed_by or "").strip()

    if not reviewed_by:
        raise ValueError("Administrator name is required.")

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        candidate = conn.execute(
            """
            SELECT *
            FROM duplicate_review_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if not candidate:
            raise ValueError("Duplicate candidate does not exist.")

        if candidate["decision"] != "Pending":
            raise ValueError("This candidate has already been reviewed.")

        new_issue_id = candidate["new_issue_id"]
        matched_issue_id = candidate["matched_issue_id"]

        new_issue = conn.execute(
            "SELECT * FROM issues WHERE issue_id = ?",
            (new_issue_id,),
        ).fetchone()

        matched_issue = conn.execute(
            "SELECT * FROM issues WHERE issue_id = ?",
            (matched_issue_id,),
        ).fetchone()

        if not new_issue or not matched_issue:
            raise ValueError("One or both reports no longer exist.")

        # Do not merge safety-risk and non-safety reports until
        # incident-level safety history handles every member report.
        if bool(new_issue["safety_risk"]) != bool(matched_issue["safety_risk"]):
            raise ValueError(
                "Cannot merge a safety-risk report with a non-safety report. "
                "Review the safety response independently first."
            )

        # Find the incident containing the matched report, if any.
        matched_membership = conn.execute(
            """
            SELECT g.*
            FROM issue_groups g
            JOIN issue_group_members m
                ON m.group_id = g.group_id
            WHERE m.issue_id = ?
            """,
            (matched_issue_id,),
        ).fetchone()

        # Do not move a report that already belongs to another incident.
        new_membership = conn.execute(
            """
            SELECT group_id
            FROM issue_group_members
            WHERE issue_id = ?
            """,
            (new_issue_id,),
        ).fetchone()

        if new_membership:
            if (
                matched_membership
                and new_membership["group_id"] == matched_membership["group_id"]
            ):
                raise ValueError("These reports are already in the same incident.")

            raise ValueError(
                "The new report already belongs to another incident."
            )

        if matched_membership:
            group_id = matched_membership["group_id"]
            group_status = matched_membership["status"]
            group_priority = matched_membership["priority"]
            assigned_team = matched_membership["assigned_team"]

        else:
            # Preserve the matched report's existing operational values.
            group_status = matched_issue["status"]
            group_priority = matched_issue["priority"]
            assigned_team = matched_issue["assigned_to"]

            cursor = conn.execute(
                """
                INSERT INTO issue_groups (
                    primary_issue_id,
                    duplicate_count,
                    status,
                    priority,
                    assigned_team,
                    resolved_at
                )
                VALUES (?, 0, ?, ?, ?, ?)
                """,
                (
                    matched_issue_id,
                    group_status,
                    group_priority,
                    assigned_team,
                    matched_issue["resolved_at"],
                ),
            )

            group_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO issue_group_members (group_id, issue_id)
                VALUES (?, ?)
                """,
                (group_id, matched_issue_id),
            )

        # A resolved incident requires an explicit reopening decision.
        if group_status == "Resolved":
            if not reopen_resolved:
                raise ValueError(
                    "This incident is resolved. Explicit reopening approval "
                    "is required before attaching the new report."
                )

            group_status = "Under Review"

            conn.execute(
                """
                UPDATE issue_groups
                SET status = ?,
                    resolved_at = NULL
                WHERE group_id = ?
                """,
                (group_status, group_id),
            )

            conn.execute(
                """
                UPDATE issues
                SET status = ?,
                    resolved_at = NULL
                WHERE issue_id IN (
                    SELECT issue_id
                    FROM issue_group_members
                    WHERE group_id = ?
                )
                """,
                (group_status, group_id),
            )

        # Add the new report without deleting or merging either report.
        conn.execute(
            """
            INSERT INTO issue_group_members (group_id, issue_id)
            VALUES (?, ?)
            """,
            (group_id, new_issue_id),
        )

        # Keep the incident and all its member reports synchronized.
        conn.execute(
            """
            UPDATE issues
            SET status = ?,
                priority = ?,
                assigned_to = ?,
                resolved_at = CASE
                    WHEN ? = 'Resolved' THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END
            WHERE issue_id = ?
            """,
            (
                group_status,
                group_priority,
                assigned_team,
                group_status,
                new_issue_id,
            ),
        )

        member_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM issue_group_members
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()["total"]

        conn.execute(
            """
            UPDATE issue_groups
            SET duplicate_count = ?
            WHERE group_id = ?
            """,
            (max(0, member_count - 1), group_id),
        )

        # Confirm the selected candidate.
        conn.execute(
            """
            UPDATE duplicate_review_candidates
            SET decision = 'Confirmed',
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE candidate_id = ?
            """,
            (reviewed_by, candidate_id),
        )

        # Other suggestions for this same new report cannot also be
        # attached independently. Close them as rejected alternatives.
        conn.execute(
            """
            UPDATE duplicate_review_candidates
            SET decision = 'Rejected',
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE new_issue_id = ?
              AND decision = 'Pending'
              AND candidate_id <> ?
            """,
            (reviewed_by, new_issue_id, candidate_id),
        )

        return group_id


def list_duplicate_review_history(limit=100):
    """Return completed duplicate reviews, newest first."""

    limit = max(1, min(int(limit), 500))

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                d.candidate_id,
                d.new_issue_id,
                d.matched_issue_id,
                d.similarity,
                d.decision,
                d.reviewed_by,
                d.reviewed_at,

                n.title AS new_title,
                n.safety_risk AS new_safety_risk,

                m.title AS matched_title,
                m.safety_risk AS matched_safety_risk,

                g.group_id

            FROM duplicate_review_candidates d

            JOIN issues n
                ON n.issue_id = d.new_issue_id

            JOIN issues m
                ON m.issue_id = d.matched_issue_id

            LEFT JOIN issue_group_members gm
                ON gm.issue_id = d.matched_issue_id

            LEFT JOIN issue_groups g
                ON g.group_id = gm.group_id

            WHERE d.decision <> 'Pending'

            ORDER BY
                d.reviewed_at DESC,
                d.candidate_id DESC

            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]


# ======================================================================
# User accounts
#
# Public lookups never return password hashes. The only function that
# returns a hash is get_credentials_by_email(), which exists solely for
# password verification during login. All SQL is parameterized.
# ======================================================================

USER_ROLES = ("student", "admin")

_PUBLIC_USER_COLUMNS = (
    "user_id, name, email, role, is_active, created_at, last_login_at"
)


def normalize_email(email):
    """Trim surrounding whitespace and lower-case an email address."""
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def _public_user(row):
    if row is None:
        return None
    user = dict(row)
    user["is_active"] = bool(user["is_active"])
    return user


def create_user(name, email, password_hash, role="student"):
    """Insert a user and return the new user_id.

    Validation of the email domain and password policy belongs to the
    ``auth`` package; this function enforces the storage invariants
    (normalized email, permitted role, Argon2id hash). The database CHECK
    constraints enforce the same rules a second time. Raises
    ``sqlite3.IntegrityError`` for a duplicate email.
    """
    if role not in USER_ROLES:
        raise ValueError(f"Unsupported role: {role!r}")

    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("A name is required.")

    if not isinstance(password_hash, str) or not password_hash.startswith(
        "$argon2id$"
    ):
        raise ValueError("password_hash must be an Argon2id hash.")

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash, role) "
            "VALUES (?, ?, ?, ?)",
            (name, normalize_email(email), password_hash, role),
        )
        return cursor.lastrowid


def get_user_by_id(user_id):
    """Return a user's public fields, or None."""
    if isinstance(user_id, bool):
        return None
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None

    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_PUBLIC_USER_COLUMNS} FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return _public_user(row)


def get_user_by_email(email):
    """Return a user's public fields by (normalized) email, or None."""
    normalized = normalize_email(email)
    if not normalized:
        return None

    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_PUBLIC_USER_COLUMNS} FROM users WHERE email = ?",
            (normalized,),
        ).fetchone()
        return _public_user(row)


def get_credentials_by_email(email):
    """Return the data needed to verify a login, or None.

    Returns user_id, password_hash and is_active. For use by
    ``auth.authentication`` only; never pass the result to the UI.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT user_id, password_hash, is_active
            FROM users
            WHERE email = ?
            """,
            (normalized,),
        ).fetchone()

    if row is None:
        return None

    return {
        "user_id": row["user_id"],
        "password_hash": row["password_hash"],
        "is_active": bool(row["is_active"]),
    }


def record_successful_login(user_id, new_password_hash=None):
    """Stamp last_login_at and optionally store an upgraded hash.

    Also clears the legacy failed_login_attempts / locked_until columns,
    which are no longer used: per-account lockout was removed because it
    let anyone lock another user's account.
    """
    if new_password_hash is not None and not str(new_password_hash).startswith(
        "$argon2id$"
    ):
        raise ValueError("password_hash must be an Argon2id hash.")

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET failed_login_attempts = 0,
                locked_until = NULL,
                last_login_at = CURRENT_TIMESTAMP,
                password_hash = COALESCE(?, password_hash)
            WHERE user_id = ?
            """,
            (new_password_hash, user_id),
        )


def admin_exists():
    with get_connection() as conn:
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM users WHERE role = 'admin')"
        ).fetchone()[0] == 1


class AdminAlreadyExistsError(ValueError):
    pass


class EmailAlreadyRegisteredError(ValueError):
    pass


def create_first_admin(name, email, password_hash):
    """Create the first administrator atomically and return its user_id.

    The existence checks and the insert run in one BEGIN IMMEDIATE
    transaction, so two concurrent bootstrap runs cannot both succeed.
    Existing accounts are never promoted.
    """
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("A name is required.")
    if not isinstance(password_hash, str) or not password_hash.startswith(
        "$argon2id$"
    ):
        raise ValueError("password_hash must be an Argon2id hash.")

    normalized = normalize_email(email)

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        if conn.execute(
            "SELECT EXISTS (SELECT 1 FROM users WHERE role = 'admin')"
        ).fetchone()[0]:
            raise AdminAlreadyExistsError(
                "An administrator account already exists. The bootstrap "
                "command can only be used once."
            )

        if conn.execute(
            "SELECT EXISTS (SELECT 1 FROM users WHERE email = ?)",
            (normalized,),
        ).fetchone()[0]:
            raise EmailAlreadyRegisteredError(
                "This email already belongs to an account. Existing "
                "accounts are never promoted; use a different email."
            )

        return conn.execute(
            "INSERT INTO users (name, email, password_hash, role) "
            "VALUES (?, ?, ?, 'admin')",
            (name, normalized, password_hash),
        ).lastrowid
