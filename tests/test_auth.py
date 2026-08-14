"""Tests for oed_cli.auth — encrypted local token storage (DPAPI / base64)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oed_cli.errors import UserError  # noqa: E402


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Point the token store at a throwaway dir and return the auth module."""

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    from oed_cli import auth

    return auth


def test_dpapi_roundtrip_on_windows(isolated):
    if not isolated._is_windows():
        pytest.skip("DPAPI is Windows-only")

    isolated.store_token("tok-abc", service="ag")
    assert isolated.read_token("ag") == "tok-abc"


def test_base64_fallback_roundtrip(isolated, monkeypatch):
    monkeypatch.setattr(isolated, "_is_windows", lambda: False)

    path = isolated.store_token("tok-secret", service="ag")
    assert isolated.read_token("ag") == "tok-secret"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["encryption"] == "base64"


def test_no_plaintext_secret_on_disk(isolated):
    isolated.store_token("supersecretvalue", service="ag")
    text = isolated.token_path("ag").read_text(encoding="utf-8")
    assert "supersecretvalue" not in text


def test_corrupt_file_reads_none(isolated):
    p = isolated.token_path("ag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert isolated.read_token("ag") is None


def test_missing_file_and_clear(isolated):
    assert isolated.read_token("ag") is None
    assert isolated.clear_token("ag") is False
    isolated.store_token("t", service="ag")
    assert isolated.clear_token("ag") is True
    assert isolated.read_token("ag") is None


def test_schema_is_versioned_and_scoped(isolated):
    p = isolated.store_token("t", service="ag")
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["service"] == "ag"
    assert raw["encryption"] in {"dpapi", "base64"}


def test_storage_respects_cache_dir(isolated, tmp_path):
    p = isolated.store_token("t", service="ag")
    assert p == tmp_path / "tokens" / "ag.json"
    assert p.is_file()


def test_store_is_service_keyed(isolated):
    isolated.store_token("ag-tok", service="ag")
    isolated.store_token("other-tok", service="other")
    assert isolated.read_token("ag") == "ag-tok"
    assert isolated.read_token("other") == "other-tok"
    assert isolated.read_token("notstored") is None


def test_store_io_failure_raises(isolated):
    block = isolated.token_path("ag")
    block.parent.mkdir(parents=True, exist_ok=True)
    block.mkdir()  # directory where the file should go → write fails
    with pytest.raises(UserError):
        isolated.store_token("t", service="ag")


def test_dpapi_marker_unreadable_off_windows(isolated, monkeypatch):
    p = isolated.token_path("ag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"version": 1, "service": "ag", "encryption": "dpapi", "secret": "x"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(isolated, "_is_windows", lambda: False)
    assert isolated.read_token("ag") is None


def test_version_mismatch_returns_none(isolated):
    p = isolated.token_path("ag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"version": 99, "service": "ag", "encryption": "base64", "secret": "eA=="}
        ),
        encoding="utf-8",
    )
    assert isolated.read_token("ag") is None


def test_unknown_encryption_marker_returns_none(isolated, monkeypatch):
    monkeypatch.setattr(isolated, "_is_windows", lambda: False)
    p = isolated.token_path("ag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"version": 1, "service": "ag", "encryption": "unknown", "secret": "c2VjcmV0"}
        ),
        encoding="utf-8",
    )
    assert isolated.read_token("ag") is None
