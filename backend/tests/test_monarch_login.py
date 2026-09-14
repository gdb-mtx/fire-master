"""Security-focused tests for the interactive Monarch login helper."""

import importlib.util
import stat
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "monarch_login.py"
SPEC = importlib.util.spec_from_file_location("monarch_login_script", SCRIPT_PATH)
monarch_login = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(monarch_login)


@pytest.mark.asyncio
async def test_login_hides_password_and_secures_only_configured_session(
    monkeypatch, tmp_path
):
    session_path = tmp_path / "session.pickle"
    calls = {}

    class FakeMonarchMoney:
        def __init__(self, session_file):
            calls["session_file"] = session_file

        async def login(self, email, password, **kwargs):
            calls["login"] = (email, password, kwargs)

        def save_session(self, filename):
            calls["saved"] = filename
            Path(filename).write_text("synthetic session")

        async def get_accounts(self):
            return {"accounts": []}

    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return "correct horse battery staple"

    monkeypatch.setenv("MONARCH_SESSION_FILE", str(session_path))
    monkeypatch.setattr(monarch_login, "MonarchMoney", FakeMonarchMoney)
    monkeypatch.setattr(monarch_login, "getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", lambda _prompt: " user@example.com ")
    monkeypatch.chdir(tmp_path)

    await monarch_login.main()

    assert prompts == ["Password: "]
    assert calls["session_file"] == str(session_path)
    assert calls["login"] == (
        "user@example.com",
        "correct horse battery staple",
        {"use_saved_session": False, "save_session": False},
    )
    assert calls["saved"] == str(session_path)
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert not (tmp_path / ".mm").exists()


@pytest.mark.asyncio
async def test_mfa_code_is_hidden(monkeypatch, tmp_path):
    session_path = tmp_path / "session.pickle"
    calls = {}

    class FakeMonarchMoney:
        def __init__(self, session_file):
            pass

        async def login(self, email, password, **kwargs):
            raise monarch_login.RequireMFAException

        async def multi_factor_authenticate(self, email, password, code):
            calls["mfa"] = (email, password, code)

        def save_session(self, filename):
            Path(filename).write_text("synthetic session")

        async def get_accounts(self):
            return {"accounts": []}

    answers = iter(["correct horse battery staple", "123456"])
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setenv("MONARCH_SESSION_FILE", str(session_path))
    monkeypatch.setattr(monarch_login, "MonarchMoney", FakeMonarchMoney)
    monkeypatch.setattr(monarch_login, "getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", lambda _prompt: "user@example.com")

    await monarch_login.main()

    assert prompts == ["Password: ", "MFA code: "]
    assert calls["mfa"] == (
        "user@example.com",
        "correct horse battery staple",
        "123456",
    )
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
