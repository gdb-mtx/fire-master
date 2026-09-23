"""The pool engine's cash repair tops up END-of-month cash, not the opening balance."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.models.cashflow_event import CashflowEvent
from app.schemas.fire import NetWorthBreakdown
from tests.conftest import _make_fire_config
from tests.test_projection_snapshots import _make_engine


def _event(name, etype, cents, dt):
    e = MagicMock(spec=CashflowEvent)
    e.name, e.event_type, e.amount_cents, e.date = name, etype, cents, dt
    e.probability, e.is_recurring, e.recurrence, e.end_date = 1.0, False, None, None
    e.status = "planned"
    return e


def _engine(liquid: float, ca: dict, events: list):
    config = _make_fire_config(
        social_security_monthly=0, healthcare_monthly_cost=None,
        target_annual_spending=12_000_000,  # $10,000/mo
    )
    config.custom_assumptions = ca
    breakdown = NetWorthBreakdown(
        liquid=liquid, retirement=0, real_estate_equity=0, illiquid_private=0, other=0,
    )
    return _make_engine(config, breakdown, [], events)


# An IRA-B the owner can't touch yet keeps the run alive through the pre-sale
# dip (the engine stops when cash is negative and every pool is empty); interest
# off, so the cash line is exact.
BASE = {
    "sepp": {"ira_a_balance": 0, "ira_b_balance": 100_000, "sepp_monthly": 0, "ira_growth_rate": 0.0},
    "projection": {"cash_savings_rate_early": 0.0, "cash_savings_rate_late": 0.0,
                   "surplus_investment_rate": 0.0},
}

SALE = {"key": "condo", "re_bucket": "primary", "value": 300_000, "cost_basis": 300_000,
        "agent_fee_pct": 0.0, "appreciation_rate": 0.0, "current_mortgage_balance": 0, "monthly_cost": 0,
        "in_base_burn": True, "proceeds_to": "taxable"}


@pytest.mark.asyncio
async def test_inflow_month_does_not_sell_the_pool_to_cover_the_opening_hole(frozen_today):
    # Cash is $15K negative when month 4 opens; that month a sale funds the taxable
    # pool AND a $50K payout lands. The payout covers the hole — nothing to repair.
    engine = _engine(25_000, {
        **BASE,
        "property_sales": [{**SALE, "sale_month": 4}],
        "taxable_pool": {"starting_balance": 0, "return_rate": 0.0},
    }, [_event("Payout", "income", 50_000_00, date(2026, 8, 15))])  # month 4 from Apr 14
    r = await engine.project_wealth_pools(end_age=82, bridge_months=12)
    pts = {p.month: p for p in r.points}
    assert pts[3].cash == pytest.approx(-15_000, abs=1)
    assert pts[4].taxable_draw == 0          # used to sell $15K of the pool here
    assert pts[4].cash == pytest.approx(25_000, abs=1)   # −15K + 50K − 10K
    assert pts[4].taxable == pytest.approx(300_000, abs=1)
