import json
import pandas as pd
import streamlit as st

from database.db import (
    list_issues,
    update_issue_status,
    list_incident_groups,
    list_group_members,
    update_incident_group,
    get_incident_analytics,
    record_safety_response,
    get_safety_response_history,
    get_issue_group,
    get_sla_incidents,
    get_incident_activity,
    list_pending_duplicate_reviews,
    review_duplicate_candidate,
    confirm_duplicate_candidate,
    list_duplicate_review_history,
)



def render_ai_review_panel(issues):
    """Display stored AI triage assessments for administrator review."""

    st.subheader("AI Triage Review")
    st.caption(
        "Review AI-generated classifications and safety assessments. "
        "These are recommendations, not administrator decisions."
    )

    assessed = [
        issue for issue in issues
        if issue.get("ai_method")
    ]

    if not assessed:
        st.info("No AI assessments have been recorded yet.")
        return

    assessed.sort(
        key=lambda issue: (
            not bool(issue.get("safety_risk")),
            issue.get("priority") != "Critical",
            -int(issue["issue_id"]),
        )
    )

    safety_count = sum(
        bool(issue.get("safety_risk")) for issue in assessed
    )

    c1, c2 = st.columns(2)
    c1.metric("AI-assessed reports", len(assessed))
    c2.metric("Safety risks detected", safety_count)

    for issue in assessed:
        issue_id = issue["issue_id"]
        safety_risk = bool(issue.get("safety_risk"))

        label = (
            f"CF-{issue_id:04d} | {issue['title']}"
        )

        if safety_risk:
            label = "⚠️ " + label

        with st.expander(label):

            if safety_risk:
                st.error("Potential safety risk identified by AI.")
            else:
                st.success("No immediate safety risk identified by AI.")

            st.caption(
                f"Location: {issue['location']} | "
                f"Status: {issue['status']} | "
                f"Priority: {issue['priority']}"
            )

            st.markdown("#### Classification comparison")

            ai_category = issue.get("ai_category") or "Not available"
            ai_severity = issue.get("ai_severity") or "Not available"
            current_category = issue.get("category") or "Not available"
            current_severity = issue.get("severity") or "Not available"

            a, b = st.columns(2)

            with a:
                st.markdown("**AI recommendation**")
                st.write(f"Category: {ai_category}")
                st.write(f"Severity: {ai_severity}")

            with b:
                st.markdown("**Current report values**")
                st.write(f"Category: {current_category}")
                st.write(f"Severity: {current_severity}")

            st.markdown(
                f"**Final operational priority:** {issue['priority']}"
            )

            raw_hazards = issue.get("secondary_hazards") or "[]"

            try:
                hazards = json.loads(raw_hazards)
                if not isinstance(hazards, list):
                    hazards = []
            except (TypeError, json.JSONDecodeError):
                hazards = []

            st.markdown("#### Secondary hazards")

            if hazards:
                for hazard in hazards:
                    st.warning(str(hazard))
            else:
                st.write("No secondary hazards recorded.")

            if safety_risk:
                st.markdown("#### Safety explanation")
                st.write(
                    issue.get("safety_risk_reason")
                    or "No separate safety explanation was recorded."
                )

            st.markdown("#### AI summary")
            st.write(
                issue.get("ai_summary")
                or "No AI summary available."
            )

            st.markdown("#### Classification reasoning")
            st.write(
                issue.get("ai_reason")
                or "No classification reasoning available."
            )

            # Safety response acknowledgement and audit history.
            if safety_risk:
                st.markdown("#### Safety Response")

                linked_group = get_issue_group(issue_id)

                if linked_group:
                    history_args = {
                        "group_id": linked_group["group_id"]
                    }
                    target_args = {
                        "group_id": linked_group["group_id"]
                    }
                    st.caption(
                        "Responses are recorded against the shared incident "
                        f"GF-{linked_group['group_id']:04d}."
                    )
                else:
                    history_args = {"issue_id": issue_id}
                    target_args = {"issue_id": issue_id}

                with st.form(key=f"safety_response_form_{issue_id}"):
                    administrator = st.text_input(
                        "Administrator name",
                        placeholder="Enter your name",
                    )

                    action_options = [
                        "Notified maintenance team",
                        "Contacted campus safety/security",
                        "Requested urgent inspection",
                        "Restricted access to affected area",
                        "Contacted emergency services",
                        "Other",
                    ]

                    action = st.selectbox(
                        "Action taken",
                        action_options,
                    )

                    notified_party = st.text_input(
                        "Person or team notified",
                        placeholder="e.g. Electrical Maintenance Team",
                    )

                    action_details = st.text_input(
                        "Additional action details (optional)",
                    )

                    response_notes = st.text_area(
                        "Response notes (optional)",
                        placeholder="Record what was communicated or observed.",
                    )

                    save_response = st.form_submit_button(
                        "Record Safety Response",
                        type="primary",
                    )

                    if save_response:
                        if not administrator.strip():
                            st.error("Enter the administrator name.")
                        elif not action_details.strip() and action == "Other":
                            st.error("Describe the action taken.")
                        else:
                            recorded_action = action
                            if action_details.strip():
                                recorded_action += ": " + action_details.strip()

                            try:
                                record_safety_response(
                                    acknowledged_by=administrator,
                                    action_taken=recorded_action,
                                    notified_party=notified_party,
                                    notes=response_notes,
                                    **target_args,
                                )

                                st.success(
                                    "Safety response recorded successfully."
                                )
                                st.rerun()

                            except Exception as error:
                                st.error(
                                    f"Could not record safety response: {error}"
                                )

                st.markdown("#### Safety Response History")

                response_history = get_safety_response_history(
                    **history_args
                )

                if not response_history:
                    st.info("No safety responses have been recorded yet.")
                else:
                    for response in response_history:
                        with st.container(border=True):
                            st.markdown(
                                f"**{response['action_taken']}**"
                            )
                            st.write(
                                f"**Acknowledged by:** "
                                f"{response['acknowledged_by']}"
                            )

                            if response.get("notified_party"):
                                st.write(
                                    f"**Notified:** "
                                    f"{response['notified_party']}"
                                )

                            if response.get("notes"):
                                st.write(response["notes"])

                            st.caption(response["created_at"])

            st.caption(
                f"Analysis method: {issue.get('ai_method') or 'Unknown'}"
            )


def render_duplicate_review_queue():
    """Display duplicate suggestions grouped by their existing incident."""

    st.divider()
    st.subheader("Duplicate Review Queue")
    st.caption(
        "AI suggestions require human approval. Reports are never merged "
        "automatically."
    )

    pending = list_pending_duplicate_reviews()

    if not pending:
        st.success("No pending duplicate reviews.")
        return

    # Group candidates by new report, then by existing incident.
    grouped = {}

    for candidate in pending:
        new_issue_id = candidate["new_issue_id"]

        if candidate.get("group_id") is not None:
            match_key = ("incident", candidate["group_id"])
        else:
            match_key = ("report", candidate["matched_issue_id"])

        grouped.setdefault(new_issue_id, {})
        grouped[new_issue_id].setdefault(match_key, [])
        grouped[new_issue_id][match_key].append(candidate)

    st.metric("Pending candidate matches", len(pending))
    st.metric("New reports awaiting review", len(grouped))

    for new_issue_id, match_groups in grouped.items():
        first_group = next(iter(match_groups.values()))
        first = first_group[0]

        with st.expander(
            f"CF-{new_issue_id:04d} | {first['new_title']} "
            f"({sum(len(items) for items in match_groups.values())} suggestions)",
            expanded=True,
        ):
            st.markdown("### New report")
            st.write(first["new_description"])

            st.caption(
                f"Location: {first['new_location']} | "
                f"Status: {first['new_status']} | "
                f"Priority: {first['new_priority']}"
            )

            if first["new_safety_risk"]:
                st.error(
                    "SAFETY ALERT: The new report has an AI-detected "
                    "safety risk. Its safety response must be reviewed "
                    "independently of duplicate matching."
                )

            # Safety-critical matches must appear before ordinary matches.
            ordered_match_groups = sorted(
                match_groups.items(),
                key=lambda item: (
                    not any(
                        bool(candidate.get("matched_safety_risk"))
                        for candidate in item[1]
                    ),
                    -max(
                        candidate["similarity"]
                        for candidate in item[1]
                    ),
                ),
            )

            for match_key, candidates in ordered_match_groups:
                # Highest similarity is the representative candidate.
                representative = max(
                    candidates,
                    key=lambda item: item["similarity"],
                )

                safety_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.get("matched_safety_risk")
                ]

                if safety_candidates:
                    st.error(
                        "🚨 SAFETY-CRITICAL MATCH: "
                        "This suggested match contains a report "
                        "with an AI-detected safety risk."
                    )

                    for safety_candidate in safety_candidates:
                        st.markdown(
                            f"**CF-{safety_candidate['matched_issue_id']:04d}** "
                            f"| Priority: "
                            f"**{safety_candidate['matched_priority']}**"
                        )

                        st.write(
                            safety_candidate.get("matched_safety_reason")
                            or "No separate safety explanation recorded."
                        )

                    st.warning(
                        "Review the safety response independently. "
                        "Duplicate confirmation must not replace "
                        "the required safety action."
                    )

                candidate_id = representative["candidate_id"]
                group_id = representative.get("group_id")

                st.divider()

                if group_id is not None:
                    st.markdown(f"### Existing incident GF-{group_id:04d}")
                    st.caption(
                        f"Contains {len(candidates)} matching report(s)"
                    )
                else:
                    st.markdown(
                        f"### Possible match: "
                        f"CF-{representative['matched_issue_id']:04d}"
                    )

                # Show all matching reports within this incident.
                for candidate in sorted(
                    candidates,
                    key=lambda item: item["similarity"],
                    reverse=True,
                ):
                    left, right = st.columns([3, 2])

                    with left:
                        st.markdown(
                            f"**CF-{candidate['matched_issue_id']:04d}: "
                            f"{candidate['matched_title']}**"
                        )
                        st.write(candidate["matched_description"])
                        st.caption(
                            f"Location: {candidate['matched_location']} | "
                            f"Status: {candidate['matched_status']} | "
                            f"Priority: {candidate['matched_priority']}"
                        )

                    with right:
                        st.metric(
                            "Semantic similarity",
                            f"{candidate['similarity']:.1%}",
                        )

                incident_status = (
                    representative.get("group_status")
                    if group_id is not None
                    else representative["matched_status"]
                )

                is_resolved = incident_status == "Resolved"

                if is_resolved:
                    st.warning(
                        "This incident is resolved. Confirmation requires "
                        "explicit approval to reopen it."
                    )

                with st.form(key=f"duplicate_review_{candidate_id}"):
                    administrator = st.text_input(
                        "Administrator name",
                        key=f"duplicate_admin_{candidate_id}",
                    )

                    decision = st.selectbox(
                        "Review decision",
                        [
                            "Confirm duplicate",
                            "Reject this incident match",
                            "Keep as a new incident",
                        ],
                        key=f"duplicate_decision_{candidate_id}",
                    )

                    reopen = False

                    if is_resolved and decision == "Confirm duplicate":
                        reopen = st.checkbox(
                            "I explicitly approve reopening this resolved "
                            "incident and attaching the new report.",
                            key=f"reopen_{candidate_id}",
                        )

                    submitted = st.form_submit_button(
                        "Save review decision",
                        type="primary",
                    )

                    if submitted:
                        if not administrator.strip():
                            st.error("Enter the administrator name.")

                        elif (
                            decision == "Confirm duplicate"
                            and is_resolved
                            and not reopen
                        ):
                            st.error(
                                "Explicit reopening approval is required."
                            )

                        else:
                            try:
                                if decision == "Confirm duplicate":
                                    result_group = confirm_duplicate_candidate(
                                        candidate_id,
                                        administrator,
                                        reopen_resolved=reopen,
                                    )

                                    st.success(
                                        f"Confirmed and attached to "
                                        f"GF-{result_group:04d}."
                                    )

                                elif decision == "Reject this incident match":
                                    # Reject every candidate belonging to
                                    # this same incident, not just one report.
                                    for candidate in candidates:
                                        review_duplicate_candidate(
                                            candidate["candidate_id"],
                                            "Rejected",
                                            administrator,
                                        )

                                    st.success(
                                        "All suggestions for this incident "
                                        "were rejected."
                                    )

                                else:
                                    review_duplicate_candidate(
                                        candidate_id,
                                        "New Incident",
                                        administrator,
                                    )

                                    st.success(
                                        "New incident decision saved. "
                                        "All other pending suggestions for "
                                        "this report were closed."
                                    )

                                st.rerun()

                            except Exception as error:
                                st.error(
                                    f"Could not save review decision: {error}"
                                )


def render_duplicate_review_history():
    """Display the permanent audit trail of completed duplicate reviews."""

    st.divider()
    st.subheader("Duplicate Review History")
    st.caption(
        "Completed administrator decisions are retained for auditing."
    )

    history = list_duplicate_review_history(limit=100)

    if not history:
        st.info("No duplicate reviews have been completed yet.")
        return

    st.metric("Completed review records", len(history))

    for item in history:
        decision = item["decision"]

        if decision == "Confirmed":
            icon = "✅"
        elif decision == "Rejected":
            icon = "❌"
        else:
            icon = "🆕"

        with st.expander(
            f"{icon} CF-{item['new_issue_id']:04d} → "
            f"CF-{item['matched_issue_id']:04d} | {decision}"
        ):
            st.write(
                f"**New report:** CF-{item['new_issue_id']:04d} "
                f"| {item['new_title']}"
            )
            st.write(
                f"**Suggested match:** CF-{item['matched_issue_id']:04d} "
                f"| {item['matched_title']}"
            )

            st.write(
                f"**Similarity at review:** {item['similarity']:.1%}"
            )
            st.write(f"**Decision:** {decision}")
            st.write(
                f"**Reviewed by:** {item['reviewed_by'] or 'Unknown'}"
            )
            st.write(
                f"**Reviewed at:** {item['reviewed_at'] or 'Unknown'}"
            )

            if item.get("group_id") is not None:
                st.write(
                    f"**Current incident association:** "
                    f"GF-{item['group_id']:04d}"
                )

            if (
                bool(item["new_safety_risk"])
                or bool(item["matched_safety_risk"])
            ):
                st.warning(
                    "At least one report in this review had an "
                    "AI-detected safety risk."
                )


def render_admin_dashboard():

    st.header("Maintenance Administration")
    st.caption("CampusFix | Incident Management Centre")

    issues = list_issues()

    if not issues:
        st.info("No reports submitted yet.")
        return

    df = pd.DataFrame(issues)

    # AI assessment review
    render_ai_review_panel(issues)

    # Persistent duplicate review queue
    render_duplicate_review_queue()

    # Completed duplicate-review audit trail
    render_duplicate_review_history()

    st.divider()

    # Incident analytics
    st.subheader("Campus Maintenance Analytics")

    analytics = get_incident_analytics()

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Unique Incidents",
        analytics["total_incidents"],
    )

    c2.metric(
        "Total Submissions",
        analytics["total_submissions"],
    )

    c3.metric(
        "Open Incidents",
        analytics["open_incidents"],
    )

    c4.metric(
        "Resolved Incidents",
        analytics["resolved_incidents"],
    )

    c5, c6, c7 = st.columns(3)

    c5.metric(
        "Duplicate Submissions",
        analytics["duplicate_submissions"],
    )

    c6.metric(
        "Resolution Rate",
        f"{analytics['resolution_rate']:.1f}%",
    )

    average = analytics["average_resolution_hours"]

    c7.metric(
        "Avg. Resolution Time",
        f"{average:.2f} hours" if average is not None else "N/A",
    )

    st.divider()

    st.markdown("#### Incident Distribution")

    chart1, chart2 = st.columns(2)

    with chart1:
        st.markdown("**Incidents by Category**")

        category_df = pd.DataFrame(analytics["by_category"])

        if not category_df.empty:
            st.bar_chart(
                category_df.set_index("name"),
                y="count",
            )
        else:
            st.info("No category data available.")

    with chart2:
        st.markdown("**Incidents by Priority**")

        priority_df = pd.DataFrame(analytics["by_priority"])

        if not priority_df.empty:
            st.bar_chart(
                priority_df.set_index("name"),
                y="count",
            )
        else:
            st.info("No priority data available.")

    st.markdown("#### Maintenance Team Workload")

    team_df = pd.DataFrame(analytics["team_workload"])

    if not team_df.empty:
        st.bar_chart(
            team_df.set_index("name"),
            y="count",
        )
    else:
        st.info("No open incidents currently assigned to maintenance teams.")

    # SLA Monitoring
    st.divider()

    st.subheader("SLA Monitoring")
    st.caption(
        "Monitor open incidents against their priority-based "
        "resolution targets."
    )

    sla_incidents = get_sla_incidents()

    if not sla_incidents:
        st.success("No open incidents. All current incidents are resolved.")
    else:
        breached = [
            item for item in sla_incidents
            if item["sla_breached"]
        ]

        within_sla = len(sla_incidents) - len(breached)

        s1, s2, s3 = st.columns(3)
        s1.metric("Open Incidents", len(sla_incidents))
        s2.metric("Within SLA", within_sla)
        s3.metric("SLA Breached", len(breached))

        for item in sla_incidents:

            with st.container(border=True):

                left, right = st.columns([3, 1])

                with left:
                    st.markdown(
                        f"**{item['incident_ref']} | {item['title']}**"
                    )

                    st.caption(
                        f"Location: {item['location']} | "
                        f"Priority: {item['priority']} | "
                        f"Team: {item['assigned_team'] or 'Unassigned'}"
                    )

                with right:
                    if item["sla_breached"]:
                        st.error("SLA Breached")
                    else:
                        st.success("Within SLA")

                age = item["age_hours"]
                target = item["target_hours"]

                st.progress(
                    min(age / target, 1.0),
                    text=(
                        f"Waiting: {age:.1f} hours / "
                        f"Target: {target} hours"
                    ),
                )

                if item["sla_breached"]:
                    st.error(
                        f"Overdue by "
                        f"{abs(item['remaining_hours']):.1f} hours"
                    )
                else:
                    st.info(
                        f"{item['remaining_hours']:.1f} hours "
                        "remaining before SLA deadline"
                    )

    # Incident groups
    st.subheader("Incident Groups")

    groups = list_incident_groups()

    grouped_issue_ids = set()

    if not groups:
        st.info("No incident groups created yet.")

    else:
        for group in groups:

            group_id = group["group_id"]
            members = list_group_members(group_id)

            grouped_issue_ids.update(
                issue["issue_id"] for issue in members
            )

            with st.expander(
                f"GF-{group_id:04d} | "
                f"{group['primary_title']} | "
                f"{len(members)} reports"
            ):

                st.write(
                    f"**Primary report:** "
                    f"CF-{group['primary_issue_id']:04d}"
                )

                st.write(f"**Location:** {group['location']}")

                st.markdown("#### Incident Management")

                statuses = [
                    "Submitted",
                    "Under Review",
                    "Assigned",
                    "In Progress",
                    "Resolved",
                    "Rejected",
                ]

                priorities = ["Low", "Medium", "High", "Critical"]

                teams = [
                    "Unassigned",
                    "Plumbing",
                    "Electrical",
                    "Civil",
                    "Housekeeping",
                    "IT",
                    "Other",
                ]

                current_status = group["status"]
                current_priority = group["priority"]
                current_team = group.get("assigned_team")

                status_index = (
                    statuses.index(current_status)
                    if current_status in statuses else 0
                )

                priority_index = (
                    priorities.index(current_priority)
                    if current_priority in priorities else 1
                )

                # Preserve custom team names if already assigned.
                if current_team and current_team not in teams:
                    teams.append(current_team)

                team_value = current_team or "Unassigned"
                team_index = teams.index(team_value)

                with st.form(key=f"incident_form_{group_id}"):

                    new_status = st.selectbox(
                        "Incident status",
                        statuses,
                        index=status_index,
                    )

                    new_priority = st.selectbox(
                        "Incident priority",
                        priorities,
                        index=priority_index,
                    )

                    new_team = st.selectbox(
                        "Assigned maintenance team",
                        teams,
                        index=team_index,
                    )

                    custom_team = ""

                    if new_team == "Other":
                        custom_team = st.text_input(
                            "Enter maintenance team name"
                        )

                    submitted = st.form_submit_button(
                        "Save Incident Changes",
                        type="primary",
                    )

                    if submitted:

                        if new_team == "Other":
                            assigned_team = custom_team.strip()

                            if not assigned_team:
                                st.error(
                                    "Please enter a maintenance team name."
                                )
                                st.stop()

                        elif new_team == "Unassigned":
                            assigned_team = None

                        else:
                            assigned_team = new_team

                        try:
                            update_incident_group(
                                group_id,
                                new_status,
                                new_priority,
                                assigned_team,
                            )

                            st.success(
                                f"GF-{group_id:04d} updated successfully. "
                                "All associated reports were synchronized."
                            )

                            st.rerun()

                        except Exception as error:
                            st.error(f"Could not update incident: {error}")

                st.divider()

                st.markdown("#### Associated Reports")

                for issue in members:

                    st.markdown(
                        f"**CF-{issue['issue_id']:04d}: "
                        f"{issue['title']}**"
                    )

                    st.write(issue["description"])

                    st.caption(
                        f"Category: {issue['category']} | "
                        f"Severity: {issue['severity']} | "
                        f"Status: {issue['status']} | "
                        f"Priority: {issue['priority']} | "
                        f"Team: {issue.get('assigned_to') or 'Unassigned'}"
                    )

                    st.divider()

                # Incident activity timeline
                st.divider()
                st.markdown("#### Incident Activity Timeline")

                activity = get_incident_activity(group_id)

                if not activity:
                    st.info(
                        "No administrative changes have been recorded yet. "
                        "Future status, priority, and assignment changes "
                        "will appear here."
                    )
                else:
                    for event in activity:
                        old = event["old_value"] or "Unassigned"
                        new = event["new_value"] or "Unassigned"

                        st.markdown(
                            f"**{event['event_type']} changed**"
                        )

                        st.write(f"{old} → {new}")

                        st.caption(event["created_at"])

    # All reports
    st.subheader("All Submitted Reports")

    st.dataframe(
        df[
            [
                "issue_id",
                "title",
                "category",
                "location",
                "severity",
                "priority",
                "status",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    # Individual management for reports not grouped into incidents.
    st.subheader("Manage Individual Reports")

    ungrouped_issues = [
        issue for issue in issues
        if issue["issue_id"] not in grouped_issue_ids
    ]

    if not ungrouped_issues:

        st.info(
            "All reports currently belong to incident groups. "
            "Manage them through the Incident Groups section above."
        )

    else:

        issue_ids = sorted(
            [int(issue["issue_id"]) for issue in ungrouped_issues]
        )

        selected_id = st.selectbox(
            "Select ungrouped report",
            issue_ids,
            format_func=lambda x: f"CF-{x:04d}",
        )

        selected = next(
            item for item in ungrouped_issues
            if item["issue_id"] == selected_id
        )

        st.markdown(f"### {selected['title']}")
        st.write(selected["description"])

        if selected.get("image_path"):
            st.image(selected["image_path"], width=350)

        statuses = [
            "Submitted",
            "Under Review",
            "Assigned",
            "In Progress",
            "Resolved",
            "Rejected",
        ]

        current = selected["status"]
        index = statuses.index(current) if current in statuses else 0

        new_status = st.selectbox(
            "Update status",
            statuses,
            index=index,
        )

        assignee = st.text_input(
            "Assigned maintenance team",
            value=selected.get("assigned_to") or "",
        )

        if st.button("Save Individual Report Changes", type="primary"):

            update_issue_status(
                selected_id,
                new_status,
                assignee.strip() or None,
            )

            st.success("Report updated successfully.")
            st.rerun()
