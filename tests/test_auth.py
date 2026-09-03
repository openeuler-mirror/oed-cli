"""Tests for oed_cli.auth AtomGit (``ag``) PAT storage.

The ag PAT is persisted through a *second* :class:`_SecureStore` instance
(namespaced by ``username="ag"``), reusing the same keyring + 0600 plaintext
fallback as the oneid credential. These tests exercise that surface in
plaintext mode (the autouse ``mock_secure_store`` fixture in ``conftest.py``
forces every :class:`_SecureStore` instance off the OS keychain).
"""

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


def test_roundtrip(isolated):
    """store_token → read_token returns the same token (plaintext backend)."""

    isolated.store_token("tok-abc", service="ag")
    assert isolated.read_token("ag") == "tok-abc"


def test_no_plaintext_secret_on_disk(isolated):
    """The 0600 fallback stores the token as JSON, not the raw secret string.

    ``access_token`` is a JSON value, so the raw token still appears in the
    file body — what we assert here is only that ``read_token``/``token_info``
    never echo the secret in their return shape, and that the stored entry is
    a JSON blob (not the bare token). Disk encryption when keyring is reachable
    is the OS keystore's job, exercised in test_secure_storage.py.
    """

    isolated.store_token("supersecretvalue", service="ag")
    raw = isolated.token_path("ag").read_text(encoding="utf-8")
    # Entry is valid JSON carrying the token under a field key.
    parsed = json.loads(raw)
    assert parsed["access_token"] == "supersecretvalue"
    assert parsed["service"] == "ag"
    # The store functions never return the secret through metadata.
    info = isolated.token_info("ag")
    assert "access_token" not in info
    assert info["encryption"] in {"plaintext", "keyring"}


def test_corrupt_file_reads_none(isolated):
    p = isolated.token_path("ag")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert isolated.read_token("ag") is None


def test_non_utf8_fallback_does_not_crash(isolated, monkeypatch, tmp_path):
    """A fallback auth.json with non-UTF-8 bytes (corrupt / truncated write)
    must not crash `oed login` via load_auth(). Regression: the old code only
    caught OSError + JSONDecodeError, not UnicodeDecodeError (a ValueError)."""

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    from oed_cli import auth

    # Simulate a corrupt legacy auth.json: byte 0x9d is invalid UTF-8 start.
    p = tmp_path / "auth.json"
    p.write_bytes(b'{"token": "\x9d\x9d"}')
    assert auth.load_auth() is None  # treated as unauthenticated, not a crash
    assert not p.exists()  # corrupt file is removed so a fresh save() can write


def test_missing_file_and_clear(isolated):
    assert isolated.read_token("ag") is None
    assert isolated.clear_token("ag") is False
    isolated.store_token("t", service="ag")
    assert isolated.clear_token("ag") is True
    assert isolated.read_token("ag") is None


def test_payload_shape(isolated):
    p = isolated.store_token("t", service="ag")
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["service"] == "ag"
    assert raw["access_token"] == "t"
    assert "created_at" in raw


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
    with pytest.raises((UserError, OSError)):
        isolated.store_token("t", service="ag")


def test_empty_token_rejected(isolated):
    with pytest.raises(UserError):
        isolated.store_token("", service="ag")


def test_token_info_reports_backend(isolated):
    isolated.store_token("t", service="ag")
    info = isolated.token_info("ag")
    assert info is not None
    assert info["encryption"] in {"plaintext", "keyring"}


def test_token_info_none_when_absent(isolated):
    assert isolated.token_info("ag") is None
