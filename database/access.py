"""Authorization-enforcing data access for the CampusFix user interface.

Every page calls these functions instead of ``database.db`` directly. Each
function takes the signed-in ``actor`` (a user ID or a dict containing
``user_id``) and:

* re-reads the actor's role and active status from the ``users`` table on
  every call. A role carried in the Streamlit session, or anywhere else
  supplied by the caller, is never trusted;
* enforces report ownership inside the SQL query (``submitted_by = ?``), so a
  student can only read their own reports regardless of which ID they ask
  for;
* records the authenticated administrator's identity in audit columns
  (``reviewed_by``, ``acknowledged_by``, ``changed_by``) instead of accepting
  a typed name.

The underlying ``database.db`` functions are unchanged and remain the
internal data layer for scripts and tests.
"""

import functools

from database import db


class AuthorizationError(PermissionError):
    """Raised when the actor may not perform an operation."""


# Report fields a student may see for their own report.
_STUDENT_ISSUE_FIELDS = (
    "issue_id", "title", "description", "location", "category",
    "status", "priority", "created_at", "resolved_at",
)


# ----------------------------------------------------------------------
# Identity checks (role is always read from the database)
# ----------------------------------------------------------------------

def _actor_id(actor):
    if isinstance(actor, dict):
        actor = actor.get("user_id")
    if isinstance(actor, bool):
        return None
    try:
        value = int(actor)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _load_active_user(conn, actor):
    user_id = _actor_id(actor)
    if user_id is None:
        return None
    row = conn.execute(
        """
        SELECT user_id, email, role
        FROM users
        WHERE user_id = ? AND is_active = 1
          AND role IN ('student', 'admin')
        """,
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def require_user(actor):
    """Return the active user for ``actor`` or raise AuthorizationError."""
    with db.get_connection() as conn:
        user = _load_active_user(conn, actor)
    if user is None:
        raise AuthorizationError("Please sign in to continue.")
    return user


def require_admin(actor):
    """Return the active administrator for ``actor`` or raise."""
    user = require_user(actor)
    if user["role"] != "admin":
        raise AuthorizationError("Administrator access is required.")
    return user


def is_admin(actor):
    try:
        require_admin(actor)
        return True
    except AuthorizationError:
        return False


# ----------------------------------------------------------------------
# Operations available to any signed-in user (students and admins)
# ----------------------------------------------------------------------

def submit_report(
    actor,
    *,
    title,
    description,
    location,
    category="Unclassified",
    severity="Medium",
    priority="Medium",
    image_path=None,
    ai_assessment=None,
):
    """Create a report owned by the signed-in user.

    Ownership comes from the authenticated identity; callers cannot set it.
    """
    user = require_user(actor)
    return db.create_issue(
        title=title,
        description=description,
        location=location,
        category=category,
        severity=severity,
        priority=priority,
        image_path=image_path,
        ai_assessment=ai_assessment,
        submitted_by=user["user_id"],
    )


def list_my_reports(actor):
    """Return a summary of the reports submitted by the signed-in user."""
    user = require_user(actor)
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT issue_id, title, location, category,
                   status, priority, created_at
            FROM issues
            WHERE submitted_by = ?
            ORDER BY created_at DESC, issue_id DESC
            """,
            (user["user_id"],),
        ).fetchall()
        return [dict(row) for row in rows]


def get_report_tracking(actor, issue_id):
    """Return tracking data if the actor may view the report, else None.

    Students may only view reports they submitted; administrators may view
    any report. The decision is made by a single query that joins the
    report to the actor's database record. "Not found" and "not yours" give
    the same result, so report IDs cannot be probed.
    """
    try:
        issue_id = int(issue_id)
    except (TypeError, ValueError):
        return None

    user = require_user(actor)

    with db.get_connection() as conn:
        allowed = conn.execute(
            """
            SELECT i.issue_id
            FROM issues i
            JOIN users u ON u.user_id = ?
            WHERE i.issue_id = ?
              AND u.is_active = 1
              AND (u.role = 'admin' OR i.submitted_by = u.user_id)
            """,
            (user["user_id"], issue_id),
        ).fetchone()

    if not allowed:
        return None

    data = db.get_student_tracking_data(issue_id)

    if data is not None and user["role"] != "admin":
        data["issue"] = {
            key: data["issue"].get(key) for key in _STUDENT_ISSUE_FIELDS
        }

    return data


def record_duplicate_candidates(actor, issue_id, candidates):
    """Queue duplicate suggestions for a report the actor just submitted.

    The report must belong to the actor (administrators may queue for any
    report). Suggestions only enter the administrator review queue; nothing
    is merged.
    """
    user = require_user(actor)

    with db.get_connection() as conn:
        allowed = conn.execute(
            """
            SELECT i.issue_id
            FROM issues i
            JOIN users u ON u.user_id = ?
            WHERE i.issue_id = ?
              AND u.is_active = 1
              AND (u.role = 'admin' OR i.submitted_by = u.user_id)
            """,
            (user["user_id"], issue_id),
        ).fetchone()

    if not allowed:
        raise AuthorizationError("You can only update your own reports.")

    return db.save_duplicate_candidates(issue_id, candidates)


def list_reports_for_duplicate_check(actor):
    """Return existing reports for server-side similarity comparison.

    The result is only used by the duplicate detector during submission and
    must never be rendered to a student. Only the fields the detector needs
    are returned.
    """
    require_user(actor)
    return [
        {
            "issue_id": issue["issue_id"],
            "title": issue["title"],
            "description": issue["description"],
            "location": issue["location"],
            "status": issue["status"],
        }
        for issue in db.list_issues()
    ]


# ----------------------------------------------------------------------
# Administrator-only operations
# ----------------------------------------------------------------------

def _admin_only(func):
    """Wrap a data-layer function so it first requires an administrator."""

    @functools.wraps(func)
    def wrapper(actor, *args, **kwargs):
        require_admin(actor)
        return func(*args, **kwargs)

    wrapper.__module__ = __name__
    wrapper.__wrapped_admin_only__ = True
    wrapper.__doc__ = (
        f"Administrator only. Wraps database.db.{func.__name__}.\n\n"
        f"{func.__doc__ or ''}"
    )
    return wrapper


list_issues = _admin_only(db.list_issues)
get_issue_activity = _admin_only(db.get_issue_activity)
list_incident_groups = _admin_only(db.list_incident_groups)
list_group_members = _admin_only(db.list_group_members)
get_incident_group = _admin_only(db.get_incident_group)
get_issue_group = _admin_only(db.get_issue_group)
get_incident_activity = _admin_only(db.get_incident_activity)
get_incident_analytics = _admin_only(db.get_incident_analytics)
get_sla_incidents = _admin_only(db.get_sla_incidents)
get_safety_response_history = _admin_only(db.get_safety_response_history)
list_pending_duplicate_reviews = _admin_only(db.list_pending_duplicate_reviews)
list_duplicate_review_history = _admin_only(db.list_duplicate_review_history)


def update_issue_status(actor, issue_id, status, assigned_to=None):
    """Administrator only. Records the administrator in issue_activity."""
    admin = require_admin(actor)
    return db.update_issue_status(
        issue_id, status, assigned_to, changed_by=admin["email"],
    )


def update_incident_group(actor, group_id, status, priority, assigned_team):
    """Administrator only. Records the administrator in the activity log."""
    admin = require_admin(actor)
    return db.update_incident_group(
        group_id, status, priority, assigned_team,
        changed_by=admin["email"],
    )


def record_safety_response(
    actor,
    action_taken,
    issue_id=None,
    group_id=None,
    notified_party=None,
    notes=None,
):
    """Administrator only. ``acknowledged_by`` is the signed-in admin."""
    admin = require_admin(actor)
    return db.record_safety_response(
        acknowledged_by=admin["email"],
        action_taken=action_taken,
        issue_id=issue_id,
        group_id=group_id,
        notified_party=notified_party,
        notes=notes,
    )


def review_duplicate_candidate(actor, candidate_id, decision):
    """Administrator only. ``reviewed_by`` is the signed-in admin."""
    admin = require_admin(actor)
    return db.review_duplicate_candidate(
        candidate_id, decision, admin["email"],
    )


def confirm_duplicate_candidate(actor, candidate_id, reopen_resolved=False):
    """Administrator only. ``reviewed_by`` is the signed-in admin."""
    admin = require_admin(actor)
    return db.confirm_duplicate_candidate(
        candidate_id, admin["email"], reopen_resolved=reopen_resolved,
    )
