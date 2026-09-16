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


def _client_for(path, **mm_methods):
    client = MonarchClient(str(path))
    client.mm = MagicMock()
    client.mm.get_subscription_details = AsyncMock(return_value={})
    for name, value in mm_methods.items():
        setattr(client.mm, name, value)
    return client


# --- Session file guard (GHSA-w2mg-x44j-2cm4) ---------------------------------------
#
# The session is a pickle the dependency unpickles blindly. connect() therefore
# refuses anything that is not a regular file and forces 0600 before loading.


@pytest.mark.asyncio
@pytest.mark.parametrize("loose_mode", [0o644, 0o666, 0o600])
async def test_connect_forces_owner_only_mode(tmp_path, loose_mode):
    session = tmp_path / ".monarch_session"
    session.write_bytes(b"not-a-real-pickle")
    session.chmod(loose_mode)

    client = _client_for(session)
    await client.connect()

    assert stat.S_IMODE(session.stat().st_mode) == 0o600
    client.mm.load_session.assert_called_once_with(str(session))


@pytest.mark.asyncio
@pytest.mark.parametrize("make_path", [
    pytest.param(lambda d: (d / "real").write_bytes(b"x") or (d / "link").symlink_to(d / "real") or d / "link", id="symlink"),
    pytest.param(lambda d: (d / "dir").mkdir() or d / "dir", id="directory"),
    pytest.param(lambda d: d / "missing", id="missing"),
])
async def test_connect_refuses_anything_but_a_regular_file(tmp_path, make_path):
    path = make_path(tmp_path)
    client = _client_for(path)

    with pytest.raises(RuntimeError, match="not a regular file"):
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
