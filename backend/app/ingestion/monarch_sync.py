"""Monarch Money sync orchestrator — upserts accounts, transactions, and balance snapshots."""

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.monarch_client import MonarchClient
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot
from app.models.enums import AccountType, DataSource
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

BATCH_SIZE = 500

# Incremental sync look-back window. Must comfortably exceed the longest realistic gap
# between syncs (and late-posting/pending->cleared lag in Monarch). At 7 days, any txn that
# landed in Monarch more than a week after its date — or any sync gap > 7 days — was missed
# permanently (no incremental pass ever looks back further). 45 days covers a billing cycle
# plus a buffer. If the stack is ever offline longer than this, run a full backfill:
#   uv run python -c "import asyncio; from datetime import date; ..."  (or run_full_sync(full_history=True))
INCREMENTAL_SYNC_DAYS = 45


def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


# Map Monarch account type strings to our enum
ACCOUNT_TYPE_MAP: dict[str, AccountType] = {
    "brokerage": AccountType.TAXABLE,
    "depository": AccountType.CHECKING,
    "checking": AccountType.CHECKING,
    "savings": AccountType.SAVINGS,
    "cd": AccountType.SAVINGS,
    "cash_management": AccountType.SAVINGS,
    "money_market": AccountType.SAVINGS,
    "investment": AccountType.TAXABLE,
    "credit": AccountType.CREDIT_CARD,
    "loan": AccountType.OTHER,
    "car": AccountType.VEHICLE,
    "auto": AccountType.VEHICLE,
    "auto_loan": AccountType.VEHICLE,
    "vehicle_loan": AccountType.VEHICLE,
    "mortgage": AccountType.REAL_ESTATE,
    "property": AccountType.REAL_ESTATE,
    "real_estate": AccountType.REAL_ESTATE,
    "vehicle": AccountType.VEHICLE,
    "cryptocurrency": AccountType.CRYPTO,
    "other_asset": AccountType.OTHER,
    "other_liability": AccountType.OTHER,
}


def _dollars_to_cents(amount: float) -> int:
    """Convert dollar float to integer cents, avoiding floating-point drift."""
    return int(round(amount * 100))


def _snapshot_balance(current_balance: int, is_asset: bool) -> int:
    """Balance for a today-snapshot row, matching the history API's sign
    convention: liabilities negative. Monarch's account API reports loan
    balances as positive amounts owed, while its balance-history API reports
    them negative — writing current_balance verbatim made every liability's
    chart spike positive on the last point (fire-master#7)."""
    if is_asset:
        return current_balance
    return -abs(current_balance)


def _account_upsert_stmt(raw: dict):
    """Build the accounts upsert for one raw Monarch account payload."""
    balance = raw.get("displayBalance") or raw.get("currentBalance", 0) or 0
    account_type = _map_account_type(
        raw.get("type", {}).get("name", "other") if isinstance(raw.get("type"), dict) else raw.get("type", "other"),
        raw.get("subtype", {}).get("name") if isinstance(raw.get("subtype"), dict) else raw.get("subtype"),
    )
    institution_name = None
    inst = raw.get("institution")
    if isinstance(inst, dict):
        institution_name = inst.get("name")
    elif isinstance(inst, str):
        institution_name = inst

    stmt = insert(Account).values(
        external_id=str(raw["id"]),
        name=raw.get("displayName") or raw.get("name", "Unknown"),
        account_type=account_type,
        institution=institution_name,
        current_balance=_dollars_to_cents(balance),
        is_asset=raw.get("isAsset", True),
        include_in_net_worth=raw.get("includeInNetWorth", True),
        source=DataSource.MONARCH,
        last_synced_at=datetime.now(timezone.utc),
        extra_data={
            "monarch_type": raw.get("type"),
            "monarch_subtype": raw.get("subtype"),
            "is_closed": raw.get("isArchived", False),
        },
    )
    return stmt.on_conflict_do_update(
        index_elements=["external_id"],
        set_={
            "name": raw.get("displayName") or raw.get("name", "Unknown"),
            # account_type self-heals from the mapper on every sync
            # (fire-master#6) UNLESS the user set a manual override via
            # enrichment — custom_data.account_type_manual is the clobber
            # guard (fire-master#8), same pattern as property_source='manual'.
            # else_ must be excluded.account_type, not the raw enum member:
            # a bare literal binds as a plain string (the lowercase .value),
            # which Postgres rejects — the enum labels are the member NAMES.
            "account_type": case(
                (
                    Account.custom_data["account_type_manual"].as_boolean().is_(True),
                    Account.account_type,
                ),
                else_=stmt.excluded.account_type,
            ),
            "institution": institution_name,
            "current_balance": _dollars_to_cents(balance),
            "is_asset": raw.get("isAsset", True),
            "include_in_net_worth": raw.get("includeInNetWorth", True),
            "last_synced_at": datetime.now(timezone.utc),
            "extra_data": {
                "monarch_type": raw.get("type"),
                "monarch_subtype": raw.get("subtype"),
                "is_closed": raw.get("isArchived", False),
            },
        },
    )


def _map_account_type(monarch_type: str, monarch_subtype: str | None = None) -> AccountType:
    """Map Monarch account type/subtype to our AccountType enum."""
    if monarch_subtype:
        subtype_lower = monarch_subtype.lower().replace(" ", "_")
        # Check for specific subtypes first
        if "401" in subtype_lower:
            return AccountType.FOUR_OH_ONE_K
        if "ira" in subtype_lower and "roth" in subtype_lower:
            return AccountType.ROTH_IRA
        if "ira" in subtype_lower:
            return AccountType.IRA
        # Bare "roth" (Monarch brokerage subtype) — after the 401/ira checks
        # so roth_401k stays FOUR_OH_ONE_K and roth_ira stays ROTH_IRA
        if "roth" in subtype_lower:
            return AccountType.ROTH_IRA
        if "hsa" in subtype_lower:
            return AccountType.HSA
        if subtype_lower in ACCOUNT_TYPE_MAP:
            return ACCOUNT_TYPE_MAP[subtype_lower]

    type_lower = monarch_type.lower().replace(" ", "_") if monarch_type else "other"
    return ACCOUNT_TYPE_MAP.get(type_lower, AccountType.OTHER)


@dataclass
class SyncResult:
    accounts_synced: int = 0
    transactions_synced: int = 0
    transactions_deleted: int = 0
    snapshots_synced: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class MonarchSyncService:
    def __init__(self, db: AsyncSession, client: MonarchClient):
        self.db = db
        self.client = client

    async def sync_accounts(self) -> int:
        """Upsert all Monarch accounts into the database."""
        raw_accounts = await self.client.get_accounts()
        count = 0

        for raw in raw_accounts:
            try:
                # Skip hidden/deactivated accounts
                if raw.get("isHidden") or raw.get("deactivatedAt"):
                    continue

                await self.db.execute(_account_upsert_stmt(raw))
                count += 1
            except Exception as e:
                logger.error("Failed to sync account %s: %s", raw.get("id"), e)

        await self.db.flush()

        # Write a balance snapshot for today using each account's current balance.
        # This ensures compute_snapshot(today) matches calculate_current() even for
        # stale accounts whose historical snapshots lag behind.
        result = await self.db.execute(
            select(Account.id, Account.current_balance, Account.is_asset).where(
                Account.source == DataSource.MONARCH
            )
        )
        today_rows = [
            {
                "account_id": row.id,
                "date": date.today(),
                "balance": _snapshot_balance(row.current_balance, row.is_asset),
                "source": DataSource.MONARCH,
            }
            for row in result.all()
        ]
        if today_rows:
            for chunk in _chunks(today_rows, BATCH_SIZE):
                stmt = insert(BalanceSnapshot).values(chunk).on_conflict_do_update(
                    constraint="uq_balance_snapshot_account_date",
                    set_={"balance": insert(BalanceSnapshot).excluded.balance},
                )
                await self.db.execute(stmt)
            await self.db.flush()

        logger.info("Synced %d accounts", count)
        return count

    async def sync_transactions(self, start_date: date | None = None) -> int:
        """Upsert transactions from Monarch in batches. Default: last INCREMENTAL_SYNC_DAYS."""
        if start_date is None:
            start_date = date.today() - timedelta(days=INCREMENTAL_SYNC_DAYS)

        raw_transactions = await self.client.get_transactions(start_date=start_date)

        # Build a map of external_id -> internal account id
        result = await self.db.execute(
            select(Account.id, Account.external_id).where(Account.external_id.isnot(None))
        )
        account_map = {row.external_id: row.id for row in result}

        # Parse all rows first, skip bad ones
        rows = []
        for raw in raw_transactions:
            try:
                external_id = str(raw["id"])
                account_ext_id = str(raw.get("account", {}).get("id", ""))
                account_id = account_map.get(account_ext_id)
                if not account_id:
                    logger.warning("Transaction %s references unknown account %s", external_id, account_ext_id)
                    continue

                amount = raw.get("amount", 0) or 0
                category = raw.get("category", {})
                category_name = category.get("name") if isinstance(category, dict) else None

                tags = []
                raw_tags = raw.get("tags", [])
                if raw_tags:
                    tags = [t.get("name", "") for t in raw_tags if isinstance(t, dict)]

                tx_date = raw.get("date", date.today().isoformat())
                if isinstance(tx_date, str):
                    tx_date = date.fromisoformat(tx_date)

                merchant = raw.get("merchant", {}).get("name") if isinstance(raw.get("merchant"), dict) else raw.get("merchant")

                rows.append({
                    "external_id": external_id,
                    "account_id": account_id,
                    "date": tx_date,
                    "amount": _dollars_to_cents(amount),
                    "category": category_name,
                    "subcategory": None,
                    "merchant": merchant,
                    "tags": tags,
                    "is_recurring": raw.get("isRecurring", False),
                    "hide_from_reports": raw.get("hideFromReports", False),
                    "notes": raw.get("notes"),
                    "source": DataSource.MONARCH,
                })
            except Exception as e:
                logger.error("Failed to parse transaction %s: %s", raw.get("id"), e)

        # Batch upsert in chunks
        count = 0
        for chunk in _chunks(rows, BATCH_SIZE):
            stmt = insert(Transaction).values(chunk).on_conflict_do_update(
                index_elements=["external_id"],
                set_={
                    "date": insert(Transaction).excluded.date,
                    "amount": insert(Transaction).excluded.amount,
                    "category": insert(Transaction).excluded.category,
                    "merchant": insert(Transaction).excluded.merchant,
                    "tags": insert(Transaction).excluded.tags,
                    "is_recurring": insert(Transaction).excluded.is_recurring,
                    "hide_from_reports": insert(Transaction).excluded.hide_from_reports,
                    "notes": insert(Transaction).excluded.notes,
                },
            )
            await self.db.execute(stmt)
            count += len(chunk)

        await self.db.flush()
        logger.info("Synced %d transactions (from %s)", count, start_date)
        return count

    async def sync_balance_snapshots(self) -> int:
        """Upsert historical balance snapshots for all accounts in batches."""
        result = await self.db.execute(
            select(Account.id, Account.external_id).where(
                Account.external_id.isnot(None),
                Account.source == DataSource.MONARCH,
            )
        )
        accounts = result.all()

        # Collect all snapshot rows across all accounts
        rows = []
        for account_id, external_id in accounts:
            try:
                history = await self.client.get_account_history(external_id)
                for point in history:
                    snap_date = point.get("date")
                    if isinstance(snap_date, str):
                        snap_date = date.fromisoformat(snap_date)
                    balance = point.get("signedBalance", 0) or point.get("balance", 0) or 0

                    rows.append({
                        "account_id": account_id,
                        "date": snap_date,
                        "balance": _dollars_to_cents(balance),
                        "source": DataSource.MONARCH,
                    })
            except Exception as e:
                logger.error("Failed to fetch snapshots for account %s: %s", external_id, e)

        # Batch upsert in chunks
        count = 0
        for chunk in _chunks(rows, BATCH_SIZE):
            stmt = insert(BalanceSnapshot).values(chunk).on_conflict_do_update(
                constraint="uq_balance_snapshot_account_date",
                set_={"balance": insert(BalanceSnapshot).excluded.balance},
            )
            await self.db.execute(stmt)
            count += len(chunk)

        await self.db.flush()
        logger.info("Synced %d balance snapshots", count)
        return count

    async def reconcile_transactions(self, start_date: date | None = None) -> int:
        """Delete local transactions that no longer exist in Monarch.

        Compares external_ids from Monarch against our DB and removes orphans.
        Only runs on the same date range as the transaction sync to avoid
        deleting transactions outside the fetched window.
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=INCREMENTAL_SYNC_DAYS)

        # Get all transaction external_ids from Monarch for the date range
        raw_transactions = await self.client.get_transactions(start_date=start_date)
        monarch_ids = {str(raw["id"]) for raw in raw_transactions if "id" in raw}

        # Get our external_ids for the same date range
        result = await self.db.execute(
            select(Transaction.id, Transaction.external_id).where(
                Transaction.source == DataSource.MONARCH,
                Transaction.external_id.isnot(None),
                Transaction.date >= start_date,
            )
        )
        local_rows = result.all()

        # Find orphans: in our DB but not in Monarch
        orphan_ids = [row.id for row in local_rows if row.external_id not in monarch_ids]

        if orphan_ids:
            for chunk in _chunks(orphan_ids, BATCH_SIZE):
                await self.db.execute(
                    delete(Transaction).where(Transaction.id.in_(chunk))
                )
            await self.db.flush()
            logger.info("Reconciled: deleted %d orphaned transactions (from %s)", len(orphan_ids), start_date)
        else:
            logger.info("Reconciliation: no orphaned transactions found (from %s)", start_date)

        return len(orphan_ids)

    async def run_full_sync(self, full_history: bool = False) -> SyncResult:
        """Run a complete sync: accounts → transactions → balance snapshots."""
        result = SyncResult()

        try:
            result.accounts_synced = await self.sync_accounts()
        except Exception as e:
            logger.error("Account sync failed: %s", e)
            result.errors.append(f"Account sync: {e}")

        try:
            start = date(2018, 1, 1) if full_history else None
            result.transactions_synced = await self.sync_transactions(start_date=start)
        except Exception as e:
            logger.error("Transaction sync failed: %s", e)
            result.errors.append(f"Transaction sync: {e}")

        try:
            result.transactions_deleted = await self.reconcile_transactions(start_date=start)
        except Exception as e:
            logger.error("Transaction reconciliation failed: %s", e)
            result.errors.append(f"Transaction reconciliation: {e}")

        try:
            result.snapshots_synced = await self.sync_balance_snapshots()
        except Exception as e:
            logger.error("Balance snapshot sync failed: %s", e)
            result.errors.append(f"Snapshot sync: {e}")

        # Re-classify property transactions so newly synced rows land in the Property P&L
        # module (and out of the Tracker). Manual overrides are preserved by reclassify.
        # Isolated in its own try/except so a classifier failure never aborts the sync.
        try:
            from app.engines.property_pnl import PropertyPnLEngine

            counts = await PropertyPnLEngine(self.db).reclassify()
            logger.info("Property reclassify: %s", counts)
        except Exception as e:
            logger.error("Property reclassification failed: %s", e)
            result.errors.append(f"Property reclassification: {e}")

        # Going live: once a sync has brought in real (Monarch) accounts, auto-clear the
        # demo persona so the user sees only their own data. Marker-scoped — it can only
        # ever delete demo rows, never real ones. Opt out with AUTO_CLEAR_DEMO=false.
        # Isolated in its own try/except so a failure never aborts the sync.
        try:
            if os.environ.get("AUTO_CLEAR_DEMO", "true").strip().lower() != "false":
                from app.ingestion.demo_data import (
                    clear_demo_data,
                    has_demo_rows,
                    has_real_accounts,
                )

                if await has_real_accounts(self.db) and await has_demo_rows(self.db):
                    summary = await clear_demo_data(self.db)
                    logger.info("Demo persona auto-cleared after going live: %s", summary)
        except Exception as e:
            logger.error("Demo auto-clear failed: %s", e)
            result.errors.append(f"Demo auto-clear: {e}")

        await self.db.flush()
        logger.info(
            "Full sync complete: %d accounts, %d transactions (%d deleted), %d snapshots, %d errors",
            result.accounts_synced,
            result.transactions_synced,
            result.transactions_deleted,
            result.snapshots_synced,
            len(result.errors),
        )
        return result
