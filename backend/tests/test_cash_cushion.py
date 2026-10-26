"""The opt-in cash cushion (projection.cash_cushion_months), Oct 2026.

Unset, the pool engine runs the legacy threshold rule byte for byte; that proof
is tests/test_wealth_pools_golden.py. These tests pin the cushion itself:
refill order, what sizes the cushion, the floor, investing the excess, and the
money identity the Retirement page draws from.

Interest and growth are switched off wherever an exact dollar figure matters.
"""

from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engines.fire_projections import FireProjectionsEngine
from app.models.cashflow_event import CashflowEvent
from app.models.enums import IncomeType
from app.models.income_source import IncomeSource
from app.schemas.fire import NetWorthBreakdown
from tests.conftest import SCENARIO_SELL_ALL_THREE, _apply_scenario, _make_fire_config
from tests.test_projection_snapshots import _make_engine

QUIET = {"cash_savings_rate_early": 0.0, "cash_savings_rate_late": 0.0,
         "surplus_investment_rate": 0.0}
FLAT = {"spending_phase_slow": 1.0, "spending_phase_floor": 1.0}  # no step-down at 70/80


def _event(name, etype, dollars, dt, recurring=False, end=None, probability=1.0):
    e = MagicMock(spec=CashflowEvent)
    e.name, e.event_type, e.amount_cents, e.date = name, etype, int(dollars * 100), dt
    e.probability, e.is_recurring = probability, recurring
    e.recurrence, e.end_date = ("monthly" if recurring else None), end
    e.status = "planned"
    return e


def _source(name, itype, monthly):
    s = MagicMock(spec=IncomeSource)
    s.name, s.income_type, s.annual_amount = name, itype, int(monthly * 12 * 100)
    s.start_date = s.end_date = None
    return s


def _engine(liquid, ca, events=(), sources=(), dob=date(1973, 7, 1), spending=10_000):
    """$10K a month of spending, nothing else unless the test adds it."""
    config = _make_fire_config(
        social_security_monthly=0, healthcare_monthly_cost=None,
        target_annual_spending=spending * 12 * 100, date_of_birth=dob,
    )
    config.custom_assumptions = ca
    breakdown = NetWorthBreakdown(liquid=liquid, retirement=0, real_estate_equity=0,
                                  illiquid_private=0, other=0)
    return _make_engine(config, breakdown, [], list(events), list(sources))


def _ca(*, taxable=0.0, ira_b=0.0, roth=0.0, **projection):
    ca = {
        "sepp": {"ira_a_balance": 0, "ira_b_balance": ira_b, "sepp_monthly": 0, "ira_growth_rate": 0.0},
        "taxable_pool": {"starting_balance": taxable, "return_rate": 0.0},
        "projection": {**QUIET, **projection},
    }
    if roth:
        ca["roth_pool"] = {"starting_balance": roth, "return_rate": 0.0}
    return ca


async def _run(engine, months=120, end_age=82):
    r = await engine.project_wealth_pools(end_age=end_age, bridge_months=months)
    return r, {p.month: p for p in r.points}


@pytest.mark.asyncio
async def test_unset_is_the_threshold_rule(frozen_today):
    r, pts = await _run(_engine(10_000, _ca(taxable=500_000)))
    assert r.cash_rule.mode == "threshold" and r.cash_rule.cushion_months is None
    assert all(p.cash_cushion == 0 and p.cash_invested == 0 for p in r.points)


@pytest.mark.asyncio
async def test_brokerage_refills_cash_to_the_cushion(frozen_today):
    # 6 months of a $10K shortfall = $60K. Cash starts at $10K, so month 0 sells
    # $60K (the $10K month plus the $50K hole); after that $10K a month.
    r, pts = await _run(_engine(10_000, _ca(taxable=500_000, cash_cushion_months=6)))
    assert r.cash_rule.mode == "cushion" and r.cash_rule.cushion_months == 6
    assert pts[0].cash_cushion == 60_000
    assert pts[0].taxable_draw == 60_000 and pts[0].cash == 60_000
    for m in range(1, 24):
        assert pts[m].taxable_draw == 10_000 and pts[m].cash == 60_000


@pytest.mark.asyncio
async def test_cash_above_the_cushion_pays_first(frozen_today):
    r, pts = await _run(_engine(200_000, _ca(taxable=500_000, cash_cushion_months=6)))
    for m in range(14):  # $200K falls $10K a month to exactly $60K at month 13
        assert pts[m].taxable_draw == 0
    assert pts[13].cash == 60_000
    assert pts[14].taxable_draw == 10_000 and pts[14].cash == 60_000


@pytest.mark.asyncio
async def test_no_refill_when_no_pool_has_money(frozen_today):
    # IRA-B can't be touched before 59½ (Jan 2033 here) and nothing else exists.
    r, pts = await _run(_engine(30_000, _ca(ira_b=100_000, cash_cushion_months=6)))
    for m in range(12):
        assert pts[m].taxable_draw == pts[m].ira_draw == pts[m].roth_draw == 0
    assert pts[2].cash == 0 and pts[3].cash == -10_000  # the shortfall shows, unrepaired
    assert r.cash_zero_month == 2


@pytest.mark.asyncio
async def test_ira_b_refills_only_from_59_and_a_half(frozen_today):
    r, pts = await _run(_engine(0, _ca(ira_b=2_000_000, cash_cushion_months=6)), months=200)
    m59 = next(x.month for x in r.milestones if x.label == "Age 59½")
    assert all(pts[m].ira_draw == 0 for m in range(m59))
    assert pts[m59].ira_draw > 0 and pts[m59].cash == 60_000


@pytest.mark.asyncio
async def test_required_withdrawals_fill_the_need_before_the_brokerage(frozen_today):
    # Age 74: the IRS minimum on $1M is $1M / 25.5 / 12 = $3,268 a month. It is
    # forced out anyway, so it covers part of the $10K refill; the brokerage
    # sells only the rest, and nothing is redeposited.
    engine = _engine(60_000, _ca(taxable=500_000, ira_b=1_000_000, cash_cushion_months=6, **FLAT),
                     dob=date(1952, 1, 1))
    r, pts = await _run(engine, months=24, end_age=90)
    rmd = 1_000_000 / 25.5 / 12
    assert pts[0].ira_draw == pytest.approx(rmd, abs=1)
    assert pts[0].taxable_draw == pytest.approx(10_000 - rmd, abs=1)
    assert pts[0].rmd_redeposit == 0 and pts[0].cash == 60_000


@pytest.mark.asyncio
async def test_required_withdrawals_beyond_the_need_go_to_the_brokerage(frozen_today):
    # Cash is far above the cushion, so the need is $0: the whole forced
    # withdrawal is redeposited and cash keeps paying the bills.
    engine = _engine(300_000, _ca(taxable=500_000, ira_b=1_000_000, cash_cushion_months=6, **FLAT),
                     dob=date(1952, 1, 1))
    r, pts = await _run(engine, months=24, end_age=90)
    assert pts[0].taxable_draw == 0
    assert pts[0].rmd_redeposit == pts[0].ira_draw > 3_000
    assert pts[0].taxable == pytest.approx(500_000 + pts[0].rmd_redeposit, abs=1)
    assert pts[0].cash == 290_000


@pytest.mark.asyncio
async def test_roth_is_drawn_last(frozen_today):
    # Age 61: brokerage $20K and IRA-B $30K go before the Roth is touched.
    engine = _engine(0, _ca(taxable=20_000, ira_b=30_000, roth=100_000, cash_cushion_months=6),
                     dob=date(1965, 1, 1))
    r, pts = await _run(engine, months=24)
    assert (pts[0].taxable_draw, pts[0].ira_draw, pts[0].roth_draw) == (20_000, 30_000, 20_000)
    assert pts[0].cash == 60_000
    assert (pts[1].taxable_draw, pts[1].ira_draw, pts[1].roth_draw) == (0, 0, 10_000)


@pytest.mark.asyncio
async def test_events_do_not_size_the_cushion(frozen_today):
    # The prototype sized the cushion on the month's whole gap, so a windfall
    # month set it to $0. Events (one-off or recurring) now leave it alone.
    events = [
        _event("Payout", "income", 150_000, date(2026, 7, 15)),                       # month 3
        _event("Benefit", "income", 1_200, date(2026, 5, 1), recurring=True, end=date(2026, 9, 30)),
        _event("Assessment", "expense", 2_000, date(2026, 6, 1), recurring=True, end=date(2026, 8, 31)),
    ]
    _, with_events = await _run(_engine(60_000, _ca(taxable=500_000, cash_cushion_months=6), events))
    _, without = await _run(_engine(60_000, _ca(taxable=500_000, cash_cushion_months=6)))
    assert all(with_events[m].cash_cushion == without[m].cash_cushion == 60_000 for m in range(24))
    # ...while the money still moves cash: the payout sits above the cushion and pays first.
    assert with_events[3].cash == 199_200        # 60K − 10K + 1.2K − 2K + 150K
    assert with_events[4].cash == 188_400
    assert with_events[3].taxable_draw == 0 and with_events[4].taxable_draw == 0


@pytest.mark.asyncio
async def test_floor_holds_when_income_covers_most_spending(frozen_today):
    pension = [_source("Pension", IncomeType.PENSION, 9_000)]  # $1K shortfall → 6 months = $6K
    _, no_floor = await _run(_engine(100_000, _ca(taxable=500_000, cash_cushion_months=6), sources=pension))
    _, floored = await _run(_engine(100_000, _ca(taxable=500_000, cash_cushion_months=6,
                                                 cash_cushion_floor=25_000), sources=pension))
    assert no_floor[0].cash_cushion == 6_000
    assert floored[0].cash_cushion == 25_000
    # Cash spends down to the floor, then the brokerage holds it there.
    m = next(k for k in sorted(floored) if floored[k].taxable_draw > 0)
    assert floored[m].cash == 25_000 and floored[m + 1].cash == 25_000
    # Income above spending: the cushion is the floor alone.
    rich = [_source("Pension", IncomeType.PENSION, 12_000)]
    _, surplus = await _run(_engine(10_000, _ca(taxable=0, cash_cushion_months=6,
                                                cash_cushion_floor=25_000), sources=rich))
    assert surplus[0].cash_cushion == 25_000


@pytest.mark.asyncio
async def test_floor_is_ignored_without_a_cushion(frozen_today):
    r, pts = await _run(_engine(10_000, _ca(taxable=500_000, cash_cushion_floor=25_000)))
    assert r.cash_rule.mode == "threshold" and all(p.cash_cushion == 0 for p in r.points)


@pytest.mark.asyncio
async def test_cash_above_the_cushion_is_invested(frozen_today):
    events = [_event("Payout", "income", 150_000, date(2026, 7, 15))]  # month 3
    engine = _engine(200_000, _ca(taxable=0, cash_cushion_months=6, invest_cash_above_cushion=True), events)
    r, pts = await _run(engine)
    assert r.cash_rule.invest_above_cushion is True
    assert pts[0].cash_invested == 130_000 and pts[0].cash == 60_000 and pts[0].taxable == 130_000
    assert pts[1].taxable_draw == 10_000 and pts[1].cash == 60_000
    assert pts[3].cash_invested == 140_000 and pts[3].cash == 60_000  # payout less the month's $10K
    # The brokerage ($250K after month 3) holds cash on the cushion until it runs dry.
    assert all(pts[m].cash == 60_000 for m in range(29))
    assert pts[28].taxable == 0 and pts[29].cash == 50_000


@pytest.mark.asyncio
async def test_invest_without_a_cushion_does_nothing(frozen_today):
    r, pts = await _run(_engine(200_000, _ca(taxable=0, invest_cash_above_cushion=True)))
    assert all(p.cash_invested == 0 for p in r.points)


def _rich_config(**projection):
    config = _make_fire_config()
    _apply_scenario(config, deepcopy(SCENARIO_SELL_ALL_THREE))
    config.custom_assumptions["roth_pool"] = {"starting_balance": 80_000, "return_rate": 0.05}
    config.custom_assumptions["projection"] = {**config.custom_assumptions["projection"], **projection}
    return config


@pytest.mark.parametrize("projection", [
    {},
    {"cash_cushion_months": 6},
    {"cash_cushion_months": 6, "cash_cushion_floor": 25_000, "invest_cash_above_cushion": True},
], ids=["threshold", "cushion", "cushion+floor+invest"])
@pytest.mark.asyncio
async def test_money_identity_every_month(
    projection, frozen_today, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources,
):
    """Spending + change in cash (+ money invested) = income + Social Security +
    one-time money + withdrawals, every month — the identity the Retirement
    page's money-sources chart draws. Fields are rounded to dollars, so allow $6."""
    engine = _make_engine(_rich_config(**projection), net_worth_breakdown, mock_accounts,
                          mock_cashflow_events, mock_income_sources)
    r = await engine.project_wealth_pools(end_age=95, bridge_months=12 * 60)
    prev = r.starting_cash
    assert any(p.one_time_income > 0 for p in r.points) and any(p.ss_income > 0 for p in r.points)
    for p in r.points:
        d_cash = p.cash - prev
        prev = p.cash
        spending = p.expenses + p.event_expense
        regular = p.income - p.ss_income - p.event_income + p.event_expense
        recurring_events = p.event_income - p.one_time_income
        withdrawals = (p.ira_draw - p.rmd_redeposit) + p.roth_draw + p.rrsp_draw + p.taxable_draw
        lhs = spending + d_cash + p.cash_invested
        rhs = regular + recurring_events + p.cash_interest + p.ss_income + p.one_time_income + withdrawals
        assert lhs == pytest.approx(rhs, abs=6), f"month {p.month}: {lhs} vs {rhs}"
        assert p.sepp_draw <= p.ira_draw + 1


@pytest.mark.asyncio
async def test_milestones_are_computed_from_the_plan(
    frozen_today, net_worth_breakdown, mock_accounts, mock_cashflow_events, mock_income_sources,
):
    engine = _make_engine(_rich_config(), net_worth_breakdown, mock_accounts,
                          mock_cashflow_events, mock_income_sources)
    r = await engine.project_wealth_pools(end_age=82, bridge_months=12 * 60)
    by_label = {m.label: m for m in r.milestones}
    sales = [m for m in r.milestones if m.kind == "sale"]
    assert [m.label for m in sales] == ["Coastal Condo sold", "Mountain House sold", "River House sold"]
    assert [m.month for m in sales] == [13, 25, 39]
    assert all(m.amount > 0 and m.destination == "brokerage" for m in sales)
    assert by_label["72(t) payments start"].month == 12
    assert by_label["Social Security starts"].amount == 4_650
    assert by_label["Medicare at 65"].amount == -600
    assert "Spending steps to 85%" in by_label and "Required IRA withdrawals start" in by_label
    # One-off events of $5K or more, under their own names, probability-weighted.
    assert by_label["Overseas Payout"].amount == 57_000
    assert "Money runs out" not in by_label
    assert [m.month for m in r.milestones] == sorted(m.month for m in r.milestones)


@pytest.mark.asyncio
async def test_running_out_is_marked(frozen_today):
    r, pts = await _run(_engine(50_000, _ca(taxable=40_000)), months=400)
    assert r.money_runs_out_month is not None
    assert r.points[-1].month == r.money_runs_out_month
    assert r.milestones[-1].kind == "warning" and r.milestones[-1].month == r.money_runs_out_month


@pytest.mark.asyncio
async def test_assumptions_describe_the_effective_config(frozen_today):
    r, _ = await _run(_engine(10_000, {**_ca(taxable=500_000), "taxable_pool": {"starting_balance": 500_000}}))
    a = {x.key: x for x in r.assumptions}
    assert a["real_terms"].kind == "rule"
    assert a["cash_rule"].label == "When savings start paying" and a["cash_rule"].value == "below 12 months"
    assert a["brokerage_growth"].value == "6.5% a year" and a["brokerage_growth"].is_default
    assert a["withdrawal_taxes"].kind == a["work_income"].kind == "limit"
    assert "home_values" not in a and "rental_occupancy" not in a and "uncertain_money" not in a

    r, _ = await _run(_engine(10_000, _ca(taxable=500_000, ira_b=100_000, cash_cushion_months=6,
                                          cash_cushion_floor=25_000, invest_cash_above_cushion=True)))
    a = {x.key: x for x in r.assumptions}
    assert a["cash_rule"].label == "Cash cushion"
    assert a["cash_rule"].value == "6 months, $25,000 floor"
    assert "never less than $25,000" in a["cash_rule"].detail
    assert "the brokerage, then the IRAs after 59½" in a["cash_rule"].detail
    assert a["cash_rule"].detail.endswith("Cash above the cushion is invested in the brokerage.")
    assert a["brokerage_growth"].value == "0% a year" and not a["brokerage_growth"].is_default


@pytest.mark.asyncio
async def test_base_only_skips_the_active_scenario():
    engine = FireProjectionsEngine(AsyncMock())
    base = _make_fire_config()
    engine.get_or_create_config = AsyncMock(return_value=base)
    engine.db.execute = AsyncMock(side_effect=AssertionError("must not look up the active scenario"))
    config = await engine.get_effective_config(base_only=True)
    assert config is not base and config.custom_assumptions == base.custom_assumptions
