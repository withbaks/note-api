"""Heuristic detection tests."""

from note_ai.heuristics import run_heuristics


def test_phone_detection():
    helpers, meta = run_heuristics("Call me at 09061281792")
    keys = [h.key for h in helpers]
    assert "is_phone_number" in keys


def test_shoe_size_mum():
    helpers, _ = run_heuristics("My mum wears size 8")
    shoe = next(h for h in helpers if h.key == "is_shoe_size")
    assert shoe.value == "8"
    assert shoe.subject == "named"
    assert shoe.relationship == "mother"
    assert shoe.semantic_type == "measurement"


def test_shoe_size_joshua():
    helpers, _ = run_heuristics("Joshua's shoe size is 12")
    shoe = next(h for h in helpers if h.key == "is_shoe_size")
    assert shoe.value == "12"
    assert shoe.subject == "named"
    assert shoe.display_name == "Joshua"
    assert not any(h.key == "is_age" for h in helpers)


def test_age_mum():
    helpers, _ = run_heuristics("My mum is 78 years old")
    age = next(h for h in helpers if h.key == "is_age")
    assert age.value == "78"
    assert age.subject == "named"
    assert age.relationship == "mother"
    assert age.semantic_type == "person_meta"


def test_reminder_detection():
    helpers, _ = run_heuristics("Remind me to buy milk tomorrow")
    keys = [h.key for h in helpers]
    assert "is_reminder" in keys


def test_budget_detection():
    helpers, _ = run_heuristics("My monthly budget: 50000")
    keys = [h.key for h in helpers]
    assert "is_monthly_budget" in keys
