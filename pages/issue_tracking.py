import streamlit as st
from database.db import get_student_tracking_data


STATUS_STEPS = [
    "Submitted",
    "Under Review",
    "Assigned",
    "In Progress",
    "Resolved",
]


def render_issue_tracking():
    st.header("Track Your Campus Issue")
    st.caption(
        "Enter your CampusFix reference ID to view the latest progress."
    )

    with st.form("track_issue_form"):
        reference = st.text_input(
            "Report reference ID",
            placeholder="e.g. CF-0002",
        )
        submitted = st.form_submit_button(
            "Track Report",
            type="primary",
        )

    if not submitted:
        return

    normalized = reference.strip().upper()

    if normalized.startswith("CF-"):
        normalized = normalized[3:]

    try:
        issue_id = int(normalized)
        if issue_id <= 0:
            raise ValueError
    except ValueError:
        st.error("Enter a valid reference, such as CF-0002.")
        return

    data = get_student_tracking_data(issue_id)

    if data is None:
        st.warning("No report found for that reference.")
        return

    issue = data["issue"]
    status = data["status"]

    st.divider()

    st.subheader(f"CF-{issue_id:04d} | {issue['title']}")

    if data["incident_ref"]:
        st.caption(
            f"Part of incident {data['incident_ref']}. "
            "Updates to this incident apply to associated reports."
        )

    # Current status and report details
    c1, c2, c3 = st.columns(3)

    c1.metric("Current Status", status)
    c2.metric("Priority", data["priority"])
    c3.metric(
        "Maintenance Team",
        data["assigned_team"] or "Not assigned",
    )

    st.write(f"**Location:** {issue['location']}")
    st.write(f"**Category:** {issue['category']}")
    st.write(f"**Submitted:** {issue['created_at']}")

    if data["resolved_at"] and status == "Resolved":
        st.success(f"Resolved on: {data['resolved_at']}")

    st.divider()

    # Progress indicator
    st.subheader("Report Progress")

    if status == "Rejected":
        st.error("This report has been rejected.")
    else:
        current_index = (
            STATUS_STEPS.index(status)
            if status in STATUS_STEPS else 0
        )

        st.progress(
            (current_index + 1) / len(STATUS_STEPS),
            text=f"{status} ({current_index + 1}/{len(STATUS_STEPS)})",
        )

        completed = STATUS_STEPS[:current_index + 1]

        for step in STATUS_STEPS:
            if step in completed:
                st.markdown(f"✓ **{step}**")
            else:
                st.markdown(f"○ {step}")

    st.divider()

    # Public activity timeline
    st.subheader("Activity Timeline")

    timeline = data["timeline"]

    if not timeline:
        st.info("No updates are available yet.")
    else:
        for event in reversed(timeline):
            with st.container(border=True):
                st.markdown(f"**{event['event']}**")
                st.write(event["details"])
                st.caption(event["created_at"])

    st.divider()

    st.caption(
        "CampusFix displays the latest recorded report information. "
        "For urgent safety hazards, contact campus authorities directly."
    )
