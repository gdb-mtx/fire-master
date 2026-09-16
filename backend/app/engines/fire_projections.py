"""FIRE Projections Engine — lifetime projections, scenario modeling, readiness scoring.

EVERY projection in this module is REAL-TERMS (today's dollars): spending is
flat (constant purchasing power), SS/pension are flat (COLA offsets
inflation), and balances compound at real (after-inflation) rates. Nominal
config inputs (expected_annual_return / expected_inflation_rate) are deflated
at the point of use: real = (1 + nominal) / (1 + inflation) − 1.

Two modeling STRUCTURES remain (do not copy mechanics between them):
- project_lifetime(): single-pool net-worth simulation. Used for the
  3-scenario timeline and what-if comparisons. (Was nominal until Jul 2026.)
- project_wealth_pools(): pool-aware model (cash / taxable / IRA-A / IRA-B /
  RRSP / real estate). The trusted engine for the retirement dashboard,
  bridge chart, and scenario comparisons.
"""

import logging
import uuid as uuid_mod
from copy import deepcopy
from datetime import date, timedelta
from typing import Callable
from dateutil.relativedelta import relativedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.cashflow_event import CashflowEvent
from app.models.fire_config import FireConfig
from app.models.fire_scenario import FireScenario
from app.models.goal import Goal
from app.models.income_source import IncomeSource
from app.models.net_worth_snapshot import NetWorthSnapshot
from app.engines.net_worth import NetWorthEngine
from app.engines.tax_engine import _get_rmd_divisor
from app.engines.spending import SpendingEngine
from app.schemas.fire import (
    BridgeStatus,
    FireNumberResponse,
    IncomeStream,
    LifetimeProjectionResponse,
    Milestone,
    MilestonesResponse,
    NetWorthBreakdown,
    ProjectionPoint,
    ReadinessBreakdown,
    ReadinessResponse,
    RetirementTimelineResponse,
    ScenarioComparison,
    ScenarioInput,
    UpcomingEvent,
    WealthPoolPoint,
    WealthPoolProjection,
)

logger = logging.getLogger(__name__)


def _cents_to_dollars(cents: int) -> float:
    return cents / 100


def fmtCompact(value: float) -> str:
    """Format dollar amount compactly: $1.2M, $340K, $5.2K."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _dollars_to_cents(dollars: float) -> int:
    return int(round(dollars * 100))


# Retirement spending phases (go-go / slow-go / no-go)
# Based on Blanchett (2013): real spending declines ~1-2%/yr in retirement
SPENDING_PHASES = [
    (70, 1.00),   # Active early retirement: full spending
    (80, 0.85),   # Slower pace: 85% of base
]
SPENDING_FLOOR = 0.75  # Age 80+: 75% of base

# fire_role → net worth category mapping
# speculative (crypto etc.) is deliberately NOT liquid (decided Jul 27, 2026):
# volatile assets aren't bridge fuel. It counts in net worth ("other" bucket via
# the breakdown's fallthrough) but is inert in projections — same as illiquid.
LIQUID_ROLES = {"cash_reserve", "operating_account"}
SPECULATIVE_ROLES = {"speculative"}
RETIREMENT_ROLES = {"retirement_core", "retirement_bridge", "retirement_supplemental", "tax_free_reserve"}
RE_ASSET_ROLES = {"primary_residence", "sell_candidate", "income_producing"}
RE_LIABILITY_ROLES = {"primary_mortgage", "sell_with_property"}
ILLIQUID_ROLES = {"illiquid_private"}


def _spending_multiplier(age: float) -> float:
    """Spending multiplier based on retirement phase (go-go/slow-go/no-go)."""
    for threshold, mult in SPENDING_PHASES:
        if age < threshold:
            return mult
    return SPENDING_FLOOR



def savings_rate_component(rate: float | None) -> float:
    """Readiness sub-score (0-100) for a savings rate (%). 30%+ is full marks.

    Takes the dollar-weighted AVERAGE over the window, not the latest month: one
    miscategorized or partial month must not dominate the score. Clamped on both
    sides — fire-master#11 saw -693,000 on the 0-100 scale from an un-floored
    single-month point.
    """
    if rate is None:
        return 0.0
    return max(0.0, min(100.0, rate / 30 * 100))


_RECURRENCE_STEP_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def build_cashflow_schedule(
    events,
    today: date,
    total_months: int,
    skip: "Callable[[CashflowEvent], bool] | None" = None,
) -> tuple[dict[int, list[tuple[str, float]]], dict[int, list[str]]]:
    """Expand CashflowEvent rows into a month-offset → [(label, signed $)] map.

    THE one place cashflow events become projection flows. Every engine that
    models the plan forward (project_wealth_pools, project_lifetime, Monte
    Carlo) consumes this so they cannot drift apart again (fire-master#16/#17:
    only project_wealth_pools read events; the other three simulated a
    zero-income household for an event-based plan).

    Returns (by_month, labels_by_month):
      by_month drives the money math for every month an event fires;
      labels_by_month tags label-worthy occurrences only (one-offs + the first
      remaining occurrence of a recurring) so chart markers aren't stamped on
      every month a recurring event is active.

    Rules (shared with the Runway page's expansion in engines/cashflow.py):
    - Amount = amount_cents/100 × probability, signed (+income / −expense).
    - CALENDAR-month offsets (an Aug-12 event 16 days out is "next month",
      never "this month").
    - Recurring: monthly / quarterly / annual, in phase with the start date;
      occurrences before today are dropped (only remaining ones count);
      end_date month is INCLUSIVE (an event ending Jul 31 still fires in July).
    - One-off dated before today already happened — dropped, not clamped to
      month 0 (that double-counted a finished severance).
    - `skip(event)` filters events another mechanism owns (e.g. a generic
      property sale that replaces its legacy "… Sale Proceeds" event, or a
      conversion event a single-pool model must not add on top of net worth).
    """
    by_month: dict[int, list[tuple[str, float]]] = {}
    labels_by_month: dict[int, list[str]] = {}
    for cf in events:
        if skip is not None and skip(cf):
            continue
        sign = 1.0 if cf.event_type == "income" else -1.0
        amount = cf.amount_cents / 100.0 * sign * (cf.probability if cf.probability is not None else 1.0)
        raw_offset = (cf.date.year - today.year) * 12 + (cf.date.month - today.month)

        if cf.is_recurring:
            step = _RECURRENCE_STEP_MONTHS.get(cf.recurrence or "monthly", 1)
            last_offset = total_months - 1
            if cf.end_date:
                cal_end = (cf.end_date.year - today.year) * 12 + (cf.end_date.month - today.month)
                last_offset = min(last_offset, cal_end)
            if last_offset < 0:
                continue  # recurring window is entirely in the past
            first = True
            for mo in range(raw_offset, last_offset + 1, step):
                if mo < 0:
                    continue
                by_month.setdefault(mo, []).append((cf.name, amount))
                if first:
                    labels_by_month.setdefault(mo, []).append(cf.name)
                    first = False
        else:
            if raw_offset < 0 or raw_offset >= total_months:
                continue
            by_month.setdefault(raw_offset, []).append((cf.name, amount))
            labels_by_month.setdefault(raw_offset, []).append(cf.name)
    return by_month, labels_by_month


def cashflow_by_year(by_month: dict[int, list[tuple[str, float]]], total_years: int) -> list[float]:
    """Collapse a month-offset schedule into net signed dollars per year."""
    out = [0.0] * total_years
    for mo, entries in by_month.items():
        yr = mo // 12
        if 0 <= yr < total_years:
            out[yr] += sum(a for _, a in entries)
    return out


class FireProjectionsEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_config(self) -> FireConfig:
        """Get the FIRE config (single-row), creating a default if none exists.

        Returns the BASE config — used by API routes for config CRUD.
        For projections, use get_effective_config() which merges active scenario.
        """
        result = await self.db.execute(select(FireConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is None:
            config = FireConfig()
            self.db.add(config)
            await self.db.flush()
        return config

    async def get_effective_config(
        self, scenario_id: uuid_mod.UUID | None = None
    ) -> FireConfig:
        """Get FIRE config merged with the active scenario (or a specific one).

        If scenario_id is provided, use that scenario.
        Otherwise, check for an active scenario.
        If no scenario, returns the base config unchanged.

        Returns a DETACHED copy — safe to read, never written back to DB.
        """
        config = await self.get_or_create_config()

        # Find the scenario to apply
        scenario = None
        if scenario_id is not None:
            result = await self.db.execute(
                select(FireScenario).where(FireScenario.id == scenario_id)
            )
            scenario = result.scalar_one_or_none()
        else:
            result = await self.db.execute(
                select(FireScenario).where(FireScenario.is_active == True).limit(1)
            )
            scenario = result.scalar_one_or_none()

        if scenario and scenario.overrides:
            return self._apply_overrides(config, scenario.overrides)
        return config

    @staticmethod
    def _apply_overrides(config: FireConfig, overrides: dict) -> FireConfig:
        """Create a detached copy of config with scenario overrides applied.

        Top-level keys override fire_config columns directly.
        The "custom_assumptions" key is deep-merged (scenario subkeys override
        or add to base subkeys; base subkeys not in scenario are preserved).

        Does NOT modify the original config object.
        """
        merged = FireConfig()
        # Copy all column values from original
        for col in FireConfig.__table__.columns:
            setattr(merged, col.name, getattr(config, col.name))

        # Apply top-level column overrides
        for key, value in overrides.items():
            if key == "custom_assumptions":
                continue  # handled separately below
            if hasattr(merged, key):
                setattr(merged, key, value)

        # Deep-merge custom_assumptions
        base_ca = deepcopy(config.custom_assumptions or {})
        override_ca = overrides.get("custom_assumptions", {})
        for subkey, subval in override_ca.items():
            if isinstance(subval, dict) and isinstance(base_ca.get(subkey), dict):
                base_ca[subkey] = {**base_ca[subkey], **subval}
            else:
                base_ca[subkey] = subval
        merged.custom_assumptions = base_ca

        return merged

    async def _compute_net_worth_breakdown(self) -> NetWorthBreakdown:
        """Partition account balances by fire_role into wealth categories."""
        result = await self.db.execute(
            select(Account).where(Account.include_in_net_worth == True)
        )
        accounts = result.scalars().all()

        liquid = 0
        retirement = 0
        re_equity = 0
        illiquid = 0
        other = 0

        for acct in accounts:
            bal = acct.current_balance if acct.is_asset else -acct.current_balance
            role = (acct.fire_role or "").lower().strip()

            if role in LIQUID_ROLES:
                liquid += bal
            elif role in RETIREMENT_ROLES:
                retirement += bal
            elif role in RE_ASSET_ROLES or role in RE_LIABILITY_ROLES:
                re_equity += bal
            elif role in ILLIQUID_ROLES:
                illiquid += bal
            elif role == "system":
                continue  # exclude system accounts (e.g., Net Worth History)
            else:
                other += bal

        return NetWorthBreakdown(
            liquid=_cents_to_dollars(liquid),
            retirement=_cents_to_dollars(retirement),
            real_estate_equity=_cents_to_dollars(re_equity),
            illiquid_private=_cents_to_dollars(illiquid),
            other=_cents_to_dollars(other),
        )

    async def compute_fire_number(self) -> FireNumberResponse:
        """Compute FIRE number using draw-down-to-target-legacy strategy.

        Year-by-year PV of net withdrawals, accounting for:
        - SS/pension starting at specific ages (not day 1 of retirement)
        - Spending phases (go-go / slow-go / no-go reduction with age)
        - Target legacy as terminal value

        Uses geometric mean return (volatility-adjusted) to match Monte Carlo
        median outcomes: geo_return ≈ arithmetic_return - σ²/2.
        """
        from app.engines.monte_carlo import DEFAULT_RETURN_STD

        config = await self.get_effective_config()
        nw_engine = NetWorthEngine(self.db)
        nw = await nw_engine.calculate_current()

        annual_spending_cents = await self._get_annual_spending(config)
        income_sources = await self._get_income_sources()

        # Real return rate with volatility drag (geometric mean)
        arithmetic_real = (config.expected_annual_return - config.expected_inflation_rate) / 100.0
        volatility_drag = (DEFAULT_RETURN_STD ** 2) / 2
        real_return = arithmetic_real - volatility_drag

        # Years in retirement
        retirement_age = config.target_retirement_age or 55
        years_in_retirement = config.life_expectancy - retirement_age

        if years_in_retirement <= 0 or real_return <= 0:
            fire_number_cents = annual_spending_cents * max(1, years_in_retirement)
        else:
            # Income start ages (only count if configured)
            ss_start_age = config.social_security_start_age if config.social_security_monthly else None
            ss_annual = (config.social_security_monthly * 12) if config.social_security_monthly else 0

            pension_start_age = config.pension_start_age if config.pension_monthly else None
            pension_annual = (config.pension_monthly * 12) if config.pension_monthly else 0

            # Continuing income from sources (rental, dividends, etc.)
            # Only count sources WITHOUT an end_date — they persist through retirement.
            # Sources with end_date (severance, unemployment, temp rental) are temporary.
            continuing_annual = sum(
                s.annual_amount for s in income_sources
                if s.income_type.value not in ("salary", "bonus", "side_hustle")
                and s.end_date is None
            )

            # Year-by-year PV of net withdrawals
            fire_number_cents = 0
            for yr in range(years_in_retirement):
                age = retirement_age + yr

                # Spending adjusted for retirement phase
                yr_spending = annual_spending_cents * _spending_multiplier(age)

                # Post-retirement income at this age
                yr_income = continuing_annual
                if ss_start_age and age >= ss_start_age:
                    yr_income += ss_annual
                if pension_start_age and age >= pension_start_age:
                    yr_income += pension_annual

                net_withdrawal = max(0, yr_spending - yr_income)

                # Discount to retirement date (geometric real return)
                fire_number_cents += int(net_withdrawal / ((1 + real_return) ** yr))

            # PV of target legacy
            legacy_cents = config.target_legacy or 0
            if legacy_cents > 0:
                fire_number_cents += int(legacy_cents / ((1 + real_return) ** years_in_retirement))

        current_nw_cents = _dollars_to_cents(nw.net_worth)
        gap_cents = fire_number_cents - current_nw_cents
        progress = (current_nw_cents / fire_number_cents * 100) if fire_number_cents > 0 else 100.0

        # Compute accessible net worth (liquid + retirement only)
        breakdown = await self._compute_net_worth_breakdown()
        accessible = breakdown.liquid + breakdown.retirement
        accessible_gap = _cents_to_dollars(fire_number_cents) - accessible
        accessible_progress = (accessible / _cents_to_dollars(fire_number_cents) * 100) if fire_number_cents > 0 else 100.0

        return FireNumberResponse(
            fire_number=_cents_to_dollars(fire_number_cents),
            annual_spending=_cents_to_dollars(annual_spending_cents),
            safe_withdrawal_rate=config.safe_withdrawal_rate,
            current_net_worth=nw.net_worth,
            gap=_cents_to_dollars(gap_cents),
            progress_pct=round(progress, 1),
            accessible_net_worth=round(accessible, 2),
            accessible_progress_pct=round(accessible_progress, 1),
            net_worth_breakdown=breakdown,
        )

    async def _get_annual_spending(self, config: FireConfig) -> int:
        """Get annual spending: from config override or trailing 12-month actual."""
        if config.target_annual_spending:
            return config.target_annual_spending

        spending_engine = SpendingEngine(self.db)
        savings_data = await spending_engine.get_savings_rate(months=12)

        if not savings_data.points:
            return 0

        total_spending_dollars = sum(p.spending for p in savings_data.points)
        months_count = len(savings_data.points)
        if months_count < 12:
            total_spending_dollars = total_spending_dollars / months_count * 12

        return _dollars_to_cents(total_spending_dollars)

    async def _get_annual_income(self) -> int:
        """Get annual income from trailing 12-month transaction data."""
        spending_engine = SpendingEngine(self.db)
        savings_data = await spending_engine.get_savings_rate(months=12)

        if not savings_data.points:
            return 0

        total_income_dollars = sum(p.income for p in savings_data.points)
        months_count = len(savings_data.points)
        if months_count < 12:
            total_income_dollars = total_income_dollars / months_count * 12

        return _dollars_to_cents(total_income_dollars)

    async def _get_income_sources(self) -> list[IncomeSource]:
        """Get all active income sources."""
        result = await self.db.execute(
            select(IncomeSource).where(IncomeSource.is_active == True)
        )
        return list(result.scalars().all())

    async def _get_cashflow_events(self) -> list[CashflowEvent]:
        """Planned + confirmed cashflow events — the declared plan's dated flows."""
        result = await self.db.execute(
            select(CashflowEvent).where(
                CashflowEvent.status.in_(["planned", "confirmed"]),
            )
        )
        return list(result.scalars().all())

    def _single_pool_event_skip(self, config: FireConfig) -> Callable[[CashflowEvent], bool]:
        """Skip predicate for engines that model ONE undifferentiated net worth
        (project_lifetime, Monte Carlo). Their starting balance already holds
        every asset at book value, so a CONVERSION event — a property sale's
        proceeds, a private-investment vest — must not be added on top (that
        would count the asset twice). project_wealth_pools partitions those
        assets out and handles the conversion itself; single-pool models drop
        the event and keep the book value. Only INCOME events are conversions —
        an expense carrying the same label (a special assessment on the property
        being sold) is real money out and stays. Matchers: projection.sell_event_label_match,
        projection.illiquid_vest_event_match, property_sales[].suppress_cashflow_match.
        """
        ca = config.custom_assumptions or {}
        proj_cfg = ca.get("projection", {}) or {}
        tokens: list[str] = []
        sell_match = proj_cfg.get("sell_event_label_match")
        if sell_match:
            tokens.append(sell_match)
        tokens.extend(t for t in (proj_cfg.get("illiquid_vest_event_match", ["vest"]) or []) if t)
        for sale in ca.get("property_sales", []) or []:
            t = sale.get("suppress_cashflow_match") if isinstance(sale, dict) else None
            if t:
                tokens.append(t)
        lowered = [t.lower() for t in tokens]
        return lambda cf: cf.event_type == "income" and any(t in cf.name.lower() for t in lowered)

    @staticmethod
    def _recurring_events_active_now(
        events: list[CashflowEvent], today: date,
    ) -> list[tuple[CashflowEvent, float]]:
        """Recurring events whose window covers this calendar month, with their
        MONTHLY-EQUIVALENT signed dollars (quarterly ÷ 3, annual ÷ 12,
        probability-weighted). Used by the "now" snapshots (bridge status,
        readiness) — the forward engines use build_cashflow_schedule()."""
        out: list[tuple[CashflowEvent, float]] = []
        for cf in events:
            if not cf.is_recurring:
                continue
            start_offset = (cf.date.year - today.year) * 12 + (cf.date.month - today.month)
            if start_offset > 0:
                continue
            if cf.end_date:
                end_offset = (cf.end_date.year - today.year) * 12 + (cf.end_date.month - today.month)
                if end_offset < 0:
                    continue
            step = _RECURRENCE_STEP_MONTHS.get(cf.recurrence or "monthly", 1)
            sign = 1.0 if cf.event_type == "income" else -1.0
            prob = cf.probability if cf.probability is not None else 1.0
            out.append((cf, cf.amount_cents / 100.0 * sign * prob / step))
        return out

    def _compute_age(self, config: FireConfig, target_date: date) -> float:
        """Compute age at a given date."""
        if not config.date_of_birth:
            return 0
        delta = target_date - config.date_of_birth
        return delta.days / 365.25

    def _get_retirement_date(self, config: FireConfig) -> date | None:
        """Compute the target retirement date from config."""
        if config.target_retirement_date:
            return config.target_retirement_date
        if config.target_retirement_age and config.date_of_birth:
            return config.date_of_birth + relativedelta(years=config.target_retirement_age)
        return None

    def _income_at_month(
        self,
        sources: list[IncomeSource],
        current_date: date,
        retirement_date: date | None,
        years_from_start: float,
        inflation_pct: float = 0.0,
    ) -> int:
        """Compute monthly REAL income from sources at a given date (in cents)."""
        total = 0
        for src in sources:
            # Check if source is active at this date
            if src.start_date and current_date < src.start_date:
                continue
            if src.end_date and current_date > src.end_date:
                continue

            # Salary/bonus end at retirement unless explicit end_date
            if src.income_type.value in ("salary", "bonus", "side_hustle"):
                if retirement_date and current_date >= retirement_date and not src.end_date:
                    continue

            monthly = src.annual_amount / 12

            # growth_rate is a NOMINAL raise — deflate to real before compounding
            if src.growth_rate and years_from_start > 0:
                real_growth = (1 + src.growth_rate / 100) / (1 + inflation_pct / 100) - 1
                monthly = monthly * ((1 + real_growth) ** years_from_start)

            total += int(monthly)
        return total

    async def project_lifetime(
        self,
        scenario: str = "moderate",
        overrides: ScenarioInput | None = None,
    ) -> LifetimeProjectionResponse:
        """Full lifecycle projection: accumulation → retirement → end of life.

        REAL-TERMS (today's dollars): the portfolio compounds at the real
        return derived from the scenario's nominal return and inflation
        ((1+r)/(1+i) − 1); spending stays flat (constant purchasing power,
        plus the retirement phase step-down); SS/pension stay flat (COLA
        offsets inflation). Scenario adjusters shift the NOMINAL inputs
        before deflation, so "conservative" (return −2, inflation +1) still
        squeezes the real return from both sides.
        """
        config = await self.get_effective_config()
        nw_engine = NetWorthEngine(self.db)
        nw = await nw_engine.calculate_current()
        current_nw_cents = _dollars_to_cents(nw.net_worth)

        annual_spending_cents = await self._get_annual_spending(config)
        annual_income_cents = await self._get_annual_income()
        income_sources = await self._get_income_sources()
        cashflow_events = await self._get_cashflow_events()

        # Scenario adjustments
        return_adj = 0.0
        inflation_adj = 0.0
        if scenario == "conservative":
            return_adj = -2.0
            inflation_adj = 1.0
        elif scenario == "optimistic":
            return_adj = 2.0
            inflation_adj = -0.5

        # Override adjustments
        spending_adj_cents = 0
        income_adj_cents = 0
        swr = config.safe_withdrawal_rate
        retirement_age = config.target_retirement_age
        life_exp = config.life_expectancy
        annual_return = config.expected_annual_return + return_adj
        inflation = config.expected_inflation_rate + inflation_adj

        if overrides:
            if overrides.spending_change:
                spending_adj_cents = overrides.spending_change
            if overrides.income_change:
                income_adj_cents = overrides.income_change
            if overrides.return_rate_override is not None:
                annual_return = overrides.return_rate_override + return_adj
            if overrides.retirement_age_override is not None:
                retirement_age = overrides.retirement_age_override
            if overrides.life_expectancy_override is not None:
                life_exp = overrides.life_expectancy_override
            if overrides.swr_override is not None:
                swr = overrides.swr_override
            if overrides.savings_rate_change and annual_income_cents > 0:
                # Convert savings rate change to spending change
                spending_adj_cents = -int(annual_income_cents * overrides.savings_rate_change / 100)

        retirement_date = self._get_retirement_date(config)
        if retirement_age and config.date_of_birth and not config.target_retirement_date:
            retirement_date = config.date_of_birth + relativedelta(years=retirement_age)

        today = date.today()
        # Deflate the (scenario-adjusted) nominal return to REAL, then to monthly
        real_return = (1 + annual_return / 100) / (1 + inflation / 100) - 1
        monthly_return = (1 + real_return) ** (1 / 12) - 1

        # Determine end date from life expectancy
        if config.date_of_birth:
            end_date = config.date_of_birth + relativedelta(years=life_exp)
        else:
            end_date = today + relativedelta(years=40)

        # Cashflow events are part of the declared plan (fire-master#17). Skip
        # conversion events (property sale proceeds, vests): this single-pool
        # model already carries those assets in net worth at book value.
        total_months = (end_date.year - today.year) * 12 + (end_date.month - today.month) + 1
        cf_by_month, _ = build_cashflow_schedule(
            cashflow_events, today, total_months, skip=self._single_pool_event_skip(config),
        )
        has_income_events = any(
            amt > 0 for entries in cf_by_month.values() for _, amt in entries
        )

        # Income comes from the DECLARED plan (income sources + cashflow events)
        # whenever one exists. The trailing-12-month transaction income is a
        # fallback for a blank-slate config only — held flat to retirement it
        # overstates any plan whose income steps down first (fire-master#17).
        use_sources = len(income_sources) > 0 or has_income_events

        net_worth = current_nw_cents
        monthly_spending_base = (annual_spending_cents + spending_adj_cents) / 12

        # Apply one-time events
        if overrides and overrides.one_time_windfall:
            net_worth += overrides.one_time_windfall
        if overrides and overrides.one_time_expense:
            net_worth -= overrides.one_time_expense

        points: list[ProjectionPoint] = []
        lowest_nw = net_worth
        nw_at_retirement = net_worth
        retirement_found = retirement_date
        current = today

        month_idx = 0
        while current <= end_date:
            years_elapsed = month_idx / 12.0
            age = self._compute_age(config, current)
            is_retired = retirement_date and current >= retirement_date
            phase = "retirement" if is_retired else "accumulation"

            # Monthly spending: flat real (constant purchasing power) with
            # the retirement phase step-down
            monthly_spending = monthly_spending_base
            if is_retired:
                monthly_spending *= _spending_multiplier(age)

            # Healthcare adjustment
            if config.healthcare_monthly_cost and not is_retired:
                pass  # healthcare costs already in spending
            elif config.healthcare_monthly_cost and is_retired:
                if config.date_of_birth:
                    age_now = self._compute_age(config, current)
                    if age_now < config.medicare_start_age:
                        monthly_spending += config.healthcare_monthly_cost

            # Monthly income (flat real throughout)
            if is_retired:
                # Post-retirement: only SS, pension, rental, dividends
                if use_sources:
                    monthly_income = self._income_at_month(
                        income_sources, current, retirement_date, years_elapsed, inflation
                    )
                else:
                    monthly_income = 0

                # Social Security — flat real (COLA offsets inflation)
                if config.social_security_monthly and config.date_of_birth:
                    ss_start = config.date_of_birth + relativedelta(
                        years=config.social_security_start_age
                    )
                    if current >= ss_start:
                        monthly_income += int(config.social_security_monthly)

                # Pension — flat real (COLA offsets inflation)
                if config.pension_monthly and config.pension_start_age and config.date_of_birth:
                    pension_start = config.date_of_birth + relativedelta(
                        years=config.pension_start_age
                    )
                    if current >= pension_start:
                        monthly_income += int(config.pension_monthly)
            else:
                # Pre-retirement: full income, flat real. The what-if income
                # change applies to whichever income model is in force.
                if use_sources:
                    monthly_income = self._income_at_month(
                        income_sources, current, retirement_date, years_elapsed, inflation
                    ) + income_adj_cents / 12
                else:
                    monthly_income = (annual_income_cents + income_adj_cents) / 12

            # Cashflow events (dollars, signed, probability-weighted) → cents.
            # Income events add to income, expense events to spending, so the
            # chart's income/spending series show them.
            for _label, amount in cf_by_month.get(month_idx, ()):
                cents = int(round(amount * 100))
                if cents >= 0:
                    monthly_income += cents
                else:
                    monthly_spending += -cents

            # Net savings (pre-retirement) or withdrawal (post-retirement)
            monthly_savings = monthly_income - monthly_spending

            # Investment returns on portfolio
            investment_return = net_worth * monthly_return

            net_worth = net_worth + monthly_savings + investment_return

            if net_worth < lowest_nw:
                lowest_nw = net_worth

            # Record retirement net worth
            if retirement_date and current == retirement_date:
                nw_at_retirement = net_worth

            # Record point (yearly granularity for chart efficiency)
            if month_idx % 12 == 0 or current == retirement_date:
                points.append(ProjectionPoint(
                    date=current.isoformat(),
                    age=round(age, 1),
                    net_worth=round(_cents_to_dollars(net_worth), 2),
                    income=round(_cents_to_dollars(monthly_income * 12), 2),
                    spending=round(_cents_to_dollars(monthly_spending * 12), 2),
                    savings=round(_cents_to_dollars(monthly_savings * 12), 2),
                    phase=phase,
                ))

            current += relativedelta(months=1)
            month_idx += 1

        money_lasts = net_worth > 0

        return LifetimeProjectionResponse(
            points=points,
            scenario=scenario,
            retirement_date=retirement_date.isoformat() if retirement_date else None,
            retirement_age=self._compute_age(config, retirement_date) if retirement_date and config.date_of_birth else None,
            net_worth_at_retirement=round(_cents_to_dollars(nw_at_retirement), 2),
            net_worth_at_end=round(_cents_to_dollars(net_worth), 2),
            lowest_net_worth=round(_cents_to_dollars(lowest_nw), 2),
            money_lasts=money_lasts,
        )

    async def project_timeline(self) -> RetirementTimelineResponse:
        """Run 3 scenarios and return the full timeline."""
        conservative = await self.project_lifetime("conservative")
        moderate = await self.project_lifetime("moderate")
        optimistic = await self.project_lifetime("optimistic")

        # Compute months remaining from moderate scenario
        months_remaining = None
        if moderate.retirement_date:
            ret_date = date.fromisoformat(moderate.retirement_date)
            delta = ret_date - date.today()
            months_remaining = max(0, int(delta.days / 30.44))

        on_track = moderate.money_lasts and (months_remaining is not None)

        return RetirementTimelineResponse(
            conservative=conservative,
            moderate=moderate,
            optimistic=optimistic,
            projected_retirement_date=moderate.retirement_date,
            months_remaining=months_remaining,
            on_track=on_track,
        )

    async def run_scenario(self, scenario_input: ScenarioInput) -> ScenarioComparison:
        """Run a what-if scenario and compare against base case."""
        base = await self.project_lifetime("moderate")
        scenario = await self.project_lifetime("moderate", overrides=scenario_input)

        # Compute deltas
        ret_delta_days = None
        if base.retirement_date and scenario.retirement_date:
            base_ret = date.fromisoformat(base.retirement_date)
            scen_ret = date.fromisoformat(scenario.retirement_date)
            ret_delta_days = (scen_ret - base_ret).days

        nw_ret_delta = scenario.net_worth_at_retirement - base.net_worth_at_retirement
        nw_end_delta = scenario.net_worth_at_end - base.net_worth_at_end

        if base.money_lasts == scenario.money_lasts:
            money_change = "unchanged"
        elif scenario.money_lasts:
            money_change = "now_lasts"
        else:
            money_change = "no_longer_lasts"

        return ScenarioComparison(
            base=base,
            scenario=scenario,
            retirement_date_delta_days=ret_delta_days,
            net_worth_at_retirement_delta=round(nw_ret_delta, 2),
            net_worth_at_end_delta=round(nw_end_delta, 2),
            money_lasts_change=money_change,
        )

    async def compute_readiness(self) -> ReadinessResponse:
        """Compute FIRE readiness score (0-100)."""
        fire_num = await self.compute_fire_number()
        config = await self.get_effective_config()

        # Savings progress (40%)
        savings_progress = min(100, fire_num.progress_pct) * 0.4

        # Savings rate (20%)
        spending_engine = SpendingEngine(self.db)
        savings_data = await spending_engine.get_savings_rate(months=6)
        savings_rate_score = savings_rate_component(savings_data.average_rate) * 0.2

        # Income stability (15%) — share of the DECLARED plan that is stable.
        # Income sources by type, plus recurring income cashflow events active
        # now (a monthly event is by construction a stable stream; fire-master#17
        # — an event-based plan used to score 0). Nothing declared → neutral 50.
        sources = await self._get_income_sources()
        cashflow_events = await self._get_cashflow_events()
        stable_types = {"salary", "rental", "pension", "social_security"}
        stable_income = sum(s.annual_amount for s in sources if s.income_type.value in stable_types)
        total_income = sum(s.annual_amount for s in sources)
        for _cf, amt in self._recurring_events_active_now(cashflow_events, date.today()):
            if amt > 0:
                annual_cents = amt * 12 * 100
                stable_income += annual_cents
                total_income += annual_cents
        income_stability = (stable_income / total_income * 100 if total_income > 0 else 50) * 0.15

        # Expense stability (10%) — low variance in monthly spending
        if len(savings_data.points) >= 3:
            spending_values = [p.spending for p in savings_data.points if p.spending > 0]
            if spending_values:
                avg = sum(spending_values) / len(spending_values)
                variance = sum((v - avg) ** 2 for v in spending_values) / len(spending_values)
                cv = (variance ** 0.5) / avg if avg > 0 else 1
                expense_stability = max(0, min(100, (1 - cv) * 100)) * 0.1
            else:
                expense_stability = 50 * 0.1
        else:
            expense_stability = 50 * 0.1

        # Healthcare plan (5%)
        healthcare_score = (100 if config.healthcare_monthly_cost else 0) * 0.05

        # Emergency fund (5%) — check for accounts tagged as emergency fund
        result = await self.db.execute(
            select(Account).where(
                Account.fire_role.ilike("%emergency%"),
                Account.include_in_net_worth == True,
            )
        )
        emergency_accounts = result.scalars().all()
        emergency_score = (100 if emergency_accounts else 0) * 0.05

        # Goal progress (5%)
        goal_result = await self.db.execute(
            select(Goal).where(Goal.status == "active")
        )
        goals = goal_result.scalars().all()
        if goals:
            avg_progress = sum((g.progress_pct or 0) for g in goals) / len(goals)
            goal_score = min(100, avg_progress) * 0.05
        else:
            goal_score = 50 * 0.05  # neutral if no goals set

        total = (
            savings_progress + savings_rate_score + income_stability +
            expense_stability + healthcare_score + emergency_score + goal_score
        )

        return ReadinessResponse(
            score=round(total, 1),
            breakdown=ReadinessBreakdown(
                savings_progress=round(savings_progress / 0.4, 1),
                savings_rate=round(savings_rate_score / 0.2, 1),
                income_stability=round(income_stability / 0.15, 1),
                expense_stability=round(expense_stability / 0.1, 1),
                healthcare_plan=round(healthcare_score / 0.05, 1),
                emergency_fund=round(emergency_score / 0.05, 1),
                goal_progress=round(goal_score / 0.05, 1),
            ),
            fire_number=fire_num.fire_number,
            current_net_worth=fire_num.current_net_worth,
            progress_pct=fire_num.progress_pct,
        )

    async def compute_milestones(self) -> MilestonesResponse:
        """Compute age-gated financial milestones timeline."""
        config = await self.get_effective_config()
        if not config.date_of_birth:
            return MilestonesResponse(
                milestones=[], current_age=0, bridge_end_age=59.5, bridge_months_remaining=0,
            )

        today = date.today()
        current_age = self._compute_age(config, today)
        dob = config.date_of_birth

        # Get retirement account balances for financial impact labels
        breakdown = await self._compute_net_worth_breakdown()

        # SS amounts from config. Engine-wide semantic: social_security_monthly is the
        # benefit AT social_security_start_age (that's how both projection engines apply
        # it), NOT the FRA-67 amount. Back into the implied full benefit via approximate
        # SSA claiming factors, then derive both milestone figures from that — assuming
        # the configured amount was the full benefit double-reduced it for anyone who
        # entered their age-62 figure (field-reported the day after launch).
        _SSA_FACTORS = {62: 0.70, 63: 0.75, 64: 0.80, 65: 0.867, 66: 0.933, 67: 1.0,
                        68: 1.08, 69: 1.16, 70: 1.24}
        ss_monthly = config.social_security_monthly or 0
        ss_start_age = min(max(int(config.social_security_start_age or 67), 62), 70)
        ss_at_start = _cents_to_dollars(ss_monthly) if ss_monthly else 0
        ss_full = round(ss_at_start / _SSA_FACTORS[ss_start_age]) if ss_at_start else 0
        ss_early = round(ss_full * 0.70)
        healthcare_monthly = _cents_to_dollars(config.healthcare_monthly_cost or 0)

        def _milestone_date(age: float) -> date:
            years = int(age)
            months = int((age - years) * 12)
            return dob + relativedelta(years=years, months=months)

        def _status(age: float) -> str:
            if current_age >= age:
                return "past"
            if age - current_age <= 3:
                return "upcoming"
            return "distant"

        milestones = [
            Milestone(
                age=59.5,
                date=_milestone_date(59.5).isoformat(),
                label="Penalty-Free Access",
                description="IRA and 401(k) withdrawals without 10% early penalty",
                financial_impact=f"{fmtCompact(breakdown.retirement)} accessible",
                status=_status(59.5),
            ),
            Milestone(
                age=62,
                date=_milestone_date(62).isoformat(),
                label="Early Social Security",
                description="Reduced SS benefit (~70% of full). Permanent reduction if claimed.",
                financial_impact=f"${ss_early:,.0f}/mo" if ss_early else "Configure SS in FIRE settings",
                status=_status(62),
            ),
            Milestone(
                age=65,
                date=_milestone_date(65).isoformat(),
                label="Medicare",
                description="Federal health insurance. Eliminates private premium.",
                financial_impact=f"Saves ${healthcare_monthly:,.0f}/mo" if healthcare_monthly else "Healthcare cost TBD",
                status=_status(65),
            ),
            Milestone(
                age=67,
                date=_milestone_date(67).isoformat(),
                label="Full Social Security",
                description="Full retirement age benefit. No reduction for claiming.",
                financial_impact=f"${ss_full:,.0f}/mo" if ss_full else "Configure SS in FIRE settings",
                status=_status(67),
            ),
        ]

        # Bridge months remaining (until 59.5)
        bridge_end = _milestone_date(59.5)
        if today < bridge_end:
            bridge_months = max(0, int((bridge_end - today).days / 30.44))
        else:
            bridge_months = 0

        return MilestonesResponse(
            milestones=milestones,
            current_age=round(current_age, 1),
            bridge_end_age=59.5,
            bridge_months_remaining=bridge_months,
        )

    async def project_wealth_pools(
        self,
        end_age: int = 82,
        bridge_months: int = 0,
        scenario_id: uuid_mod.UUID | None = None,
        spending_override_cents: int | None = None,
    ) -> WealthPoolProjection:
        """Pool-aware wealth projection: cash, IRA-A (SEPP), IRA-B (growth).

        Pulls all assumptions from the DB (FIRE config, income sources, accounts).

        SEPP/IRA split parameters stored in fire_config.custom_assumptions["sepp"].
        If scenario_id is provided, uses that scenario's overrides directly.
        If spending_override_cents is provided, replaces target_annual_spending.
        """
        config = await self.get_effective_config(scenario_id=scenario_id)
        if not config.date_of_birth:
            return WealthPoolProjection(
                points=[], events=[], cash_zero_month=None,
                total_at_end=0, sepp_monthly=0, sepp_depletes_age=None,
                demo_persona=bool((config.custom_assumptions or {}).get("demo_persona")),
            )

        breakdown = await self._compute_net_worth_breakdown()
        sources = await self._get_income_sources()
        if spending_override_cents is not None:
            annual_spending_cents = spending_override_cents
        else:
            annual_spending_cents = await self._get_annual_spending(config)

        today = date.today()
        dob = config.date_of_birth
        current_age = self._compute_age(config, today)

        # SEPP / IRA-split assumptions from custom_assumptions (inactive unless configured)
        # ALL RATES ARE REAL (after inflation) — see projection config block below.
        sepp_cfg = (config.custom_assumptions or {}).get("sepp", {})
        ira_a_start = sepp_cfg.get("ira_a_balance", 0)
        ira_b_start = sepp_cfg.get("ira_b_balance", 0)
        sepp_monthly = sepp_cfg.get("sepp_monthly", 0)
        sepp_start_month = sepp_cfg.get("sepp_start_month", 0)
        ira_growth_rate = sepp_cfg.get("ira_growth_rate", 0.06)  # 6% real — investment return assumption (distinct from SEPP's 5% IRS amortization rate)

        # LEGACY ("miami_sale") — superseded by custom_assumptions["property_sales"];
        # retained for author back-compat; do not use in new configs.
        # Primary-property sale block: inert unless sale_month is set.
        miami_cfg = (config.custom_assumptions or {}).get("miami_sale", {})
        miami_sale_month = miami_cfg.get("sale_month", None)  # month offset; None = no sale
        miami_sale_proceeds_fixed = miami_cfg.get("proceeds", None)  # legacy fixed proceeds (used as fallback only)
        miami_mortgage_balance_at_sale = miami_cfg.get("mortgage_balance_at_sale", None)  # remaining mortgage at sale
        miami_monthly_cost = miami_cfg.get("monthly_cost", 0)  # current all-in monthly cost
        miami_post_sale_rent = miami_cfg.get("post_sale_rent", 0)  # rent after selling
        miami_rental_income_lost = miami_cfg.get("rental_income_lost", 0)  # rental income that ends at sale

        # LEGACY ("park_city") — superseded by property_sales; do not use in new configs.
        # Secondary-property carrying cost (removed from burn when it sells).
        pc_cfg = (config.custom_assumptions or {}).get("park_city", {})
        pc_monthly_cost = pc_cfg.get("monthly_cost", 0)  # mortgage + HOA + insurance + utilities

        # RRSP/RRIF drawdown assumptions from custom_assumptions (inactive unless configured)
        rrsp_cfg = (config.custom_assumptions or {}).get("rrsp", {})
        rrsp_monthly_net = rrsp_cfg.get("monthly_net", 0)  # net monthly draw after withholding
        rrsp_start_month = rrsp_cfg.get("start_month", 0)  # month offset when draws begin
        rrsp_total_available = rrsp_cfg.get("total_available", 0)  # total net amount available

        # LEGACY ("sauvie_sale") — superseded by property_sales; retained for author
        # back-compat; do not use in new configs. Income-property sale whose proceeds
        # pay down the primary mortgage.
        sauvie_cfg = (config.custom_assumptions or {}).get("sauvie_sale", {})
        sauvie_sale_enabled = sauvie_cfg.get("enabled", False)
        sauvie_sale_month = sauvie_cfg.get("sale_month", 0)  # month offset
        sauvie_net_proceeds = sauvie_cfg.get("net_proceeds", 0)
        sauvie_miami_mortgage_paydown = sauvie_cfg.get("miami_mortgage_paydown", 0)
        sauvie_pc_loan_paydown = sauvie_cfg.get("park_city_loan_paydown", 0)
        sauvie_monthly_cost_saved = sauvie_cfg.get("monthly_cost_saved", 0)
        sauvie_monthly_income_lost = sauvie_cfg.get("monthly_income_lost", 0)

        # LEGACY ("str_income") — superseded by property_sales; retained for author
        # back-compat; do not use in new configs. Short-term-rental income overlay.
        str_cfg = (config.custom_assumptions or {}).get("str_income", {})
        str_enabled = str_cfg.get("enabled", False)
        str_miami_monthly = str_cfg.get("miami_monthly_averaged", 0)
        str_pc_monthly = str_cfg.get("park_city_monthly_averaged", 0)
        str_start_month = str_cfg.get("start_month", 0)

        # Debt paydown assumptions from custom_assumptions
        debt_cfg = (config.custom_assumptions or {}).get("debt_paydown", {})
        debt_car_payoff_month = debt_cfg.get("car_loan_payoff_month", None)
        debt_car_payment = debt_cfg.get("car_loan_payment", 0)

        # --- Projection assumptions (all configurable via custom_assumptions["projection"]) ---
        # ALL RATES ARE REAL (after inflation, in today's dollars).
        # Spending is flat nominal = constant purchasing power. SS is flat = COLA offsets inflation.
        proj_cfg = (config.custom_assumptions or {}).get("projection", {})
        # Investment & savings rates (real, after inflation)
        surplus_investment_rate = proj_cfg.get("surplus_investment_rate", 0.04)  # 4% real after-tax (balanced portfolio)
        cash_reserve_months = proj_cfg.get("cash_reserve_months", 12)  # emergency fund = N months expenses
        cash_savings_rate_early = proj_cfg.get("cash_savings_rate_early", 0.01)  # 1% real (savings ~4% nominal - 3% inflation)
        cash_savings_rate_late = proj_cfg.get("cash_savings_rate_late", 0.0)  # 0% real (long-term savings ≈ inflation)
        cash_savings_cutover_month = proj_cfg.get("cash_savings_cutover_month", 60)  # month when rate drops
        # Real estate
        re_appreciation_rate = proj_cfg.get("re_appreciation_rate", 0.01)  # 1% real (RE historically tracks inflation + ~1%)
        primary_property_purchase_price = proj_cfg.get("primary_property_purchase_price", 0)  # for dynamic sale calc
        primary_property_agent_fee_pct = proj_cfg.get("primary_property_agent_fee_pct", 0.06)  # 6% agent fees
        primary_property_mortgage_pi = proj_cfg.get("primary_property_mortgage_pi", 0)  # P&I portion of primary-property cost
        primary_property_cost_basis = proj_cfg.get("primary_property_cost_basis", 0)  # cost basis for the LTCG calc
        primary_property_ltcg_rate = proj_cfg.get("primary_property_ltcg_rate", 0.15)  # 15% federal LTCG rate
        # Event-label matchers (configurable; consumed when cashflow events fire)
        sell_event_label_match = proj_cfg.get("sell_event_label_match", None)  # substring marking the secondary-property sale event; None = disabled
        illiquid_vest_event_match = proj_cfg.get("illiquid_vest_event_match", ["vest"])  # substrings that reduce the illiquid pool
        # Social Security — ONE source of truth: config.social_security_monthly is the
        # benefit payable at config.social_security_start_age (same reading as
        # project_lifetime, Monte Carlo, readiness and compute_fire_number). The legacy
        # projection.ss_claim_age / ss_early_reduction keys are ignored (fire-master#20):
        # they re-reduced an amount already entered for its claiming age and let this
        # engine disagree with every other one.
        ss_claim_age = int(config.social_security_start_age or 67)
        # Spending phases (Blanchett 2013)
        spending_phase_slow = proj_cfg.get("spending_phase_slow", 0.85)  # age 70-80 multiplier
        spending_phase_floor = proj_cfg.get("spending_phase_floor", 0.75)  # age 80+ multiplier
        spending_phase_slow_age = proj_cfg.get("spending_phase_slow_age", 70)  # when slow-go starts
        spending_phase_floor_age = proj_cfg.get("spending_phase_floor_age", 80)  # when no-go starts
        # IRA-B draw behavior
        ira_b_draw_threshold_months = proj_cfg.get("ira_b_draw_threshold_months", 12)  # draw when cash < N months of gap
        # Required minimum distributions (fire-master#14). From config.rmd_start_age
        # the IRS Uniform Lifetime minimum is forced out of the tax-deferred IRAs
        # (IRA-A + IRA-B aggregate, as the IRS aggregates traditional IRAs) whenever
        # the month's voluntary draws fall short. Same mechanics as the tax
        # engine's withdrawal planner. Opt out with projection.enforce_rmd = false.
        # Limitation: the divisor keys off config.date_of_birth — a two-owner
        # household has one date to give; keying it to the younger owner starts
        # RMDs later and divides by a larger factor, understating them.
        enforce_rmd = bool(proj_cfg.get("enforce_rmd", True))
        rmd_start_age = int(config.rmd_start_age or 73)

        # LEGACY ("mortgage_recast") — superseded by property_sales; retained for author
        # back-compat; do not use in new configs. Partial paydown → lower P&I.
        recast_cfg = (config.custom_assumptions or {}).get("mortgage_recast", {})
        recast_enabled = recast_cfg.get("enabled", False)
        recast_month = recast_cfg.get("month", 0)
        recast_paydown = recast_cfg.get("paydown_amount", 0)
        recast_new_pi = recast_cfg.get("new_monthly_pi", primary_property_mortgage_pi)
        mortgage_recast_done = False

        # Healthcare costs (pre-Medicare)
        healthcare_monthly = _cents_to_dollars(config.healthcare_monthly_cost or 0)
        medicare_age = config.medicare_start_age or 65

        # Rental occupancy rate — multiplier on rental income from DB sources.
        # 1.0 = 100% occupancy (default). 0.7 = 70% (30% vacancy).
        rental_occupancy_rate = (config.custom_assumptions or {}).get(
            "rental_occupancy_rate", 1.0
        )
        # Which rental sources the occupancy haircut applies to (name substrings).
        # Empty (default) = every rental source.
        occupancy_source_match = (config.custom_assumptions or {}).get(
            "occupancy_source_match", []
        )

        # Pool starting balances (dollars)
        cash = breakdown.liquid
        ira_a = float(ira_a_start)
        ira_b = float(ira_b_start)
        rrsp_remaining = float(rrsp_total_available)

        # --- Taxable brokerage pool + generic property sales (additive, opt-in) ---
        # When custom_assumptions["property_sales"] is present, the engine uses ONE
        # generic, config-driven sale path for every property (any date, dynamic value,
        # cap-gains incl. §121 + state tax, mortgage payoff via amortization, proceeds
        # routed to a chosen pool). This SUPERSEDES the legacy single-property
        # sale blocks (gated below) so existing scenarios — which have no
        # "property_sales" key — keep byte-for-byte behavior.
        taxable_cfg = (config.custom_assumptions or {}).get("taxable_pool", {})
        taxable = float(taxable_cfg.get("starting_balance", 0) or 0)
        taxable_rate_m = (taxable_cfg.get("return_rate", 0.065) or 0.0) / 12  # real annual → monthly
        property_sales = (config.custom_assumptions or {}).get("property_sales", []) or []
        # use_generic_sales gates ONLY the property-sale mechanics (legacy-vs-generic
        # sale paths, burn deltas, event suppression, markers). It does NOT gate the
        # drawdown waterfall: a pool is drawable whenever it holds money, however it
        # was funded (fire-master#12 — it used to, leaving a configured brokerage
        # unspendable without a property sale).
        use_generic_sales = bool(property_sales)

        # --- Roth / tax-free pool (additive, opt-in; fire-master#13) ---
        # Same opt-in shape as taxable_pool: custom_assumptions["roth_pool"] =
        # {starting_balance, return_rate}. This is the only door tax-free money
        # enters the projection through — tax_free_reserve account balances feed net
        # worth, not this engine (same as retirement-role accounts vs the SEPP block).
        # Grows at its own REAL rate (default = the IRA growth rate: same index,
        # never taxed on the way out). Drawn LAST in the waterfall — a tax-free
        # dollar is worth more than any other dollar, so it is the last one spent.
        # No age gate: contribution basis is withdrawable at any age, earnings are
        # not, and the engine cannot tell them apart — the user decides what balance
        # to expose here. No RMDs (owner Roth IRAs have none).
        roth_cfg = (config.custom_assumptions or {}).get("roth_pool", {}) or {}
        roth = float(roth_cfg.get("starting_balance", 0) or 0)
        _roth_rate = roth_cfg.get("return_rate")
        roth_rate_m = (ira_growth_rate if _roth_rate is None else float(_roth_rate)) / 12  # real annual → monthly
        sales_by_month: dict[int, list[dict]] = {}
        generic_sold: dict[str, bool] = {}
        for _s in property_sales:
            sales_by_month.setdefault(int(_s.get("sale_month", 0)), []).append(_s)
            generic_sold[_s.get("key", "")] = False
        # re_bucket tokens: primary | secondary | income. The property-name tokens
        # below are a LEGACY alias for author back-compat; do not use in new configs.
        legacy_re_buckets = {"miami": "primary", "park_city": "secondary", "sauvie": "income"}

        def _amortized_payoff(s: dict, sale_m: int) -> float:
            """Remaining mortgage at the sale month. A static mortgage_balance_at_sale
            wins; else amortize the live balance forward from current_mortgage_balance
            + mortgage_rate + mortgage_pi; if rate/P&I are missing, fall back to the
            current balance as-is (conservative)."""
            if s.get("mortgage_balance_at_sale") is not None:
                return float(s["mortgage_balance_at_sale"])
            bal = s.get("current_mortgage_balance")
            if bal is None:
                return 0.0
            bal = float(bal)
            rate = s.get("mortgage_rate")
            pi = s.get("mortgage_pi")
            if not rate or not pi:
                return bal
            r = rate / 12.0
            for _ in range(max(0, int(sale_m))):
                interest = bal * r
                bal = max(0.0, bal - (pi - interest))
            return bal

        # Monthly burn from config
        monthly_burn = annual_spending_cents / 12.0 / 100.0  # dollars
        primary_sold = False
        income_prop_sold = False
        primary_mortgage_eliminated = False

        # Real estate equity tracking — partition by fire_role
        # sell_candidate + sell_with_property = secondary property (sold via cashflow event)
        # primary_residence + primary_mortgage = primary property
        # income_producing = income property
        result = await self.db.execute(
            select(Account).where(Account.include_in_net_worth == True)
        )
        re_secondary = 0
        re_primary = 0
        re_income_prop = 0
        for acct in result.scalars().all():
            bal = acct.current_balance if acct.is_asset else -acct.current_balance
            role = (acct.fire_role or "").lower().strip()
            if role in ("sell_candidate", "sell_with_property"):
                re_secondary += bal
            elif role in ("primary_residence", "primary_mortgage"):
                re_primary += bal
            elif role == "income_producing":
                re_income_prop += bal
        re_secondary = _cents_to_dollars(re_secondary)
        re_primary = _cents_to_dollars(re_primary)
        re_income_prop = _cents_to_dollars(re_income_prop)
        re_equity = re_secondary + re_primary + re_income_prop
        secondary_sold = False
        illiquid = breakdown.illiquid_private

        # Key month offsets (from today)
        months_to_59_5 = max(0, int((dob + relativedelta(years=59, months=6) - today).days / 30.44))

        # SS from config — the entered amount IS the benefit at the entered start age
        ss_amount = _cents_to_dollars(config.social_security_monthly or 0)
        months_to_ss = max(0, int((dob + relativedelta(years=ss_claim_age) - today).days / 30.44))
        ss_start_month = months_to_ss

        total_months = int((end_age - current_age) * 12)
        ira_b_monthly_growth = ira_growth_rate / 12

        # Load cashflow events for injection into projection.
        # A generic property sale "owns" its property: suppress any legacy
        # cashflow event it replaces (e.g. a planned "… Sale Proceeds" event)
        # so proceeds aren't double-counted AND no phantom event label/marker
        # renders for money that never lands. Opt-in per sale via
        # "suppress_cashflow_match"; only active when generic sales are on.
        cashflow_events = await self._get_cashflow_events()

        def _sale_suppresses(cf: CashflowEvent) -> bool:
            return use_generic_sales and any(
                (s.get("suppress_cashflow_match") or "")
                and (s.get("suppress_cashflow_match") or "").lower() in cf.name.lower()
                for s in property_sales
            )

        # cf_by_month drives income/expense math for every month an event fires;
        # cf_labels_by_month tags label-worthy occurrences only (see helper).
        cf_by_month, cf_labels_by_month = build_cashflow_schedule(
            cashflow_events, today, total_months, skip=_sale_suppresses,
        )

        # Build income source schedule (non-salary, by month offset from today)
        def _source_income_at_month(m: int) -> float:
            """Monthly non-salary income from active sources at month offset m."""
            target = today + relativedelta(months=m)
            total = 0.0
            for src in sources:
                if src.income_type.value in ("salary", "bonus", "side_hustle"):
                    continue
                if src.start_date and target < src.start_date:
                    continue
                if src.end_date and target > src.end_date:
                    continue
                monthly = src.annual_amount / 12.0 / 100.0  # cents to dollars/mo
                # Apply occupancy haircut to matched rental sources (every rental
                # source when occupancy_source_match is empty)
                if (src.income_type.value == "rental"
                        and rental_occupancy_rate < 1.0
                        and (not occupancy_source_match
                             or any(t.lower() in (src.name or "").lower()
                                    for t in occupancy_source_match))):
                    monthly *= rental_occupancy_rate
                total += monthly
            return total

        points: list[WealthPoolPoint] = []
        chart_events: list[dict] = []
        cash_zero_month = None
        sepp_depletes_age = None

        for m in range(total_months):
            age = current_age + m / 12.0
            dt = today + relativedelta(months=m)

            # --- Income (non-IRA) ---
            income = _source_income_at_month(m)

            # Social Security
            if m >= ss_start_month:
                income += ss_amount

            # RRSP/RRIF drawdown — tracked as visible pool, not lumped into income
            rrsp_draw = 0.0
            if m >= rrsp_start_month and rrsp_remaining > 0:
                rrsp_draw = min(rrsp_monthly_net, rrsp_remaining)
                rrsp_remaining -= rrsp_draw

            # Cashflow events: accumulate money every month an event fires.
            # (Sale-suppressed events were filtered out at construction, so
            # neither their money nor their labels/markers ever appear here.)
            if m in cf_by_month:
                for label, amount in cf_by_month[m]:
                    income += amount
                    # Drop secondary-property RE equity when its (non-suppressed) sale
                    # event fires — matched via projection.sell_event_label_match.
                    # When the generic path owns the property, its event was
                    # suppressed at construction, so this never runs.
                    if (sell_event_label_match and not secondary_sold
                            and sell_event_label_match.lower() in label.lower()):
                        re_equity -= re_secondary
                        re_secondary = 0
                        secondary_sold = True
                    # Drop illiquid when private investments vest — matched via
                    # projection.illiquid_vest_event_match substrings
                    if illiquid > 0 and any(t.lower() in label.lower() for t in illiquid_vest_event_match):
                        illiquid = max(0, illiquid - abs(amount))

            # Chart marker: only label-worthy occurrences (one-offs + start of recurrings)
            event_label = "; ".join(cf_labels_by_month[m]) if m in cf_labels_by_month else None

            # --- LEGACY income-property sale ("sauvie_sale"); superseded by property_sales ---
            if not use_generic_sales and sauvie_sale_enabled and m == sauvie_sale_month and not income_prop_sold:
                income_prop_sold = True
                # Net proceeds go to cash (after mortgage paydown allocation)
                cash_from_sale = sauvie_net_proceeds - sauvie_miami_mortgage_paydown - sauvie_pc_loan_paydown
                income += max(0, cash_from_sale)
                # Remove the income property from RE equity
                re_equity -= re_income_prop
                re_income_prop = 0
                # Primary mortgage is eliminated — adjust monthly costs
                primary_mortgage_eliminated = True
                event_label = f"Sell Sauvie (+${sauvie_net_proceeds:,.0f}), pay off Miami mortgage"

            # --- LEGACY STR income overlay ("str_income"); superseded by property_sales ---
            if str_enabled and m >= str_start_month:
                # Primary-property STR income replaces the historical DB rental source
                if not primary_sold:
                    income += str_miami_monthly
                    income -= miami_rental_income_lost  # offset DB source that STR replaces
                # Secondary-property STR income (while owned and not sold)
                if not secondary_sold:
                    income += str_pc_monthly

            # --- LEGACY mortgage recast ("mortgage_recast"); superseded by property_sales ---
            if recast_enabled and m == recast_month and not mortgage_recast_done:
                mortgage_recast_done = True
                income -= recast_paydown  # cash outflow for principal paydown
                # Reduce ongoing burn by P&I savings (monthly_burn includes the original cost)
                pi_savings = primary_property_mortgage_pi - recast_new_pi
                monthly_burn -= pi_savings
                # Update primary-property tracking — both drop by the same amount so sale math stays correct
                primary_property_mortgage_pi = recast_new_pi
                miami_monthly_cost -= pi_savings
                if event_label:
                    event_label += f" + mortgage recast (-${recast_paydown:,.0f}, saves ${pi_savings:,.0f}/mo)"
                else:
                    event_label = f"Mortgage recast (-${recast_paydown:,.0f}, saves ${pi_savings:,.0f}/mo)"

            # --- Debt paydown: car loan (scenario-driven) ---
            if debt_car_payoff_month is not None and m == debt_car_payoff_month:
                # Car paid off — reduce monthly burn going forward
                monthly_burn -= debt_car_payment
                if event_label:
                    event_label += f" + car loan paid off"
                else:
                    event_label = f"Car loan paid off (-${debt_car_payment}/mo)"

            # --- LEGACY primary-property sale ("miami_sale"); superseded by property_sales ---
            if not use_generic_sales and miami_sale_month and m == miami_sale_month and not primary_sold:
                primary_sold = True
                # Always compute sale price dynamically from appreciation
                primary_value_at_sale = primary_property_purchase_price * (1 + re_appreciation_rate) ** (miami_sale_month / 12)
                gross_after_fees = primary_value_at_sale * (1 - primary_property_agent_fee_pct)
                taxable_gain = max(0, gross_after_fees - primary_property_cost_basis)
                cap_gains_tax = taxable_gain * primary_property_ltcg_rate
                net_after_tax = gross_after_fees - cap_gains_tax
                # Subtract remaining mortgage unless already paid off
                if primary_mortgage_eliminated:
                    actual_proceeds = net_after_tax
                elif miami_mortgage_balance_at_sale is not None:
                    actual_proceeds = net_after_tax - miami_mortgage_balance_at_sale
                elif miami_sale_proceeds_fixed is not None:
                    actual_proceeds = miami_sale_proceeds_fixed  # legacy fallback
                else:
                    actual_proceeds = net_after_tax  # no mortgage info — assume paid off
                income += actual_proceeds
                re_equity -= re_primary  # primary-property equity moves to cash
                re_primary = 0
                event_label = f"Sell Miami (+${actual_proceeds:,.0f})"

            # --- Generic property sales (scenario-driven; supersedes legacy blocks) ---
            if use_generic_sales and m in sales_by_month:
                for s in sales_by_month[m]:
                    key = s.get("key", "")
                    if generic_sold.get(key):
                        continue
                    generic_sold[key] = True
                    appr = s.get("appreciation_rate")
                    appr = re_appreciation_rate if appr is None else appr
                    value_at_sale = float(s.get("value", 0)) * (1 + appr) ** (m / 12)
                    gross_after_fees = value_at_sale * (1 - s.get("agent_fee_pct", 0))
                    gain = max(0.0, gross_after_fees - float(s.get("cost_basis", 0)) - float(s.get("section_121_exclusion", 0)))
                    cap_gains_tax = gain * (s.get("ltcg_rate", 0) + s.get("state_tax_rate", 0))
                    payoff = _amortized_payoff(s, m)
                    net_proceeds = max(0.0, gross_after_fees - cap_gains_tax - payoff)
                    if s.get("proceeds_to", "taxable") == "cash":
                        income += net_proceeds
                    else:
                        taxable += net_proceeds
                    # Move the matching RE bucket out of equity (also keeps the property
                    # flags coherent for the LEGACY STR/recast/marker paths)
                    bucket = s.get("re_bucket")
                    bucket = legacy_re_buckets.get(bucket, bucket)
                    if bucket == "primary":
                        re_equity -= re_primary; re_primary = 0
                        primary_sold = True
                    elif bucket == "secondary":
                        re_equity -= re_secondary; re_secondary = 0
                        secondary_sold = True
                    elif bucket == "income":
                        re_equity -= re_income_prop; re_income_prop = 0
                        income_prop_sold = True
                    _dest = s.get("proceeds_to", "taxable")
                    _lbl = f"Sell {key.replace('_', ' ').title()} (+${net_proceeds:,.0f}→{_dest})"
                    event_label = (event_label + "; " + _lbl) if event_label else _lbl

            # --- Expenses ---
            # Base burn adjusted for property sales and mortgage paydown
            base_expenses = monthly_burn

            if use_generic_sales:
                # Generic per-sale burn/income. Carrying cost is added while a property
                # is HELD only if it's NOT already inside the base burn (in_base_burn: false);
                # at sale, costs that ARE in the base burn are removed and any post-sale
                # rent is added. A property's rental keeps flowing from DB income_sources
                # (those rows have no sale-aware end_date), so it's CANCELLED at sale via
                # monthly_income (×occupancy when occupancy_adjusted).
                for s in property_sales:
                    sold = generic_sold.get(s.get("key", ""), False)
                    in_burn = s.get("in_base_burn", True)
                    if sold:
                        if in_burn:
                            base_expenses -= s.get("monthly_cost", 0)
                        base_expenses += s.get("post_sale_rent", 0)
                        occ = rental_occupancy_rate if s.get("occupancy_adjusted") else 1.0
                        income -= s.get("monthly_income", 0) * occ
                    elif not in_burn:
                        # Held and not in base burn → add its carrying cost honestly
                        base_expenses += s.get("monthly_cost", 0)
            else:
                # LEGACY single-property burn adjustments (miami_sale / sauvie_sale blocks)
                if primary_sold:
                    # Primary property sold: remove ALL its costs, add rent.
                    # If the mortgage was already eliminated (income-property sale), the
                    # all-in cost was already partially removed — but here we remove it
                    # completely and replace with rent, which is correct regardless of
                    # mortgage state.
                    base_expenses = base_expenses - miami_monthly_cost + miami_post_sale_rent
                    income -= miami_rental_income_lost  # lost rental offset
                elif primary_mortgage_eliminated:
                    # Income property sold to pay off the primary mortgage: remove P&I
                    # but keep HOA + insurance + utilities
                    base_expenses = base_expenses - miami_monthly_cost + (miami_monthly_cost - primary_property_mortgage_pi)

                # Note: the legacy park_city.monthly_cost is NOT subtracted here — the
                # legacy model assumes target_annual_spending was set with that
                # property's carrying cost already outside the budget.

                if income_prop_sold:
                    # Income-property costs eliminated from burn
                    base_expenses -= sauvie_monthly_cost_saved
                    # Offset its rental that still flows from DB income sources
                    # (adjusted by occupancy rate — only lose what we were actually earning)
                    income -= sauvie_monthly_income_lost * rental_occupancy_rate

            # Spending phases (Blanchett 2013): go-go / slow-go / no-go
            if age >= spending_phase_floor_age:
                spending_mult = spending_phase_floor   # no-go: 75% default
            elif age >= spending_phase_slow_age:
                spending_mult = spending_phase_slow    # slow-go: 85% default
            else:
                spending_mult = 1.0                    # go-go: 100%
            expenses = base_expenses * spending_mult

            # Healthcare costs (pre-Medicare only, not in base spending)
            if healthcare_monthly > 0 and age < medicare_age:
                expenses += healthcare_monthly

            # --- IRA-A: grows + SEPP draws ---
            # IRA-A is invested (same growth rate as IRA-B) but has fixed SEPP withdrawals
            if ira_a > 0:
                ira_a *= (1 + ira_b_monthly_growth)
            ira_draw = 0.0
            if m >= sepp_start_month and ira_a > 0:
                draw = min(sepp_monthly, ira_a)
                ira_a -= draw
                ira_draw += draw
                if ira_a <= 0 and sepp_depletes_age is None:
                    sepp_depletes_age = age

            # --- IRA-B: grows, accessible after 59½ ---
            ira_b *= (1 + ira_b_monthly_growth)

            # --- Taxable brokerage: grows at its own (market) real rate ---
            if taxable > 0:
                taxable *= (1 + taxable_rate_m)

            # --- Roth: grows tax-free at its own real rate ---
            if roth > 0:
                roth *= (1 + roth_rate_m)

            # --- Drawdown waterfall ---
            # Order: taxable brokerage → IRA-B (after 59½) → forced RMDs → Roth.
            # Each step sees only the gap the previous ones left, and a pool is
            # drawable whenever it holds money — configured starting balance, sale
            # proceeds, or RMD redeposits alike (no flag gates this block; see
            # use_generic_sales above, fire-master#12).
            #
            # Taxable brokerage is penalty-free at any age → draw it FIRST when cash
            # is thin (this gap credits RRSP income). IRA-B is drawn only after 59½,
            # on the residual gap, using the SAME formula as the pre-taxable engine
            # (it does NOT credit rrsp_draw) so legacy baselines are unaffected.
            # This is what lets the projection answer "is 72(t)/SEPP still needed?":
            # with sepp_monthly=0, if taxable covers every pre-59½ gap (cash never
            # hits zero), it isn't.
            taxable_draw = 0.0
            gap_t = expenses - income - ira_draw - rrsp_draw
            if gap_t > 0 and cash < gap_t * ira_b_draw_threshold_months and taxable > 0:
                taxable_draw = min(gap_t, taxable)
                taxable -= taxable_draw
            if m >= months_to_59_5 and ira_b > 0:
                gap_b = expenses - income - ira_draw - taxable_draw
                if gap_b > 0 and cash < gap_b * ira_b_draw_threshold_months:
                    b_draw = min(gap_b, ira_b)
                    ira_b -= b_draw
                    ira_draw += b_draw

            # --- Required minimum distributions (fire-master#14) ---
            # From rmd_start_age the IRS forces (aggregate traditional IRA balance /
            # Uniform Lifetime divisor) out per year, whether or not it is needed.
            # Monthly approximation: balance / divisor / 12, mirroring the tax
            # engine's planner. When the month's voluntary draws (SEPP + IRA-B gap
            # draw) already meet it nothing changes; otherwise the shortfall is
            # forced out of IRA-B first, then IRA-A. Forced money is cash in hand:
            # it covers whatever gap is still open this month, and the surplus is
            # NOT spending — it is redeposited (gross) into the taxable brokerage,
            # where next month's waterfall can draw it. The redeposit rides inside
            # ira_draw for reporting (it IS a taxable distribution) but is carried
            # separately as rmd_redeposit so the cash line never counts it.
            rmd_redeposit = 0.0
            if enforce_rmd and age >= rmd_start_age:
                deferred = max(0.0, ira_a) + max(0.0, ira_b)
                if deferred > 0:
                    rmd_monthly = deferred / _get_rmd_divisor(int(age)) / 12
                    if rmd_monthly > ira_draw:
                        excess = min(rmd_monthly - ira_draw, deferred)
                        from_b = min(excess, max(0.0, ira_b))
                        ira_b -= from_b
                        ira_a -= excess - from_b
                        if ira_a <= 0 and sepp_monthly and sepp_depletes_age is None and m >= sepp_start_month:
                            sepp_depletes_age = age  # RMDs finished off IRA-A
                        gap_open = max(0.0, expenses - income - ira_draw - rrsp_draw - taxable_draw)
                        ira_draw += excess
                        rmd_redeposit = max(0.0, excess - gap_open)
                        taxable += rmd_redeposit

            # --- Roth: last resort on whatever gap survives (fire-master#13) ---
            roth_draw = 0.0
            if roth > 0:
                gap_r = expenses - income - (ira_draw - rmd_redeposit) - rrsp_draw - taxable_draw
                if gap_r > 0 and cash < gap_r * ira_b_draw_threshold_months:
                    roth_draw = min(gap_r, roth)
                    roth -= roth_draw

            # Cash repair: negative cash is genuine bridge stress only while
            # no drawable pool exists — nobody runs checking negative for
            # years beside a funded brokerage. Once a penalty-free pool has
            # money (taxable first, then Roth), sell enough extra to bring cash
            # back to zero. Pre-rescue dips (no pool yet) still show, and
            # cash == 0 still trips cash_zero_month, so the stress signal survives.
            # (The repair reaches cash through the net line below, via *_draw.)
            deficit = -cash if cash < 0 else 0.0
            if deficit > 0 and taxable > 0:
                repair = min(deficit, taxable)
                taxable -= repair
                taxable_draw += repair
                deficit -= repair
            if deficit > 0 and roth > 0:
                repair = min(deficit, roth)
                roth -= repair
                roth_draw += repair
                deficit -= repair

            # --- Net cash flow ---
            # Cash earns tiered returns:
            # - Reserve (N months of expenses): savings rate (4% yr 1-5, 2% after)
            # - Surplus above reserve: investment rate (default 6%)
            savings_rate = cash_savings_rate_early if m < cash_savings_cutover_month else cash_savings_rate_late
            reserve_threshold = max(0, expenses) * cash_reserve_months
            if cash > reserve_threshold and cash > 0:
                reserve_interest = reserve_threshold * (savings_rate / 12)
                surplus = cash - reserve_threshold
                surplus_interest = surplus * (surplus_investment_rate / 12)
                cash_interest = reserve_interest + surplus_interest
            else:
                cash_interest = max(0, cash) * (savings_rate / 12)
            # ira_draw includes any RMD redeposit, which went to taxable, not cash.
            net = (income + ira_draw - rmd_redeposit + rrsp_draw + taxable_draw
                   + roth_draw + cash_interest - expenses)
            cash += net

            if cash <= 0 and cash_zero_month is None:
                cash_zero_month = m

            # RE appreciation (configurable via projection.re_appreciation_rate)
            re_monthly_appr = re_appreciation_rate / 12
            re_income_prop *= (1 + re_monthly_appr)
            re_primary *= (1 + re_monthly_appr)
            re_secondary *= (1 + re_monthly_appr)
            re_equity = re_income_prop + re_primary + re_secondary

            # Record points: monthly for bridge period, yearly after. No terminal
            # extra point (unless the run is shorter than a year): the chart's
            # categorical axis gives every point a full-width slot, so a point 1
            # month after the last yearly one renders as a duplicate-age flat
            # segment at the right edge.
            if m < bridge_months or m % 12 == 0 or (m == total_months - 1 and total_months < 12):
                re_val = round(max(0, re_equity), 0)
                ill_val = round(max(0, illiquid), 0)
                rrsp_val = round(max(0, rrsp_remaining), 0)
                tax_val = round(max(0, taxable), 0)
                roth_val = round(max(0, roth), 0)
                points.append(WealthPoolPoint(
                    date=dt.isoformat(),
                    age=round(age, 1),
                    cash=round(cash, 0),  # raw value — frontend clamps for stacked chart
                    ira_sepp=round(max(0, ira_a), 0),
                    ira_growth=round(max(0, ira_b), 0),
                    rrsp=rrsp_val,
                    real_estate=re_val,
                    illiquid=ill_val,
                    taxable=tax_val,
                    taxable_draw=round(taxable_draw, 0),
                    roth=roth_val,
                    roth_draw=round(roth_draw, 0),
                    total=round(cash + max(0, ira_a) + max(0, ira_b) + rrsp_val + re_val + ill_val + tax_val + roth_val, 0),
                    income=round(income, 0),
                    expenses=round(expenses, 0),
                    ira_draw=round(ira_draw, 0),
                    rmd_redeposit=round(rmd_redeposit, 0),
                    rrsp_draw=round(rrsp_draw, 0),
                    cash_interest=round(cash_interest, 0),
                    month=m,
                    event=event_label,
                ))

            if cash <= 0 and ira_b <= 0 and ira_a <= 0 and taxable <= 0 and roth <= 0:
                break

        # Key events for chart markers
        chart_events = []
        if use_generic_sales:
            for s in property_sales:
                sm = int(s.get("sale_month", 0))
                chart_events.append(
                    {"month": sm, "age": round(current_age + sm / 12, 1),
                     "label": f"Sell {s.get('key', '').replace('_', ' ').title()}", "color": "#ff4d6a"},
                )
        else:
            # LEGACY markers for the single-property sale blocks
            if sauvie_sale_enabled:
                chart_events.append(
                    {"month": sauvie_sale_month, "age": round(current_age + sauvie_sale_month / 12, 1),
                     "label": "Sell Sauvie", "color": "#ffc04d"},
                )
            if miami_sale_month:
                chart_events.append(
                    {"month": miami_sale_month, "age": round(current_age + miami_sale_month / 12, 1),
                     "label": "Sell Miami", "color": "#ff4d6a"},
                )
        if sepp_monthly:
            chart_events.append(
                {"month": sepp_start_month, "age": round(current_age + sepp_start_month / 12, 1),
                 "label": "SEPP starts", "color": "#4d8eff"},
            )
        chart_events.extend([
            {"month": months_to_59_5, "age": 59.5, "label": "59\u00BD", "color": "#4d8eff"},
            {"month": months_to_ss, "age": float(ss_claim_age), "label": f"SS at {ss_claim_age}", "color": "#00d4aa"},
        ])
        if enforce_rmd and (ira_a_start or ira_b_start) and current_age < rmd_start_age < end_age:
            months_to_rmd = max(0, int((dob + relativedelta(years=rmd_start_age) - today).days / 30.44))
            chart_events.append(
                {"month": months_to_rmd, "age": float(rmd_start_age), "label": f"RMDs at {rmd_start_age}", "color": "#4d8eff"},
            )

        return WealthPoolProjection(
            points=points,
            events=chart_events,
            cash_zero_month=cash_zero_month,
            total_at_end=round(cash + max(0, ira_a) + max(0, ira_b) + max(0, rrsp_remaining) + max(0, re_equity) + max(0, illiquid) + max(0, taxable) + max(0, roth), 0),
            sepp_monthly=sepp_monthly,
            sepp_depletes_age=round(sepp_depletes_age, 1) if sepp_depletes_age else None,
            demo_persona=bool((config.custom_assumptions or {}).get("demo_persona")),
        )

    async def project_wealth_pools_for_scenario(
        self, scenario_id: uuid_mod.UUID, end_age: int = 82, bridge_months: int = 0
    ) -> WealthPoolProjection:
        """Run wealth pool projection with a specific scenario (without activating it)."""
        return await self.project_wealth_pools(
            end_age=end_age, bridge_months=bridge_months, scenario_id=scenario_id,
        )

    async def compute_bridge_status(self) -> BridgeStatus:
        """Compute bridge period cash flow: income vs burn, runway, upcoming events."""
        config = await self.get_effective_config()
        breakdown = await self._compute_net_worth_breakdown()
        sources = await self._get_income_sources()
        cashflow_events = await self._get_cashflow_events()
        annual_spending_cents = await self._get_annual_spending(config)
        today = date.today()

        monthly_burn = annual_spending_cents / 12.0 / 100.0

        # Recurring cashflow events active THIS month are part of the "now"
        # snapshot (fire-master#17): income events join the streams (temp when
        # they carry an end_date), expense events add to burn — same layering
        # the Runway page applies on top of trailing burn. One-offs stay in
        # upcoming_events; a flat runway division has no place for a lump.
        active_recurring = [
            (cf, amt) for cf, amt in self._recurring_events_active_now(cashflow_events, today)
        ]
        for cf, amt in active_recurring:
            if amt < 0:
                monthly_burn += -amt

        # SEPP/RRSP from config (inactive unless configured)
        sepp_cfg = (config.custom_assumptions or {}).get("sepp", {})
        rrsp_cfg = (config.custom_assumptions or {}).get("rrsp", {})
        # LEGACY ("miami_sale") — superseded by property_sales; read for author back-compat.
        miami_cfg = (config.custom_assumptions or {}).get("miami_sale", {})
        sepp_monthly = sepp_cfg.get("sepp_monthly", 0)
        sepp_start_month = sepp_cfg.get("sepp_start_month", 0)
        rrsp_monthly = rrsp_cfg.get("monthly_net", 0)
        rrsp_start_month = rrsp_cfg.get("start_month", 0)

        miami_monthly_cost = miami_cfg.get("monthly_cost", 0)
        miami_post_rent = miami_cfg.get("post_sale_rent", 0)
        miami_rental_lost = miami_cfg.get("rental_income_lost", 0)

        # Income streams — split ongoing vs temporary
        streams: list[IncomeStream] = []
        ongoing_income = 0.0
        temp_income = 0.0
        colors = ["#00d4aa", "#06b6d4", "#ffc04d", "#f97316", "#ec4899"]
        ci = 0
        for src in sources:
            if src.income_type.value in ("salary", "bonus", "side_hustle"):
                continue
            if src.start_date and today < src.start_date:
                continue
            if src.end_date and today > src.end_date:
                continue
            mo = src.annual_amount / 12.0 / 100.0
            is_temp = src.end_date is not None
            label = f"{src.name} (temp)" if is_temp else src.name
            streams.append(IncomeStream(label=label, monthly=round(mo, 0), color=colors[ci % len(colors)]))
            if is_temp:
                temp_income += mo
            else:
                ongoing_income += mo
            ci += 1
        for cf, amt in active_recurring:
            if amt <= 0:
                continue
            is_temp = cf.end_date is not None
            label = f"{cf.name} (temp)" if is_temp else cf.name
            streams.append(IncomeStream(label=label, monthly=round(amt, 0), color=colors[ci % len(colors)]))
            if is_temp:
                temp_income += amt
            else:
                ongoing_income += amt
            ci += 1

        # IRA streams (if started)
        ira_total = 0.0
        months_elapsed = 0  # from model start — approximate as 0 for "now"
        if sepp_monthly and sepp_start_month <= months_elapsed:
            ira_total += sepp_monthly
            streams.append(IncomeStream(label="72(t) SEPP", monthly=sepp_monthly, color="#8b5cf6"))
        if rrsp_monthly and rrsp_start_month <= months_elapsed:
            ira_total += rrsp_monthly
            streams.append(IncomeStream(label="RRSP drawdown", monthly=rrsp_monthly, color="#4d8eff"))

        # Runway uses only ongoing income — temporary sources end soon
        income_total = ongoing_income + temp_income
        deficit = monthly_burn - ongoing_income - ira_total
        cash = breakdown.liquid
        runway = int(cash / deficit) if deficit > 0 else None

        # Post-sale comparison (LEGACY single-property model: burn after selling the
        # primary property and switching to rent, with SEPP/RRSP draws running)
        post_sale_burn = monthly_burn - miami_monthly_cost + miami_post_rent
        post_sale_income = income_total - miami_rental_lost + sepp_monthly + rrsp_monthly
        post_sale_deficit = post_sale_burn - post_sale_income

        # Upcoming cashflow events
        cf_result = await self.db.execute(
            select(CashflowEvent).where(
                CashflowEvent.status.in_(["planned", "confirmed"]),
                CashflowEvent.date >= today,
                CashflowEvent.is_recurring == False,
            ).order_by(CashflowEvent.date).limit(6)
        )
        upcoming = []
        for cf in cf_result.scalars().all():
            amt = cf.amount_cents / 100.0 * cf.probability
            if cf.event_type == "expense":
                amt = -amt
            upcoming.append(UpcomingEvent(
                name=cf.name,
                date=cf.date.isoformat(),
                amount=round(amt, 0),
                event_type=cf.event_type,
            ))

        return BridgeStatus(
            cash_balance=round(cash, 0),
            monthly_burn=round(monthly_burn, 0),
            monthly_income_total=round(income_total, 0),
            monthly_ira_total=round(ira_total, 0),
            monthly_deficit=round(deficit, 0),
            cash_runway_months=runway,
            income_streams=streams,
            post_sale_burn=round(post_sale_burn, 0),
            post_sale_income_total=round(post_sale_income, 0),
            post_sale_deficit=round(post_sale_deficit, 0),
            upcoming_events=upcoming,
        )
