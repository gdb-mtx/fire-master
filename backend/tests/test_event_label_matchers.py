"""The pool engine's event-label matchers fire on INCOME events only.

project_wealth_pools reads two name matchers from custom_assumptions.projection:
- sell_event_label_match: the secondary property's sale event; when it fires the
  property's equity leaves the real-estate pool (the cash arrives via the event);
- illiquid_vest_event_match: private-investment vests; each one moves its amount
  out of the illiquid pool (the cash arrives via the event).

Both are conversions of an asset into cash, so only income can trigger them. Until
Oct 2026 they matched EVERY label, so an expense such as "Mountain House Special
Assessment" zeroed the secondary property's equity in its month, and an expense
containing "vest" drained the illiquid pool. sale_event_suppressed and the
single-pool skip were already income-only; this pins the pool engine to the same rule.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.models.cashflow_event import CashflowEvent
from tests.conftest import _make_fire_config
from tests.test_projection_snapshots import _make_engine


def _event(name: str, event_type: str, amount_cents: int, when: date) -> MagicMock:
    e = MagicMock(spec=CashflowEvent)
    e.name = name
    e.event_type = event_type
    e.amount_cents = amount_cents
    e.date = when
    e.probability = 1.0
    e.is_recurring = False
    e.recurrence = None
    e.end_date = None
    e.status = "planned"
    return e


async def _run(events, net_worth_breakdown, mock_accounts):
    config = _make_fire_config()  # persona: sell_event_label_match = "mountain house"
    engine = _make_engine(config, net_worth_breakdown, mock_accounts, events)
    return await engine.project_wealth_pools(end_age=60)


def _series(result, field: str) -> list[float]:
    return [round(getattr(p, field), 2) for p in result.points]


@pytest.mark.asyncio
async def test_expense_with_sale_label_keeps_property_equity(
    net_worth_breakdown, mock_accounts, frozen_today,
):
    baseline = await _run([], net_worth_breakdown, mock_accounts)
    with_expense = await _run(
        [_event("Mountain House Special Assessment", "expense", 500_000, date(2026, 7, 1))],
        net_worth_breakdown, mock_accounts,
    )
    assert _series(with_expense, "real_estate") == _series(baseline, "real_estate")


@pytest.mark.asyncio
async def test_income_with_sale_label_still_sells(net_worth_breakdown, mock_accounts, frozen_today):
    baseline = await _run([], net_worth_breakdown, mock_accounts)
    sold = await _run(
        [_event("Mountain House Sale Proceeds", "income", 10_000_000, date(2026, 7, 1))],
        net_worth_breakdown, mock_accounts,
    )
    assert _series(sold, "real_estate")[-1] < _series(baseline, "real_estate")[-1]


@pytest.mark.asyncio
async def test_expense_with_vest_label_keeps_illiquid(net_worth_breakdown, mock_accounts, frozen_today):
    baseline = await _run([], net_worth_breakdown, mock_accounts)
    with_expense = await _run(
        [_event("Harvest festival booth", "expense", 300_000, date(2026, 8, 1))],
        net_worth_breakdown, mock_accounts,
    )
    assert _series(with_expense, "illiquid") == _series(baseline, "illiquid")


@pytest.mark.asyncio
async def test_income_vest_still_reduces_illiquid(net_worth_breakdown, mock_accounts, frozen_today):
    baseline = await _run([], net_worth_breakdown, mock_accounts)
    vested = await _run(
        [_event("Startup A vests", "income", 300_000, date(2026, 8, 1))],
        net_worth_breakdown, mock_accounts,
    )
    assert _series(vested, "illiquid")[-1] == pytest.approx(_series(baseline, "illiquid")[-1] - 3_000)
