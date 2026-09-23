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
