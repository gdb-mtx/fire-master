"""Cash flow projection engine — monthly runway projection from events, burn rate, and income."""

import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.cashflow_event import CashflowEvent
from app.models.category_mapping import CategoryMapping
from app.models.fire_config import FireConfig
from app.models.income_source import IncomeSource, projection_annual_amount_cents
from app.models.transaction import Transaction
from app.schemas.cashflow import MonthlyProjectionPoint, RunwayResponse

logger = logging.getLogger(__name__)


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

    async def get_trailing_monthly_burn(self, months: int = 3) -> float:
        """Average monthly spending over the last N months from transaction data."""
        start = date.today() - timedelta(days=months * 30)

        result = await self.db.execute(
            select(func.sum(-Transaction.amount).label("total_cents"))
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
        )
        total_cents = result.scalar() or 0
        return _cents_to_dollars(int(total_cents)) / months

    async def get_trailing_monthly_income(self, months: int = 3) -> float:
        """Average monthly income over the last N months from transaction data."""
        start = date.today() - timedelta(days=months * 30)

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
            monthly = projection_annual_amount_cents(src) / 12
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

    def _expand_events_to_months(
        self,
        events: list[CashflowEvent],
        start_month: date,
        num_months: int,
    ) -> dict[str, list[tuple[str, float, str, bool]]]:
        """Expand events into month -> [(name, amount, type, is_start)].

        is_start is True for one-off events and for the first month of a
        recurring event. Consumers use it to label the chart only on the
        start month so recurring events don't stamp a label every tick.
        """
        monthly: dict[str, list[tuple[str, float, str, bool]]] = {}

        for i in range(num_months):
            month_date = start_month + relativedelta(months=i)
            month_key = month_date.strftime("%Y-%m")
            monthly.setdefault(month_key, [])

        for event in events:
            amount = _cents_to_dollars(event.amount_cents) * event.probability
            if event.event_type == "expense":
                amount = -amount

            if not event.is_recurring:
                month_key = event.date.strftime("%Y-%m")
                if month_key in monthly:
                    monthly[month_key].append((event.name, amount, event.event_type, True))
            else:
                end = event.end_date or (start_month + relativedelta(months=num_months))
                current = event.date
                first = True
                while current <= end:
                    month_key = current.strftime("%Y-%m")
                    if month_key in monthly:
                        monthly[month_key].append((event.name, amount, event.event_type, first))
                    first = False

                    if event.recurrence == "monthly":
                        current += relativedelta(months=1)
                    elif event.recurrence == "quarterly":
                        current += relativedelta(months=3)
                    elif event.recurrence == "annual":
                        current += relativedelta(years=1)
                    else:
                        break

        return monthly

    async def project_runway(
        self,
        months: int = 24,
        income_override: float | None = None,
        burn_override: float | None = None,
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
        lumps from discrete dated sources, so it must be modeled.

        Overrides (user-declared flat baselines) win over both when provided.
        """
        current_cash = await self.get_current_cash()
        trailing_burn = await self.get_trailing_monthly_burn(months=3)
        trailing_income = await self.get_trailing_monthly_income(months=3)

        monthly_burn = burn_override if burn_override is not None else trailing_burn
        sources, retirement_date, inflation = await self._get_income_model_inputs()

        events = await self.get_active_events()

        today = date.today()
        start_month = today.replace(day=1)

        event_map = self._expand_events_to_months(events, start_month, months)

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

            # Layer events on top — label only start months (one-offs + recurring starts)
            event_names: list[str] = []
            for name, amount, etype, is_start in event_map.get(month_key, []):
                if is_start:
                    event_names.append(name)
                if amount > 0:
                    month_income += amount
                else:
                    month_expenses += abs(amount)

            net = month_income - month_expenses
            cash = starting_cash + net

            if cash <= 0 and cash_zero_date is None:
                if net < 0:
                    days_into_month = int(starting_cash / (abs(net) / 30))
                    days_into_month = max(0, min(30, days_into_month))
                    cash_zero_date = month_date + timedelta(days=days_into_month)

            projection.append(MonthlyProjectionPoint(
                month=month_key,
                starting_cash=round(starting_cash, 2),
                income=round(month_income, 2),
                expenses=round(month_expenses, 2),
                net=round(net, 2),
                ending_cash=round(cash, 2),
                events=event_names,
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
            trailing_income=round(trailing_income, 2),
            projection=projection,
        )
