"""Security checks for loading the persisted Monarch session."""

import stat
from unittest.mock import MagicMock

import pytest

from app.ingestion.monarch_client import MonarchClient


@pytest.mark.asyncio
async def test_connect_restricts_existing_session_permissions(tmp_path):
    session_path = tmp_path / "session.pickle"
    session_path.write_text("synthetic session")
    session_path.chmod(0o644)

    client = MonarchClient(str(session_path))
    client.mm = MagicMock()

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
