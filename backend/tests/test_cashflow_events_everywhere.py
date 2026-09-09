"""Cashflow events reach EVERY projection path (fire-master#16, #17).

Before Sep 2026 only project_wealth_pools read CashflowEvent. Monte Carlo,
project_lifetime (→ /timeline on_track), compute_bridge_status (→ the runway
KPI card) and compute_readiness (income_stability) derived income from
IncomeSource rows alone, so a plan expressed as dated events — the only way
to model two earners claiming SS at different ages — simulated a household
with no income at all and reported a confident number.

All four now consume the same build_cashflow_schedule() /
_recurring_events_active_now() helpers. These tests pin:
- the schedule expansion rules (calendar offsets, recurrence phase, inclusive
  end month, past one-offs dropped, skip predicate);
- kirvin's reproduction: an events-only plan in Monte Carlo at zero
  volatility must match the same plan expressed as an IncomeSource;
- project_lifetime uses the declared plan (no trailing-income fallback when
  events exist) and skips conversion events it already holds at book value;
- bridge status counts recurring events active this month;
- readiness income_stability is 100 for a monthly-event plan and neutral (50)
  when nothing is declared.
"""

from contextlib import ExitStack
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engines.fire_projections import (
    FireProjectionsEngine,
    build_cashflow_schedule,
    cashflow_by_year,
)
from app.engines.monte_carlo import MonteCarloEngine
from app.engines.net_worth import NetWorthEngine
from app.models.cashflow_event import CashflowEvent
from app.models.enums import IncomeType
from app.models.income_source import IncomeSource
from app.schemas.fire import NetWorthBreakdown

from .conftest import FROZEN_TODAY, _make_fire_config
from .test_monte_carlo import _mc_env, frozen_today_mc  # noqa: F401 (fixture)


def _event(name, etype, cents, dt, *, prob=1.0, recurring=False, recurrence=None, end=None):
    e = MagicMock(spec=CashflowEvent)
    e.name, e.event_type, e.amount_cents, e.date = name, etype, cents, dt
    e.probability, e.is_recurring, e.recurrence, e.end_date = prob, recurring, recurrence, end
    e.status = "planned"
    return e


def _source(name, itype, annual_cents, *, start=None, end=None):
    s = MagicMock(spec=IncomeSource)
    s.name, s.income_type, s.annual_amount = name, itype, annual_cents
    s.start_date, s.end_date, s.is_active, s.growth_rate = start, end, True, None
    return s


# ---------------------------------------------------------------------------
# build_cashflow_schedule
# ---------------------------------------------------------------------------

class TestSchedule:
    def test_monthly_recurring_is_end_inclusive_and_labelled_once(self):
        ev = _event("Unemployment", "income", 120_000, date(2026, 5, 1),
                    prob=0.9, recurring=True, recurrence="monthly", end=date(2026, 7, 31))
        by_month, labels = build_cashflow_schedule([ev], FROZEN_TODAY, 120)
        # May, Jun, Jul (Jul 31 end → July still fires)
        assert sorted(by_month) == [1, 2, 3]
        assert by_month[1] == [("Unemployment", pytest.approx(1_080.0))]
        assert labels == {1: ["Unemployment"]}

    def test_quarterly_keeps_phase_from_a_past_start(self):
        # Started Jan 2026, quarterly → Jan, Apr, Jul, Oct ... only Apr onward remain
        ev = _event("HOA", "expense", 90_000, date(2026, 1, 15),
                    recurring=True, recurrence="quarterly")
        by_month, labels = build_cashflow_schedule([ev], FROZEN_TODAY, 13)
        assert sorted(by_month) == [0, 3, 6, 9, 12]
        assert by_month[0] == [("HOA", -900.0)]
        assert labels == {0: ["HOA"]}

    def test_annual_recurring(self):
        ev = _event("Property tax", "expense", 1_200_000, date(2026, 11, 1),
                    recurring=True, recurrence="annual")
        by_month, _ = build_cashflow_schedule([ev], FROZEN_TODAY, 40)
        assert sorted(by_month) == [7, 19, 31]

    def test_past_oneoff_dropped_future_oneoff_calendar_offset(self):
        past = _event("Severance", "income", 3_000_000, date(2026, 3, 31))
        soon = _event("Refund", "income", 100_000, date(2026, 5, 1))  # 17 days out → NEXT month
        by_month, _ = build_cashflow_schedule([past, soon], FROZEN_TODAY, 120)
        assert list(by_month) == [1]

    def test_skip_predicate_removes_money_and_label(self):
        sale = _event("Mountain House Sale Proceeds", "income", 10_000_000, date(2026, 10, 15))
        keep = _event("Bonus", "income", 100_000, date(2026, 10, 15))
        by_month, labels = build_cashflow_schedule(
            [sale, keep], FROZEN_TODAY, 120, skip=lambda cf: "sale" in cf.name.lower())
        assert by_month[6] == [("Bonus", 1_000.0)]
        assert labels[6] == ["Bonus"]

    def test_cashflow_by_year_nets_signed_flows(self):
        inc = _event("Rent", "income", 100_000, date(2026, 5, 1), recurring=True, recurrence="monthly")
        exp = _event("Tuition", "expense", 500_000, date(2027, 1, 1))
        by_month, _ = build_cashflow_schedule([inc, exp], FROZEN_TODAY, 24)
        years = cashflow_by_year(by_month, 2)
        # Year 0 = months 0..11 (Apr 2026..Mar 2027): rent May..Mar (11×) − Jan tuition
        assert years[0] == pytest.approx(11 * 1_000.0 - 5_000.0)
        assert years[1] == pytest.approx(12 * 1_000.0)


# ---------------------------------------------------------------------------
# Monte Carlo — kirvin's reproduction (#16)
# ---------------------------------------------------------------------------

class TestMonteCarloEvents:
    @staticmethod
    def _cfg():
        return _make_fire_config(
            social_security_monthly=0,
            custom_assumptions={"monte_carlo": {"return_std": 0.0, "inflation_std": 0.0}},
        )

    async def test_events_only_plan_matches_equivalent_source(self, frozen_today_mc):
        """$5,000/mo from a monthly income event == a $60K/yr rental source
        (rental survives retirement in _income_at_month). Zero vol → the
        deterministic paths must be identical, not 'no income at all'."""
        engine = MonteCarloEngine(db=None)
        ev = _event("Spouse pension", "income", 500_000, date(2026, 4, 1),
                    recurring=True, recurrence="monthly")
        src = _source("Rental", IncomeType.RENTAL, 6_000_000)
        kw = dict(net_worth=500_000.0, spending_cents=6_000_000)  # $60K/yr, covered by the income
        with _mc_env(self._cfg(), events=[ev], **kw):
            r_events = await engine.run_simulation(n_runs=5, seed=1)
        with _mc_env(self._cfg(), income_sources=[src], **kw):
            r_source = await engine.run_simulation(n_runs=5, seed=1)
        with _mc_env(self._cfg(), **kw):
            r_nothing = await engine.run_simulation(n_runs=5, seed=1)

        assert r_events.percentile_50 == pytest.approx(r_source.percentile_50, rel=1e-6)
        assert r_events.success_rate == r_source.success_rate == 100.0
        assert r_nothing.success_rate == 0.0  # the pre-fix answer for the event plan
        assert "events applied" in r_events.assumptions["cashflow_events"]

    async def test_conversion_events_are_skipped(self, frozen_today_mc):
        """A vest / sale event converts an asset already in net worth — a
        single-pool model must not add it on top."""
        engine = MonteCarloEngine(db=None)
        vest = _event("Startup A vests", "income", 50_000_000, date(2028, 4, 1))
        with _mc_env(self._cfg(), net_worth=1_000_000.0, events=[vest]):
            r_vest = await engine.run_simulation(n_runs=5, seed=1)
        with _mc_env(self._cfg(), net_worth=1_000_000.0):
            r_none = await engine.run_simulation(n_runs=5, seed=1)
        assert r_vest.percentile_50 == r_none.percentile_50


# ---------------------------------------------------------------------------
# project_lifetime — /timeline on_track (#17)
# ---------------------------------------------------------------------------

class TestLifetimeEvents:
    @staticmethod
    def _run(config, *, events=(), sources=(), income_cents=0, net_worth=1_000_000.0):
        engine = FireProjectionsEngine(db=None)
        patches = [
            patch.object(FireProjectionsEngine, "get_effective_config", AsyncMock(return_value=config)),
            patch.object(FireProjectionsEngine, "_get_annual_spending", AsyncMock(return_value=12_000_000)),
            patch.object(FireProjectionsEngine, "_get_annual_income", AsyncMock(return_value=income_cents)),
            patch.object(FireProjectionsEngine, "_get_income_sources", AsyncMock(return_value=list(sources))),
            patch.object(FireProjectionsEngine, "_get_cashflow_events", AsyncMock(return_value=list(events))),
            patch.object(NetWorthEngine, "calculate_current", AsyncMock(return_value=MagicMock(net_worth=net_worth))),
        ]
        return engine, patches

    async def _lifetime(self, config, **kw):
        engine, patches = self._run(config, **kw)
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return await engine.project_lifetime("moderate")

    async def test_event_income_flows_into_projection(self, frozen_today):
        cfg = _make_fire_config(social_security_monthly=0, healthcare_monthly_cost=None)
        ev = _event("Spouse SS", "income", 300_000, date(2030, 1, 1), recurring=True, recurrence="monthly")
        with_ev = await self._lifetime(cfg, events=[ev])
        without = await self._lifetime(cfg)
        assert with_ev.net_worth_at_end > without.net_worth_at_end
        late = [p for p in with_ev.points if p.age >= 60]
        assert late and all(p.income == pytest.approx(36_000, abs=1) for p in late)

    async def test_events_replace_trailing_income_fallback(self, frozen_today):
        """With no IncomeSource rows the engine used to hold trailing
        transaction income flat until retirement. An event-based plan is a
        declared plan: the fallback must not be layered on top of it."""
        cfg = _make_fire_config(social_security_monthly=0, healthcare_monthly_cost=None,
                                target_retirement_age=60)
        ev = _event("Consulting", "income", 200_000, date(2026, 5, 1), recurring=True,
                    recurrence="monthly", end=date(2027, 4, 30))
        r = await self._lifetime(cfg, events=[ev], income_cents=30_000_000)
        first = r.points[0]
        assert first.phase == "accumulation"
        assert first.income == 0  # Apr 2026: event starts in May; NO $300K trailing income

    async def test_expense_event_raises_spending(self, frozen_today):
        cfg = _make_fire_config(social_security_monthly=0, healthcare_monthly_cost=None)
        ev = _event("Tuition", "expense", 2_400_000, date(2026, 4, 1), recurring=True,
                    recurrence="monthly", end=date(2027, 3, 31))
        r = await self._lifetime(cfg, events=[ev])
        assert r.points[0].spending == pytest.approx(120_000 + 24_000 * 12, abs=1)

    async def test_conversion_event_skipped_via_matchers(self, frozen_today):
        cfg = _make_fire_config(social_security_monthly=0, healthcare_monthly_cost=None)
        cfg.custom_assumptions = {"projection": {"sell_event_label_match": "mountain house"}}
        sale = _event("Mountain House Sale Proceeds", "income", 50_000_000, date(2026, 10, 15))
        r_sale = await self._lifetime(cfg, events=[sale])
        r_none = await self._lifetime(cfg)
        assert r_sale.net_worth_at_end == r_none.net_worth_at_end
        # An EXPENSE carrying the matched label is real money out — not skipped
        hoa = _event("Mountain House Special Assessment", "expense", 500_000, date(2026, 10, 15))
        r_hoa = await self._lifetime(cfg, events=[hoa])
        assert r_hoa.net_worth_at_end < r_none.net_worth_at_end


# ---------------------------------------------------------------------------
# compute_bridge_status — runway KPI card (#17)
# ---------------------------------------------------------------------------

class TestBridgeEvents:
    @staticmethod
    async def _bridge(events, sources=()):
        db = AsyncMock()
        upcoming = MagicMock()
        upcoming.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=upcoming)
        engine = FireProjectionsEngine(db)
        cfg = _make_fire_config(target_annual_spending=12_000_000)  # $10K/mo burn
        bd = NetWorthBreakdown(liquid=60_000, retirement=0, real_estate_equity=0,
                               illiquid_private=0, other=0)  # $60K cash (dollars)
        with ExitStack() as stack:
            stack.enter_context(patch.object(FireProjectionsEngine, "get_effective_config", AsyncMock(return_value=cfg)))
            stack.enter_context(patch.object(FireProjectionsEngine, "_compute_net_worth_breakdown", AsyncMock(return_value=bd)))
            stack.enter_context(patch.object(FireProjectionsEngine, "_get_income_sources", AsyncMock(return_value=list(sources))))
            stack.enter_context(patch.object(FireProjectionsEngine, "_get_cashflow_events", AsyncMock(return_value=list(events))))
            stack.enter_context(patch.object(FireProjectionsEngine, "_get_annual_spending", AsyncMock(return_value=12_000_000)))
            return await engine.compute_bridge_status()

    async def test_active_recurring_income_event_extends_runway(self, frozen_today):
        ev = _event("Spouse salary", "income", 600_000, date(2026, 1, 1), recurring=True, recurrence="monthly")
        r = await self._bridge([ev])
        assert [s.label for s in r.income_streams] == ["Spouse salary"]
        assert r.monthly_income_total == 6_000
        assert r.monthly_deficit == 4_000
        assert r.cash_runway_months == 15  # 60K / 4K — was 6 (full burn) before

    async def test_future_and_ended_recurring_events_do_not_count_now(self, frozen_today):
        future = _event("SS", "income", 250_000, date(2029, 1, 1), recurring=True, recurrence="monthly")
        ended = _event("UI", "income", 120_000, date(2025, 6, 1), recurring=True,
                       recurrence="monthly", end=date(2026, 3, 31))
        r = await self._bridge([future, ended])
        assert r.income_streams == []
        assert r.cash_runway_months == 6

    async def test_recurring_expense_event_adds_to_burn_temp_flagged(self, frozen_today):
        cobra = _event("COBRA", "expense", 200_000, date(2026, 4, 1), recurring=True,
                       recurrence="monthly", end=date(2027, 3, 31))
        temp = _event("Consulting", "income", 300_000, date(2026, 2, 1), recurring=True,
                      recurrence="monthly", end=date(2026, 12, 31))
        quarterly = _event("Dividends", "income", 900_000, date(2026, 1, 1), recurring=True, recurrence="quarterly")
        r = await self._bridge([cobra, temp, quarterly])
        assert r.monthly_burn == 12_000
        labels = {s.label: s.monthly for s in r.income_streams}
        assert labels == {"Consulting (temp)": 3_000, "Dividends": 3_000}
        # Runway ignores temp income: (12K − 3K ongoing) = 9K deficit
        assert r.monthly_deficit == 9_000


# ---------------------------------------------------------------------------
# compute_readiness — income_stability (#17)
# ---------------------------------------------------------------------------

class TestReadinessEvents:
    @staticmethod
    async def _readiness(events, sources=()):
        db = AsyncMock()
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=empty)  # emergency accounts, goals
        engine = FireProjectionsEngine(db)
        cfg = _make_fire_config()
        fire_num = MagicMock(progress_pct=50.0, fire_number=1.0, current_net_worth=1.0)
        savings = MagicMock(average_rate=20.0, points=[])
        from app.engines.spending import SpendingEngine
        with ExitStack() as stack:
            stack.enter_context(patch.object(FireProjectionsEngine, "compute_fire_number", AsyncMock(return_value=fire_num)))
            stack.enter_context(patch.object(FireProjectionsEngine, "get_effective_config", AsyncMock(return_value=cfg)))
            stack.enter_context(patch.object(FireProjectionsEngine, "_get_income_sources", AsyncMock(return_value=list(sources))))
            stack.enter_context(patch.object(FireProjectionsEngine, "_get_cashflow_events", AsyncMock(return_value=list(events))))
            stack.enter_context(patch.object(SpendingEngine, "get_savings_rate", AsyncMock(return_value=savings)))
            return await engine.compute_readiness()

    async def test_event_based_plan_scores_stable(self, frozen_today):
        ev = _event("Spouse salary", "income", 600_000, date(2026, 1, 1), recurring=True, recurrence="monthly")
        r = await self._readiness([ev])
        assert r.breakdown.income_stability == 100.0

    async def test_nothing_declared_is_neutral_not_zero(self, frozen_today):
        r = await self._readiness([])
        assert r.breakdown.income_stability == 50.0

    async def test_mixed_sources_and_events(self, frozen_today):
        side = _source("Gigs", IncomeType.SIDE_HUSTLE, 3_600_000)  # unstable $36K
        ev = _event("Rent", "income", 300_000, date(2026, 1, 1), recurring=True, recurrence="monthly")  # stable $36K
        r = await self._readiness([ev], sources=[side])
        assert r.breakdown.income_stability == 50.0
