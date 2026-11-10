from modules.priority_engine import calculate_priority


def test_critical_safety_risk():
    assert calculate_priority("Low", safety_risk=True) == "Critical"


def test_high_severity():
    assert calculate_priority("High") == "High"


def test_medium_default():
    assert calculate_priority("Medium") == "Medium"
