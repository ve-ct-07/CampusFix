from pathlib import Path
import uuid
import streamlit as st

from database.access import (
    list_reports_for_duplicate_check,
    record_duplicate_candidates,
    submit_report,
)
from modules.ai_classifier import classify_issue
from modules.duplicate_detector import find_similar_issues
from modules.priority_engine import calculate_priority


def render_student_portal(user):
    st.header("Report a campus issue")
    st.caption("Submit a clear description and location. Do not include personal or sensitive information.")

    with st.form("issue_report_form", clear_on_submit=True):
        title = st.text_input("Issue title", placeholder="e.g. Water leakage near hostel washroom")
        description = st.text_area("Describe the issue", placeholder="What happened? Is anyone at risk?")
        location = st.text_input("Location", placeholder="e.g. Hostel block, floor, common area")
        image = st.file_uploader("Attach a photograph (optional)", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("Analyze and submit report", type="primary")

    if not submitted:
        return
    if not title.strip() or not description.strip() or not location.strip():
        st.error("Please provide a title, description and location.")
        return

    image_path = None
    if image is not None:
        upload_dir = Path(__file__).resolve().parents[1] / "uploads"
        upload_dir.mkdir(exist_ok=True)
        suffix = Path(image.name).suffix.lower()
        image_path = str(upload_dir / f"report_{uuid.uuid4().hex}{suffix}")
        Path(image_path).write_bytes(image.getbuffer())

    with st.spinner("Analyzing report and checking for similar issues..."):
        prediction = classify_issue(description, image_path)
        try:
            # Comparison happens server-side only; other reports are
            # never displayed to the student.
            possible_duplicates = find_similar_issues(
                title,
                description,
                list_reports_for_duplicate_check(user),
                location=location,
                threshold=0.82,
            )
        except Exception:
            possible_duplicates = []

    severity = prediction.get("severity", "Medium")
    priority = calculate_priority(
        severity,
        safety_risk=prediction.get("safety_risk", False),
        duplicate_count=0,
    )

    issue_id = submit_report(
        user,
        title=title.strip(),
        description=description.strip(),
        location=location.strip(),
        category=prediction.get("category", "Other"),
        severity=severity,
        priority=priority,
        image_path=image_path,
        ai_assessment=prediction,
    )

    saved_candidates = 0

    if possible_duplicates:
        try:
            saved_candidates = record_duplicate_candidates(
                user,
                issue_id,
                possible_duplicates,
            )
        except Exception as error:
            st.warning(
                "Your report was submitted, but potential duplicate "
                f"matches could not be saved for administrator review: {error}"
            )

    st.success(f"Report submitted. Reference ID: CF-{issue_id:04d}")
    st.subheader("Triage assessment")
    c1, c2, c3 = st.columns(3)
    c1.metric("Category", prediction.get("category", "Other"))
    c2.metric("Estimated severity", severity)
    c3.metric("Suggested priority", priority)
    st.write(prediction.get("reason", ""))
    st.caption(f"Analysis method: {prediction.get('method', 'unknown')}. "
               "AI assessments are suggestions and require administrator review.")

    if possible_duplicates:
        # Details of other students' reports (titles, locations, IDs) are
        # deliberately not shown. Administrators review the matches.
        st.warning(
            "This issue may already have been reported at this location. "
            "Reports are never merged automatically; an administrator "
            "will review the possible match."
        )

        if saved_candidates:
            st.info("Your report has been queued for duplicate review.")

    st.markdown("#### AI Safety Assessment")

    hazards = prediction.get("secondary_hazards", [])

    if hazards:
        st.write("**Detected secondary hazards:**")
        for hazard in hazards:
            st.warning(hazard)
    else:
        st.write("No secondary hazards identified.")

    if prediction.get("safety_risk"):
        st.error(
            "Potential safety risk detected. "
            "Please contact campus authorities immediately "
            "if there is an urgent danger."
        )
        st.write(prediction.get("safety_risk_reason", ""))
    else:
        st.success("No immediate safety risk identified by the AI.")

    with st.expander("View AI assessment details"):
        st.write("**AI summary**")
        st.write(prediction.get("summary", "Not available."))

        st.write("**Classification reasoning**")
        st.write(prediction.get("reason", "Not available."))
