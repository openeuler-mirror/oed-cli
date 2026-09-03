"""Tests for the per-user CLI service allow-list gate.

Three layers under test:

- :func:`oed_cli.auth._fetch_user_allowlist` — one-shot HTTP GET right
  after device-flow success, with the bearer access_token. Fail-open on
  any network / parse / shape failure.
- :func:`oed_cli.auth.save_msal_auth(..., allowlist=...)` — persist the
  list alongside the MSAL cache; ``None`` must NOT clobber an existing
  field (legacy compat).
- :func:`oed_cli.main._check_allowlist` — block any ``service_name`` not
  in the local list, raise ``UserError(kind="service_blocked_by_allowlist")``.
  Fail-open when the file is missing, the field is absent, or the list
  is empty.

The check runs *before* any HTTP / discovery call, so all tests run
against a monkey-patched discovery feed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oed_cli.cli import cli  # noqa: E402

SAMPLE_FEED = {
    "kind": "discovery#servicesListByCommunity",
    "communities": {
        "openeuler": [
            {
                "name": "openeuler/cvemanager",
                "service_name": "cvemanager",
                "community": "openeuler",
                "title": "APIG_OPENEULER_CVEMANAGER",
                "version": "1.0.0",
                "description": "",
                "base_url": "$APIG_GROUP_ENTRY_URL",
            },
            {
                "name": "openeuler/easysearch",
                "service_name": "easysearch",
                "community": "openeuler",
                "title": "APIG_OPENEULER_EASYSEARCH",
                "version": "1.0.0",
                "description": "doc search",
                "base_url": "$APIG_GROUP_ENTRY_URL",
            },
        ]
    },
}

SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "APIG_OPENEULER_CVEMANAGER", "version": "1.0.0"},
    "servers": [{"url": "$APIG_GROUP_ENTRY_URL"}],
    "paths": {
        "/v1/list": {
            "get": {
                "summary": "list",
                "operationId": "API_list",
                "responses": {},
            }
        }
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def tmp_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def patched_discovery(monkeypatch):
    """Stub out discovery + HTTP so dynamic.py thinks the gateway is local.

    Cross-test pollution problem:
    :mod:`oed_cli.main` imports ``fetch_service_spec`` via
    ``from .dynamic import fetch_service_spec`` at module load. Once
    ``main`` is imported, ``main.fetch_service_spec`` is a permanent
    reference — patching ``dyn.fetch_service_spec`` after that does NOT
    update ``main``'s binding, so tests that go through ``main.main([...])``
    (test_dynamic does this) would still call the original. Patching
    ``_read_spec_cache`` instead sidesteps the trap: both the original and
    any re-export see the patch because the cache lookup happens inside
    ``fetch_service_spec`` at CALL time (not at import time).
    """

    from oed_cli import cli as cli_mod
    from oed_cli import discovery as disc
    from oed_cli import dynamic as dyn

    class FakeFeed:
        def __init__(self, raw, services):
            self.raw = raw
            self.services = services
            self.fetched_at = 0

        def find_service(self, community, service_name):
            for s in self.services:
                if s.community == community and s.service_name == service_name:
                    return s
            from oed_cli.errors import NotFoundError

            raise NotFoundError(f"unknown {service_name}")

    services = [disc.ServiceMeta.from_raw(s) for s in SAMPLE_FEED["communities"]["openeuler"]]
    fake = FakeFeed(SAMPLE_FEED, services)
    fake.for_community = lambda community: [s for s in services if s.community == community]

    def _fake_fetch(*, community=None, force_refresh=False):
        return fake

    def fake_read_spec_cache(path):
        # Return SAMPLE_SPEC regardless of the cache file (fresh or stale).
        return SAMPLE_SPEC

    # Patch the discovery module — every importer sees the fake feed.
    monkeypatch.setattr(disc, "fetch_discovery", _fake_fetch)
    monkeypatch.setattr(dyn, "fetch_discovery", _fake_fetch)
    monkeypatch.setattr(cli_mod, "fetch_discovery", _fake_fetch)

    # Spec comes from the cache layer, which is resolved at call time.
    monkeypatch.setattr(dyn, "_read_spec_cache", fake_read_spec_cache)

    return fake


def _extract_json(result) -> str:
    for stream in (result.output or "", result.stderr or ""):
        start = -1
        for i, ch in enumerate(stream):
            if ch in "{[":
                start = i
                break
        if start < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(stream)):
            ch = stream[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return stream[start : i + 1]
    return ""


# ---------------------------------------------------------------------------
# _fetch_user_allowlist — parsing & HTTP-error semantics
# ---------------------------------------------------------------------------


def test_fetch_user_data_parses_data_field(monkeypatch):
    """Happy path: response shape ``{"data":"a,b,c"}`` → ["a","b","c"]."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url, headers=kw.get("headers", {}))
        return httpx.Response(200, json={"data": "cvemanager,cve,easysearch"}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy-token") == [
        "cvemanager",
        "cve",
        "easysearch",
    ]


def test_fetch_user_data_parses_enveloped_response(monkeypatch):
    """oneid wraps the CSV in ``{msg, code, data: {data: "..."}}``.

    Real server response shape (captured 2026-08-13):
        {"msg": {"code": "S0001", "message_en": "Success",
                 "message_zh": "成功"}, "code": 200,
         "data": {"data": "search,cve"}}
    Parser must peel both layers to reach the allowlist.
    """

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "msg": {
                    "code": "S0001",
                    "message_en": "Success",
                    "message_zh": "成功",
                },
                "code": 200,
                "data": {"data": "search,cve"},
            },
            request=req,
        )

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy-token") == ["search", "cve"]


def test_fetch_user_data_handles_whitespace_and_empty_segments(monkeypatch):
    """Whitespace and empty ``,``-segments are stripped."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": " a , b ,, c "}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") == ["a", "b", "c"]


def test_fetch_user_data_returns_list_on_empty_string(monkeypatch):
    """Explicit empty ``data`` ("") → ``[]`` (user cleared, not None)."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": ""}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") == []


def test_fetch_user_data_returns_none_on_http_error(monkeypatch):
    """5xx → ``None`` (fail-open: caller treats as no allow-list)."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(500, text="oops", request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") is None


def test_fetch_user_data_returns_none_on_network_error(monkeypatch):
    """ConnectError / Timeout → ``None``."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") is None


def test_fetch_user_data_returns_none_on_malformed_body(monkeypatch):
    """Missing ``data`` key / wrong type → ``None``."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json={"wrong": "key"}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") is None


def test_fetch_user_data_returns_none_when_envelope_inner_data_missing(monkeypatch):
    """Enveloped shape where ``data.data`` is not a string → ``None``.

    Confirms the parser does not crash on the envelope when the inner
    field is absent or the wrong type — same fail-open guarantee as the
    flat-shape malformed case.
    """

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        # Envelope present but inner data field is a list (not str).
        return httpx.Response(
            200,
            json={"msg": {"code": "S0001"}, "code": 200, "data": ["wrong", "shape"]},
            request=req,
        )

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") is None


def test_fetch_user_data_returns_none_on_invalid_json(monkeypatch):
    """Response body isn't JSON → ``None``."""

    from oed_cli import auth as auth_mod

    def _fake_get(url, **kw):
        req = httpx.Request("GET", url)
        return httpx.Response(200, text="not json at all", request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    assert auth_mod._fetch_user_allowlist("dummy") is None


def test_fetch_user_data_returns_none_when_url_missing(monkeypatch):
    """No URL configured (TOML stripped + env unset) → ``None`` (fail-open)."""

    from oed_cli import auth as auth_mod

    monkeypatch.delenv("OED_USER_DATA_URL", raising=False)
    monkeypatch.setattr(auth_mod, "_get_user_data_url", lambda: None)
    # httpx.get must NOT be called when there's no URL.
    called = []
    monkeypatch.setattr(auth_mod.httpx, "get", lambda *a, **kw: called.append(1))
    assert auth_mod._fetch_user_allowlist("dummy") is None
    assert called == []


def test_fetch_user_data_sends_bearer_header(monkeypatch):
    """The Authorization: Bearer header carries the access_token."""

    from oed_cli import auth as auth_mod

    seen = {}

    def _fake_get(url, **kw):
        seen["headers"] = dict(kw.get("headers", {}))
        seen["url"] = url
        req = httpx.Request("GET", url, headers=kw.get("headers", {}))
        return httpx.Response(200, json={"data": "x"}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")
    auth_mod._fetch_user_allowlist("MY-TOKEN-1234")
    assert seen["headers"].get("Authorization") == "Bearer MY-TOKEN-1234"
    assert seen["url"] == "https://example.test/user-data"


def test_fetch_user_data_sends_user_code_param_when_user_code_given(monkeypatch):
    """oneid-specific: pass user_code as ``user_code`` query param.

    RFC 8628 considers user_code a public display value, but oneid's
    test-environment /device/user-data endpoint uses it for user lookup
    instead of resolving the bearer token. Send it as ``user_code=...``
    when supplied; omit when ``None`` (manual flow path).
    """

    from oed_cli import auth as auth_mod

    seen = {}

    def _fake_get(url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        req = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": "a,b"}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")

    # With user_code → sent as user_code query param.
    auth_mod._fetch_user_allowlist("MY-TOKEN", user_code="ABCD-EFGH")
    assert seen["params"] == {"user_code": "ABCD-EFGH"}

    # Without user_code → no params sent (httpx.get sees None).
    seen.clear()
    auth_mod._fetch_user_allowlist("MY-TOKEN")
    assert seen["params"] is None


# ---------------------------------------------------------------------------
# save_msal_auth — persist + legacy compat
# ---------------------------------------------------------------------------


def _fake_cache_with_token(token: str):
    """Build a stand-in for ``msal.SerializableTokenCache`` that already
    holds an access_token (so ``has_state_changed`` is True after a no-op
    write). Mirrors the contract used in ``tests/test_cli.py``."""

    class _Cache:
        def __init__(self, t):
            self._tok = t
            self._changed = True

        @property
        def has_state_changed(self) -> bool:
            return self._changed

        def serialize(self) -> str:
            return json.dumps({"AccessToken": {"k": {"access_token": self._tok}}})

    return _Cache(token)


def test_save_persists_allowlist(tmp_cache_dir):
    """save_msal_auth(allowlist=[...]) writes the list into auth.json."""

    from oed_cli.auth import load_auth, save_msal_auth

    cache = _fake_cache_with_token("tok-a")
    save_msal_auth(cache, allowlist=["cvemanager", "easysearch"])
    stored = load_auth()
    assert stored["allowlist"] == ["cvemanager", "easysearch"]


def test_save_omits_allowlist_when_none(tmp_cache_dir):
    """save_msal_auth(allowlist=None) MUST NOT add an ``allowlist`` key."""

    from oed_cli.auth import load_auth, save_msal_auth

    cache = _fake_cache_with_token("tok-b")
    save_msal_auth(cache)
    stored = load_auth()
    assert "allowlist" not in stored


def test_save_none_does_not_clobber_existing_allowlist(tmp_cache_dir):
    """Legacy auth.json with allowlist: re-saving with allowlist=None keeps it."""

    from oed_cli.auth import load_auth, save_msal_auth

    # Seed: write an allowlist.
    save_msal_auth(_fake_cache_with_token("tok-c"), allowlist=["old-service"])

    # Now save again with allowlist=None — must preserve.
    save_msal_auth(_fake_cache_with_token("tok-c"), allowlist=None)
    stored = load_auth()
    assert stored["allowlist"] == ["old-service"]


def test_save_refreshes_created_at_on_relogin(tmp_cache_dir, monkeypatch):
    """A fresh device-flow login must update ``created_at``.

    Regression: ``save_msal_auth`` guarded the write with
    ``if "created_at" not in data``, so a re-login onto an existing
    auth.json left the *previous* login's timestamp in place. ``oed auth
    status`` then read that stale timestamp via :func:`is_token_expired`
    and reported ``expired: true`` for a token that was actually valid.
    """
    from oed_cli import auth
    from oed_cli.auth import load_auth, save_msal_auth

    # First login at t=1000.
    monkeypatch.setattr(auth.time, "time", lambda: 1000.0)
    save_msal_auth(_fake_cache_with_token("tok-old"), allowlist=["svc-a"])
    assert load_auth()["created_at"] == 1000.0

    # Re-login at t=2000 — must refresh the timestamp.
    monkeypatch.setattr(auth.time, "time", lambda: 2000.0)
    save_msal_auth(_fake_cache_with_token("tok-new"), allowlist=["svc-b"])
    stored = load_auth()
    assert stored["created_at"] == 2000.0
    assert stored["allowlist"] == ["svc-b"]



# ---------------------------------------------------------------------------
# main._check_allowlist — dispatch gate
# ---------------------------------------------------------------------------


def _run(argv, *, runner, patched):
    """Invoke the full main() entry point so the dynamic path is exercised.

    ``cli`` (the click tree) only handles reserved commands — dynamic
    ``oed <service>`` routing lives in ``oed_cli.main.main``. The two
    paths are stitched together there.
    """
    from oed_cli import main as main_mod

    return main_mod.main(argv)


def _extract_json_str(s: str) -> str:
    """Extract the first JSON document from a free-form string (stderr)."""
    start = -1
    for i, ch in enumerate(s):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return ""
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return ""


def test_dispatch_blocks_service_not_in_allowlist(
    runner, tmp_cache_dir, patched_discovery
):
    """``oed easysearch ...`` when allowlist=["cvemanager"] → blocked, exit 1."""

    import contextlib
    import io

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-d"), allowlist=["cvemanager"])

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    stderr = buf.getvalue()
    assert rc == 1, stderr
    payload = json.loads(_extract_json_str(stderr))
    assert payload["ok"] is False
    assert payload["code"] == 1
    assert payload["error"] == "service_blocked_by_allowlist"
    assert "easysearch" in payload["message"]
    assert "cvemanager" in payload["hint"]
    assert "oed auth login" in payload["hint"]


def test_dispatch_allows_service_in_allowlist(
    runner, tmp_cache_dir, patched_discovery
):
    """``oed cvemanager`` with allowlist=["cvemanager"] → reaches the list cmd."""

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-e"), allowlist=["cvemanager"])

    rc = _run(["cvemanager"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_dispatch_failopen_when_no_auth_json(
    runner, tmp_cache_dir, patched_discovery
):
    """No auth.json at all → allow everything (fail-open)."""

    assert not (tmp_cache_dir / "auth.json").exists()
    rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_dispatch_failopen_when_allowlist_field_missing(
    runner, tmp_cache_dir, patched_discovery
):
    """auth.json without ``allowlist`` field → fail-open (legacy compat)."""

    from oed_cli.auth import auth_token_path, save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-f"))  # no allowlist
    assert "allowlist" not in auth_token_path().read_text()

    rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_dispatch_failopen_when_allowlist_empty(
    runner, tmp_cache_dir, patched_discovery
):
    """allowlist=[] (user cleared) → fail-open."""

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-g"), allowlist=[])

    rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_dispatch_reserved_unaffected_by_allowlist(
    runner, tmp_cache_dir, patched_discovery
):
    """Reserved commands (e.g. ``services``) bypass the gate entirely."""

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-h"), allowlist=["cvemanager"])

    # ``services`` is reserved → handled by the click tree, never reaches
    # the allow-list gate.
    rc = _run(["services"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_ag_exempt_from_allowlist(tmp_cache_dir):
    """``ag`` authenticates with its own PAT, not oneid — exempt from the gate.

    Regardless of the stored allow-list, ``_check_allowlist("ag")`` must never
    raise. (Login/logout are short-circuited earlier in dispatch; this asserts
    the exemption holds even for real ``ag`` service calls that reach the gate.)
    """

    from oed_cli.auth import save_msal_auth
    from oed_cli.main import _check_allowlist

    # An allow-list that explicitly excludes "ag".
    save_msal_auth(_fake_cache_with_token("tok-ag"), allowlist=["cvemanager"])

    # No raise — ag is PAT-authenticated, not oneid-governed.
    _check_allowlist("ag")

    # Sanity: a non-ag service not in the list still raises.
    from oed_cli.errors import UserError

    with pytest.raises(UserError, match="not in your CLI allow-list"):
        _check_allowlist("easysearch")


def test_default_allowlist_always_allows(tmp_cache_dir):
    """Default-pass services are allowed even when an explicit allow-list
    excludes them — the two sources are a union.

    Covers the "allowlist 两来源" change: ``DEFAULT_ALLOWLIST`` members
    (``search``/``cve``/``easysoftware``/``mailman``/``ag``/``CI``/``meeting``)
    bypass the per-user oneid list regardless of login state, because they are
    public / PAT-authed services the CLI must keep usable.
    """

    from oed_cli.auth import save_msal_auth
    from oed_cli.main import DEFAULT_ALLOWLIST, _check_allowlist

    # An allow-list that explicitly excludes every default-listed service.
    save_msal_auth(
        _fake_cache_with_token("tok-default"),
        allowlist=["cvemanager"],
    )

    assert "cve" in DEFAULT_ALLOWLIST
    assert "ag" in DEFAULT_ALLOWLIST
    assert "mailman" in DEFAULT_ALLOWLIST
    assert "meeting" in DEFAULT_ALLOWLIST
    for svc in DEFAULT_ALLOWLIST:
        # None of these are in the stored ["cvemanager"] list, yet each must
        # pass the gate via the default source.
        _check_allowlist(svc)


def test_default_allowlist_ci_is_case_sensitive(tmp_cache_dir):
    """``CI`` is matched exactly (capital C, lowercase i) — ``ci``/``Ci`` are NOT
    the default-listed service and must fall through to the stored list."""

    from oed_cli.auth import save_msal_auth
    from oed_cli.errors import UserError
    from oed_cli.main import _check_allowlist

    save_msal_auth(_fake_cache_with_token("tok-ci"), allowlist=["cvemanager"])

    # The registered service is "CI" — passes the default list.
    _check_allowlist("CI")
    # Lowercase variants are a different string → blocked (not in stored list).
    for variant in ("ci", "Ci", "cI"):
        with pytest.raises(UserError, match="not in your CLI allow-list"):
            _check_allowlist(variant)


def test_default_allowlist_not_logged_in_still_passes(tmp_cache_dir):
    """Default-listed services are usable without any login at all."""

    from oed_cli.main import _check_allowlist

    # No auth.json present at all.
    _check_allowlist("search")
    _check_allowlist("easysoftware")




def test_clear_auth_removes_allowlist(runner, tmp_cache_dir, patched_discovery):
    """`oed auth logout` clears auth.json (including the allowlist field)."""

    from oed_cli.auth import auth_token_path, load_auth, save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-i"), allowlist=["cvemanager"])
    assert load_auth()["allowlist"] == ["cvemanager"]

    result = runner.invoke(cli, ["auth", "logout"])
    assert result.exit_code == 0
    assert not auth_token_path().exists()
    # After logout, dispatch is fail-open.
    rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    assert rc == 0


def test_auth_status_reports_allowlist(runner, tmp_cache_dir):
    """`oed auth status` surfaces the current allowlist to the user."""

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-j"), allowlist=["cvemanager", "cve"])

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert sorted(payload["allowlist"]) == ["cve", "cvemanager"]


def test_auth_status_allowlist_none_for_legacy_auth(runner, tmp_cache_dir):
    """Legacy auth.json (no allowlist key) → status reports ``None``."""

    from oed_cli.auth import save_msal_auth

    save_msal_auth(_fake_cache_with_token("tok-k"))  # no allowlist

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(_extract_json(result))
    assert payload["allowlist"] is None


# ---------------------------------------------------------------------------
# End-to-end: login path persists allowlist + downstream check uses it
# ---------------------------------------------------------------------------


def test_login_flow_persists_allowlist_and_gates_dispatch(
    runner, tmp_cache_dir, patched_discovery, monkeypatch
):
    """Full integration: device-flow login → fetch user-data → save →
    subsequent dispatch blocks an unknown service and allows a known one."""

    from oed_cli import auth as auth_mod

    class _FakeApp:
        def __init__(self, *a, token_cache=None, **kw):
            self._cache = token_cache

        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "AAAA-BBBB",
                "device_code": "dev-fake",
                "verification_uri": "https://example.test/device",
                "expires_in": 600,
                "interval": 0,
                "message": "go",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            if self._cache is not None:
                self._cache.add(access_token="login-token-xyz")
            return {
                "access_token": "login-token-xyz",  # gitleaks:allow
                "refresh_token": "rt",
            }

    class _FakeCache:
        def __init__(self):
            self._changed = False
            self._at = ""
            self._rt = ""

        @property
        def has_state_changed(self) -> bool:
            return self._changed

        def add(self, access_token: str = "", refresh_token: str = "") -> None:
            if access_token:
                self._at = access_token
            if refresh_token:
                self._rt = refresh_token
            self._changed = True

        def serialize(self) -> str:
            if not self._at:
                return "{}"
            return json.dumps(
                {
                    "AccessToken": {"k": {"access_token": self._at}},
                    "RefreshToken": {"k": {"refresh_token": self._rt}},
                }
            )

    monkeypatch.setattr(auth_mod, "msal", type("M", (), {
        "PublicClientApplication": _FakeApp,
        "SerializableTokenCache": _FakeCache,
    }))

    # Allow-list endpoint returns ["cvemanager"] only.
    def _fake_get(url, **kw):
        req = httpx.Request("GET", url, headers=kw.get("headers", {}))
        return httpx.Response(200, json={"data": "cvemanager"}, request=req)

    monkeypatch.setattr(auth_mod.httpx, "get", _fake_get)
    monkeypatch.setenv("OED_USER_DATA_URL", "https://example.test/user-data")

    # Step 1: device-flow login
    result = runner.invoke(cli, ["auth", "login"])
    assert result.exit_code == 0, (result.output, result.stderr)

    # Allowlist persisted on disk
    from oed_cli.auth import load_auth

    stored = load_auth()
    assert stored["allowlist"] == ["cvemanager"]

    # Step 2: known service → allowed
    rc = _run(["cvemanager"], runner=runner, patched=patched_discovery)
    assert rc == 0, "cvemanager should be allowed"

    # Step 3: unknown service → blocked
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = _run(["easysearch"], runner=runner, patched=patched_discovery)
    stderr = buf.getvalue()
    assert rc == 1, stderr
    payload = json.loads(_extract_json_str(stderr))
    assert payload["error"] == "service_blocked_by_allowlist"
