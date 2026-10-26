"""A property_sales entry marked `closed: true` is a sale that already happened.

Its proceeds are already in the account balances. Before this state existed a sold
property's entry had two wrong options: kept, its past sale_date clamps to "now"
(plan_months.resolve_month_offset) and BOTH the Runway and the pool engine land
the proceeds again every month; deleted, its carrying cost comes back into the
plan. A closed entry counts as sold from month 0 with zero proceeds: carrying cost
off, post-sale rent on, its rental income stopped, its hand-made event suppressed.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.cashflow import CashflowEngine
from app.engines.fire_projections import sale_event_suppressed
from app.engines.plan_months import is_closed, is_held, sale_offset
from tests.conftest import _make_fire_config
from tests.test_projection_snapshots import _make_engine
from tests.test_runway_income import (
    CONDO_SALE,
    FIRST_OF_MONTH,
    _engine,
    _mock_db_for_runway,
    _src,
)

TODAY = date(2026, 4, 14)  # conftest FROZEN_TODAY


def test_closed_is_sold_now_never_held():
    assert is_closed({"key": "condo", "closed": True})
    assert not is_held({"key": "condo", "closed": True})            # no date, still not held
    assert sale_offset({"key": "condo", "closed": True}, TODAY) == 0
    assert sale_offset({"key": "condo", "closed": True, "sale_date": "2027-07"}, TODAY) == 0
    assert sale_offset({"key": "condo", "sale_date": "2027-07"}, TODAY) == 15  # unchanged


def test_closed_sale_still_owns_its_hand_made_event():
    closed = {"key": "condo", "closed": True, "suppress_cashflow_match": "condo sale"}
    ev = SimpleNamespace(name="Condo Sale Proceeds", event_type="income")
    assert sale_event_suppressed(ev, [closed])


def _pool_config(sale: dict):
    config = _make_fire_config(healthcare_monthly_cost=None, target_annual_spending=12_000_000)
    config.custom_assumptions = {**config.custom_assumptions, "property_sales": [sale]}
    return config


SOLD_LAST_MONTH = {
    "key": "condo", "re_bucket": "primary", "value": 500_000, "cost_basis": 500_000,
    "monthly_cost": 3_000, "in_base_burn": True, "sale_date": "2026-03",  # before FROZEN_TODAY
}


@pytest.mark.asyncio
async def test_pool_engine_never_lands_closed_proceeds(frozen_today, net_worth_breakdown, mock_accounts):
    stale = await _make_engine(_pool_config(SOLD_LAST_MONTH), net_worth_breakdown, mock_accounts, []) \
        .project_wealth_pools(end_age=60)
    closed = await _make_engine(_pool_config({**SOLD_LAST_MONTH, "closed": True}),
                                net_worth_breakdown, mock_accounts, []).project_wealth_pools(end_age=60)
    s0, c0 = stale.points[0], closed.points[0]
    # The stale past date re-lands ~$470K of proceeds in month 0; closed lands nothing.
    assert s0.taxable > 400_000
    assert c0.taxable == pytest.approx(0.0, abs=1.0)
    # Both treat the property as sold from month 0: equity out, carrying cost off.
    assert c0.real_estate == pytest.approx(s0.real_estate)
    assert c0.expenses == pytest.approx(s0.expenses)
    assert "Condo sold" in [e["label"] for e in closed.events]
    assert c0.event == "Condo sold (proceeds in balances)"


@pytest.mark.asyncio
async def test_runway_applies_closed_sale_from_month_zero_without_proceeds():
    db = _mock_db_for_runway(
        cash_cents=50_000_00, trailing_income_cents=0,
        sources=[_src("Condo rent", 12_000_00)], events=[],   # $1,000/mo
    )  # burn $5,000/mo
    sale = {**CONDO_SALE, "closed": True}
    r = await _engine(db, sales=[sale]).project_runway(months=3, today=FIRST_OF_MONTH)
    m0 = r.projection[0]
    assert m0.income == pytest.approx(0.0)              # rent stopped, no proceeds
    assert m0.expenses == pytest.approx(5_000 - 2_000 + 500)
    assert "Condo sold (proceeds in balances)" in m0.events
    [s] = r.scenario_sales
    assert (s.closed, s.month_index, s.net_proceeds) == (True, 0, 0.0)


@pytest.mark.asyncio
async def test_runway_keeps_a_closed_entry_even_without_a_date():
    from app.engines import fire_projections

    config = MagicMock()
    config.custom_assumptions = {"property_sales": [
        {"key": "condo", "closed": True, "monthly_cost": 2_000},  # no sale_date
        {"key": "cabin", "value": 100_000},                       # held: stays out
    ]}
    db = AsyncMock()
    name = MagicMock(); name.scalar_one_or_none.return_value = "Plan B"
    db.execute.return_value = name
    with patch.object(fire_projections.FireProjectionsEngine, "get_effective_config",
                      AsyncMock(return_value=config)):
        sales, _, _ = await CashflowEngine(db)._get_plan_sales()
    assert [s["key"] for s in sales] == ["condo"]
