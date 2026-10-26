"""Accounts Monarch stops returning are excluded from net worth (Oct 24 2026).

A re-link in Monarch created new account copies while the old rows stayed
counted with stale balances, inflating net worth by ~$1.2M. sync_accounts now
flags every MONARCH row absent from Monarch's live set (custom_data.
monarch_orphaned_at) and excludes it — never deletes it. These tests cover the
statement shape and the guards; tests/integration/test_sync_pg.py runs the
same paths against real Postgres.
"""

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert, Update

from app.ingestion.monarch_sync import (
    ORPHAN_FLAG,
    SYSTEM_FIRE_ROLE,
    MonarchSyncService,
    _account_upsert_stmt,
    _orphan_accounts_stmt,
)
from app.models.enums import AccountType, DataSource
from app.schemas.account import AccountResponse


def _compiled(stmt):
    return stmt.compile(dialect=postgresql.dialect())


def _sql(stmt) -> str:
    return str(_compiled(stmt))


def _live_ids(stmt) -> set:
    """The NOT IN set of an orphan UPDATE (rendered as an expanding bind)."""
    (ids,) = [v for k, v in _compiled(stmt).params.items() if k.startswith("external_id")]
    return set(ids)


def _raw(eid, **kw):
    return {"id": eid, "displayName": f"acct-{eid}", "type": {"name": "depository"},
            "displayBalance": 10.0, "includeInNetWorth": True, **kw}


class _Result:
    rowcount = 0

    def all(self):
        return []


class _RecordingDB:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result()

    async def flush(self):
        pass

    def updates(self):
        return [s for s in self.statements if isinstance(s, Update)]


class _Client:
    def __init__(self, accounts):
        self._accounts = accounts

    async def get_accounts(self):
        return self._accounts


async def _sync(accounts):
    db = _RecordingDB()
    await MonarchSyncService(db, _Client(accounts)).sync_accounts()
    return db


class TestOrphanStatement:
    def test_scoped_to_monarch_rows_outside_the_live_set(self):
        stmt = _orphan_accounts_stmt({"a", "b"}, datetime(2026, 10, 24, tzinfo=timezone.utc))
        sql, params = _sql(stmt), _compiled(stmt).params
        assert "accounts.source = %(source_1)s" in sql
        assert params["source_1"] == DataSource.MONARCH  # typed enum bind, not a raw .value
        assert "NOT IN" in sql and _live_ids(stmt) == {"a", "b"}
        assert params["include_in_net_worth"] is False
        assert "'{}'::jsonb" in sql  # literal, not a bound JSON string

    def test_system_accounts_are_never_orphaned(self):
        stmt = _orphan_accounts_stmt({"a"}, datetime.now(timezone.utc))
        sql, params = _sql(stmt), _compiled(stmt).params
        # NULL-safe: a plain != would also drop every account with no fire_role
        assert "accounts.fire_role IS NULL OR accounts.fire_role != %(fire_role_1)s" in sql
        assert params["fire_role_1"] == SYSTEM_FIRE_ROLE == "system"

    def test_already_flagged_rows_are_left_alone(self):
        # a user who re-includes an orphan by hand must not be re-excluded every sync
        sql = _sql(_orphan_accounts_stmt({"a"}, datetime.now(timezone.utc)))
        assert "NOT ((accounts.custom_data ? %(custom_data_1)s))" in sql

    def test_upsert_clears_the_flag_on_reappearance(self):
        compiled = _compiled(_account_upsert_stmt(_raw("a")))
        m = re.search(r"custom_data = \(accounts\.custom_data - CAST\(%\((\w+)\)s AS TEXT\)\)", str(compiled))
        assert m, "ON CONFLICT must strip the orphan flag"
        assert compiled.params[m.group(1)] == ORPHAN_FLAG


class TestSyncAccountsGuards:
    @pytest.mark.asyncio
    async def test_live_fetch_runs_the_orphan_pass_with_live_ids(self):
        db = await _sync([_raw("a"), _raw("b")])
        (stmt,) = db.updates()
        assert _live_ids(stmt) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_hidden_account_stays_live_deactivated_does_not(self):
        db = await _sync([_raw("a"), _raw("h", isHidden=True), _raw("d", deactivatedAt="2026-10-01")])
        (stmt,) = db.updates()
        assert _live_ids(stmt) == {"a", "h"}  # hidden still exists in Monarch: never orphaned
        upserts = [s for s in db.statements if isinstance(s, Insert)]
        assert len(upserts) == 1  # hidden and deactivated are still not upserted

    @pytest.mark.asyncio
    async def test_empty_fetch_never_orphans_everything(self):
        db = await _sync([])
        assert db.updates() == []

    @pytest.mark.asyncio
    async def test_fetch_with_nothing_live_never_orphans_everything(self):
        db = await _sync([_raw("d", deactivatedAt="2026-10-01")])
        assert db.updates() == []

    @pytest.mark.asyncio
    async def test_failed_fetch_raises_before_any_write(self):
        class _Dead:
            async def get_accounts(self):
                raise RuntimeError("401")

        db = _RecordingDB()
        with pytest.raises(RuntimeError):
            await MonarchSyncService(db, _Dead()).sync_accounts()
        assert db.statements == []


class TestAccountResponse:
    def test_exposes_orphan_timestamp(self):
        base = dict(id="00000000-0000-0000-0000-000000000001", external_id="a", name="x",
                    account_type=AccountType.CHECKING, institution=None, balance_cents=0, is_asset=True,
                    include_in_net_worth=False, source=DataSource.MONARCH, last_synced_at=None)
        flagged = AccountResponse(**base, custom_data={ORPHAN_FLAG: "2026-10-24T00:00:00+00:00"})
        assert flagged.monarch_orphaned_at == "2026-10-24T00:00:00+00:00"
        assert AccountResponse(**base).monarch_orphaned_at is None
