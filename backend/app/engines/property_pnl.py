"""Property P&L engine — classifies transactions to properties and aggregates per-property P&L.

Two layers:
  1. A pure classifier (`classify_transaction`) ported from scripts/report_property_pnl.py.
     No DB access, fully unit-testable. Walks a list of RuleView ordered by priority.
  2. DB orchestration on `PropertyPnLEngine`: reclassify (post-sync + manual trigger),
     per-transaction override, and the month-by-month P&L aggregation.

The classifier NEVER touches a transaction whose property_source == 'manual'; that is the
user-override escape hatch and it survives both Monarch re-sync and reclassification.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.enums import AccountType
from app.models.property import Property
from app.models.property_rule import PropertyRule
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

# Canonical expense category vocabulary (single source of truth). The config's short keys
# (hoa, mortgage, ...) are translated to these labels by the seed importer.
RENTAL_INCOME = "Rental Income"
EXPENSE_CATEGORIES = [
    "Mortgage",
    "HOA / Condo Fees",
    "Assessments",
    "Moorage",
    "Insurance",
    "Property Tax",
    "Utilities",
    "Repairs / Maintenance",
    "Cleaning / Turnover",
    "Other",
]


@dataclass(frozen=True)
class RuleView:
    """Lightweight, DB-free view of a PropertyRule for the pure classifier."""

    property_id: uuid.UUID | None
    pattern: str
    expense_category: str | None
    require_tx_category: str | None
    rule_kind: str  # 'exclusion' | 'income' | 'expense'
    priority: int


@dataclass(frozen=True)
class Classification:
    property_id: uuid.UUID | None
    category: str | None
    matched: bool  # True if a property rule matched (income or expense)


def classify_transaction(
    merchant: str | None,
    tx_category: str | None,
    amount_cents: int,
    rules: list[RuleView],
    is_credit_card: bool = False,
) -> Classification:
    """Pure classifier. Returns the property + category a transaction belongs to, or unmatched.

    Algorithm (mirrors scripts/report_property_pnl.py match_property/categorize/match_income):
      1. Exclusions first — a substring hit means "never a property transaction".
      2. Income rules — only for positive (income) amounts -> Rental Income.
      3. Expense rules — only for negative (expense) amounts, honoring require_tx_category.
    Rules are evaluated in ascending priority order.

    `is_credit_card` guards the income branch: rental income (a host payout) lands in a
    depository account, never on a credit card. A positive Airbnb/etc. amount on a credit
    card is a guest REFUND of a trip the owner booked, not rental income — so income rules
    are skipped for credit-card rows. (Expenses on a credit card still classify normally —
    property charges legitimately go on cards.)
    """
    merchant_lower = (merchant or "").lower()
    ordered = sorted(rules, key=lambda r: r.priority)

    # 1. Exclusions win outright.
    for rule in ordered:
        if rule.rule_kind == "exclusion" and rule.pattern.lower() in merchant_lower:
            return Classification(None, None, False)

    if amount_cents > 0:
        # 2. Income — but a positive amount on a credit card is a refund, not rental income.
        if is_credit_card:
            return Classification(None, None, False)
        for rule in ordered:
            if rule.rule_kind == "income" and rule.pattern.lower() in merchant_lower:
                return Classification(rule.property_id, RENTAL_INCOME, True)
        return Classification(None, None, False)

    if amount_cents < 0:
        # 3. Expense.
        for rule in ordered:
            if rule.rule_kind != "expense":
                continue
            if rule.pattern.lower() not in merchant_lower:
                continue
            if rule.require_tx_category and (tx_category or "") != rule.require_tx_category:
                continue
            category = rule.expense_category or _fallback_category(tx_category)
            return Classification(rule.property_id, category, True)

    return Classification(None, None, False)


def _fallback_category(tx_category: str | None) -> str:
    """Derive an expense category from the raw transaction category when a rule carries none."""
    cat = (tx_category or "").lower()
    if "mortgage" in cat:
        return "Mortgage"
    if "rent" in cat:
        return "HOA / Condo Fees"
    if "insurance" in cat:
        return "Insurance"
    if "tax" in cat:
        return "Property Tax"
    if "repair" in cat or "improvement" in cat:
        return "Repairs / Maintenance"
    if "electric" in cat or "gas" in cat or "water" in cat:
        return "Utilities"
    return "Other"


def tag_category(amount_cents: int, tx_category: str | None) -> str:
    """Property category for a row classified by a Monarch tag.

    Sign is the usual signal but it is NOT identity. A returned security deposit, a
    rent chargeback or a refunded overpayment is negative Rental Income, and the
    module had no way to express that: the negative branch fell through to
    _fallback_category, whose `"rent" in cat` test matches the substring inside
    "Rental Income" and returned "HOA / Condo Fees". A returned deposit on an income
    property booked itself as an HOA fee that way — gross rents and HOA expense each
    overstated by the same amount, so net profit looked right and Schedule E did not.

    So: when Monarch's own category says Rental Income, honour it in both directions.
    The match is EXACT, never a substring, because _fallback_category deliberately
    maps the expense categories "Rent" and "Mortgage & Rent" to HOA / Condo Fees for
    ~83 real condo-fee rows. A substring test on "rent" is precisely the bug.
    """
    if amount_cents > 0:
        return RENTAL_INCOME
    if (tx_category or "").strip().lower() == RENTAL_INCOME.lower():
        return RENTAL_INCOME
    return _fallback_category(tx_category)


def match_tag_to_property(tags, name_to_id: dict[str, uuid.UUID]) -> uuid.UUID | None:
    """Inbound direction: a Monarch tag whose name matches a property -> that property's id.

    This is what lets the user tag a transaction in Monarch (e.g. a Venmo with no merchant
    signal a rule could catch) and have it feed our per-property P&L. Tag names are matched
    case-insensitively against property names; first match wins. `tags` is the list of tag
    names synced from Monarch (Transaction.tags), or None.
    """
    if not tags:
        return None
    for tag in tags:
        pid = name_to_id.get((tag or "").strip().lower())
        if pid is not None:
            return pid
    return None


def resolve_with_tag(
    c: Classification,
    tag_pid: uuid.UUID | None,
    amount_cents: int,
    tx_category: str | None,
) -> tuple[uuid.UUID | None, str | None, str | None]:
    """Combine a rule Classification with an optional Monarch-tag property. Precedence:
    explicit Monarch tag > merchant rule. (Manual overrides are handled upstream in reclassify
    and never reach here.) Returns (property_id, property_category, property_source).

    When the tag merely agrees with a matched rule we keep the rule's (richer) category and a
    'rule' source, so the ~hundreds of rows we already mirror to Monarch don't churn to a new
    source on every reclassify.
    """
    if tag_pid is not None and not (c.matched and c.property_id == tag_pid):
        category = tag_category(amount_cents, tx_category)
        return tag_pid, category, "monarch_tag"
    if c.matched:
        return c.property_id, c.category, "rule"
    return None, None, None


class PropertyPnLEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _load_rules(self) -> list[RuleView]:
        result = await self.db.execute(
            select(PropertyRule).where(PropertyRule.is_active == True)  # noqa: E712
        )
        return [
            RuleView(
                property_id=r.property_id,
                pattern=r.pattern,
                expense_category=r.expense_category,
                require_tx_category=r.require_tx_category,
                rule_kind=r.rule_kind,
                priority=r.priority,
            )
            for r in result.scalars().all()
        ]

    # ------------------------------------------------------------------
    #  Classification
    # ------------------------------------------------------------------

    async def reclassify(self) -> dict[str, int]:
        """Re-run the classifier over every non-manual transaction.

        Stamps property_id / property_category / property_source='rule' (or clears them when
        nothing matches, so deleting a rule un-assigns previously matched rows on the next run).
        Rows with property_source == 'manual' are never touched (the override guard).
        """
        rules = await self._load_rules()

        # Property-name -> id, for mapping inbound Monarch tags onto properties.
        prop_rows = await self.db.execute(select(Property.id, Property.name))
        name_to_id = {name.strip().lower(): pid for pid, name in prop_rows.all()}

        result = await self.db.execute(
            select(
                Transaction.id,
                Transaction.merchant,
                Transaction.category,
                Transaction.amount,
                Transaction.tags,
                Transaction.property_id,
                Transaction.property_category,
                Account.account_type,
            )
            .join(Account, Account.id == Transaction.account_id)
            .where(Transaction.property_source.is_distinct_from("manual"))
        )
        rows = result.all()

        matched = 0
        unmatched = 0
        for row in rows:
            is_cc = row.account_type == AccountType.CREDIT_CARD
            c = classify_transaction(
                row.merchant, row.category, int(row.amount), rules, is_credit_card=is_cc
            )
            tag_pid = match_tag_to_property(row.tags, name_to_id)
            new_pid, new_cat, new_src = resolve_with_tag(
                c, tag_pid, int(row.amount), row.category
            )
            if new_pid is not None:
                matched += 1
            else:
                unmatched += 1
            # Only write when something actually changed.
            if row.property_id != new_pid or row.property_category != new_cat:
                await self.db.execute(
                    update(Transaction)
                    .where(Transaction.id == row.id)
                    .values(property_id=new_pid, property_category=new_cat, property_source=new_src)
                )

        # Count manual rows for reporting.
        skipped_result = await self.db.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.property_source == "manual")
        )
        skipped_manual = int(skipped_result.scalar() or 0)

        await self.db.flush()
        return {"matched": matched, "unmatched": unmatched, "skipped_manual": skipped_manual}

    async def set_override(
        self,
        tx_id: uuid.UUID,
        property_id: uuid.UUID | None,
        category: str | None,
    ) -> None:
        """Manually assign (or, with property_id=None, exclude) a single transaction.

        Sets property_source='manual' so reclassify never overwrites it.
        """
        await self.db.execute(
            update(Transaction)
            .where(Transaction.id == tx_id)
            .values(property_id=property_id, property_category=category, property_source="manual")
        )
        await self.db.flush()

    async def clear_override(self, tx_id: uuid.UUID) -> None:
        """Reset a manual override and re-classify that single transaction by the rules."""
        rules = await self._load_rules()
        result = await self.db.execute(
            select(
                Transaction.merchant,
                Transaction.category,
                Transaction.amount,
                Account.account_type,
            )
            .join(Account, Account.id == Transaction.account_id)
            .where(Transaction.id == tx_id)
        )
        row = result.first()
        if row is None:
            return
        is_cc = row.account_type == AccountType.CREDIT_CARD
        c = classify_transaction(
            row.merchant, row.category, int(row.amount), rules, is_credit_card=is_cc
        )
        await self.db.execute(
            update(Transaction)
            .where(Transaction.id == tx_id)
            .values(
                property_id=c.property_id if c.matched else None,
                property_category=c.category if c.matched else None,
                property_source="rule" if c.matched else None,
            )
        )
        await self.db.flush()

    # ------------------------------------------------------------------
    #  P&L aggregation
    # ------------------------------------------------------------------

    async def get_pnl(self, start: date | None = None, end: date | None = None) -> dict:
        """Month-by-month P&L per property.

        Selects straight from transactions filtered by property_id (no CategoryMapping join),
        so manual / off-Monarch entries are included. Income vs expense is decided by amount sign.
        """
        props_result = await self.db.execute(
            select(Property).where(Property.is_active == True).order_by(Property.display_order)  # noqa: E712
        )
        properties = props_result.scalars().all()
        prop_by_id = {p.id: p for p in properties}

        month_col = func.to_char(Transaction.date, "YYYY-MM").label("month")
        conditions = [Transaction.property_id.isnot(None)]
        if start:
            conditions.append(Transaction.date >= start)
        if end:
            conditions.append(Transaction.date <= end)

        rows_result = await self.db.execute(
            select(
                Transaction.property_id,
                Transaction.property_category,
                month_col,
                func.sum(Transaction.amount).label("total_cents"),
            )
            .where(*conditions)
            .group_by(Transaction.property_id, Transaction.property_category, month_col)
        )
        rows = rows_result.all()

        # property_id -> category -> {month: cents}
        matrix: dict[uuid.UUID, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        all_months: set[str] = set()
        for r in rows:
            all_months.add(r.month)
            category = r.property_category or "Other"
            # Expenses are stored negative; surface them as positive cost magnitudes.
            # Rental Income stays positive.
            cents = int(r.total_cents)
            if category == RENTAL_INCOME:
                matrix[r.property_id][RENTAL_INCOME][r.month] += cents
            else:
                matrix[r.property_id][category][r.month] += -cents

        months = sorted(all_months)

        properties_out = []
        for p in properties:
            cat_matrix = matrix.get(p.id, {})
            total_cost = sum(
                v
                for cat, by_month in cat_matrix.items()
                if cat != RENTAL_INCOME
                for v in by_month.values()
            )
            rental_actual = sum(cat_matrix.get(RENTAL_INCOME, {}).values())
            potential = p.potential_monthly_rental_cents or 0
            # Annualize potential over the months covered for a like-for-like comparison.
            months_covered = len({m for by_month in cat_matrix.values() for m in by_month}) or 1
            rental_potential = potential * months_covered

            equity = p.equity_cents
            cost_per_equity = (
                round(total_cost / equity, 4) if equity > 0 else None
            )

            properties_out.append(
                {
                    "property": _property_dict(p),
                    "categories": {
                        cat: dict(by_month) for cat, by_month in cat_matrix.items()
                    },
                    "totals": {
                        "total_cost_cents": total_cost,
                        "rental_actual_cents": rental_actual,
                        "rental_potential_cents": rental_potential,
                        "net_cost_cents": total_cost - rental_actual,
                        "monthly_cost_cents": round(total_cost / months_covered),
                        # Net annualized over the months of activity (same basis as monthly_cost),
                        # so the card shows net PER YEAR, not the cumulative all-time net.
                        "net_per_year_cents": round((total_cost - rental_actual) * 12 / months_covered),
                    },
                    "cost_per_equity": cost_per_equity,
                }
            )

        return {"months": months, "properties": properties_out}


def _property_dict(p: Property) -> dict:
    return {
        "id": str(p.id),
        "key": p.key,
        "name": p.name,
        "address": p.address,
        "color": p.color,
        "value_cents": p.value_cents,
        "loan_balance_cents": p.loan_balance_cents,
        "equity_cents": p.equity_cents,
        "purchase_price_cents": p.purchase_price_cents,
        "purchase_date": p.purchase_date.isoformat() if p.purchase_date else None,
        "potential_monthly_rental_cents": p.potential_monthly_rental_cents,
        "potential_monthly_rental_full_cents": p.potential_monthly_rental_full_cents,
        "display_order": p.display_order,
        "is_active": p.is_active,
        "notes": p.notes or [],
        "extra_data": p.extra_data or {},
    }
