"""Smoke tests for the oed-cli minimum scaffold (v0.1).

These tests do not require network — they exercise the CLI's argument parsing,
JSON shaping and exit-code contracts using a monkey-patched discovery layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oed_cli import __version__  # noqa: E402
from oed_cli.cli import cli  # noqa: E402

SAMPLE_FEED = {
    "kind": "discovery#servicesListByCommunity",
    "communities": {
        "openeuler": [
            {
                "name": "openeuler/pkgcontrib",
                "service_name": "pkgcontrib",
                "community": "openeuler",
                "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
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
    "info": {"title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER", "version": "1.0.0"},
    "servers": [{"url": "$APIG_GROUP_ENTRY_URL"}],
    "paths": {
        "/v1/cla": {
            "get": {"summary": "CLA", "operationId": "listSigs", "responses": {}}
        },
        "/v1/softwarepkg": {
            "get": {
                "summary": "list",
                "operationId": "listSoftwarePackages",
                "responses": {},
            }
        },
    },
}


@pytest.fixture()
def patched_discovery(monkeypatch):
    from oed_cli import cli as cli_mod
    from oed_cli import discovery as disc

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

    def fake_spec(svc):
        return SAMPLE_SPEC
    monkeypatch.setattr(cli_mod, "fetch_discovery", _fake_fetch)
    monkeypatch.setattr(cli_mod, "fetch_spec", fake_spec)
    return fake


def test_version(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in (result.output or "") + (result.stderr or "")


def test_info_returns_structured_payload(runner, patched_discovery):
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["ok"] is True
    assert payload["services_total"] == 2
    assert "openeuler" in payload["communities_seen"]


def test_services_lists_only_current_community(runner, patched_discovery, monkeypatch):
    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    result = runner.invoke(cli, ["services"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert {s["service_name"] for s in payload} == {"pkgcontrib", "easysearch"}


def test_schema_full(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "pkgcontrib"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["openapi"] == "3.0.3"
    assert "/v1/cla" in payload["paths"]


def test_schema_method_lookup(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "pkgcontrib.listSoftwarePackages"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["service"] == "openeuler/pkgcontrib"
    assert any(
        m["operation"]["operationId"] == "listSoftwarePackages"
        for m in payload["matches"]
    )


def test_schema_unknown_method_reports_available(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "pkgcontrib.DOES_NOT_EXIST"])
    assert result.exit_code == 4
    payload = json.loads(_extract_json(result))
    assert payload["code"] == 4
    assert payload["error"] == "method_not_found"
    assert "/v1/cla" in payload["available_paths"]


def test_schema_missing_spec_reports_hint(monkeypatch, runner):
    """When the upstream returns an empty body, surface a clear exit-4 hint."""

    from oed_cli import cli as cli_mod
    from oed_cli.errors import NotFoundError

    class _StubFeed:
        def __init__(self):
            self.services = [
                cli_mod.ServiceMeta(
                    name="openeuler/easysearch",
                    service_name="easysearch",
                    community="openeuler",
                    title="APIG_OPENEULER_EASYSEARCH",
                    version="1.0.0",
                    description="",
                    base_url="$APIG_GROUP_ENTRY_URL",
                )
            ]
            self.fetched_at = 0

        def find_service(self, community, service_name):
            for s in self.services:
                if s.service_name == service_name:
                    return s
            raise NotFoundError("unknown")

    monkeypatch.setattr(cli_mod, "fetch_discovery", lambda **_: _StubFeed())
    monkeypatch.setattr(
        cli_mod,
        "fetch_spec",
        lambda svc: (_ for _ in ()).throw(
            NotFoundError(
                "spec missing",
                kind="spec_missing",
                hint=(
                    "The discovery feed lists this service but its OpenAPI "
                    "spec has not been published yet."
                ),
            )
        ),
    )

    result = runner.invoke(cli, ["schema", "easysearch"])
    assert result.exit_code == 4
    payload = json.loads(_extract_json(result))
    assert payload["code"] == 4
    assert payload["error"] == "spec_missing"


def test_help_lists_commands(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("info", "services", "schema", "cache"):
        assert cmd in (result.output or "")


def test_help_enumerates_discovered_services(runner, patched_discovery):
    """`oed --help` should append an Auto-discovered services section."""

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    text = (result.output or "") + (result.stderr or "")
    assert "Auto-discovered services" in text
    assert "pkgcontrib" in text
    assert "easysearch" in text
    # Quickstart now leads with the per-parameter flag style and the canonical
    # CVE example; it should not mention the legacy API_<NAME> placeholder.
    assert "API_<NAME>" not in text
    assert "--<kebab-case>" in text
    assert "getSecurityNoticeByCveId --cve-id" in text


def test_help_does_not_break_when_discovery_fails(runner, monkeypatch):
    """If discovery fails (offline / empty cache), --help must still render."""

    from oed_cli import cli as cli_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(cli_mod.OedCli, "_services_help_section", staticmethod(lambda: None))
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    text = result.output or ""
    assert "Commands:" in text
    # Auto-discovered section is omitted in the offline case.
    assert "Auto-discovered services" not in text


# ---------------------------------------------------------------------------
# Auth sub-group (v0.4)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_cache_dir(monkeypatch, tmp_path):
    """Redirect OED_CACHE_DIR to a tmp_path so auth.json is never written to
    the user's real cache during tests."""

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_auth_status_no_token(runner, tmp_cache_dir):
    """Clean state: auth.json does not exist, status reports logged_in: false."""

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["logged_in"] is False
    # path / backend / token details are intentionally omitted when logged out.
    assert "path" not in payload
    assert "backend" not in payload


def test_auth_token_save_roundtrip(runner, tmp_cache_dir):
    """`oed auth token <val>` persists to auth.json; subsequent status confirms."""

    token = "eyJhbGciOiJIUzI1NiJ9.test.signature"  # gitleaks:allow
    result = runner.invoke(cli, ["auth", "token", token])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["ok"] is True
    assert "auth.json" in payload["path"]

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(_extract_json(result))
    assert payload["logged_in"] is True
    assert payload["token_present"] is True
    assert payload["created_at"] > 0
    # Removed fields: cookie / refresh_token / id_token / username / path /
    # backend are no longer surfaced by status.
    assert "has_cookie" not in payload
    assert "has_refresh_token" not in payload
    assert "path" not in payload


def test_auth_logout_clears_file(runner, tmp_cache_dir, tmp_path):
    """`oed auth logout` deletes the auth.json file."""

    from oed_cli.discovery import auth_token_path

    # Seed auth.json via the same command.
    runner.invoke(cli, ["auth", "token", "test-token"])
    assert auth_token_path().is_file()

    result = runner.invoke(cli, ["auth", "logout"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["ok"] is True
    assert payload["cleared"] is True
    assert not auth_token_path().exists()


def test_auth_token_with_cookie(runner, tmp_cache_dir):
    """`oed auth token <val> --cookie 'k=v'` stores the cookie (status no
    longer exposes has_cookie, so we assert the cookie round-trips via the
    auth store directly)."""

    from oed_cli.auth import load_auth

    runner.invoke(cli, ["auth", "token", "tok-xyz", "--cookie", "session=abc; csrf=def"])

    stored = load_auth()
    assert stored is not None
    assert stored.get("cookie") == "session=abc; csrf=def"

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(_extract_json(result))
    assert payload["logged_in"] is True
    assert "has_cookie" not in payload


def test_auth_help_listed_in_top_help(runner, patched_discovery):
    """`oed --help` mentions the new auth sub-group."""

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    text = (result.output or "") + (result.stderr or "")
    assert "auth" in text


def test_auth_status_redacts_token(runner, tmp_cache_dir):
    """`auth status` MUST NOT include the full token — only a fingerprint."""

    secret = "eyJhbGciOiJIUzI1NiJ9.super-secret-payload-that-must-not-leak"  # gitleaks:allow
    runner.invoke(cli, ["auth", "token", secret])

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0
    text = (result.output or "") + (result.stderr or "")
    assert secret not in text
    payload = json.loads(_extract_json(result))
    # Fingerprint is first3 + ... + last2 = "eyJ" + ".." + "ak"
    assert payload["token_fingerprint"] == "eyJ...ak"
    assert "ey" in payload["token_fingerprint"]  # at least some prefix is visible


def test_auth_status_no_longer_exposes_removed_fields(runner, tmp_cache_dir):
    """status was trimmed: path / backend / has_cookie / has_refresh_token /
    has_id_token / id_token_claims / username / created_at_iso are gone."""

    from oed_cli.auth import save_msal_auth

    class _Cache:
        def __init__(self):
            self.has_state_changed = True

        def serialize(self) -> str:
            return json.dumps(
                {
                    "AccessToken": {"k": {"secret": "tok-rt"}},
                    "RefreshToken": {"k": {"secret": "rt-secret"}},
                    "IdToken": {"k": {"secret": "x.y.z"}},
                }
            )

    save_msal_auth(_Cache(), allowlist=None)

    result = runner.invoke(cli, ["auth", "status"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["logged_in"] is True
    for removed in (
        "path", "backend", "has_cookie", "has_refresh_token",
        "has_id_token", "id_token_claims", "username", "created_at_iso",
    ):
        assert removed not in payload, removed


def test_extract_refresh_token_reads_msal_secret_field():
    """Regression: the refresh_token lives in the MSAL cache under the
    ``secret`` key (not a top-level ``refresh_token`` field, and not the old
    ``refresh_token`` entry key). _extract_refresh_token must find it."""

    from oed_cli.auth import _extract_refresh_token

    cache_json = json.dumps(
        {"RefreshToken": {"k": {"secret": "rt-secret", "credential_type": "RefreshToken"}}}
    )
    assert _extract_refresh_token(cache_json) == "rt-secret"
    # Empty cache → None.
    assert _extract_refresh_token(json.dumps({})) is None


def test_auth_login_manual_requires_tty(runner, tmp_cache_dir):
    """`auth login --manual` in non-TTY mode exits 1 with kind='not_tty'."""

    result = runner.invoke(cli, ["auth", "login", "--manual"])
    # CliRunner captures stdout (not a real TTY); expect failure + hint.
    assert result.exit_code == 1, (result.output, result.stderr)
    text = (result.output or "") + (result.stderr or "")
    assert "not_tty" in text


def _extract_json(result) -> str:
    """Return the leading JSON document from a CliRunner result, checking
    both stdout (success) and stderr (errors go through click.echo(..., err=True))."""
    for stream in (result.output or "", result.stderr or ""):
        start = _first_bracket(stream)
        if start < 0:
            continue
        end = _match_bracket(stream, start)
        if end > start:
            return stream[start : end + 1]
    return ""


def _first_bracket(s: str) -> int:
    for i, ch in enumerate(s):
        if ch in "{[":
            return i
    return -1


def _match_bracket(s: str, start: int) -> int:
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"
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
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


@pytest.fixture()
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# RFC 8628 Device Authorization Grant (v0.5)
# ---------------------------------------------------------------------------


class _FakeSerializableTokenCache:
    """Minimal stand-in for ``msal.SerializableTokenCache``.

    Stores a single ``access_token`` and behaves like MSAL's real cache with
    respect to the bits our code uses: ``has_state_changed``, ``serialize``,
    ``deserialize``, and ``search``.
    """

    def __init__(self):
        self._changed = False
        self._access_token: str = ""
        self._refresh_token: str = ""

    @property
    def has_state_changed(self) -> bool:
        return self._changed

    def add(self, access_token: str = "", refresh_token: str = "") -> None:
        if access_token:
            self._access_token = access_token
        if refresh_token:
            self._refresh_token = refresh_token
        self._changed = True

    def serialize(self) -> str:
        if not self._access_token:
            return "{}"
        return json.dumps(
            {
                "AccessToken": {
                    "fake-key": {
                        "access_token": self._access_token,
                        "token_type": "Bearer",
                    }
                },
                "RefreshToken": (
                    {"fake-key": {"refresh_token": self._refresh_token}}
                    if self._refresh_token
                    else {}
                ),
            }
        )

    def deserialize(self, raw: str) -> None:
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return
        for entry in (data.get("AccessToken") or {}).values():
            if isinstance(entry, dict):
                tok = entry.get("access_token")
                if tok:
                    self._access_token = tok
        for entry in (data.get("RefreshToken") or {}).values():
            if isinstance(entry, dict):
                tok = entry.get("refresh_token")
                if tok:
                    self._refresh_token = tok

    def search(self, credential_type: str, **_kw):
        if credential_type != "AccessToken" or not self._access_token:
            return []
        return [{"access_token": self._access_token}]


def _make_fake_msal(public_app_cls) -> SimpleNamespace:
    """Bundle a PublicClientApplication fake with a minimal token cache."""
    return SimpleNamespace(
        PublicClientApplication=public_app_cls,
        SerializableTokenCache=_FakeSerializableTokenCache,
    )


class _BaseFakeApp:
    """Base that mirrors MSAL's behavior of stuffing tokens into the cache."""

    def __init__(self, *a, token_cache=None, **kw):
        self._cache = token_cache

    def _record(self, result: dict) -> dict:
        if self._cache is not None and "access_token" in result:
            self._cache.add(
                access_token=result.get("access_token", ""),
                refresh_token=result.get("refresh_token", ""),
            )
        return result


def test_auth_login_help_browser_flow(runner, tmp_cache_dir, monkeypatch):
    """`auth login` (no --manual) emits the device_code_issued stage JSON.

    MSAL is monkeypatched so no real network call happens. The fake MSAL app
    emits a deterministic device flow, returns a deterministic token, and the
    CLI persists it.
    """

    from oed_cli import auth as auth_mod

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "ABCD-1234",
                "device_code": "device-code-fake",
                "verification_uri": "https://example.test/device",
                "expires_in": 600,
                "interval": 0,
                "message": "Visit https://example.test/device and enter ABCD-1234",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return self._record(
                {"access_token": "fake-device-access-tok", "refresh_token": "fake-rt"}
            )

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))
    # Stub the post-login allow-list fetch (real httpx call would network).
    monkeypatch.setattr(auth_mod, "_fetch_user_allowlist", lambda tok, user_code=None: None)

    result = runner.invoke(cli, ["auth", "login"])
    assert result.exit_code == 0, (result.output, result.stderr)
    text = (result.output or "") + (result.stderr or "")
    # Token is redacted in output — never the full bearer.
    assert "fake-device-access-tok" not in text
    assert "saved" in text
    # MSAL cache should have persisted the access token.
    assert auth_mod.get_token() == "fake-device-access-tok"


def test_top_level_login_is_alias_for_auth_login(runner, tmp_cache_dir, monkeypatch):
    """`oed login` is a top-level shortcut that runs the same device flow
    as `oed auth login` — both funnel through run_login()."""

    from oed_cli import auth as auth_mod

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "SHORT-0001",
                "device_code": "short-device-code",
                "verification_uri": "https://example.test/device",
                "expires_in": 600,
                "interval": 0,
                "message": "Visit https://example.test/device and enter SHORT-0001",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return self._record(
                {"access_token": "shortcut-access-tok", "refresh_token": "shortcut-rt"}
            )

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))
    monkeypatch.setattr(auth_mod, "_fetch_user_allowlist", lambda tok, user_code=None: None)

    result = runner.invoke(cli, ["login"])
    assert result.exit_code == 0, (result.output, result.stderr)
    text = (result.output or "") + (result.stderr or "")
    assert "shortcut-access-tok" not in text
    assert "saved" in text
    assert auth_mod.get_token() == "shortcut-access-tok"


def test_device_login_emits_issued_stage(monkeypatch, tmp_cache_dir, capsys):
    """`device_login` prints ``device_code_issued`` to stdout, then user_code to stderr."""

    from oed_cli import auth as auth_mod

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "WXYZ-1234",
                "device_code": "dev-fake",
                "verification_uri": "https://example.test/device",
                "expires_in": 600,
                "interval": 0,
                "message": "Visit https://example.test/device and enter WXYZ-1234",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return self._record(
                {"access_token": "tok-issued", "refresh_token": "rt-issued"}
            )

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))
    monkeypatch.setattr(auth_mod, "_fetch_user_allowlist", lambda tok, user_code=None: None)

    result = auth_mod.device_login(open_browser=False, copy_code=False)

    assert result is not None
    assert result["access_token"] == "tok-issued"
    assert result["refresh_token"] == "rt-issued"

    # MSAL cache persisted to auth.json → get_token() pulls it back out.
    assert auth_mod.get_token() == "tok-issued"

    # device_code_issued stage: user_code + verification_uri are the only
    # user-facing fields. scope_granted intentionally absent — RFC 8628
    # /device/code has no scope field; the granted set surfaces at the
    # token_received stage.
    out = capsys.readouterr().out
    issued = json.loads(out.splitlines()[0])
    assert issued["stage"] == "device_code_issued"
    assert issued["user_code"] == "WXYZ-1234"
    assert "verification_uri" in issued


def test_get_scopes_returns_hardcoded_base(monkeypatch):
    """Without env override, scopes must include email + id_token (the oneid
    wire contract). defaults.toml is NOT consulted (hardcoded to avoid
    stale-TOML footguns where shipped wheel dropped id_token)."""

    from oed_cli import auth as auth_mod

    monkeypatch.delenv("OED_SCOPES", raising=False)
    scopes = auth_mod.get_scopes()
    assert "email" in scopes
    assert "id_token" in scopes
    # Reserved scopes are filtered (MSAL auto-appends them)
    assert "openid" not in scopes
    assert "profile" not in scopes
    assert "offline_access" not in scopes


def test_get_scopes_env_overrides_base(monkeypatch):
    """OED_SCOPES env var (comma-separated) replaces the base entirely."""

    from oed_cli import auth as auth_mod

    monkeypatch.setenv("OED_SCOPES", "custom1,custom2")
    scopes = auth_mod.get_scopes()
    assert scopes == ["custom1", "custom2"]


def test_get_scopes_env_empty_string_yields_empty(monkeypatch):
    """Explicit OED_SCOPES='' is honored as 'override to empty' (env wins)."""

    from oed_cli import auth as auth_mod

    monkeypatch.setenv("OED_SCOPES", "")
    assert auth_mod.get_scopes() == []


def test_get_scopes_env_filters_reserved(monkeypatch):
    """Reserved scopes passed via env are stripped (MSAL refuses them)."""

    from oed_cli import auth as auth_mod

    monkeypatch.setenv("OED_SCOPES", "email,openid,profile,custom")
    scopes = auth_mod.get_scopes()
    assert "openid" not in scopes
    assert "profile" not in scopes
    assert "email" in scopes
    assert "custom" in scopes


def test_device_login_init_failure_returns_none(monkeypatch, capsys):
    """When MSAL refuses to initiate (e.g. usercenter not ready), device_login returns None."""

    from oed_cli import auth as auth_mod

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "error": "unsupported_response_type",
                "error_description": "device flow not enabled for this client",
            }

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))

    result = auth_mod.device_login(open_browser=False, copy_code=False)
    assert result is None

    err = capsys.readouterr().err
    assert "device_flow_init_failed" in err
    assert "--manual" in err


def test_device_login_access_denied_returns_none(monkeypatch, capsys):
    """MSAL returns ``access_denied`` → device_login returns None + clear error JSON."""

    from oed_cli import auth as auth_mod

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "AAA-111",
                "device_code": "dev",
                "verification_uri": "https://example.test/d",
                "expires_in": 600,
                "interval": 0,
                "message": "go",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return {"error": "access_denied", "error_description": "user said no"}

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))

    result = auth_mod.device_login(open_browser=False, copy_code=False)
    assert result is None

    err = capsys.readouterr().err
    assert "device_token_error" in err
    assert "access_denied" in err


def test_device_login_uses_oed_app_id_env(monkeypatch, tmp_cache_dir):
    """OED_APP_ID env var overrides the bundled default for client_id."""

    from oed_cli import auth as auth_mod

    captured: dict = {}

    class _FakeApp(_BaseFakeApp):
        def __init__(self, client_id, authority=None, oidc_authority=None, **kw):
            super().__init__(**kw)
            captured["client_id"] = client_id
            captured["authority"] = authority or oidc_authority

        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "AA-11",
                "device_code": "dev",
                "verification_uri": "https://example.test/d",
                "expires_in": 600,
                "interval": 0,
                "message": "go",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return self._record({"access_token": "tok", "refresh_token": "rt"})

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))
    monkeypatch.setenv("OED_APP_ID", "fork-cli-id")
    monkeypatch.setenv("OED_DEVICE_URL", "https://usercenter.fork.test")
    monkeypatch.setattr(auth_mod, "_fetch_user_allowlist", lambda tok, user_code=None: None)

    auth_mod.device_login(open_browser=False, copy_code=False)

    assert captured["client_id"] == "fork-cli-id"
    assert captured["authority"] == "https://usercenter.fork.test"


def test_device_login_does_not_call_webbrowser_when_disabled(monkeypatch, tmp_cache_dir):
    """open_browser=False → verification URI is NOT handed to webbrowser."""

    from oed_cli import auth as auth_mod

    opened: list[str] = []

    class _FakeApp(_BaseFakeApp):
        def initiate_device_flow(self, scopes=None):
            return {
                "user_code": "AA-11",
                "device_code": "dev",
                "verification_uri": "https://example.test/d",
                "expires_in": 600,
                "interval": 0,
                "message": "go",
            }

        def acquire_token_by_device_flow(self, flow, **kw):
            return self._record({"access_token": "tok"})

    monkeypatch.setattr(auth_mod, "msal", _make_fake_msal(_FakeApp))
    monkeypatch.setattr(
        auth_mod.webbrowser,
        "open",
        lambda u, *a, **kw: opened.append(u) or True,
    )
    monkeypatch.setattr(auth_mod, "_fetch_user_allowlist", lambda tok, user_code=None: None)

    auth_mod.device_login(open_browser=False, copy_code=False)
    assert opened == []


def test_copy_to_clipboard_falls_back_across_tools(monkeypatch):
    """_copy_to_clipboard tries wl-copy → xclip → pbcopy → clip, returns True on first success."""

    from oed_cli import auth as auth_mod

    calls: list[list[str]] = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        # First call (wl-copy) succeeds
        if cmd[0] == "wl-copy":
            return _FakeCompleted()
        raise FileNotFoundError

    class _FakeCompleted:
        returncode = 0

    monkeypatch.setattr(auth_mod.subprocess, "run", _fake_run)
    assert auth_mod._copy_to_clipboard("ABCD-1234") is True
    assert calls[0][0] == "wl-copy"


def test_copy_to_clipboard_returns_false_when_no_tool(monkeypatch):
    """When no clipboard tool exists, return False (don't raise)."""

    from oed_cli import auth as auth_mod

    def _always_missing(*a, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(auth_mod.subprocess, "run", _always_missing)
    assert auth_mod._copy_to_clipboard("XXXX") is False


def test_load_defaults_reads_bundled_toml():
    """load_defaults() returns the [oauth] section from defaults.toml in the wheel."""

    from oed_cli import auth as auth_mod

    defaults = auth_mod.load_defaults()
    assert "oauth" in defaults
    assert defaults["oauth"]["client_id"] == "623c3c2f1eca5ad5fca6c58a"
    assert "omapi.osinfra.cn" in defaults["oauth"]["device_url"]


def test_get_client_id_env_overrides_default(monkeypatch):
    """OED_APP_ID env wins over defaults.toml client_id."""

    from oed_cli import auth as auth_mod

    monkeypatch.setenv("OED_APP_ID", "override-id")
    assert auth_mod.get_client_id() == "override-id"

    monkeypatch.delenv("OED_APP_ID", raising=False)
    assert auth_mod.get_client_id() == "623c3c2f1eca5ad5fca6c58a"


def test_get_device_url_env_overrides_default(monkeypatch):
    """OED_DEVICE_URL env wins over defaults.toml device_url."""

    from oed_cli import auth as auth_mod

    monkeypatch.setenv("OED_DEVICE_URL", "https://fork.test")
    assert auth_mod.get_device_url() == "https://fork.test"

    monkeypatch.delenv("OED_DEVICE_URL", raising=False)
    assert "omapi.osinfra.cn" in auth_mod.get_device_url()


def test_extract_set_cookie_from_dict():
    """extract_set_cookie pulls the name=value pair out of a dict header."""

    from oed_cli.auth import extract_set_cookie

    headers = {
        "Content-Type": "application/json",
        "Set-Cookie": "session=abc123; Path=/; HttpOnly",
    }
    assert extract_set_cookie(headers) == "session=abc123"


def test_extract_set_cookie_returns_none_when_absent():
    from oed_cli.auth import extract_set_cookie

    assert extract_set_cookie({"Content-Type": "application/json"}) is None
    assert extract_set_cookie({}) is None


def test_update_auth_from_response_rotates_cookie(runner, tmp_cache_dir):
    """A Set-Cookie from the backend overwrites auth.json's cookie."""

    from oed_cli.auth import get_token, load_auth, update_auth_from_response_headers

    # Seed existing auth
    runner.invoke(cli, ["auth", "token", "tok", "--cookie", "session=old"])

    # Backend sends a new cookie
    rotated = update_auth_from_response_headers(
        {"Set-Cookie": "session=new-rotated-value; Path=/; HttpOnly"}
    )
    assert rotated is True

    stored = load_auth()
    assert stored["cookie"] == "session=new-rotated-value"
    # Token is stored inside the MSAL cache blob, not as a top-level key.
    assert get_token() == "tok"


def test_update_auth_skips_when_cookie_unchanged(runner, tmp_cache_dir):
    """If the new cookie equals the existing one, don't rewrite the file."""

    from oed_cli.auth import update_auth_from_response_headers

    runner.invoke(cli, ["auth", "token", "tok", "--cookie", "session=same"])

    result = update_auth_from_response_headers(
        {"Set-Cookie": "session=same; Path=/"}
    )
    assert result is False


def test_try_open_browser_skips_when_browser_env_is_none(monkeypatch, capsys):
    """BROWSER=none → skip webbrowser, print URL + hint to stderr, return False."""

    monkeypatch.setenv("BROWSER", "none")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    from oed_cli import auth as auth_mod

    # Patch webbrowser.open to make sure it is NOT called
    opened = []
    monkeypatch.setattr(auth_mod.webbrowser, "open", lambda u: opened.append(u) or True)

    result = auth_mod._try_open_browser("https://example.com/login?x=1")
    assert result is False
    assert opened == []  # never tried

    err = capsys.readouterr().err
    assert "browser_skipped" in err
    assert "https://example.com/login?x=1" in err


def test_try_open_browser_detects_no_display_on_linux(monkeypatch, capsys):
    """Headless Linux (no DISPLAY) → print URL + manual hint, return False."""

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("BROWSER", raising=False)

    from oed_cli import auth as auth_mod

    opened = []
    monkeypatch.setattr(auth_mod.webbrowser, "open", lambda u: opened.append(u) or True)

    # Force _has_display() to report False even on non-linux test runners
    monkeypatch.setattr(auth_mod, "_has_display", lambda: False)

    result = auth_mod._try_open_browser("https://example.com/login")
    assert result is False
    assert opened == []

    err = capsys.readouterr().err
    assert "no_display" in err
    assert "--manual" in err


def test_try_open_browser_calls_webbrowser_when_display_present(monkeypatch):
    """With a display present, webbrowser.open is called and its truthy return propagates."""

    monkeypatch.delenv("BROWSER", raising=False)

    from oed_cli import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_has_display", lambda: True)

    opened = []

    def _fake_open(url, *args, **kwargs):
        opened.append(url)
        return True

    monkeypatch.setattr(auth_mod.webbrowser, "open", _fake_open)

    result = auth_mod._try_open_browser("https://example.com/x")
    assert result is True
    assert opened == ["https://example.com/x"]


def test_try_open_browser_handles_webbrowser_error(monkeypatch, capsys):
    """webbrowser.Error raised → caught, return False, print hint."""

    import webbrowser as wb_mod

    from oed_cli import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_has_display", lambda: True)

    def _raise(url, *args, **kwargs):
        raise wb_mod.Error("no browser")

    monkeypatch.setattr(auth_mod.webbrowser, "open", _raise)

    result = auth_mod._try_open_browser("https://example.com/y")
    assert result is False
    err = capsys.readouterr().err
    assert "browser_failed" in err
    assert "--manual" in err


def test_summarize_token_response_redacts_sensitive_fields():
    """Token / refresh / id values must never appear in stdout summary."""

    from oed_cli import auth as auth_mod

    result = {
        "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        "refresh_token": "def50200abcdef-long-refresh-token",
        "id_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.idpayload.idsig",
        "token_type": "Bearer",
        "scope": "openid profile email id_token offline_access",
        "expires_in": 86400,
        "expires_on": 1700000000,
        "id_token_claims": {
            "iss": "https://omapi.osinfra.cn/oneid/oidc",
            "sub": "user-123",
            "aud": "623c3c2f1eca5ad5fca6c58a",
            "exp": 1700000000,
            "iat": 1699996400,
            "email": "user@example.com",
            "preferred_username": "user",
        },
    }
    summary = auth_mod._summarize_token_response(result)

    # stage + presence flags
    assert summary["stage"] == "token_received"
    assert summary["ok"] is True
    assert summary["has_access_token"] is True
    assert summary["has_refresh_token"] is True
    assert summary["has_id_token"] is True
    # raw token strings absent from summary (any key, any nesting)
    blob = json.dumps(summary)
    assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig" not in blob
    assert "def50200abcdef-long-refresh-token" not in blob
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.idpayload.idsig" not in blob
    # non-sensitive fields preserved
    assert summary["token_type"] == "Bearer"
    assert summary["expires_in"] == 86400
    assert summary["scope"] == "openid profile email id_token offline_access"
    # id_token_claims intentionally NOT dumped into the summary — too much
    # PII risk; consult oed auth status / decode id_token manually.
    assert "id_token_claims" not in summary


def test_summarize_token_response_handles_absent_id_token():
    """Server didn't return id_token — summary reflects absence, doesn't crash."""

    from oed_cli import auth as auth_mod

    result = {
        "access_token": "at-abcdefghij",
        "token_type": "Bearer",
        "expires_in": 120,
    }
    summary = auth_mod._summarize_token_response(result)
    assert summary["has_access_token"] is True
    assert summary["has_refresh_token"] is False
    assert summary["has_id_token"] is False
    # raw token string absent from summary
    assert "at-abcdefghij" not in json.dumps(summary)
    assert "id_token_claims" not in summary
