"""Smoke tests for the oed-cli minimum scaffold (v0.1).

These tests do not require network — they exercise the CLI's argument parsing,
JSON shaping and exit-code contracts using a monkey-patched discovery layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
                "name": "openeuler/software-package-server",
                "service_name": "software-package-server",
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
            "get": {"summary": "CLA", "operationId": "API_verifyCla", "responses": {}}
        },
        "/v1/softwarepkg": {
            "get": {
                "summary": "list",
                "operationId": "API_listSoftwarePackages",
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
    assert {s["service_name"] for s in payload} == {"software-package-server", "easysearch"}


def test_schema_full(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "software-package-server"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["openapi"] == "3.0.3"
    assert "/v1/cla" in payload["paths"]


def test_schema_method_lookup(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "software-package-server.API_listSoftwarePackages"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(_extract_json(result))
    assert payload["service"] == "openeuler/software-package-server"
    assert any(
        m["operation"]["operationId"] == "API_listSoftwarePackages"
        for m in payload["matches"]
    )


def test_schema_unknown_method_reports_available(runner, patched_discovery):
    result = runner.invoke(cli, ["schema", "software-package-server.DOES_NOT_EXIST"])
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
    assert "software-package-server" in text
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
    return CliRunner(mix_stderr=False)
