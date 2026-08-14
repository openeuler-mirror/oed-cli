"""Tests for per-service auth injection in :func:`oed_cli.invoke.call_operation`.

AtomGit (``ag``) authenticates through the ``access_token`` query parameter
declared on its spec. These tests run offline: the stored token is monkeypatched
and HTTP is stubbed so we only assert on the request that *would* go out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

AG_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "openeuler/ag", "version": "1.0.0"},
    "paths": {
        "/api/v5/user/issues": {
            "get": {
                "summary": "list the authenticated user's issues",
                "operationId": "listAuthenticatedUserIssues",
                "parameters": [
                    {
                        "name": "access_token",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "filter",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    },
                ],
            }
        },
        "/api/v5/repos/{owner}/{repo}/git/trees/{sha}": {
            "get": {
                "summary": "list repo tree",
                "operationId": "getRepoTree",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "sha", "in": "path", "required": True, "schema": {"type": "string"}},
                    {
                        "name": "access_token",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    },
                ],
            }
        },
        "/public/health": {
            "get": {
                "summary": "unauthenticated health check",
                "operationId": "getHealth",
                "parameters": [],
            }
        },
    },
}


@pytest.fixture()
def captured(monkeypatch):
    """Stub HTTP + the stored token, capturing the outgoing request."""

    from oed_cli import http as http_mod
    from oed_cli import invoke as invoke_mod

    box = {}

    def _fake_do_call(
        method, url, params=None, body=None, timeout=30.0, headers=None, user_agent=None
    ):
        class _R:
            status_code = 200
            content = b'{"ok":true}'
            text = content.decode()
            headers = {"content-type": "application/json"}

            def json(self):
                return {"ok": True}

        box["method"] = method
        box["url"] = url
        box["params"] = params or {}
        box["body"] = body
        box["headers"] = headers or {}
        return _R()

    monkeypatch.setattr(http_mod, "get_request", _fake_do_call)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)

    def _set_token(value: str | None):
        monkeypatch.setattr(invoke_mod, "read_token", lambda service: value)

    box["set_token"] = _set_token
    box["invoke"] = invoke_mod
    return box


def _op(name: str):
    from oed_cli.dynamic import operations_table

    return operations_table(AG_SPEC, "ag", base_url="https://apig.osinfra.cn")[name]


def test_ag_required_token_injected_from_store(captured):
    from oed_cli.invoke import call_operation

    captured["set_token"]("tok-abc")
    op = _op("listAuthenticatedUserIssues")
    call_operation(op, params={"filter": "all"})
    assert captured["params"]["access_token"] == "tok-abc"


def test_ag_explicit_token_wins_over_store(captured):
    from oed_cli.invoke import call_operation

    captured["set_token"]("stored-token")
    op = _op("listAuthenticatedUserIssues")
    call_operation(op, params={"access_token": "explicit-token"})
    assert captured["params"]["access_token"] == "explicit-token"


def test_ag_required_token_missing_raises_hint(captured):
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    captured["set_token"](None)
    op = _op("listAuthenticatedUserIssues")
    with pytest.raises(UserError) as exc:
        call_operation(op)
    assert exc.value.kind == "ag_token_missing"


def test_ag_optional_token_missing_goes_without(captured):
    from oed_cli.invoke import call_operation

    captured["set_token"](None)
    op = _op("getRepoTree")
    call_operation(op, params={"owner": "o", "repo": "r", "sha": "s"})
    assert "access_token" not in captured["params"]


def test_ag_public_operation_gets_no_token(captured):
    from oed_cli.invoke import call_operation

    captured["set_token"]("tok-abc")
    op = _op("getHealth")
    call_operation(op)
    assert "access_token" not in captured["params"]


def test_non_ag_service_gets_no_token(captured):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "x", "version": "1.0.0"},
        "paths": {
            "/v1/foo": {
                "get": {
                    "operationId": "getFoo",
                    "parameters": [
                        {
                            "name": "access_token",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                }
            }
        },
    }
    captured["set_token"]("tok-abc")
    op = operations_table(spec, "other", base_url="https://apig.osinfra.cn")["getFoo"]
    call_operation(op)
    assert "access_token" not in captured["params"]


def test_ag_dry_run_masks_stored_token(captured):
    from oed_cli.invoke import call_operation

    captured["set_token"]("super-secret-token")
    op = _op("listAuthenticatedUserIssues")
    dry = call_operation(op, dry_run=True)
    assert dry["request"]["query"]["access_token"] == "<stored>"
    assert "super-secret-token" not in str(dry)
