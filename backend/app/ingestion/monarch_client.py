"""Wrapper around the monarchmoney library for structured data access."""

import logging
from datetime import date, datetime
from pathlib import Path

from monarchmoney import MonarchMoney

logger = logging.getLogger(__name__)


class MonarchClient:
    def __init__(self, session_file: str):
        self.session_file = session_file
        self.mm = MonarchMoney()

    async def connect(self):
        """Load saved session and verify connectivity."""
        session_path = Path(self.session_file)
        if session_path.is_symlink() or not session_path.is_file():
            raise RuntimeError("Monarch session path must be a regular file")
        try:
            session_path.chmod(0o600)
        except OSError as exc:
            raise RuntimeError("Could not restrict Monarch session permissions") from exc
        self.mm.load_session(self.session_file)
        logger.info("Monarch session loaded from %s", self.session_file)

    async def get_accounts(self) -> list[dict]:
        """Fetch all linked accounts with balances."""
        data = await self.mm.get_accounts()
        return data.get("accounts", [])

    async def get_transactions(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Fetch transactions with pagination."""
        all_transactions = []
        offset = 0

        while True:
            kwargs = {"limit": limit, "offset": offset}
            if start_date:
                kwargs["start_date"] = start_date.isoformat()
                kwargs["end_date"] = (end_date or date.today()).isoformat()

            data = await self.mm.get_transactions(**kwargs)
            transactions = data.get("allTransactions", {}).get("results", [])

            if not transactions:
                break

            all_transactions.extend(transactions)
            offset += limit

            total = data.get("allTransactions", {}).get("totalCount", 0)
            if offset >= total:
                break

            logger.info("Fetched %d/%d transactions", len(all_transactions), total)

        return all_transactions

    async def get_account_history(self, account_id: str) -> list[dict]:
        """Fetch balance history for a specific account."""
        data = await self.mm.get_account_history(account_id)
        if isinstance(data, list):
            return data
        return data.get("accountSnapshotHistory", [])

    async def get_account_snapshots_by_type(
        self,
        start_date: str | None = None,
        timeframe: str = "month",
    ) -> dict:
        """Fetch historical balance snapshots grouped by account type."""
        kwargs = {"timeframe": timeframe}
        if start_date:
            kwargs["start_date"] = start_date
        return await self.mm.get_account_snapshots_by_type(**kwargs)

    async def get_aggregate_snapshots(self) -> list[dict]:
        """Fetch Monarch's calculated net worth history."""
        data = await self.mm.get_aggregate_snapshots()
        return data.get("aggregateSnapshots", [])

    async def get_transaction_categories(self) -> list[dict]:
        """Fetch the flat list of transaction categories from Monarch."""
        data = await self.mm.get_transaction_categories()
        return data.get("categories", [])

    async def get_transaction_category_groups(self) -> list[dict]:
        """Fetch category groups (parent categories) from Monarch."""
        data = await self.mm.get_transaction_category_groups()
        return data.get("categoryGroups", [])

    async def refresh_accounts(self):
        """Request Monarch to refresh account balances from institutions."""
        await self.mm.request_accounts_refresh_and_wait()
        logger.info("Monarch account refresh completed")
