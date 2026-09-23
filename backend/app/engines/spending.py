"""Spending analysis engine — category breakdowns, trends, savings rate, recurring expenses, tracker."""

import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cashflow_event import CashflowEvent
from app.models.category_mapping import CategoryMapping
from app.models.transaction import Transaction
from app.engines.event_matching import (
    DATE_WINDOW_DAYS,
    Candidate,
    expense_occurrences,
    match_occurrences,
)
from app.schemas.spending import (
    PlannedExclusion,
    CategoryBreakdownItem,
    CategoryTrend,
    HeatmapCell,
    HeatmapResponse,
    RecurringExpense,
    RecurringExpensesResponse,
    SavingsRatePoint,
    SavingsRateResponse,
    SpendingAnalysisResponse,
    SpendingTrendsResponse,
    TrackerCategoryItem,
    TrackerDailyPoint,
    TrackerDailyResponse,
    TrackerMonthSummary,
    TrackerSummaryResponse,
    TrackerTransaction,
    TrackerTransactionsResponse,
    TrendPoint,
)

logger = logging.getLogger(__name__)


def _cents_to_dollars(cents: int) -> float:
    return float(cents) / 100


def _parse_range(range_str: str) -> tuple[date, date]:
    """Convert a range string to (start_date, end_date)."""
    today = date.today()
    end = today

    if range_str == "month":
        start = today.replace(day=1)
    elif range_str == "3m":
        start = today - timedelta(days=90)
    elif range_str == "6m":
        start = today - timedelta(days=180)
    elif range_str == "ytd":
        start = today.replace(month=1, day=1)
    elif range_str == "1y":
        start = today - timedelta(days=365)
    elif range_str == "all":
        start = date(2000, 1, 1)
    else:
        start = today - timedelta(days=365)

    return start, end


class SpendingEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_category_breakdown(
        self,
        range_str: str = "1y",
        group_by_parent: bool = False,
    ) -> SpendingAnalysisResponse:
        """Spending by category for a date range, optionally grouped by parent."""
        start, end = _parse_range(range_str)

        group_col = (
            CategoryMapping.parent_category if group_by_parent
            else CategoryMapping.normalized_category
        )
        parent_col = CategoryMapping.parent_category

        # Spending query: negative amounts are expenses, negate to get positive spending
        spending_q = (
            select(
                group_col.label("category"),
                parent_col.label("parent"),
                func.sum(-Transaction.amount).label("total_cents"),
                func.count().label("tx_count"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                Transaction.date <= end,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,  # Only actual expenses
            )
            .group_by(group_col, parent_col)
            .order_by(func.sum(-Transaction.amount).desc())
        )
        result = await self.db.execute(spending_q)
        rows = result.all()

        total_spending_cents = sum(r.total_cents for r in rows)

        categories = []
        for r in rows:
            amount = _cents_to_dollars(r.total_cents)
            pct = (r.total_cents / total_spending_cents * 100) if total_spending_cents > 0 else 0
            categories.append(CategoryBreakdownItem(
                category=r.category,
                parent_category=r.parent if not group_by_parent else r.category,
                amount=round(amount, 2),
                percentage=round(pct, 1),
                transaction_count=r.tx_count,
            ))

        # Income query
        income_q = (
            select(func.sum(Transaction.amount).label("total"))
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                Transaction.date <= end,
                CategoryMapping.is_income == True,
            )
        )
        income_result = await self.db.execute(income_q)
        total_income_cents = income_result.scalar() or 0

        total_spending = _cents_to_dollars(total_spending_cents)
        total_income = _cents_to_dollars(total_income_cents)
        net_savings = total_income - total_spending
        savings_rate = (net_savings / total_income * 100) if total_income > 0 else None

        return SpendingAnalysisResponse(
            categories=categories,
            total_spending=round(total_spending, 2),
            total_income=round(total_income, 2),
            net_savings=round(net_savings, 2),
            savings_rate=round(savings_rate, 1) if savings_rate is not None else None,
            start_date=start,
            end_date=end,
            group_by_parent=group_by_parent,
        )

    async def get_trends(
        self,
        months: int = 12,
        top_n: int = 8,
    ) -> SpendingTrendsResponse:
        """Monthly spending per category over time."""
        start = date.today() - timedelta(days=months * 30)

        month_col = func.to_char(Transaction.date, "YYYY-MM").label("month")

        # Get top N categories by total spend
        top_q = (
            select(
                CategoryMapping.normalized_category.label("category"),
                func.sum(-Transaction.amount).label("total"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
            .group_by(CategoryMapping.normalized_category)
            .order_by(func.sum(-Transaction.amount).desc())
            .limit(top_n)
        )
        top_result = await self.db.execute(top_q)
        top_categories = [r.category for r in top_result.all()]

        # Monthly breakdown for top categories
        trend_q = (
            select(
                month_col,
                CategoryMapping.normalized_category.label("category"),
                func.sum(-Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
                CategoryMapping.normalized_category.in_(top_categories),
            )
            .group_by(month_col, CategoryMapping.normalized_category)
            .order_by(month_col)
        )
        trend_result = await self.db.execute(trend_q)

        # Group by category
        cat_data: dict[str, list[TrendPoint]] = defaultdict(list)
        cat_totals: dict[str, int] = defaultdict(int)
        for r in trend_result.all():
            cat_data[r.category].append(TrendPoint(
                month=r.month,
                amount=round(_cents_to_dollars(r.total_cents), 2),
            ))
            cat_totals[r.category] += r.total_cents

        category_trends = [
            CategoryTrend(
                category=cat,
                points=cat_data[cat],
                total=round(_cents_to_dollars(cat_totals[cat]), 2),
            )
            for cat in top_categories
            if cat in cat_data
        ]

        # Total spending trend
        total_q = (
            select(
                month_col,
                func.sum(-Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
            .group_by(month_col)
            .order_by(month_col)
        )
        total_result = await self.db.execute(total_q)
        total_trend = [
            TrendPoint(month=r.month, amount=round(_cents_to_dollars(r.total_cents), 2))
            for r in total_result.all()
        ]

        return SpendingTrendsResponse(
            categories=category_trends,
            total_trend=total_trend,
            months=months,
        )

    async def get_savings_rate(self, months: int = 24) -> SavingsRateResponse:
        """Monthly savings rate time series."""
        start = date.today() - timedelta(days=months * 30)
        month_col = func.to_char(Transaction.date, "YYYY-MM").label("month")

        # Monthly income
        income_q = (
            select(
                month_col,
                func.sum(Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == True,
            )
            .group_by(month_col)
        )
        income_result = await self.db.execute(income_q)
        income_by_month = {r.month: r.total_cents for r in income_result.all()}

        # Monthly spending
        spending_q = (
            select(
                month_col,
                func.sum(-Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
            .group_by(month_col)
        )
        spending_result = await self.db.execute(spending_q)
        spending_by_month = {r.month: r.total_cents for r in spending_result.all()}

        all_months = sorted(set(list(income_by_month.keys()) + list(spending_by_month.keys())))

        points = []
        total_income = 0
        total_spending = 0

        for m in all_months:
            inc = income_by_month.get(m, 0)
            spe = spending_by_month.get(m, 0)
            sav = inc - spe
            rate = (sav / inc * 100) if inc > 0 else None

            points.append(SavingsRatePoint(
                month=m,
                income=round(_cents_to_dollars(inc), 2),
                spending=round(_cents_to_dollars(spe), 2),
                savings=round(_cents_to_dollars(sav), 2),
                savings_rate=round(rate, 1) if rate is not None else None,
            ))
            total_income += inc
            total_spending += spe

        current_rate = points[-1].savings_rate if points else None
        avg_rate = (
            round((total_income - total_spending) / total_income * 100, 1)
            if total_income > 0 else None
        )

        return SavingsRateResponse(
            points=points,
            current_rate=current_rate,
            average_rate=avg_rate,
            months=months,
        )

    async def get_recurring(self) -> RecurringExpensesResponse:
        """Detect recurring expenses from is_recurring flag + merchant frequency."""
        # Get recurring transactions from last 12 months
        start = date.today() - timedelta(days=365)

        q = (
            select(
                Transaction.merchant,
                CategoryMapping.normalized_category.label("category"),
                func.avg(-Transaction.amount).label("avg_cents"),
                func.count().label("cnt"),
                func.max(Transaction.date).label("last_date"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                Transaction.merchant.isnot(None),
                Transaction.amount < 0,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
            )
            .group_by(Transaction.merchant, CategoryMapping.normalized_category)
            .having(func.count() >= 3)  # At least 3 occurrences
            .order_by(func.avg(-Transaction.amount).desc())
        )
        result = await self.db.execute(q)

        expenses = []
        total_monthly = 0

        for r in result.all():
            avg_amount = _cents_to_dollars(int(r.avg_cents))
            # Estimate frequency based on occurrence count over 12 months
            if r.cnt >= 10:
                frequency = "monthly"
                monthly = avg_amount
            elif r.cnt >= 4:
                frequency = "quarterly"
                monthly = avg_amount / 3
            else:
                frequency = "occasional"
                monthly = avg_amount * r.cnt / 12

            annual = monthly * 12
            total_monthly += monthly

            expenses.append(RecurringExpense(
                merchant=r.merchant,
                category=r.category,
                average_amount=round(avg_amount, 2),
                frequency=frequency,
                last_date=r.last_date,
                occurrence_count=r.cnt,
                estimated_annual=round(annual, 2),
            ))

        return RecurringExpensesResponse(
            expenses=expenses,
            total_monthly=round(total_monthly, 2),
            total_annual=round(total_monthly * 12, 2),
        )

    async def get_heatmap(
        self,
        months: int = 12,
        top_n: int = 15,
    ) -> HeatmapResponse:
        """Category × month spending grid for heatmap visualization."""
        start = date.today() - timedelta(days=months * 30)
        month_col = func.to_char(Transaction.date, "YYYY-MM").label("month")

        # Top N categories by total spend
        top_q = (
            select(
                CategoryMapping.normalized_category.label("category"),
                func.sum(-Transaction.amount).label("total"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
            )
            .group_by(CategoryMapping.normalized_category)
            .order_by(func.sum(-Transaction.amount).desc())
            .limit(top_n)
        )
        top_result = await self.db.execute(top_q)
        top_categories = [r.category for r in top_result.all()]

        # Grid data
        grid_q = (
            select(
                month_col,
                CategoryMapping.normalized_category.label("category"),
                func.sum(-Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                Transaction.date >= start,
                CategoryMapping.is_income == False,
                CategoryMapping.is_transfer == False,
                Transaction.amount < 0,
                CategoryMapping.normalized_category.in_(top_categories),
            )
            .group_by(month_col, CategoryMapping.normalized_category)
            .order_by(month_col)
        )
        grid_result = await self.db.execute(grid_q)

        cells = []
        all_months = set()
        for r in grid_result.all():
            cells.append(HeatmapCell(
                month=r.month,
                category=r.category,
                amount=round(_cents_to_dollars(r.total_cents), 2),
            ))
            all_months.add(r.month)

        return HeatmapResponse(
            cells=cells,
            categories=top_categories,
            months=sorted(all_months),
        )

    # ------------------------------------------------------------------
    #  Spending Tracker — running budget tracker since layoff
    # ------------------------------------------------------------------

    # Parent categories to EXCLUDE from tracker (housing, taxes, transfers)
    TRACKER_EXCLUDED_PARENTS = {"Housing", "Taxes", "Transfers"}
    # Also exclude by normalized_category (some tax items have parent=Financial/Other)
    TRACKER_EXCLUDED_CATEGORIES = {"Taxes"}

    # Layoff date — the start of the new chapter
    TRACKER_START = date(2026, 3, 24)

    # Categories on cashflow events that are already excluded by parent filter
    TRACKER_EXCLUDED_EVENT_CATEGORIES = {"Housing", "Taxes", "Transfers"}

    def _tracker_base_query(self):
        """Base filter: non-RE, non-tax, non-transfer expenses since tracker start."""
        return and_(
            CategoryMapping.is_income == False,
            CategoryMapping.is_transfer == False,
            CategoryMapping.parent_category.notin_(self.TRACKER_EXCLUDED_PARENTS),
            CategoryMapping.normalized_category.notin_(self.TRACKER_EXCLUDED_CATEGORIES),
            Transaction.hide_from_reports == False,
            # Property-assigned transactions live in the Property P&L module, not the
            # Tracker. Runway/FIRE burn (which don't use this filter) still count them.
            Transaction.property_id.is_(None),
        )

    async def _get_planned_exclusions(self) -> tuple[set, list[PlannedExclusion]]:
        """Transactions that paid a planned expense event, over the WHOLE tracker window.

        Returns (transaction ids to exclude, exclusion metadata). Matching (shared
        with the Runway burn, engines/event_matching.py): every expense-event
        occurrence up to today — recurring events expand to each occurrence —
        takes the closest-amount transaction within ±10% and ±7 days.

        Always matched once over the full window and then applied to whatever
        range a view shows. Matching inside a single month (the old per-view
        behavior) let an event just past the month's edge grab the nearest
        wrong transaction inside it: an Aug 3 event claimed a Jul 27 payment in
        the July view while correctly claiming its Aug 3 payment in August.
        Events in Housing/Taxes/Transfers are skipped — the tracker never
        shows those categories, so they could only match a wrong row.
        """
        today = date.today()
        window_start = self.TRACKER_START.replace(day=1)

        events = (await self.db.execute(
            select(CashflowEvent).where(
                CashflowEvent.event_type == "expense",
                CashflowEvent.status.notin_(["cancelled"]),
            )
        )).scalars().all()
        events = [e for e in events if e.category not in self.TRACKER_EXCLUDED_EVENT_CATEGORIES]
        occurrences = expense_occurrences(
            events, window_start - timedelta(days=DATE_WINDOW_DAYS), today,
        )
        if not occurrences:
            return set(), []

        rows = (await self.db.execute(
            select(Transaction.id, Transaction.date, Transaction.amount, Transaction.merchant)
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                self._tracker_base_query(),
                Transaction.date >= window_start,
                Transaction.date <= today,
                Transaction.amount < 0,
            )
        )).all()
        candidates = [Candidate(r.id, r.date, -int(r.amount), r.merchant) for r in rows]

        matches = match_occurrences(occurrences, candidates)
        exclusions = [
            PlannedExclusion(
                event_name=m.event_name,
                event_amount=m.event_amount_cents / 100,
                matched_transaction_id=str(m.txn.id),
                matched_amount=m.txn.amount_cents / 100,
                matched_merchant=m.txn.merchant,
                matched_date=m.txn.date.isoformat(),
            )
            for m in matches
        ]
        return {m.txn.id for m in matches}, exclusions

    async def get_tracker_summary(
        self,
        monthly_target: float = 5000.0,
        exclude_planned: bool = False,
    ) -> TrackerSummaryResponse:
        """Running spending tracker summary: current month, pace, history."""
        today = date.today()
        current_month_start = today.replace(day=1)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        days_elapsed = today.day
        days_remaining = days_in_month - days_elapsed

        # --- Planned exclusions (if toggled) ---
        # Use first of TRACKER_START month so exclusions cover the full month
        # range visible in the monthly history (e.g. Mar 1 for a Mar 24 start)
        exclude_ids: set = set()
        exclusions: list[PlannedExclusion] = []
        if exclude_planned:
            exclude_ids, exclusions = await self._get_planned_exclusions()

        def _excl_filter():
            """Transaction ID exclusion filter."""
            if exclude_ids:
                return Transaction.id.notin_(exclude_ids)
            return True

        # --- Current month spending ---
        current_q = (
            select(
                CategoryMapping.normalized_category.label("category"),
                CategoryMapping.parent_category.label("parent"),
                CategoryMapping.is_discretionary,
                func.sum(-Transaction.amount).label("total_cents"),
                func.count().label("tx_count"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                self._tracker_base_query(),
                Transaction.date >= current_month_start,
                Transaction.date <= today,
                _excl_filter(),
            )
            .group_by(
                CategoryMapping.normalized_category,
                CategoryMapping.parent_category,
                CategoryMapping.is_discretionary,
            )
            .order_by(func.sum(-Transaction.amount).desc())
        )
        result = await self.db.execute(current_q)
        rows = result.all()

        spent_cents = sum(r.total_cents for r in rows)
        spent_so_far = _cents_to_dollars(spent_cents)

        # Category breakdown
        categories = []
        for r in rows:
            amt = _cents_to_dollars(r.total_cents)
            pct = (r.total_cents / spent_cents * 100) if spent_cents > 0 else 0
            categories.append(TrackerCategoryItem(
                category=r.category,
                parent_category=r.parent,
                amount=round(amt, 2),
                percentage=round(pct, 1),
                transaction_count=r.tx_count,
                is_discretionary=r.is_discretionary,
            ))

        # Pace calculations
        daily_average = spent_so_far / max(days_elapsed, 1)
        projected_total = daily_average * days_in_month
        budget_remaining = monthly_target - spent_so_far
        target_daily = monthly_target / days_in_month

        if daily_average <= target_daily * 0.95:
            status = "under_pace"
        elif daily_average <= target_daily * 1.05:
            status = "on_pace"
        else:
            status = "over_pace"

        # --- Pre-layoff baseline (Jan 1 - Mar 23, 2026) ---
        pre_start = date(2026, 1, 1)
        pre_end = date(2026, 3, 23)
        pre_days = (pre_end - pre_start).days + 1

        pre_q = (
            select(func.sum(-Transaction.amount).label("total_cents"))
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                self._tracker_base_query(),
                Transaction.date >= pre_start,
                Transaction.date <= pre_end,
            )
        )
        pre_result = await self.db.execute(pre_q)
        pre_total_cents = pre_result.scalar() or 0
        pre_total = _cents_to_dollars(pre_total_cents)
        pre_layoff_avg = pre_total / max(pre_days, 1) * 30.44  # avg month

        savings_vs_old = pre_layoff_avg - projected_total

        # Runway impact: savings / monthly cash deficit × 30 days
        # Monthly deficit from PATH.md: $14,747 pre-cuts, use $5,500 post-SEPP
        monthly_deficit = 5500.0
        runway_days_added = (savings_vs_old / monthly_deficit * 30) if monthly_deficit > 0 else 0

        # --- Monthly history since tracker start ---
        month_col = func.to_char(Transaction.date, "YYYY-MM").label("month")
        hist_q = (
            select(
                month_col,
                func.sum(-Transaction.amount).label("total_cents"),
                func.count(func.distinct(Transaction.date)).label("active_days"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                self._tracker_base_query(),
                Transaction.date >= self.TRACKER_START,
                Transaction.date <= today,
                _excl_filter(),
            )
            .group_by(month_col)
            .order_by(month_col)
        )
        hist_result = await self.db.execute(hist_q)

        months = []
        for r in hist_result.all():
            total = _cents_to_dollars(r.total_cents)
            # Figure out how many days this month had
            y, m = map(int, r.month.split("-"))
            month_days = calendar.monthrange(y, m)[1]
            # For partial months (start month or current month)
            if y == self.TRACKER_START.year and m == self.TRACKER_START.month:
                effective_days = month_days - self.TRACKER_START.day + 1
                # Pro-rate target for partial month
                month_target = monthly_target * effective_days / month_days
            elif y == today.year and m == today.month:
                effective_days = today.day
                month_target = monthly_target  # full target, compare at end
            else:
                effective_days = month_days
                month_target = monthly_target

            davg = total / max(effective_days, 1)
            delta = month_target - total
            target_daily_m = month_target / max(effective_days, 1)

            if davg <= target_daily_m * 0.95:
                m_status = "under_pace"
            elif davg <= target_daily_m * 1.05:
                m_status = "on_pace"
            else:
                m_status = "over_pace"

            months.append(TrackerMonthSummary(
                month=r.month,
                total=round(total, 2),
                target=round(month_target, 2),
                delta=round(delta, 2),
                daily_average=round(davg, 2),
                status=m_status,
            ))

        return TrackerSummaryResponse(
            current_month=today.strftime("%Y-%m"),
            start_date=self.TRACKER_START.isoformat(),
            monthly_target=monthly_target,
            spent_so_far=round(spent_so_far, 2),
            days_elapsed=days_elapsed,
            days_remaining=days_remaining,
            days_in_month=days_in_month,
            daily_average=round(daily_average, 2),
            projected_total=round(projected_total, 2),
            budget_remaining=round(budget_remaining, 2),
            status=status,
            pre_layoff_avg=round(pre_layoff_avg, 2),
            savings_vs_old=round(savings_vs_old, 2),
            runway_days_added=round(runway_days_added, 1),
            categories=categories,
            months=months,
            exclude_planned=exclude_planned,
            planned_exclusions=exclusions if exclude_planned else [],
        )

    async def get_tracker_daily(
        self,
        month: str | None = None,
        monthly_target: float = 5000.0,
        exclude_planned: bool = False,
    ) -> TrackerDailyResponse:
        """Daily cumulative spending for the accumulation chart."""
        today = date.today()

        if month:
            y, m = map(int, month.split("-"))
        else:
            y, m = today.year, today.month

        month_start = date(y, m, 1)
        days_in_month = calendar.monthrange(y, m)[1]
        month_end = date(y, m, days_in_month)

        # Don't go past today
        effective_end = min(month_end, today)

        # Planned exclusions
        exclude_ids: set = set()
        if exclude_planned:
            exclude_ids, _ = await self._get_planned_exclusions()

        excl_filter = Transaction.id.notin_(exclude_ids) if exclude_ids else True

        day_col = func.extract("day", Transaction.date).label("day_num")

        q = (
            select(
                day_col,
                func.sum(-Transaction.amount).label("total_cents"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(
                self._tracker_base_query(),
                Transaction.date >= month_start,
                Transaction.date <= effective_end,
                excl_filter,
            )
            .group_by(day_col)
            .order_by(day_col)
        )
        result = await self.db.execute(q)
        daily_map = {int(r.day_num): r.total_cents for r in result.all()}

        # Build cumulative array
        cumulative = 0.0
        target_daily = monthly_target / days_in_month
        days = []

        for d in range(1, effective_end.day + 1):
            daily_cents = daily_map.get(d, 0)
            daily_amt = _cents_to_dollars(daily_cents)
            cumulative += daily_amt
            days.append(TrackerDailyPoint(
                day=d,
                date=date(y, m, d).isoformat(),
                daily_amount=round(daily_amt, 2),
                cumulative=round(cumulative, 2),
                target_pace=round(target_daily * d, 2),
            ))

        return TrackerDailyResponse(
            month=f"{y:04d}-{m:02d}",
            days=days,
            monthly_target=monthly_target,
        )

    async def get_tracker_categories(
        self,
        month: str | None = None,
        exclude_planned: bool = False,
    ) -> list[TrackerCategoryItem]:
        """Category breakdown for a single month (defaults to current month).

        Same shape as the `categories` field on the summary response, but
        scoped to whichever month the user has selected — so the tracker's
        category card can follow the history selector.
        """
        today = date.today()
        if month:
            y, m = map(int, month.split("-"))
            start = date(y, m, 1)
            end = date(y, m, calendar.monthrange(y, m)[1])
            end = min(end, today)
        else:
            start = today.replace(day=1)
            end = today

        exclude_ids: set = set()
        if exclude_planned:
            exclude_ids, _ = await self._get_planned_exclusions()

        filters = [
            self._tracker_base_query(),
            Transaction.date >= start,
            Transaction.date <= end,
        ]
        if exclude_ids:
            filters.append(Transaction.id.notin_(exclude_ids))

        q = (
            select(
                CategoryMapping.normalized_category.label("category"),
                CategoryMapping.parent_category.label("parent"),
                CategoryMapping.is_discretionary,
                func.sum(-Transaction.amount).label("total_cents"),
                func.count().label("tx_count"),
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(*filters)
            .group_by(
                CategoryMapping.normalized_category,
                CategoryMapping.parent_category,
                CategoryMapping.is_discretionary,
            )
            .order_by(func.sum(-Transaction.amount).desc())
        )
        result = await self.db.execute(q)
        rows = result.all()
        total_cents = sum(r.total_cents for r in rows) or 0

        items: list[TrackerCategoryItem] = []
        for r in rows:
            amt = _cents_to_dollars(r.total_cents)
            pct = (r.total_cents / total_cents * 100) if total_cents > 0 else 0
            items.append(TrackerCategoryItem(
                category=r.category,
                parent_category=r.parent,
                amount=round(amt, 2),
                percentage=round(pct, 1),
                transaction_count=r.tx_count,
                is_discretionary=r.is_discretionary,
            ))
        return items

    async def get_tracker_transactions(
        self,
        category: str | None = None,
        month: str | None = None,
        limit: int = 50,
        offset: int = 0,
        exclude_planned: bool = False,
    ) -> TrackerTransactionsResponse:
        """Paginated transaction feed for the tracker."""
        today = date.today()

        if month:
            y, m = map(int, month.split("-"))
            start = date(y, m, 1)
            end = date(y, m, calendar.monthrange(y, m)[1])
            end = min(end, today)
        else:
            start = today.replace(day=1)
            end = today

        # Planned exclusions
        exclude_ids: set = set()
        if exclude_planned:
            exclude_ids, _ = await self._get_planned_exclusions()

        base = self._tracker_base_query()
        filters = [base, Transaction.date >= start, Transaction.date <= end]
        if exclude_ids:
            filters.append(Transaction.id.notin_(exclude_ids))

        if category:
            filters.append(CategoryMapping.normalized_category == category)

        # Count
        count_q = (
            select(func.count())
            .select_from(Transaction)
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(*filters)
        )
        count_result = await self.db.execute(count_q)
        total_count = count_result.scalar() or 0

        # Total amount
        sum_q = (
            select(func.sum(-Transaction.amount).label("total_cents"))
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(*filters)
        )
        sum_result = await self.db.execute(sum_q)
        total_cents = sum_result.scalar() or 0

        # Transactions
        tx_q = (
            select(
                Transaction.id,
                Transaction.date,
                Transaction.merchant,
                Transaction.amount,
                CategoryMapping.normalized_category.label("category"),
                CategoryMapping.parent_category.label("parent"),
                CategoryMapping.is_discretionary,
            )
            .join(CategoryMapping, Transaction.category == CategoryMapping.raw_category)
            .where(*filters)
            .order_by(Transaction.date.desc(), Transaction.amount.asc())
            .limit(limit)
            .offset(offset)
        )
        tx_result = await self.db.execute(tx_q)

        transactions = []
        for r in tx_result.all():
            transactions.append(TrackerTransaction(
                id=str(r.id),
                date=r.date.isoformat(),
                merchant=r.merchant,
                amount=round(_cents_to_dollars(-r.amount), 2),
                category=r.category,
                parent_category=r.parent,
                is_discretionary=r.is_discretionary,
            ))

        return TrackerTransactionsResponse(
            transactions=transactions,
            total_count=total_count,
            total_amount=round(_cents_to_dollars(total_cents), 2),
        )
