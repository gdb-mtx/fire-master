"""Category sync — pulls Monarch taxonomy, applies Mint→Monarch normalization, seeds category_mappings."""

import logging

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.monarch_client import MonarchClient
from app.models.category_mapping import CategoryMapping

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mint→Monarch normalization: maps old Mint category names to their
# Monarch equivalents. When both exist in the DB, they collapse to one.
# ---------------------------------------------------------------------------
MINT_TO_MONARCH: dict[str, str] = {
    "Restaurants": "Restaurants & Bars",
    "Gas": "Gas & Fuel",
    "Parking": "Parking & Tolls",
    "Taxi/Uber/Lyft": "Taxi & Ride Shares",
    "Rental Car & Taxi": "Taxi & Ride Shares",
    "Mortgage": "Mortgage & Rent",
    "Rent": "Mortgage & Rent",
    "Entertainment": "Entertainment & Recreation",
    "Movies & DVDs": "Entertainment & Recreation",
    "Phone": "Mobile Phone",
    "Internet": "Internet & Cable",
    "Television": "Internet & Cable",
    "Gift": "Gifts & Donations",
    "Gifts": "Gifts & Donations",
    "Charity": "Gifts & Donations",
    "Public Transit": "Public Transportation",
    "Electronics": "Electronics & Software",
    "Furniture & Housewares": "Furnishings",
    "Misc Expenses": "Miscellaneous",
    "Loan Repayment": "Loan Payment",
    "Service Fee": "Financial Fees",
    "Bank Fee": "Financial Fees",
    "Finance Charge": "Financial Fees",
    "ATM Fee": "Financial Fees",
    "Doctor": "Medical",
    "Eyecare": "Medical",
    "Therapy": "Medical",
    "Gym": "Health & Fitness",
    "Sporting Goods": "Sports",
    "Kids Education": "Education",
    "Tuition": "Education",
    "Home Phone": "Mobile Phone",
    "Home Cleaning": "Home Improvement",
    "Home Repair": "Home Improvement",
    "Lawn & Garden": "Home Improvement",
    "Home": "Home Improvement",
    "Water": "Utilities",
    "Gas & Electric": "Utilities",
    "Hobbies": "Entertainment & Recreation",
    "Arts": "Entertainment & Recreation",
    "Amusement": "Entertainment & Recreation",
    "Kids Activities": "Kids",
    "Toys": "Kids",
    "Auto Maintenance": "Service & Parts",
    "Auto Insurance": "Insurance",
    "Home Insurance": "Insurance",
    "Health Insurance": "Insurance",
    "Boat Insurance": "Insurance",
    "Life Insurance": "Insurance",
    "State Tax": "Taxes",
    "Federal Tax": "Taxes",
    "Local Tax": "Taxes",
    "Property Tax": "Taxes",
    "Tax Preparation Fee": "Taxes",
    "Personal": "Personal Care",
    "Newspapers & Magazines": "Books",
    "Tickets & Fines": "Fees & Charges",
    "Financial": "Financial & Legal Services",
    "Legal": "Financial & Legal Services",
    "Financial Advisor": "Financial & Legal Services",
    "Office Supplies": "Business Services",
    "Advertising": "Business Services",
    "Printing": "Business Services",
    "Student Loan": "Loan Payment",
}

# ---------------------------------------------------------------------------
# Category → Parent group mapping. Covers every normalized category name
# that will appear after Mint→Monarch normalization.
# ---------------------------------------------------------------------------
PARENT_GROUP: dict[str, str] = {
    # Food & Drink
    "Restaurants & Bars": "Food & Drink",
    "Groceries": "Food & Drink",
    "Coffee Shops": "Food & Drink",
    "Fast Food": "Food & Drink",
    "Alcohol & Bars": "Food & Drink",
    "Ice Cream": "Food & Drink",
    "Food & Dining": "Food & Drink",
    # Transportation
    "Gas & Fuel": "Transportation",
    "Parking & Tolls": "Transportation",
    "Taxi & Ride Shares": "Transportation",
    "Public Transportation": "Transportation",
    "Auto & Transport": "Transportation",
    "Auto Payment": "Transportation",
    "Service & Parts": "Transportation",
    "Car Wash": "Transportation",
    # Housing
    "Mortgage & Rent": "Housing",
    "Home Improvement": "Housing",
    "Home Supplies": "Housing",
    "Furnishings": "Housing",
    "Storage": "Housing",
    # Utilities
    "Utilities": "Utilities",
    "Mobile Phone": "Utilities",
    "Internet & Cable": "Utilities",
    # Entertainment
    "Entertainment & Recreation": "Entertainment",
    "Music": "Entertainment",
    "Sports": "Entertainment",
    "Books": "Entertainment",
    # Travel
    "Air Travel": "Travel",
    "Hotel": "Travel",
    "Vacation": "Travel",
    "Travel & Vacation": "Travel",
    "Travel": "Travel",
    # Shopping
    "Shopping": "Shopping",
    "Clothing": "Shopping",
    "Electronics & Software": "Shopping",
    # Health
    "Medical": "Health",
    "Pharmacy": "Health",
    "Dentist": "Health",
    "Health & Fitness": "Health",
    # Personal
    "Personal Care": "Personal",
    "Hair": "Personal",
    "Laundry": "Personal",
    # Kids & Family
    "Kids": "Kids & Family",
    "Child Support": "Kids & Family",
    "L Support": "Kids & Family",
    "Education": "Kids & Family",
    # Financial
    "Financial & Legal Services": "Financial",
    "Financial Fees": "Financial",
    "Fees & Charges": "Financial",
    "Interest": "Financial",
    # Insurance
    "Insurance": "Insurance",
    # Taxes
    "Taxes": "Taxes",
    # Business
    "Business Services": "Business",
    "Shipping": "Business",
    # Gifts
    "Gifts & Donations": "Gifts & Donations",
    # Boat
    "Boat Storage": "Boat",
    "Boat Gas": "Boat",
    "Boat Service & Parts": "Boat",
    # Income
    "Paychecks": "Income",
    "Other Income": "Income",
    "Business Income": "Income",
    # Transfers
    "Transfer": "Transfers",
    "Credit Card Payment": "Transfers",
    "Cash & ATM": "Transfers",
    "Check": "Transfers",
    "Loan Payment": "Transfers",
    # Buying a security moves money between asset classes — it is not consumption.
    "Investments": "Investments",
    "Buy": "Investments",
    # Misc
    "Miscellaneous": "Other",
    "Hide from Budgets & Trends": "Other",
}

# Categories flagged as income by NAME. This is a fallback only — the primary signal is
# the parent group Monarch itself assigned (see classify_flags). fire-master#11: custom-named
# income categories ("Alice - Paycheck") under Monarch's Income group were silently non-income.
INCOME_CATEGORIES = {"Paychecks", "Paycheck", "Other Income", "Business Income", "Interest"}
INCOME_PARENT = "Income"
TRANSFER_PARENT = "Transfers"
INVESTMENT_PARENT = "Investments"
TRANSFER_PARENTS = {TRANSFER_PARENT, INVESTMENT_PARENT}

# Categories flagged as transfers (not real spending)
TRANSFER_CATEGORIES = {
    "Transfer", "Credit Card Payment", "Cash & ATM", "Check",
    "Loan Payment", "Hide from Budgets & Trends", "Deposit",
    # Name-only fallback for rows that arrived without a parent group (Mint-era
    # imports); with a parent, the whole Investments group is caught above.
    "Investments", "Buy",
}

# Non-discretionary categories (needs, not wants)
NON_DISCRETIONARY = {
    "Mortgage & Rent", "Utilities", "Mobile Phone", "Internet & Cable",
    "Groceries", "Insurance", "Taxes", "Medical", "Pharmacy", "Dentist",
    "Child Support", "L Support", "Education", "Auto Payment",
    "Loan Payment",
}


def classify_flags(normalized: str, parent: str | None) -> tuple[bool, bool]:
    """(is_income, is_transfer) for a category.

    Monarch already grouped the category; trust that first (parent == "Income" /
    "Transfers", and "Investments" — a buy is a change of shape, not spending), then fall
    back to the hardcoded name sets for installs whose parent came from the Mint-era lookup
    table. A category is never both.
    """
    is_transfer = parent in TRANSFER_PARENTS or normalized in TRANSFER_CATEGORIES
    is_income = not is_transfer and (parent == INCOME_PARENT or normalized in INCOME_CATEGORIES)
    return is_income, is_transfer


class CategorySyncService:
    """Syncs Monarch categories and applies Mint normalization overrides."""

    def __init__(self, db: AsyncSession, client: MonarchClient | None = None):
        self.db = db
        self.client = client

    async def sync_from_monarch(self) -> int:
        """Pull categories and groups from Monarch API, upsert into category_mappings."""
        if not self.client:
            raise ValueError("MonarchClient required for API sync")

        groups = await self.client.get_transaction_category_groups()
        categories = await self.client.get_transaction_categories()

        # Build group_id → group_name map
        group_map: dict[str, str] = {}
        for g in groups:
            gid = str(g.get("id", ""))
            group_map[gid] = g.get("name", "Other")

        count = 0
        for cat in categories:
            raw_name = cat.get("name")
            if not raw_name:
                continue

            cat_id = str(cat.get("id", ""))
            group = cat.get("group") or cat.get("categoryGroup") or {}
            group_id = str(group.get("id", "")) if isinstance(group, dict) else ""
            parent = group_map.get(group_id, _lookup_parent(raw_name))

            normalized = MINT_TO_MONARCH.get(raw_name, raw_name)
            is_income, is_transfer = classify_flags(normalized, parent)

            stmt = insert(CategoryMapping).values(
                raw_category=raw_name,
                normalized_category=normalized,
                parent_category=parent,
                is_discretionary=normalized not in NON_DISCRETIONARY,
                is_income=is_income,
                is_transfer=is_transfer,
                monarch_category_id=cat_id or None,
            ).on_conflict_do_update(
                index_elements=["raw_category"],
                set_={
                    "normalized_category": normalized,
                    "parent_category": parent,
                    "is_discretionary": normalized not in NON_DISCRETIONARY,
                    "is_income": is_income,
                    "is_transfer": is_transfer,
                    "monarch_category_id": cat_id or None,
                },
            )
            await self.db.execute(stmt)
            count += 1

        await self.db.flush()
        logger.info("Synced %d categories from Monarch API", count)

        # Now backfill any raw categories from transactions that Monarch API didn't cover
        extra = await self._backfill_from_transactions()
        return count + extra

    async def sync_from_transactions(self) -> int:
        """Seed category_mappings from all distinct raw categories in transactions table.

        Use this when Monarch API isn't available or as a standalone seeding step.
        """
        return await self._backfill_from_transactions()

    async def _backfill_from_transactions(self) -> int:
        """Find raw categories in transactions that aren't in category_mappings yet, and insert them."""
        result = await self.db.execute(
            text("""
                SELECT DISTINCT t.category
                FROM transactions t
                LEFT JOIN category_mappings cm ON t.category = cm.raw_category
                WHERE t.category IS NOT NULL AND cm.id IS NULL
            """)
        )
        missing = [row[0] for row in result.all()]

        if not missing:
            return 0

        rows = []
        for raw in missing:
            normalized = MINT_TO_MONARCH.get(raw, raw)
            parent = _lookup_parent(normalized)
            is_income, is_transfer = classify_flags(normalized, parent)
            rows.append({
                "raw_category": raw,
                "normalized_category": normalized,
                "parent_category": parent,
                "is_discretionary": normalized not in NON_DISCRETIONARY,
                "is_income": is_income,
                "is_transfer": is_transfer,
            })

        for row in rows:
            stmt = insert(CategoryMapping).values(**row).on_conflict_do_nothing(
                index_elements=["raw_category"]
            )
            await self.db.execute(stmt)

        await self.db.flush()
        logger.info("Backfilled %d categories from transactions", len(rows))
        return len(rows)


def _lookup_parent(normalized_name: str) -> str:
    """Look up the parent group for a normalized category name."""
    return PARENT_GROUP.get(normalized_name, "Other")
