"""Wrapper around the monarchmoney library for structured data access."""

import functools
import logging
from datetime import date, datetime
from pathlib import Path

from gql.transport.exceptions import TransportServerError
from monarchmoney import MonarchMoney

logger = logging.getLogger(__name__)


class MonarchAuthError(Exception):
    """Monarch rejected the saved session (HTTP 401).

    Terminal, never transient: the same token will be rejected identically on every
    retry, and the retry volume is exactly what escalates a 401 into a 429. Callers
    must abort the sync and tell the user to re-run scripts/monarch_login.py.
    """


class MonarchRateLimitError(Exception):
    """Monarch is throttling us (HTTP 429). Transient — stop calling and back off."""


def _typed_errors(fn):
    """Map gql transport errors onto typed Monarch errors.

    Without this every failure arrives as an untyped TransportServerError, so the
    sync cannot tell "your session is dead" (stop now) from "a single account
    hiccuped" (keep going) and treats both as retryable.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except TransportServerError as exc:
            if exc.code == 401:
                raise MonarchAuthError(
                    "Monarch rejected the saved session (401)."
                ) from exc
            if exc.code == 429:
                raise MonarchRateLimitError(
                    "Monarch rate-limited the request (429)."
                ) from exc
            raise

    return wrapper


def _guard_session_file(path: str) -> None:
    """Refuse anything but an owner-only regular file before it is unpickled.

    The session is a pickle the dependency unpickles blindly, so a symlink or a
    directory in its place is treated as tampering, not as a missing file. The
    mode is forced to 0600 on every connect so a copy made with a loose umask
    stops being world-readable the first time the app touches it.
    """
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise RuntimeError(f"Monarch session {path!r} is not a regular file — refusing to load it")
    try:
        p.chmod(0o600)
    except OSError as exc:
        raise RuntimeError(f"Cannot make Monarch session {path!r} owner-only: {exc}") from exc


class MonarchClient:
    def __init__(self, session_file: str):
        self.session_file = session_file
        self.mm = MonarchMoney()

    async def connect(self):
        """Load saved session and verify connectivity."""
        _guard_session_file(self.session_file)
        self.mm.load_session(self.session_file)
        logger.info("Monarch session loaded from %s", self.session_file)
        await self.verify()

    @_typed_errors
    async def verify(self):
        """Confirm the loaded session is actually accepted by Monarch.

        load_session() only reads a local file, so without this probe a revoked
        token sails through connect() and fails ~30 requests deep instead of here.
        get_subscription_details is the cheapest authenticated query available —
        a single object, no lists, no date range.
        """
        await self.mm.get_subscription_details()

    @_typed_errors
    async def get_accounts(self) -> list[dict]:
        """Fetch all linked accounts with balances."""
        data = await self.mm.get_accounts()
        return data.get("accounts", [])

    @_typed_errors
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

    @_typed_errors
    async def get_account_history(self, account_id: str) -> list[dict]:
        """Fetch balance history for a specific account."""
        data = await self.mm.get_account_history(account_id)
        if isinstance(data, list):
            return data
        return data.get("accountSnapshotHistory", [])

    @_typed_errors
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

    @_typed_errors
    async def get_aggregate_snapshots(self) -> list[dict]:
        """Fetch Monarch's calculated net worth history."""
        data = await self.mm.get_aggregate_snapshots()
        return data.get("aggregateSnapshots", [])

    @_typed_errors
    async def get_transaction_categories(self) -> list[dict]:
        """Fetch the flat list of transaction categories from Monarch."""
        data = await self.mm.get_transaction_categories()
        return data.get("categories", [])

    @_typed_errors
    async def get_transaction_category_groups(self) -> list[dict]:
        """Fetch category groups (parent categories) from Monarch."""
        data = await self.mm.get_transaction_category_groups()
        return data.get("categoryGroups", [])

    @_typed_errors
    async def refresh_accounts(self):
        """Request Monarch to refresh account balances from institutions."""
        await self.mm.request_accounts_refresh_and_wait()
        logger.info("Monarch account refresh completed")
