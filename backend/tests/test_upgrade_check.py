"""scripts/upgrade_check.py — the pure checks (the report itself is exercised end to end
against a scratch Postgres upgraded through migration c3d4e5f6a7b8)."""

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "upgrade_check", Path(__file__).resolve().parents[2] / "scripts" / "upgrade_check.py")
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)


def _ev(name, etype="income", cents=100_00, prob=1.0):
    return SimpleNamespace(name=name, event_type=etype, amount_cents=cents, probability=prob)


def test_pinned_months_offers_the_last_edit_reading():
    before = {"sepp": {"sepp_start_month": 12},
              "property_sales": [{"key": "cabin", "sale_month": 13}]}
    now = {"sepp": {"start_date": "2027-09"},
           "property_sales": [{"key": "cabin", "sale_date": "2027-10"}]}
    rows = uc.pinned_months(now, before, date(2026, 6, 2))
    assert {r["what"]: (r["pinned"], r["from_last_edit"]) for r in rows} == {
        "sepp.start_date": ("2027-09", "2027-06"),
        "property sale 'cabin'": ("2027-10", "2027-07"),
    }
    assert uc.pinned_months(now, None, None) == []


def test_sale_proceeds_event_flagged_unless_suppressed():
    selling = [{"key": "cabin", "sale_date": "2027-10", "suppress_cashflow_match": "cabin"}]
    events = [_ev("Cabin Sale Proceeds"), _ev("Condo sale proceeds"), _ev("Bonus")]
    assert uc.double_counted_sales(selling, events) == ["Condo sale proceeds"]
    assert uc.double_counted_sales([{"key": "cabin"}], events) == []  # held: sells nothing


def test_expense_events_that_used_to_be_dropped():
    sales = [{"key": "cabin", "sale_date": "2027-10", "suppress_cashflow_match": "cabin"}]
    events = [_ev("Cabin assessment", "expense"), _ev("Cabin Sale Proceeds"), _ev("Roof", "expense")]
    assert uc.expense_events_now_counted(sales, events) == ["Cabin assessment"]


def test_held_and_bad_events():
    assert uc.held_properties([{"key": "condo"}, {"key": "cabin", "sale_date": "2027-10"}]) == ["condo"]
    bad = uc.bad_events([_ev("Tax", "expense", -272_100), _ev("Maybe", prob=1.5), _ev("Fine")])
    assert len(bad) == 2 and bad[0].startswith("Tax") and bad[1].startswith("Maybe")


# --- October 2026 ---------------------------------------------------------

def _dated(name, d, etype="income", end=None, recurring=False):
    return SimpleNamespace(name=name, event_type=etype, date=d, end_date=end, is_recurring=recurring)


def test_past_dated_events_lists_only_finished_ones():
    today = date(2026, 10, 25)
    events = [
        _dated("Refund", date(2026, 10, 4)),                                   # past one-off
        _dated("Closing", date(2026, 11, 20)),                                 # future
        _dated("Benefit", date(2026, 9, 30), end=date(2026, 12, 30), recurring=True),  # still running
        _dated("Assessment", date(2026, 7, 14), "expense", date(2026, 8, 31), True),    # run over
        _dated("Rent", date(2026, 1, 1), recurring=True),                      # ongoing
    ]
    assert uc.past_dated_events(events, today) == [
        "Refund (income, 2026-10-04)", "Assessment (expense, 2026-08-31)"]


def test_stale_sales_skip_closed_current_and_held():
    today = date(2026, 10, 25)
    sales = [
        {"key": "condo", "sale_date": "2026-09"},                  # past, not closed → flagged
        {"key": "loft", "sale_date": "2026-09", "closed": True},   # closed → fine
        {"key": "cabin", "sale_date": "2026-10"},                  # this month → not yet stale
        {"key": "lake"},                                           # held
    ]
    assert uc.stale_sales(sales, today) == ["'condo' (sale_date 2026-09)"]


def test_matcher_expense_hits():
    proj = {"sell_event_label_match": "mountain house", "illiquid_vest_event_match": ["vest"]}
    events = [_ev("Mountain House Special Assessment", "expense"), _ev("Harvest fee", "expense"),
              _ev("Mountain House Sale Proceeds"), _ev("Startup vests")]
    assert uc.matcher_expense_hits(proj, events) == [
        "'Mountain House Special Assessment' no longer removes the secondary property's equity",
        "'Harvest fee' no longer drains the illiquid pool",
    ]


def test_scenario_override_problem():
    assert uc.scenario_override_problem({"custom_assumptions": {"sepp": {}}}) is None
    assert uc.scenario_override_problem(None) is None
    assert "rental_occupancy_rate" in uc.scenario_override_problem({"rental_occupancy_rate": 0.8})


def test_categories_turning_income():
    cats = [SimpleNamespace(normalized_category="Rental Income", parent_category="Other", is_income=False),
            SimpleNamespace(normalized_category="Paychecks", parent_category="Income", is_income=True),
            SimpleNamespace(normalized_category="Groceries", parent_category="Food", is_income=False)]
    assert uc.categories_turning_income(cats) == ["Rental Income"]
