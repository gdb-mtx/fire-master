"""Asset Hub engine — unified view of financial accounts + physical assets with enrichment."""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot
from app.models.net_worth_snapshot import NetWorthSnapshot
from app.models.physical_asset import PhysicalAsset
from app.models.transaction import Transaction
from app.schemas.assets import (
    AccountTileResponse,
    AssetHubResponse,
    PhysicalAssetTileResponse,
)

logger = logging.getLogger(__name__)


def _cents_to_dollars(cents: int) -> float:
    return cents / 100


class AssetHubEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_asset_hub(self) -> AssetHubResponse:
        """Unified Asset Hub: all active accounts + all active physical assets."""
        account_tiles = await self.get_account_tiles()
        physical_tiles = await self.get_physical_asset_tiles()

        # Compute totals from accounts
        total_assets = 0
        total_liabilities = 0
        for tile in account_tiles:
            if tile.is_asset:
                total_assets += abs(tile.balance_cents)
            else:
                total_liabilities += abs(tile.balance_cents)

        total_physical = sum(t.current_value_cents for t in physical_tiles)

        return AssetHubResponse(
            accounts=account_tiles,
            physical_assets=physical_tiles,
            total_net_worth=_cents_to_dollars(total_assets - total_liabilities),
            total_assets=_cents_to_dollars(total_assets),
            total_liabilities=_cents_to_dollars(total_liabilities),
            total_physical_assets=_cents_to_dollars(total_physical),
        )

    async def get_account_tiles(self) -> list[AccountTileResponse]:
        """All active accounts as tiles with enrichment indicators and 30d change."""
        result = await self.db.execute(
            select(Account)
            .where(Account.include_in_net_worth == True)
            .order_by(Account.is_asset.desc(), Account.current_balance.desc())
        )
        accounts = result.scalars().all()

        # Batch-fetch 30d balance changes
        thirty_days_ago = date.today() - timedelta(days=30)
        snap_result = await self.db.execute(
            select(BalanceSnapshot.account_id, BalanceSnapshot.balance)
            .where(BalanceSnapshot.date <= thirty_days_ago)
            .distinct(BalanceSnapshot.account_id)
            .order_by(BalanceSnapshot.account_id, BalanceSnapshot.date.desc())
        )
        old_balances = {row.account_id: row.balance for row in snap_result.all()}

        tiles = []
        for acct in accounts:
            old_bal = old_balances.get(acct.id)
            change_30d = None
            if old_bal is not None:
                change_30d = _cents_to_dollars(acct.current_balance - old_bal)

            has_enrichment = any([acct.notes, acct.tags, acct.fire_role, acct.strategy])

            tiles.append(AccountTileResponse(
                id=acct.id,
                name=acct.name,
                account_type=acct.account_type.value,
                institution=acct.institution,
                balance_cents=acct.current_balance,
                is_asset=acct.is_asset,
                fire_role=acct.fire_role,
                tags=acct.tags,
                has_enrichment=has_enrichment,
                value_change_30d=change_30d,
            ))
        return tiles

    async def get_physical_asset_tiles(self) -> list[PhysicalAssetTileResponse]:
        """All active physical assets as tiles."""
        result = await self.db.execute(
            select(PhysicalAsset)
            .where(PhysicalAsset.is_active == True)
            .order_by(PhysicalAsset.current_value.desc())
        )
        assets = result.scalars().all()

        tiles = []
        for asset in assets:
            # Compute equity if linked to a mortgage/loan account
            equity_cents = None
            if asset.linked_account_id:
                acct = await self.db.get(Account, asset.linked_account_id)
                if acct:
                    equity_cents = asset.current_value - abs(acct.current_balance)

            tiles.append(PhysicalAssetTileResponse(
                id=asset.id,
                name=asset.name,
                asset_category=asset.asset_category,
                subcategory=asset.subcategory,
                current_value_cents=asset.current_value,
                equity_cents=equity_cents,
                fire_role=asset.fire_role,
                tags=asset.tags,
                has_notes=bool(asset.notes),
            ))
        return tiles

    async def get_account_detail(self, account_id) -> dict:
        """Full account detail: enrichment + balance history + recent transactions."""
        account = await self.db.get(Account, account_id)
        if not account:
            return None

        # Balance history (all time)
        hist_result = await self.db.execute(
            select(BalanceSnapshot)
            .where(BalanceSnapshot.account_id == account_id)
            .order_by(BalanceSnapshot.date)
        )
        snapshots = hist_result.scalars().all()
        balance_history = [
            {"date": s.date.isoformat(), "balance": _cents_to_dollars(s.balance)}
            for s in snapshots
        ]

        # Recent transactions (last 90 days)
        ninety_days_ago = date.today() - timedelta(days=90)
        tx_result = await self.db.execute(
            select(Transaction)
            .where(
                Transaction.account_id == account_id,
                Transaction.date >= ninety_days_ago,
            )
            .order_by(Transaction.date.desc())
            .limit(100)
        )
        transactions = tx_result.scalars().all()
        recent_transactions = [
            {
                "id": str(tx.id),
                "date": tx.date.isoformat(),
                "amount": _cents_to_dollars(tx.amount),
                "category": tx.category,
                "merchant": tx.merchant,
            }
            for tx in transactions
        ]

        return {
            "account": account,
            "balance_history": balance_history,
            "recent_transactions": recent_transactions,
        }

    async def update_account_enrichment(self, account_id, data) -> Account | None:
        """Update only enrichment fields on an account."""
        account = await self.db.get(Account, account_id)
        if not account:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # account_type is a sync-managed field with a manual-override escape
        # hatch (fire-master#8): a value stamps the manual flag so the sync
        # upsert's clobber guard preserves it; an explicit null clears the
        # flag and lets auto-mapping resume on the next sync.
        account_type = update_data.pop("account_type", "__absent__")

        for field, value in update_data.items():
            setattr(account, field, value)

        if account_type != "__absent__":
            base_custom = dict(account.custom_data or {})
            # honor a custom_data payload from this same request
            if "custom_data" in update_data and update_data["custom_data"] is not None:
                base_custom = dict(update_data["custom_data"])
            if account_type is None:
                base_custom.pop("account_type_manual", None)
            else:
                account.account_type = account_type
                base_custom["account_type_manual"] = True
            account.custom_data = base_custom

        await self.db.flush()
        return account
