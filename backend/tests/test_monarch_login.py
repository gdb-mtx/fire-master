"""monarch_login.py must never echo secrets or leave a second session file around.

Regression tests for GHSA-w2mg-x44j-2cm4 (Sept 2026): the script used input() for
the password and MFA code, and let the library save a stray copy of the token to
.mm/mm_session.pickle with default permissions.
"""

import importlib.util
import stat
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "monarch_login.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("monarch_login_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Recorder:
    """Stand-in for MonarchMoney that records how the script drives it."""

    instances: list = []

    def __init__(self, session_file=None, **_kwargs):
        self.session_file = session_file
        self.login_calls: list = []
        self.mfa_calls: list = []
        self.saved_to: list = []
        self.require_mfa = False
        _Recorder.instances.append(self)

    async def login(self, email, password, **kwargs):
        self.login_calls.append((email, password, kwargs))
        if self.require_mfa:
            raise _Recorder.mfa_exception

    async def multi_factor_authenticate(self, email, password, code):
        self.mfa_calls.append((email, password, code))

    def save_session(self, filename):
        self.saved_to.append(filename)
        Path(filename).write_bytes(b"token")

    async def get_accounts(self):
        return {"accounts": []}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Run the script in tmp_path with scripted prompt answers; return what happened."""
    module = _load_script()
    _Recorder.instances = []
    _Recorder.mfa_exception = module.RequireMFAException
    session = tmp_path / "session-under-test"
    hidden_prompts: list[str] = []

    async def run(*, mfa_code=None, hidden_answers):
        answers = iter(hidden_answers)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MONARCH_SESSION_FILE", str(session))
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.setattr(module, "MonarchMoney", _Recorder)
        monkeypatch.setattr("builtins.input", lambda prompt: "  who@example.org ")

        def fake_getpass(prompt):
            hidden_prompts.append(prompt)
            return next(answers)

        monkeypatch.setattr(module, "getpass", fake_getpass)
        if mfa_code is not None:
            orig_init = _Recorder.__init__

            def init_requiring_mfa(self, *a, **kw):
                orig_init(self, *a, **kw)
                self.require_mfa = True

            monkeypatch.setattr(_Recorder, "__init__", init_requiring_mfa)
        await module.main()
        return _Recorder.instances[-1]

    return run, session, hidden_prompts, tmp_path


@pytest.mark.asyncio
async def test_password_is_read_hidden_and_email_is_trimmed(harness):
    run, session, hidden, root = harness
    mm = await run(hidden_answers=["hunter2"])

    assert hidden == ["Password: "], "password must go through getpass, not input()"
    [(email, password, kwargs)] = mm.login_calls
    assert email == "who@example.org"
    assert password == "hunter2"


@pytest.mark.asyncio
async def test_library_is_told_not_to_save_its_own_copy(harness):
    run, session, _hidden, root = harness
    mm = await run(hidden_answers=["hunter2"])

    assert mm.session_file == str(session), "client must be built with OUR session path"
    [(_e, _p, kwargs)] = mm.login_calls
    assert kwargs == {"use_saved_session": False, "save_session": False}
    assert mm.saved_to == [str(session)], "exactly one save, to the configured path"
    assert not (root / ".mm").exists(), "no stray .mm/mm_session.pickle"


@pytest.mark.asyncio
async def test_saved_session_is_owner_only(harness):
    run, session, _hidden, _root = harness
    await run(hidden_answers=["hunter2"])

    assert stat.S_IMODE(session.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_mfa_code_is_also_hidden(harness):
    run, session, hidden, _root = harness
    mm = await run(mfa_code="424242", hidden_answers=["hunter2", "424242"])

    assert hidden == ["Password: ", "MFA code: "]
    assert mm.mfa_calls == [("who@example.org", "hunter2", "424242")]
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
