"""fire-master#27: earned income that starts after retirement is not cut off at it.

One rule (engines/earned_income.stops_at_retirement) in every engine that has a
retirement date: an undated salary / bonus / side hustle that started before
retirement stops there; one that starts on or after it runs to its own end_date,
or for life; an explicit end_date always wins. The Retirement page's pool engine
is deliberately outside it (no working phase: it skips earned income of every
kind) and is not exercised here.
"""

from contextlib import ExitStack
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.engines.cashflow import CashflowEngine
from app.engines.earned_income import stops_at_retirement
from app.engines.fire_projections import FireProjectionsEngine
from app.models.enums import IncomeType

from .conftest import FROZEN_TODAY, _make_fire_config
from .test_lifetime_projection import _patch_common
from .test_runway_income import _src
from .test_tax_planning import (
    CEILING_22,
    STD_DED,
    _income_source,
    _make_planning_engine,
    _patch_config,
)

RETIRES = date(2032, 8, 31)  # the issue's repro


@pytest.fixture
def frozen_today_tax():
    with patch("app.engines.tax_engine.date") as mock_date:
        mock_date.today.return_value = FROZEN_TODAY
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        yield mock_date


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("itype,start,end,retirement,expected", [
    (IncomeType.SALARY, None, None, RETIRES, True),                         # the job you retire from
    (IncomeType.BONUS, date(2030, 1, 1), None, RETIRES, True),             # started before retirement
    (IncomeType.SIDE_HUSTLE, date(2032, 9, 1), None, RETIRES, False),      # the reported case
    (IncomeType.SIDE_HUSTLE, RETIRES, None, RETIRES, False),               # starts on the day: retirement work
    (IncomeType.SALARY, None, date(2034, 6, 30), RETIRES, False),          # explicit end wins
    (IncomeType.SALARY, None, None, None, True),                           # no date: still working-years pay
    (IncomeType.RENTAL, None, None, RETIRES, False),                       # not earned income
    (IncomeType.OTHER, date(2033, 1, 1), None, RETIRES, False),
])
def test_stops_at_retirement(itype, start, end, retirement, expected):
    assert stops_at_retirement(_src("x", 1, itype, start=start, end=end), retirement) is expected


# ---------------------------------------------------------------------------
# Monthly engines: timeline / Monte Carlo (_income_at_month) and the Runway
# ---------------------------------------------------------------------------

CONSULTING = _src("Consulting", 40_000_00, IncomeType.SIDE_HUSTLE, start=date(2032, 9, 1))
OLD_JOB = _src("Salary", 150_000_00, IncomeType.SALARY)
MONTHLY = int(40_000_00 / 12)


@pytest.mark.parametrize("model", ["timeline", "runway"])
def test_side_hustle_that_starts_in_retirement_pays(model):
    def income(sources, when):
        if model == "runway":
            return CashflowEngine.modeled_income_for_month(sources, when, RETIRES)
        return FireProjectionsEngine(AsyncMock())._income_at_month(sources, when, RETIRES, 0.0)

    assert income([CONSULTING], date(2033, 1, 1)) == MONTHLY      # was 0
    assert income([CONSULTING], date(2032, 8, 1)) == 0            # not started yet
    assert income([CONSULTING, OLD_JOB], date(2033, 1, 1)) == MONTHLY  # the old job still stops
    assert income([CONSULTING, OLD_JOB], date(2032, 8, 1)) == int(150_000_00 / 12)


# ---------------------------------------------------------------------------
# Withdrawal plan (calendar-year proration) — persona retires 2026-07-01
# ---------------------------------------------------------------------------

async def _withdrawal_years(sources, years=3):
    engine = _make_planning_engine(deferred=500_000, income_sources=sources)
    with _patch_config(_make_fire_config(target_annual_spending=12_000_000)):
        plan = await engine.optimize_withdrawal_sequence(years=years, roth_conversions_enabled=False)
    return {y.year: y for y in plan.years}


class TestWithdrawalPlan:
    async def test_retirement_side_hustle_counts_from_its_start(self, frozen_today_tax):
        consulting = _income_source("Consulting", IncomeType.SIDE_HUSTLE, 40_000,
                                    start=date(2027, 1, 1))
        y = await _withdrawal_years([consulting])
        assert y[2026].fica_tax == 0
        assert y[2027].fica_tax > 0 and y[2028].fica_tax > 0          # was 0: capped at 2026-07-01
        assert y[2028].fica_tax == pytest.approx(y[2027].fica_tax)    # a full year, for life

    async def test_explicit_end_after_retirement_is_honoured(self, frozen_today_tax):
        working_on = _income_source("Salary", IncomeType.SALARY, 100_000, end=date(2027, 12, 31))
        undated = _income_source("Salary", IncomeType.SALARY, 100_000)
        honoured = await _withdrawal_years([working_on])
        cut = await _withdrawal_years([undated])
        assert honoured[2027].fica_tax > 0                            # was 0: capped at retirement
        assert honoured[2028].fica_tax == 0
        assert cut[2027].fica_tax == 0                                # undated job still stops
        assert honoured[2026].fica_tax == pytest.approx(cut[2026].fica_tax * 2, rel=0.02)


# ---------------------------------------------------------------------------
# Roth-conversion ladder baseline — window opens at retirement (2026-07-01)
# ---------------------------------------------------------------------------

async def _ladder(sources):
    engine = _make_planning_engine(deferred=2_000_000, income_sources=sources)
    with _patch_config(_make_fire_config()):
        plan = await engine.plan_roth_conversions(target_bracket_rate=0.22)
    return plan.years


class TestRothLadderBaseline:
    async def test_retirement_work_fills_the_bracket(self, frozen_today_tax):
        consulting = _income_source("Consulting", IncomeType.SIDE_HUSTLE, 30_000,
                                    start=date(2027, 1, 1))
        rungs = await _ladder([consulting])
        assert rungs[0].conversion_amount == pytest.approx(CEILING_22 + STD_DED)   # 2026: not started
        assert rungs[1].baseline_income == pytest.approx(30_000)                   # was 0
        assert rungs[1].conversion_amount == pytest.approx(CEILING_22 + STD_DED - 30_000)

    async def test_working_years_pay_stays_out(self, frozen_today_tax):
        rungs = await _ladder([_income_source("Salary", IncomeType.SALARY, 150_000)])
        assert all(r.baseline_income == 0 for r in rungs)


# ---------------------------------------------------------------------------
# FIRE number: work done in retirement is continuing income
# ---------------------------------------------------------------------------

async def _fire_number(sources, net_worth_breakdown):
    config = _make_fire_config()
    patches = _patch_common(config, sources=sources) + [
        patch.object(FireProjectionsEngine, "_compute_net_worth_breakdown",
                     AsyncMock(return_value=net_worth_breakdown)),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return (await FireProjectionsEngine(db=None).compute_fire_number()).fire_number


async def test_fire_number_nets_retirement_work(frozen_today, net_worth_breakdown):
    none = await _fire_number([], net_worth_breakdown)
    consulting = _src("Consulting", 40_000_00, IncomeType.SIDE_HUSTLE, start=date(2027, 1, 1))
    old_job = _src("Salary", 150_000_00, IncomeType.SALARY)
    assert await _fire_number([consulting], net_worth_breakdown) < none   # was equal
    assert await _fire_number([old_job], net_worth_breakdown) == none
