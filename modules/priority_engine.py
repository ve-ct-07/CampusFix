"""Deterministic, explainable priority rules."""


def calculate_priority(severity: str, safety_risk: bool = False,
                       duplicate_count: int = 0) -> str:
    if safety_risk or severity == "Critical":
        return "Critical"
    if severity == "High":
        return "High"
    if severity == "Low":
        return "Low"
    # Multiple independent reports can raise a medium issue by one level.
    if duplicate_count >= 5:
        return "High"
    return "Medium"
