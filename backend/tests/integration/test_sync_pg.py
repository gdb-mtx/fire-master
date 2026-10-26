"""Postgres-backed integration tests for the Monarch sync upsert layer.

Exists because of the Aug 4 2026 incident: the #8 self-heal CASE bound the
enum's raw value instead of the Postgres enum label, aborting every real
sync — while all 188 mock-based unit tests and a SQL compile check passed.
This bug class (bind-vs-label, enum coercion, ON CONFLICT semantics) is
structurally invisible without executing against real Postgres.

Runs only when TEST_DATABASE_URL is set (CI provides a service container;
locally: create a scratch DB and export TEST_DATABASE_URL — NEVER point
this at a real database, tables are created and dropped).
"""

import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.monarch_sync import _account_upsert_stmt
from app.models.account import Account
from app.models.enums import AccountType

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set (integration tests run in CI or against a scratch DB)",
)


def _raw(external_id: str, subtype: str = "roth", mtype: str = "brokerage"):
    return {
        "id": external_id,
        "displayName": f"acct-{external_id}",
        "type": {"name": mtype},
        "subtype": {"name": subtype},
        "displayBalance": 100.0,
        "isAsset": True,
        "includeInNetWorth": True,
        "institution": {"name": "Test Bank"},
    }


@pytest.fixture
async def db():
    """Engine + schema on the scratch database; dropped afterwards."""
    from app.core.database import Base

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _fetch(db, external_id: str) -> Account:
    from sqlalchemy import select

    res = await db.execute(select(Account).where(Account.external_id == external_id))
    return res.scalar_one()


class TestAccountUpsertAgainstPostgres:
    """The tests that would have caught the Aug 4 enum-bind bug."""

    async def test_insert_executes_and_stores_enum(self, db):
        eid = uuid.uuid4().hex
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        acct = await _fetch(db, eid)
        assert acct.account_type == AccountType.ROTH_IRA

    async def test_conflict_update_executes_the_case(self, db):
        """Second upsert takes the ON CONFLICT path — this exact statement
        aborted the transaction pre-d5c9959."""
        eid = uuid.uuid4().hex
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        # Same account, subtype now generic — mapper self-heal path runs
        await db.execute(_account_upsert_stmt(_raw(eid, subtype="brokerage")))
        await db.flush()
        acct = await _fetch(db, eid)
        assert acct.account_type == AccountType.TAXABLE  # self-healed

    async def test_manual_override_survives_sync(self, db):
        eid = uuid.uuid4().hex
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        acct = await _fetch(db, eid)
        acct.account_type = AccountType.VEHICLE
        acct.custom_data = {"account_type_manual": True}
        await db.flush()
        # Re-sync says roth; manual flag must win
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        db.expire_all()
        acct = await _fetch(db, eid)
        assert acct.account_type == AccountType.VEHICLE

    async def test_cleared_flag_resumes_self_heal(self, db):
        eid = uuid.uuid4().hex
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        acct = await _fetch(db, eid)
        acct.account_type = AccountType.VEHICLE
        acct.custom_data = {}  # flag absent = auto
        await db.flush()
        await db.execute(_account_upsert_stmt(_raw(eid)))
        await db.flush()
        db.expire_all()
        acct = await _fetch(db, eid)
        assert acct.account_type == AccountType.ROTH_IRA


class _AccountsClient:
    def __init__(self, accounts):
        self.accounts = accounts

    async def get_accounts(self):
        return self.accounts


async def _sync_accounts(db, accounts):
    from app.ingestion.monarch_sync import MonarchSyncService

    await MonarchSyncService(db, _AccountsClient(accounts)).sync_accounts()
    db.expire_all()


class TestOrphanedAccountsAgainstPostgres:
    """Oct 24 2026: a Monarch re-link left the old rows counted (~$1.2M inflation).
    The orphan UPDATE and the flag-clearing upsert are JSONB expressions on an enum
    filter — exactly the bind/operator class mocks can't see."""

    async def test_vanished_account_excluded_with_enrichment_kept(self, db):
        keep, gone = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(gone)])
        acct = await _fetch(db, gone)
        acct.fire_role = "bridge"
        acct.notes = "old link"
        acct.custom_data = {"account_type_manual": True}
        await db.flush()

        await _sync_accounts(db, [_raw(keep)])

        acct = await _fetch(db, gone)
        assert acct.include_in_net_worth is False
        assert acct.custom_data["monarch_orphaned_at"]
        assert acct.custom_data["account_type_manual"] is True  # merged, not replaced
        assert (acct.fire_role, acct.notes) == ("bridge", "old link")
        live = await _fetch(db, keep)
        assert live.include_in_net_worth is True
        assert "monarch_orphaned_at" not in (live.custom_data or {})

    async def test_hidden_account_stays_live(self, db):
        keep, hidden = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(hidden)])
        await _sync_accounts(db, [_raw(keep), {**_raw(hidden), "isHidden": True}])
        acct = await _fetch(db, hidden)
        assert acct.include_in_net_worth is True
        assert "monarch_orphaned_at" not in (acct.custom_data or {})

    async def test_deactivated_account_is_orphaned(self, db):
        keep, closed = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(closed)])
        await _sync_accounts(db, [_raw(keep), {**_raw(closed), "deactivatedAt": "2026-10-01T00:00:00Z"}])
        acct = await _fetch(db, closed)
        assert acct.include_in_net_worth is False
        assert acct.custom_data["monarch_orphaned_at"]

    async def test_system_account_never_orphaned(self, db):
        keep, system = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(system)])
        acct = await _fetch(db, system)
        acct.fire_role = "system"
        await db.flush()

        await _sync_accounts(db, [_raw(keep)])  # Monarch stops returning it

        acct = await _fetch(db, system)
        assert acct.include_in_net_worth is True
        assert "monarch_orphaned_at" not in (acct.custom_data or {})

    async def test_reappearance_restores_and_follows_monarch(self, db):
        keep, back = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(back)])
        await _sync_accounts(db, [_raw(keep)])
        assert (await _fetch(db, back)).include_in_net_worth is False

        await _sync_accounts(db, [_raw(keep), _raw(back)])
        acct = await _fetch(db, back)
        assert acct.include_in_net_worth is True
        assert "monarch_orphaned_at" not in (acct.custom_data or {})

        # ...and it follows Monarch's flag, whatever it is
        await _sync_accounts(db, [_raw(keep), {**_raw(back), "includeInNetWorth": False}])
        assert (await _fetch(db, back)).include_in_net_worth is False

    async def test_manual_account_untouched(self, db):
        from app.models.enums import DataSource

        keep = uuid.uuid4().hex
        manual = Account(name="Off-Monarch", account_type=AccountType.CHECKING,
                         current_balance=500, source=DataSource.MANUAL, include_in_net_worth=True)
        db.add(manual)
        await db.flush()

        await _sync_accounts(db, [_raw(keep)])

        await db.refresh(manual)
        assert manual.include_in_net_worth is True
        assert manual.custom_data is None

    async def test_empty_fetch_excludes_nothing(self, db):
        eid = uuid.uuid4().hex
        await _sync_accounts(db, [_raw(eid)])
        await _sync_accounts(db, [])
        acct = await _fetch(db, eid)
        assert acct.include_in_net_worth is True
        assert "monarch_orphaned_at" not in (acct.custom_data or {})

    async def test_manual_reinclude_of_orphan_sticks(self, db):
        keep, gone = uuid.uuid4().hex, uuid.uuid4().hex
        await _sync_accounts(db, [_raw(keep), _raw(gone)])
        await _sync_accounts(db, [_raw(keep)])
        acct = await _fetch(db, gone)
        stamped = acct.custom_data["monarch_orphaned_at"]
        acct.include_in_net_worth = True
        await db.flush()

        await _sync_accounts(db, [_raw(keep)])
        acct = await _fetch(db, gone)
        assert acct.include_in_net_worth is True
        assert acct.custom_data["monarch_orphaned_at"] == stamped
