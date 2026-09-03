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
        method,
        url,
        params=None,
        body=None,
        timeout=30.0,
        headers=None,
        user_agent=None,
        token=None,
        cookie=None,
        service_name="",
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


def test_mask_echo_headers_redacts_authorization_and_cookie():
    """``_mask_echo_headers`` redacts ``Authorization`` + ``Cookie`` only.

    Regression: the query ``access_token`` was masked but the Bearer token in
    the ``Authorization`` header (and the ``Cookie`` header) were echoed
    verbatim into ``--dry-run`` JSON output, leaking a live credential to
    stdout / shared CI logs.
    """
    from oed_cli.invoke import _mask_echo_headers

    masked = _mask_echo_headers({
        "Authorization": "Bearer super-secret-bearer",
        "Cookie": "session-id=abc123; csrf=xyz",
        "User-Agent": "oed/0.3.0rc1",
        "x-oed-source": "oed-cli",
        "Content-Type": "application/json",
    })
    assert masked["Authorization"] == "Bearer <stored>"
    assert masked["Cookie"] == "<stored>"
    # Non-sensitive headers pass through untouched.
    assert masked["User-Agent"] == "oed/0.3.0rc1"
    assert masked["x-oed-source"] == "oed-cli"
    assert masked["Content-Type"] == "application/json"
    # The real secrets are gone.
    joined = " ".join(masked.values())
    assert "super-secret-bearer" not in joined
    assert "session-id=abc123" not in joined


def test_mask_echo_headers_case_insensitive_and_non_bearer():
    """Header name matching is case-insensitive; non-Bearer auth is fully masked."""

    from oed_cli.invoke import _mask_echo_headers

    masked = _mask_echo_headers({
        "authorization": "Basic dXNlcjpwYXNz",
        "COOKIE": "k=v",
    })
    assert masked["authorization"] == "<stored>"
    assert masked["COOKIE"] == "<stored>"


def test_dry_run_view_uses_masked_headers(captured):
    """``--dry-run`` echo carries ``Bearer <stored>`` for the Authorization header."""

    from oed_cli.invoke import call_operation

    captured["set_token"]("ag-pat")  # satisfy ag's required access_token query param
    op = _op("listAuthenticatedUserIssues")
    dry = call_operation(
        op,
        dry_run=True,
        token="super-secret-bearer",
        cookie="session-id=abc123",
    )
    headers = dry["request"]["headers"]
    assert headers["Authorization"] == "Bearer <stored>"
    assert headers["Cookie"] == "<stored>"
    assert "super-secret-bearer" not in str(dry)
    assert "session-id=abc123" not in str(dry)


def test_include_request_view_uses_masked_headers(captured):
    """``include_request`` (non-dry-run echo) redacts the Bearer header too."""

    from oed_cli.invoke import call_operation

    captured["set_token"]("ag-pat")
    op = _op("listAuthenticatedUserIssues")
    out = call_operation(
        op,
        params={"filter": "all"},
        include_request=True,
        token="live-bearer-token",
    )
    headers = out["request"]["headers"]
    assert headers["Authorization"] == "Bearer <stored>"
    assert "live-bearer-token" not in str(out)


# ── request-body visibility & the --params-vs--json trap ────────────────────
# Regression for the forum createTopicPostPM incident: the gateway spec marks
# body fields required at the *schema* level but never sets requestBody.required,
# so body_required=False and old help hid the body entirely while still showing
# a --params example — which silently drops body fields (body: null → 400).

BODY_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "forum", "version": "1.0.0"},
    "paths": {
        "/posts": {
            "post": {
                "summary": "create a topic post",
                "operationId": "createTopicPostPM",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["raw"],
                                "properties": {
                                    "title": {"type": "string"},
                                    "raw": {"type": "string"},
                                },
                            }
                        }
                    }
                },
            }
        },
        "/plain": {
            "get": {"summary": "no params, no body", "operationId": "getPlain", "parameters": []},
        },
    },
}


def _body_op(name: str):
    from oed_cli.dynamic import operations_table

    return operations_table(BODY_SPEC, "forum", base_url="https://apig.osinfra.cn")[name]


def test_help_exposes_body_when_not_marked_required():
    """A body present but not marked requestBody.required still surfaces in help."""
    from types import SimpleNamespace

    from oed_cli.invoke import describe_operation_help

    op = _body_op("createTopicPostPM")
    svc = SimpleNamespace(name="openeuler/forum", service_name="forum", title="forum")
    doc = describe_operation_help(op, svc)
    assert doc["has_body"] is True
    assert doc["body_required"] is False
    fields = {f["name"]: f for f in doc["body_required_fields"]}
    assert set(fields) == {"title", "raw"}
    assert fields["raw"]["required"] is True
    assert fields["title"]["required"] is False
    assert "title" in doc["body_schema"]["properties"]
    assert "--json" in doc["usage"]


def test_body_only_op_usage_uses_json_not_params():
    from oed_cli.invoke import _usage_examples

    examples = _usage_examples(_body_op("createTopicPostPM"))
    assert examples and "--json" in examples[0]
    assert all("--params" not in ex for ex in examples)


def test_no_params_no_body_usage_is_dry_run():
    from oed_cli.invoke import _usage_example, _usage_examples

    op = _body_op("getPlain")
    assert _usage_example(op) == "oed <service> getPlain [--dry-run]"
    assert _usage_examples(op) == ["oed <service> getPlain [--dry-run]"]


def test_body_fields_via_params_raises_hint(captured):
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    op = _body_op("createTopicPostPM")
    with pytest.raises(UserError) as exc:
        call_operation(op, params={"title": "hi", "raw": "body"})
    assert exc.value.kind == "body_fields_via_params"
    assert "--json" in exc.value.hint


def test_body_fields_via_params_ok_when_json_passed(captured):
    from oed_cli.invoke import call_operation

    op = _body_op("createTopicPostPM")
    call_operation(op, params={"title": "hi"}, body={"raw": "body"})
    assert captured["body"] == {"raw": "body"}


# ── eulermaker getJobLog result_root auto-fix ───────────────────────────────
# The jobs-search API returns result_root as a relative path that the spec
# requires to end with /dmesg. Agents routinely pass the raw value (missing the
# suffix, often with a stray leading /); oed normalizes this one known path
# param and reports the fix in param_corrections.

EULERMAKER_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "openeuler/eulermaker", "version": "1.0.0"},
    "paths": {
        "/jobs/{result_root}": {
            "get": {
                "summary": "get a job log",
                "operationId": "getJobLog",
                "parameters": [
                    {
                        "name": "result_root",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
        "/other/{root}": {
            "get": {
                "summary": "another operation on the same service",
                "operationId": "getOther",
                "parameters": [
                    {"name": "root", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
            }
        },
    },
}


def _eulermaker_op(name: str):
    from oed_cli.dynamic import operations_table

    return operations_table(EULERMAKER_SPEC, "eulermaker", base_url="https://apig.osinfra.cn")[name]


def test_get_job_log_appends_dmesg_and_strips_slash(captured):
    from oed_cli.invoke import call_operation

    op = _eulermaker_op("getJobLog")
    out = call_operation(op, params={"result_root": "/result/rpmbuild/2026-08-26/job"})
    assert captured["url"] == "https://apig.osinfra.cn/jobs/result/rpmbuild/2026-08-26/job/dmesg"
    assert out["param_corrections"] == [
        "result_root: stripped leading '/'",
        "result_root: appended '/dmesg'",
    ]


def test_get_job_log_appends_dmesg_only(captured):
    from oed_cli.invoke import call_operation

    op = _eulermaker_op("getJobLog")
    call_operation(op, params={"result_root": "result/job"})
    assert captured["url"] == "https://apig.osinfra.cn/jobs/result/job/dmesg"


def test_get_job_log_already_normalized_untouched(captured):
    from oed_cli.invoke import call_operation

    op = _eulermaker_op("getJobLog")
    out = call_operation(op, params={"result_root": "result/job/dmesg"})
    assert "param_corrections" not in out
    assert captured["url"] == "https://apig.osinfra.cn/jobs/result/job/dmesg"


def test_get_job_log_other_operation_untouched(captured):
    from oed_cli.invoke import call_operation

    op = _eulermaker_op("getOther")
    out = call_operation(op, params={"root": "/foo"})
    assert "param_corrections" not in out
    assert captured["url"] == "https://apig.osinfra.cn/other//foo"


def test_get_job_log_other_service_untouched(captured):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "x", "version": "1.0.0"},
        "paths": {
            "/jobs/{result_root}": {
                "get": {
                    "operationId": "getJobLog",
                    "parameters": [
                        {
                            "name": "result_root",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                }
            }
        },
    }
    op = operations_table(spec, "other", base_url="https://apig.osinfra.cn")["getJobLog"]
    out = call_operation(op, params={"result_root": "/foo"})
    assert "param_corrections" not in out
    assert captured["url"] == "https://apig.osinfra.cn/jobs//foo"
