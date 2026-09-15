"""Security checks for loading the persisted Monarch session."""

import stat
from unittest.mock import AsyncMock, MagicMock

import pytest

from gql.transport.exceptions import TransportServerError

from app.ingestion.monarch_client import (
    MonarchAuthError,
    MonarchClient,
    MonarchRateLimitError,
)


@pytest.mark.asyncio
async def test_connect_restricts_existing_session_permissions(tmp_path):
    session_path = tmp_path / "session.pickle"
    session_path.write_text("synthetic session")
    session_path.chmod(0o644)

    client = MonarchClient(str(session_path))
    client.mm = MagicMock()
    client.mm.get_subscription_details = AsyncMock(return_value={})

    await client.connect()

    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    client.mm.load_session.assert_called_once_with(str(session_path))


@pytest.mark.asyncio
async def test_connect_rejects_symlinked_session(tmp_path):
    target = tmp_path / "target.pickle"
    target.write_text("synthetic session")
    session_path = tmp_path / "session.pickle"
    session_path.symlink_to(target)

    client = MonarchClient(str(session_path))
    client.mm = MagicMock()

    with pytest.raises(RuntimeError, match="must be a regular file"):
        await client.connect()

    client.mm.load_session.assert_not_called()


# --- Typed auth/rate-limit errors (the Sept 2026 401->429 escalation) -------------
#
# A revoked Monarch session used to sail through connect() (load_session only reads a
# local file), get swallowed by every per-step handler in run_full_sync, and then be
# retried by Celery 3 more times. ~4 full syncs x ~30 calls against a rejecting
# endpoint is what made Monarch answer 429 instead of 401 — so the surfaced error
# named a rate limit and hid the dead token underneath it.


def _transport_error(status: int):
    return TransportServerError(f"{status}, message='nope', url='https://api.monarch.com/graphql'", status)


@pytest.mark.asyncio
async def test_connect_raises_auth_error_when_session_is_revoked(tmp_path):
    session_path = tmp_path / "session.pickle"
    session_path.write_text("synthetic session")

    client = MonarchClient(str(session_path))
    client.mm = MagicMock()
    client.mm.get_subscription_details = AsyncMock(side_effect=_transport_error(401))

    with pytest.raises(MonarchAuthError):
        await client.connect()


@pytest.mark.asyncio
async def test_401_on_any_call_becomes_auth_error(tmp_path):
    client = MonarchClient(str(tmp_path / "s"))
    client.mm = MagicMock()
    client.mm.get_accounts = AsyncMock(side_effect=_transport_error(401))

    with pytest.raises(MonarchAuthError):
        await client.get_accounts()


@pytest.mark.asyncio
async def test_429_becomes_rate_limit_error(tmp_path):
    client = MonarchClient(str(tmp_path / "s"))
    client.mm = MagicMock()
    client.mm.get_account_history = AsyncMock(side_effect=_transport_error(429))

    with pytest.raises(MonarchRateLimitError):
        await client.get_account_history("acct-1")


@pytest.mark.asyncio
async def test_other_transport_errors_are_left_untyped(tmp_path):
    """Only 401/429 carry sync-control meaning; a 500 stays a plain transport error
    so it keeps its existing retryable behaviour."""
    client = MonarchClient(str(tmp_path / "s"))
    client.mm = MagicMock()
    client.mm.get_accounts = AsyncMock(side_effect=_transport_error(500))

    with pytest.raises(TransportServerError):
        await client.get_accounts()
