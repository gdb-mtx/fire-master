"""Sync-level handling of a revoked Monarch session (the Sept 2026 401->429 escalation).

The failure that motivated these tests: a session revoked server-side around Sept 6
kept reporting itself as "429 Too Many Requests". The 401 was real and the 429 was
manufactured by our own retry volume —

  1. connect() only read a local file, so a dead token was never detected up front;
  2. sync_balance_snapshots fired a request per account and swallowed each failure,
     so one dead session produced ~26 doomed calls per pass;
  3. every run_full_sync step swallowed its error and continued to the next;
  4. Celery then retried the whole sync 3 more times.

~4 passes x ~30 rejected calls is enough for Monarch to start throttling, at which
point the surfaced error named a rate limit and hid the dead token underneath it.
"""

import pytest

from app.ingestion.monarch_client import MonarchAuthError, MonarchRateLimitError
from app.ingestion.monarch_sync import MonarchSyncService


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Minimal AsyncSession stand-in: hands back the account list, records upserts."""

    def __init__(self, accounts):
        self._accounts = accounts
        self.executes = 0

    async def execute(self, stmt):
        self.executes += 1
        if self.executes == 1:  # the account SELECT
            return _FakeResult(self._accounts)
        return _FakeResult([])

    async def flush(self):
        pass


class _FakeClient:
    """Raises `error` on the Nth get_account_history call, counting every call."""

    def __init__(self, error=None, error_on_call=None):
        self.calls = []
        self._error = error
        self._error_on_call = error_on_call

    async def get_account_history(self, external_id):
        self.calls.append(external_id)
        if self._error is not None and len(self.calls) == self._error_on_call:
            raise self._error
        return [{"date": "2026-01-01", "signedBalance": 100.0}]


ACCOUNTS = [(i, f"ext-{i}") for i in range(1, 6)]


class TestBalanceSnapshotFanOut:
    @pytest.mark.asyncio
    async def test_rate_limit_stops_the_loop_instead_of_hammering(self):
        """A 429 is connection-wide. Firing the remaining 3 requests anyway is what
        keeps the throttle alive."""
        client = _FakeClient(error=MonarchRateLimitError("429"), error_on_call=2)
        service = MonarchSyncService(_FakeDB(ACCOUNTS), client)

        with pytest.raises(MonarchRateLimitError):
            await service.sync_balance_snapshots()

        assert len(client.calls) == 2, "must stop at the throttle, not walk all 5 accounts"

    @pytest.mark.asyncio
    async def test_rate_limit_keeps_rows_already_fetched(self):
        """The partial fetch is still upserted before the error propagates, so a
        throttle costs the remaining accounts and not the whole pass."""
        db = _FakeDB(ACCOUNTS)
        client = _FakeClient(error=MonarchRateLimitError("429"), error_on_call=3)
        service = MonarchSyncService(db, client)

        with pytest.raises(MonarchRateLimitError):
            await service.sync_balance_snapshots()

        assert db.executes > 1, "rows from accounts 1-2 should have been upserted"

    @pytest.mark.asyncio
    async def test_auth_error_aborts_immediately(self):
        client = _FakeClient(error=MonarchAuthError("401"), error_on_call=1)
        service = MonarchSyncService(_FakeDB(ACCOUNTS), client)

        with pytest.raises(MonarchAuthError):
            await service.sync_balance_snapshots()

        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_ordinary_per_account_failure_still_skips_and_continues(self):
        """One flaky account must not abort the other four — the original behaviour."""
        client = _FakeClient(error=ValueError("one bad account"), error_on_call=2)
        service = MonarchSyncService(_FakeDB(ACCOUNTS), client)

        count = await service.sync_balance_snapshots()

        assert len(client.calls) == 5
        assert count == 4


class TestFullSyncAbortsOnAuth:
    @pytest.mark.asyncio
    async def test_auth_failure_stops_the_remaining_steps(self, monkeypatch):
        """Before the fix, a dead session was recorded as a step error and the sync
        marched on through transactions, reconcile and snapshots — every one of them
        rejected, all of them counting against the rate limit."""
        service = MonarchSyncService(_FakeDB([]), _FakeClient())
        reached = []

        async def _auth_dead():
            raise MonarchAuthError("401")

        async def _tracked(name, *_args, **_kwargs):
            reached.append(name)
            return 0

        monkeypatch.setattr(service, "sync_accounts", _auth_dead)
        monkeypatch.setattr(service, "sync_transactions", lambda **kw: _tracked("transactions"))
        monkeypatch.setattr(service, "reconcile_transactions", lambda **kw: _tracked("reconcile"))
        monkeypatch.setattr(service, "sync_balance_snapshots", lambda: _tracked("snapshots"))

        with pytest.raises(MonarchAuthError):
            await service.run_full_sync()

        assert reached == [], "no step may run after the session is known dead"

    @pytest.mark.asyncio
    async def test_ordinary_step_failure_is_still_recorded_and_sync_continues(self, monkeypatch):
        service = MonarchSyncService(_FakeDB([]), _FakeClient())
        reached = []

        async def _blow_up():
            raise ValueError("transient account glitch")

        async def _tracked(name):
            reached.append(name)
            return 0

        monkeypatch.setattr(service, "sync_accounts", _blow_up)
        monkeypatch.setattr(service, "sync_transactions", lambda **kw: _tracked("transactions"))
        monkeypatch.setattr(service, "reconcile_transactions", lambda **kw: _tracked("reconcile"))
        monkeypatch.setattr(service, "sync_balance_snapshots", lambda: _tracked("snapshots"))

        result = await service.run_full_sync()

        assert reached == ["transactions", "reconcile", "snapshots"]
        assert any("Account sync" in e for e in result.errors)


class _Retried(Exception):
    """Stands in for Celery's Retry signal."""


class TestTaskDoesNotRetryAuthFailures:
    def _arm(self, monkeypatch, exc):
        from app.tasks import sync_tasks

        def _boom(coro):
            coro.close()  # the task builds the coroutine before asyncio.run sees it
            raise exc

        retries = []

        def _fake_retry(**kwargs):
            retries.append(kwargs)
            return _Retried()

        monkeypatch.setattr(sync_tasks.asyncio, "run", _boom)
        monkeypatch.setattr(sync_tasks.run_monarch_sync, "retry", _fake_retry)
        return sync_tasks, retries

    def test_auth_error_is_terminal(self, monkeypatch):
        """The retry fan-out is the mechanism that converts a 401 into a 429, and a
        rejected token cannot become valid by being asked again."""
        sync_tasks, retries = self._arm(monkeypatch, MonarchAuthError("session revoked"))

        with pytest.raises(MonarchAuthError):
            sync_tasks.run_monarch_sync.run()

        assert retries == []

    def test_other_failures_still_retry(self, monkeypatch):
        sync_tasks, retries = self._arm(monkeypatch, ConnectionError("network blip"))

        with pytest.raises(_Retried):
            sync_tasks.run_monarch_sync.run()

        assert len(retries) == 1


class _FakeEngine:
    async def dispose(self):
        pass


class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_exc):
        return False


class _CommitDB:
    async def commit(self):
        pass


class TestStatusReportsTheRealFailure:
    """The net worth import used to run OUTSIDE run_full_sync's per-step handlers.

    When it raised, its exception replaced the whole status and result.errors — every
    step failure the sync had actually collected — was thrown away. That is why a dead
    session surfaced to the user as a bare net-worth-call rate limit.
    """

    def _arm(self, monkeypatch, tmp_path, nw_error):
        import app.engines.net_worth as net_worth_mod
        import app.ingestion.category_sync as category_sync_mod
        import app.ingestion.monarch_client as client_mod
        import app.ingestion.monarch_sync as sync_mod
        from app.tasks import sync_tasks

        session_file = tmp_path / "session"
        session_file.write_text("x")

        settings = type(
            "S", (), {"DEMO_MODE": False, "MONARCH_SESSION_FILE": str(session_file), "DATABASE_URL": "postgresql+asyncpg://x/y"}
        )()
        monkeypatch.setattr(sync_tasks, "get_settings", lambda: settings)
        monkeypatch.setattr(sync_tasks, "create_async_engine", lambda *_a, **_k: _FakeEngine())
        monkeypatch.setattr(
            sync_tasks, "async_sessionmaker", lambda *_a, **_k: _FakeSessionCtx(_CommitDB())
        )

        captured = {}
        monkeypatch.setattr(sync_tasks, "_set_sync_status", lambda s: captured.update(s))

        class _Client:
            def __init__(self, *_a):
                pass

            async def connect(self):
                pass

        class _CatSync:
            def __init__(self, *_a):
                pass

            async def sync_from_monarch(self):
                return 0

            async def sync_from_transactions(self):
                return 0

        class _SyncService:
            def __init__(self, *_a):
                pass

            async def run_full_sync(self, full_history=False):
                return sync_mod.SyncResult(accounts_synced=26, transactions_synced=212)

        class _NetWorth:
            def __init__(self, *_a):
                pass

            async def import_monarch_net_worth(self, _client):
                raise nw_error

        monkeypatch.setattr(client_mod, "MonarchClient", _Client)
        monkeypatch.setattr(category_sync_mod, "CategorySyncService", _CatSync)
        monkeypatch.setattr(sync_mod, "MonarchSyncService", _SyncService)
        monkeypatch.setattr(net_worth_mod, "NetWorthEngine", _NetWorth)
        return sync_tasks, captured

    @pytest.mark.asyncio
    async def test_net_worth_failure_is_recorded_not_substituted(self, monkeypatch, tmp_path):
        sync_tasks, captured = self._arm(
            monkeypatch, tmp_path, MonarchRateLimitError("429, message='Too Many Requests'")
        )

        status = await sync_tasks._run_monarch_sync_async()

        assert status["status"] == "error"
        assert "Net worth import" in status["error_message"]
        # The work that DID succeed is still reported rather than lost with the raise
        assert status["accounts_synced"] == 26
        assert status["transactions_synced"] == 212

    @pytest.mark.asyncio
    async def test_dead_session_says_reconnect(self, monkeypatch, tmp_path):
        """The user-facing message must name the fix, not the HTTP code."""
        sync_tasks, captured = self._arm(monkeypatch, tmp_path, MonarchAuthError("401"))

        with pytest.raises(MonarchAuthError):
            await sync_tasks._run_monarch_sync_async()

        assert captured["status"] == "error"
        assert "monarch_login" in captured["error_message"]
