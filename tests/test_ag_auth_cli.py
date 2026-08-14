"""CLI-level tests for ``oed ag login / logout / status`` (v0.4 auth).

These run the real ``main()`` dispatcher end to end: the token store points at
a throwaway cache dir and the AtomGit verify backend is stubbed, so no network
or real credentials are involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def ag_env(monkeypatch, tmp_path):
    """Isolate the token store and stub the verify backend used by ``login``."""

    from oed_cli import main as main_mod

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))

    class FakeService:
        service_name = "ag"
        name = "openeuler/ag"

    class FakeOp:
        service_name = "ag"
        operation_id = "listAuthenticatedUserIssues"
        display_name = "listAuthenticatedUserIssues"

    state = {
        "main": main_mod.main,
        "verify_result": {"ok": True},
        "resolve_error": None,
        "call_error": None,
    }

    def _resolve(name):
        if state["resolve_error"] is not None:
            raise state["resolve_error"]
        return FakeService()

    monkeypatch.setattr(main_mod, "resolve_service_by_name", _resolve)
    monkeypatch.setattr(main_mod, "fetch_service_spec", lambda svc, **kw: {})
    monkeypatch.setattr(main_mod, "resolve_runtime_gateway", lambda svc: "https://apig.osinfra.cn")
    monkeypatch.setattr(main_mod, "operations_table", lambda spec, name, base_url="": {})
    monkeypatch.setattr(main_mod, "resolve_operation", lambda table, name: FakeOp())

    def _call(op, **kw):
        if state["call_error"] is not None:
            raise state["call_error"]
        return state["verify_result"]

    monkeypatch.setattr(main_mod, "call_operation", _call)
    return state


def test_ag_login_help(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--help"]) == 0
    assert '"help_for": "ag login"' in capsys.readouterr().out


def test_ag_login_status_reports_unconfigured(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--status"]) == 0
    assert '"configured": false' in capsys.readouterr().out


def test_ag_login_status_configured(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--token", "t", "--no-verify"]) == 0
    assert ag_env["main"](["ag", "login", "--status"]) == 0
    assert '"configured": true' in capsys.readouterr().out


def test_ag_login_empty_token_flag(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--token", ""]) == 1
    assert '"missing_flag_value"' in capsys.readouterr().err


def test_ag_login_interactive_prompt(capsys, ag_env, monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: "prompted-token")
    assert ag_env["main"](["ag", "login"]) == 0
    assert "AtomGit login" in capsys.readouterr().err


def test_ag_login_interactive_abort(capsys, ag_env, monkeypatch):
    from click import Abort

    monkeypatch.setattr("click.prompt", lambda *a, **k: (_ for _ in ()).throw(Abort()))
    assert ag_env["main"](["ag", "login"]) == 1
    assert '"aborted"' in capsys.readouterr().err


def test_ag_login_whitespace_token(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--token", "   "]) == 1
    assert '"missing_token"' in capsys.readouterr().err


def test_ag_login_verify_success_and_store(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--token", "good-token"]) == 0
    assert '"configured": true' in capsys.readouterr().out


def test_ag_login_no_verify_skips_backend(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "--token", "t", "--no-verify"]) == 0


def test_ag_login_verify_rejected_token(capsys, ag_env):
    ag_env["verify_result"] = {"ok": False}
    assert ag_env["main"](["ag", "login", "--token", "bad-token"]) == 1
    assert '"invalid_token"' in capsys.readouterr().err


def test_ag_login_verify_backend_missing_is_ok(capsys, ag_env):
    from oed_cli.errors import NotFoundError

    ag_env["resolve_error"] = NotFoundError("no ag service")
    assert ag_env["main"](["ag", "login", "--token", "t"]) == 0


def test_ag_login_verify_resolve_error(capsys, ag_env):
    from oed_cli.errors import UserError

    ag_env["resolve_error"] = UserError("boom", kind="boom")
    assert ag_env["main"](["ag", "login", "--token", "t"]) == 1


def test_ag_login_verify_call_error(capsys, ag_env):
    from oed_cli.errors import UserError

    ag_env["call_error"] = UserError("upstream boom", kind="boom")
    assert ag_env["main"](["ag", "login", "--token", "t"]) == 1


def test_ag_logout(capsys, ag_env):
    assert ag_env["main"](["ag", "logout"]) == 0
    assert '"was_set": false' in capsys.readouterr().out


def test_ag_logout_help(capsys, ag_env):
    assert ag_env["main"](["ag", "logout", "--help"]) == 0
    assert '"help_for": "ag logout"' in capsys.readouterr().out


def test_ag_login_too_many_positional(capsys, ag_env):
    assert ag_env["main"](["ag", "login", "extra", "--token", "t"]) == 1
    assert '"too_many_positional"' in capsys.readouterr().err
