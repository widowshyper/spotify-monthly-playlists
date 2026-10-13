from gui import describe_range


def test_describe_range():
    assert describe_range(None, None) == "all months"
    assert describe_range((2025, 3), None) == "from March 2025"
    assert describe_range((2025, 3), (2026, 6)) == "March 2025 to June 2026"
    assert describe_range((2026, 6), (2026, 6)) == "June 2026"
    assert describe_range(None, (2026, 6)) == "up to June 2026"
