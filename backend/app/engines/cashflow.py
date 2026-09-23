"""Cash flow projection engine — monthly runway projection from events, burn rate, and income."""

import calendar
import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.cashflow_event import CashflowEvent
from app.models.category_mapping import CategoryMapping
from app.models.fire_config import FireConfig
from app.models.income_source import IncomeSource
from app.models.transaction import Transaction
from app.engines.event_matching import (
    DATE_WINDOW_DAYS,
    Candidate,
    expense_occurrences,
    match_occurrences,
)
from app.schemas.cashflow import (
    BurnExclusion,
    MonthlyProjectionPoint,
    RunwayResponse,
    ScenarioSale,
)

logger = logging.getLogger(__name__)

# Trailing window for the burn baseline (the income reference uses the same span).
TRAILING_DAYS = 90


def _cents_to_dollars(cents: int) -> float:
    return float(cents) / 100


class CashflowEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_current_cash(self) -> float:
        """Liquid cash for the runway projection.

        fire_role-based, using the SAME definition as the pool engine's liquid
        bucket (cash_reserve + operating_account — speculative is deliberately
        not cash), so Runway and the Retirement bridge open on the same number.
        Falls back to account_type checking+savings when NO liquid roles are
        enriched yet — a virgin kit install must not read $0.
        """
        from app.engines.fire_projections import LIQUID_ROLES
        from app.models.enums import AccountType

        result = await self.db.execute(
            select(Account).where(Account.include_in_net_worth == True)
        )
        accounts = list(result.scalars().all())

        role_cents = 0
        has_liquid_role = False
        for a in accounts:
            role = (a.fire_role or "").lower().strip()
            if role in LIQUID_ROLES:
                has_liquid_role = True
                role_cents += a.current_balance if a.is_asset else -a.current_balance
        if has_liquid_role:
            return _cents_to_dollars(int(role_cents))

        total = sum(
            a.current_balance for a in accounts
            if a.is_asset and a.account_type in (AccountType.CHECKING, AccountType.SAVINGS)
        )
        return _cents_to_dollars(int(total))

    async def _outflows(self, start: date, end: date) -> list[Candidate]:
        """Spending transactions in [start, end]: non-income, non-transfer outflows
        (property spend included — Runway burn deliberately counts it)."""
        result = await self.db.execute(
            select(Transaction.id, Transaction.date, Transaction.amount, Transaction.merchant)
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                Transaction.date <= end,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
        )
        return [Candidate(r.id, r.date, -int(r.amount), r.merchant) for r in result.all()]

    async def get_trailing_monthly_income(self, months: int = 3, today: date | None = None) -> float:
        """Average monthly income over the last N months from transaction data."""
        start = (today or date.today()) - timedelta(days=months * 30)

        result = await self.db.execute(
            select(func.sum(Transaction.amount).label("total_cents"))
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == True,
            )
        )
        total_cents = result.scalar() or 0
        return _cents_to_dollars(int(total_cents)) / months

    @staticmethod
    def modeled_income_for_month(
        sources: list[IncomeSource],
        current_date: date,
        retirement_date: date | None,
        years_from_start: float = 0.0,
        inflation_pct: float = 0.0,
    ) -> int:
        """Monthly REAL income (cents) from declared sources at a given date.

        INTENTIONAL DUPLICATE of FireProjectionsEngine._income_at_month — same rules,
        kept in lockstep by tests/test_runway_income.py::test_engines_agree. Runway
        projects DECLARED money only (income sources + events); trailing transaction
        averages are never a projection input — a one-off receipt must not become
        permanent income (the Jul 27 launch blocker). Extract a shared helper
        post-launch with the agreement test already in place to prove it inert.
        """
        total = 0
        for src in sources:
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

    async def _get_income_model_inputs(self) -> tuple[list[IncomeSource], date | None, float]:
        """Active income sources + retirement date + inflation, for the declared model."""
        sources = list(
            (await self.db.execute(select(IncomeSource).where(IncomeSource.is_active == True)))
            .scalars().all()
        )
        config = (await self.db.execute(select(FireConfig))).scalars().first()
        retirement_date: date | None = None
        inflation = 0.0
        if config:
            if config.target_retirement_date:
                retirement_date = config.target_retirement_date
            elif config.target_retirement_age and config.date_of_birth:
                retirement_date = config.date_of_birth + relativedelta(
                    years=config.target_retirement_age
                )
            inflation = config.expected_inflation_rate or 0.0
        return sources, retirement_date, inflation

    async def get_active_events(self) -> list[CashflowEvent]:
        """All non-cancelled, non-completed events."""
        result = await self.db.execute(
            select(CashflowEvent)
            .where(CashflowEvent.status.in_(["planned", "confirmed"]))
            .order_by(CashflowEvent.date)
        )
        return list(result.scalars().all())

    async def _get_plan_sales(self) -> tuple[list[dict], float, str | None]:
        """Pinned property sales from the ACTIVE plan (scenario or base config).

        Only entries with a calendar `sale_date` count — a pinned sale is a known
        future cash event; a legacy `sale_month` is a rolling "N months from now"
        what-if with no date to put on a cash calendar. Returns
        (sales, real RE appreciation rate, active scenario name or None).
        """
        from app.engines.fire_projections import FireProjectionsEngine
        from app.models.fire_scenario import FireScenario

        config = await FireProjectionsEngine(self.db).get_effective_config()
        ca = config.custom_assumptions or {}
        sales = [
            s for s in (ca.get("property_sales") or [])
            if isinstance(s, dict) and s.get("sale_date")
        ]
        appreciation = (ca.get("projection") or {}).get("re_appreciation_rate", 0.01)
        name = None
        if sales:
            name = (await self.db.execute(
                select(FireScenario.name).where(FireScenario.is_active == True).limit(1)
            )).scalar_one_or_none()
        return sales, appreciation, name

    async def project_runway(
        self,
        months: int = 24,
        income_override: float | None = None,
        burn_override: float | None = None,
        today: date | None = None,
    ) -> RunwayResponse:
        """Project monthly cash balance forward.

        INCOME projects DECLARED money only: income sources (start/end dates
        honored, so streams taper) plus cashflow events. The trailing average is
        computed for display/reference but is never a projection input — a
        backward-looking mean over lump receipts reads a one-off as permanent
        income (the Jul 27 launch blocker). No sources modeled → income is 0 +
        events: fails alarming, not reassuring.

        BURN keeps its trailing fallback deliberately — spending is a continuous
        flow, so a trailing average is a defensible estimator; income arrives in
        lumps from discrete dated sources, so it must be modeled. The average
        EXCLUDES payments matched to cashflow events (engines/event_matching.py):
        a finished special assessment is not an ongoing cost.

        Overrides (user-declared flat baselines) win over both when provided.

        Rows are CALENDAR months and month 0 is only the rest of this one: the
        opening balance already reflects everything that cleared since the 1st,
        so month 0 carries the remaining fraction of the burn and baseline
        income, and only events dated today or later (build_cashflow_schedule —
        the same schedule the Retirement engines use). A full month here
        double-counted the part of the month already spent.

        PROPERTY SALES come from the active plan (scenario or base config): each
        pinned property_sales entry lands its net proceeds in its month (the
        same formula as the Retirement engine) and, from then on, removes its
        carrying cost from the burn, adds any post-sale rent, and stops its
        rental income. A hand-made "… Sale Proceeds" income event the entry
        names in suppress_cashflow_match is dropped — the plan owns that sale.
        """
        from app.engines.fire_projections import (
            build_cashflow_schedule,
            property_sale_net_proceeds,
            sale_event_suppressed,
        )
        from app.engines.plan_months import resolve_month_offset

        today = today or date.today()
        current_cash = await self.get_current_cash()
        burn_start = today - timedelta(days=TRAILING_DAYS)
        outflows = await self._outflows(burn_start, today)
        trailing_income = await self.get_trailing_monthly_income(months=3, today=today)
        sources, retirement_date, inflation = await self._get_income_model_inputs()

        events = await self.get_active_events()

        # Trailing burn, CLEANED: a payment that paid a cashflow event (a special
        # assessment installment, a one-off bill) is the event, already modeled —
        # left in the average it would be projected forward as if it recurred.
        burn_matches = match_occurrences(
            expense_occurrences(events, burn_start - timedelta(days=DATE_WINDOW_DAYS), today),
            outflows,
        )
        months_in_window = TRAILING_DAYS / 30
        raw_cents = sum(c.amount_cents for c in outflows)
        matched_cents = sum(m.txn.amount_cents for m in burn_matches)
        trailing_burn_raw = _cents_to_dollars(raw_cents) / months_in_window
        trailing_burn = _cents_to_dollars(raw_cents - matched_cents) / months_in_window
        burn_exclusions = [
            BurnExclusion(
                event_name=m.event_name,
                amount=_cents_to_dollars(m.txn.amount_cents),
                date=m.txn.date,
                merchant=m.txn.merchant,
            )
            for m in burn_matches
        ]

        monthly_burn = burn_override if burn_override is not None else trailing_burn

        start_month = today.replace(day=1)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        remaining_days = days_in_month - today.day + 1  # today counts: it may not have cleared
        month0_fraction = remaining_days / days_in_month

        plan_sales, re_appreciation, scenario_name = await self._get_plan_sales()
        by_month, labels_by_month = build_cashflow_schedule(
            events, today, months,
            skip=(lambda cf: sale_event_suppressed(cf, plan_sales)) if plan_sales else None,
        )
        sales: list[ScenarioSale] = []
        for sale in plan_sales:
            m = resolve_month_offset(sale, "sale_date", "sale_month", today)
            if m >= months:
                continue
            sales.append(ScenarioSale(
                key=str(sale.get("key", "property")),
                month=(start_month + relativedelta(months=m)).strftime("%Y-%m"),
                month_index=m,
                net_proceeds=round(property_sale_net_proceeds(sale, m, re_appreciation), 2),
                proceeds_to=str(sale.get("proceeds_to", "taxable")),
                burn_change=round(float(sale.get("post_sale_rent", 0) or 0)
                                  - float(sale.get("monthly_cost", 0) or 0), 2),
                income_change=-round(float(sale.get("monthly_income", 0) or 0), 2),
            ))

        projection: list[MonthlyProjectionPoint] = []
        cash = current_cash
        cash_zero_date: date | None = None

        for i in range(months):
            month_date = start_month + relativedelta(months=i)
            month_key = month_date.strftime("%Y-%m")

            starting_cash = cash

            # Start with baseline: declared income for THIS month (tapering), flat burn.
            # Month 0 checks stream activity at TODAY, not the 1st — otherwise a source
            # that ended earlier this month (e.g. the persona's salary, ended 3 weeks
            # ago) counts for a full phantom month.
            month_expenses = monthly_burn
            if income_override is not None:
                month_income = income_override
            else:
                effective_date = today if i == 0 else month_date
                month_income = _cents_to_dollars(self.modeled_income_for_month(
                    sources, effective_date, retirement_date,
                    years_from_start=i / 12.0, inflation_pct=inflation,
                ))
            # Sold properties: carrying cost off the burn (post-sale rent on),
            # their rental income stops — from the sale month on.
            for sale in sales:
                if i >= sale.month_index:
                    month_expenses += sale.burn_change
                    month_income += sale.income_change
            month_expenses = max(0.0, month_expenses)
            month_income = max(0.0, month_income)
            if i == 0:
                month_expenses *= month0_fraction
                month_income *= month0_fraction

            # Layer events on top — label only one-offs + first remaining occurrences
            for _name, amount in by_month.get(i, []):
                if amount > 0:
                    month_income += amount
                else:
                    month_expenses += abs(amount)
            event_names = list(labels_by_month.get(i, []))
            for sale in sales:
                if i == sale.month_index:
                    month_income += sale.net_proceeds
                    event_names.append(
                        f"Sell {sale.key.replace('_', ' ').title()} "
                        f"(+${sale.net_proceeds:,.0f}\u2192{sale.proceeds_to})"
                    )

            net = month_income - month_expenses
            cash = starting_cash + net

            if cash <= 0 and cash_zero_date is None:
                if net < 0:
                    # Spread the month's net evenly over the days it covers.
                    period_start = today if i == 0 else month_date
                    period_days = remaining_days if i == 0 else calendar.monthrange(
                        month_date.year, month_date.month)[1]
                    days_in = int(max(0.0, starting_cash) / (abs(net) / period_days))
                    cash_zero_date = period_start + timedelta(days=max(0, min(period_days - 1, days_in)))

            projection.append(MonthlyProjectionPoint(
                month=month_key,
                starting_cash=round(starting_cash, 2),
                income=round(month_income, 2),
                expenses=round(month_expenses, 2),
                net=round(net, 2),
                ending_cash=round(cash, 2),
                events=event_names,
                from_day=today.day if i == 0 and today.day > 1 else None,
            ))

        # Headline figures reflect the CURRENT month's modeled baseline (income
        # tapers, so there is no single flat "income/mo"); months_remaining comes
        # from the projection's actual cash-zero crossing, not flat division.
        income_now = (
            income_override if income_override is not None
            else _cents_to_dollars(self.modeled_income_for_month(
                sources, today, retirement_date, 0.0, inflation
            ))
        )
        net_monthly = income_now - monthly_burn
        months_remaining: float | None = None
        if cash_zero_date is not None:
            months_remaining = round(max(0.0, (cash_zero_date - today).days / 30.44), 1)

        return RunwayResponse(
            current_cash=round(current_cash, 2),
            monthly_burn=round(monthly_burn, 2),
            monthly_income=round(income_now, 2),
            net_monthly=round(net_monthly, 2),
            months_remaining=months_remaining,
            cash_zero_date=cash_zero_date,
            income_provenance="override" if income_override is not None else "modeled",
            trailing_burn=round(trailing_burn, 2),
            trailing_burn_raw=round(trailing_burn_raw, 2),
            burn_exclusions=burn_exclusions,
            trailing_income=round(trailing_income, 2),
            projection=projection,
            scenario_name=scenario_name,
            scenario_sales=sales,
        )
