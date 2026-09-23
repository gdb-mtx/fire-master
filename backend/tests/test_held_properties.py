"""A property_sales entry with no sale date is a property KEPT for good."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.api.fire import spending_breakdown
from app.engines.fire_projections import sale_event_suppressed
from app.engines.plan_months import is_held, sale_offset
from tests.conftest import _make_fire_config
from tests.test_projection_snapshots import _make_engine

TODAY = date(2026, 4, 14)  # conftest FROZEN_TODAY


def test_no_date_means_held_not_sold_now():
    assert is_held({"key": "cabin"})
    assert sale_offset({"key": "cabin"}, TODAY) is None
    assert sale_offset({"key": "cabin", "sale_month": 0}, TODAY) == 0      # explicit "now" still sells
    assert sale_offset({"key": "cabin", "sale_date": "2027-07"}, TODAY) == 15
    assert not is_held({"key": "cabin", "sale_date": "2027-07"})


def test_held_property_replaces_no_sale_event():
    held = {"key": "cabin", "suppress_cashflow_match": "cabin sale"}
    sold = {**held, "sale_date": "2027-07"}
    ev = SimpleNamespace(name="Cabin Sale Proceeds", event_type="income")
    assert not sale_event_suppressed(ev, [held])
    assert sale_event_suppressed(ev, [sold])


def test_breakdown_marks_held_lines():
    b = spending_breakdown({"property_sales": [
        {"key": "cabin", "monthly_cost": 2_000, "in_base_burn": False},
        {"key": "condo", "monthly_cost": 5_000, "sale_date": "2027-07"},
    ]}, 10_000)
    assert [(l.label, l.held) for l in b.properties] == [("Cabin", True), ("Condo", False)]
    assert b.outside_budget_monthly == 2_000


@pytest.mark.asyncio
async def test_held_off_budget_property_costs_every_month_and_never_sells(
    frozen_today, net_worth_breakdown, mock_accounts,
):
    config = _make_fire_config(healthcare_monthly_cost=None, target_annual_spending=12_000_000)
    config.custom_assumptions = {
        **config.custom_assumptions,
        "property_sales": [
            {"key": "cabin", "re_bucket": "secondary", "value": 400_000,
             "monthly_cost": 2_000, "in_base_burn": False},             # held
            {"key": "condo", "re_bucket": "primary", "value": 500_000, "cost_basis": 500_000,
             "monthly_cost": 3_000, "in_base_burn": True, "sale_date": "2027-04"},  # sells m12
        ],
    }
    engine = _make_engine(config, net_worth_breakdown, mock_accounts, [])
    r = await engine.project_wealth_pools(end_age=82, bridge_months=36)
    labels = [e["label"] for e in r.events]
    assert "Sell Condo" in labels and "Sell Cabin" not in labels
    pts = {p.month: p for p in r.points}
    # $10K budget + the held cabin's $2K on top, before and long after the condo sells
    assert pts[0].expenses == pytest.approx(12_000)
    assert pts[11].expenses == pytest.approx(12_000)
    assert pts[12].expenses == pytest.approx(12_000 - 3_000)   # condo's in-budget cost gone
    assert pts[35].expenses == pytest.approx(9_000)             # cabin still costing
