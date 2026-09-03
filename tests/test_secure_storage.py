"""Tests for the OS-native credential store (``_SecureStore``).

The autouse ``mock_secure_store`` fixture in ``conftest.py`` forces plaintext
mode for ordinary auth tests. Tests in this file explicitly override that
fixture (or unset ``_secure_store._available``) so the keyring probe path
runs against an injected fake ``keyring`` module.

Coverage:
- roundtrip: keyring save → keyring load → identical payload
- keyring takes precedence over a stale plaintext file
- plaintext file is removed after successful keyring save
- NoKeyringError → fallback to plaintext
- get/set_password exceptions → fallback
- legacy plaintext auth.json → next save migrates to keyring
- clear removes both keyring entry and plaintext file
- ``oed auth status`` reports ``backend: keyring`` / ``plaintext``
- ``keyring`` ImportError → plaintext fallback
- locked/broken keyring backend → plaintext fallback + stderr note
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from oed_cli import auth

# ---------------------------------------------------------------------------
# Fake keyring module injection
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """Drop-in for the ``keyring`` package — backs a single service+user.

    Records every call so tests can assert what was touched, and lets each
    test stage exceptions via ``raise_on`` to simulate backend failures.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, tuple[str, str], str | None]] = []
        self.raise_on: dict[str, BaseException] = {}

    def _record(self, op: str, args: tuple[str, str], value: str | None) -> None:
        self.calls.append((op, args, value))

    def get_password(self, service: str, username: str) -> str | None:
        self._record("get", (service, username), None)
        if "get" in self.raise_on:
            raise self.raise_on["get"]
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self._record("set", (service, username), value)
        if "set" in self.raise_on:
            raise self.raise_on["set"]
        self._store[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self._record("delete", (service, username), None)
        if "delete" in self.raise_on:
            raise self.raise_on["delete"]
        self._store.pop((service, username), None)


class _FakeErrors:
    """Stand-in for ``keyring.errors`` — exposes ``NoKeyringError`` etc."""

    class NoKeyringError(Exception):
        pass

    class KeyringError(Exception):
        pass


@pytest.fixture
def fake_keyring(monkeypatch):
    """Install a fake ``keyring`` package and force ``_secure_store`` to probe it."""

    fake = _FakeKeyring()
    fake_pkg = types.ModuleType("keyring")
    fake_pkg.get_password = fake.get_password
    fake_pkg.set_password = fake.set_password
    fake_pkg.delete_password = fake.delete_password
    fake_pkg.backend = fake  # ``keyring.backend`` is what some libs check
    fake_errors = types.ModuleType("keyring.errors")
    fake_errors.NoKeyringError = _FakeErrors.NoKeyringError
    fake_errors.KeyringError = _FakeErrors.KeyringError
    # ``keyring.errors`` must resolve both via the ``import keyring.errors``
    # statement and via attribute lookup on the ``keyring`` module — bind
    # the errors submodule as an attribute of ``fake_pkg`` so the except
    # clause ``except keyring.errors.NoKeyringError:`` works.
    fake_pkg.errors = fake_errors

    monkeypatch.setitem(sys.modules, "keyring", fake_pkg)
    monkeypatch.setitem(sys.modules, "keyring.errors", fake_errors)

    # Force the lazy probe to run again so it picks up our fake module.
    # conftest's autouse fixture replaces ``_resolve`` with a stub that
    # returns False (plaintext) for all _SecureStore instances — restore
    # the real probe so the fake keyring below is actually exercised.
    monkeypatch.setattr(
        auth._SecureStore, "_resolve", auth._SecureStore._original_resolve
    )
    monkeypatch.setattr(auth._secure_store, "_available", None)
    monkeypatch.setattr(auth._secure_store, "_keyring", None)
    monkeypatch.setattr(auth._secure_store, "_last_backend", "unprobed")

    return fake


@pytest.fixture
def tmp_cache_dir(monkeypatch, tmp_path):
    """Sandbox the auth file under ``tmp_path`` via ``OED_CACHE_DIR``."""

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Roundtrip + precedence
# ---------------------------------------------------------------------------


def test_keyring_save_load_roundtrip(fake_keyring, tmp_cache_dir):
    payload = {"msal_cache": "abc", "created_at": 12345.0, "cookie": "k=v"}
    backend = auth._secure_store.save(payload)
    assert backend == "keyring"
    assert not (tmp_cache_dir / "auth.json").exists()
    loaded = auth._secure_store.load()
    assert loaded == payload
    assert auth._secure_store.backend_label() == "keyring"


def test_keyring_takes_precedence_over_stale_plaintext(fake_keyring, tmp_cache_dir):
    # Pre-existing plaintext file with old data
    stale = {"msal_cache": "OLD", "created_at": 1.0}
    (tmp_cache_dir / "auth.json").write_text(json.dumps(stale), encoding="utf-8")
    # Save new data → keyring is authoritative, plaintext gets deleted
    fresh = {"msal_cache": "NEW", "created_at": 2.0}
    auth._secure_store.save(fresh)
    assert not (tmp_cache_dir / "auth.json").exists()
    assert auth._secure_store.load() == fresh


def test_plaintext_file_deleted_after_keyring_save(fake_keyring, tmp_cache_dir):
    # Force a plaintext write first
    fake_keyring.raise_on["set"] = _FakeErrors.NoKeyringError("simulated")
    auth._secure_store._available = None
    auth._secure_store.save({"msal_cache": "P", "created_at": 1.0})
    assert (tmp_cache_dir / "auth.json").exists()
    # Now let keyring succeed — next save should drop the plaintext file
    del fake_keyring.raise_on["set"]
    auth._secure_store._available = None
    auth._secure_store.save({"msal_cache": "K", "created_at": 2.0})
    assert not (tmp_cache_dir / "auth.json").exists()
    assert fake_keyring._store[("oed-cli", "default")] == json.dumps(
        {"msal_cache": "K", "created_at": 2.0}, ensure_ascii=False
    )


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_no_keyring_backend_falls_back_to_plaintext(fake_keyring, tmp_cache_dir):
    fake_keyring.raise_on["get"] = _FakeErrors.NoKeyringError("no backend")
    fake_keyring.raise_on["set"] = _FakeErrors.NoKeyringError("no backend")
    auth._secure_store._available = None
    backend = auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    assert backend == "plaintext"
    assert (tmp_cache_dir / "auth.json").exists()
    assert auth._secure_store.load() == {"msal_cache": "X", "created_at": 1.0}


def test_keyring_get_failure_falls_back_to_plaintext(fake_keyring, tmp_cache_dir):
    # Seed a plaintext file first
    (tmp_cache_dir / "auth.json").write_text(
        json.dumps({"msal_cache": "PLAIN", "created_at": 1.0}), encoding="utf-8"
    )
    # Make get_password blow up
    fake_keyring.raise_on["get"] = _FakeErrors.KeyringError("locked db")
    auth._secure_store._available = None
    loaded = auth._secure_store.load()
    assert loaded == {"msal_cache": "PLAIN", "created_at": 1.0}


def test_keyring_set_failure_falls_back_to_plaintext(fake_keyring, tmp_cache_dir):
    fake_keyring.raise_on["set"] = _FakeErrors.KeyringError("disk full")
    auth._secure_store._available = None
    backend = auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    assert backend == "plaintext"
    assert (tmp_cache_dir / "auth.json").exists()


def test_legacy_plaintext_file_migrates_to_keyring(fake_keyring, tmp_cache_dir):
    # Existing plaintext auth.json from a previous install
    legacy = {"msal_cache": "LEGACY", "created_at": 1700000000.0, "cookie": "old=1"}
    (tmp_cache_dir / "auth.json").write_text(json.dumps(legacy), encoding="utf-8")

    # First read goes to plaintext (keyring has nothing); the cached label
    # flips to ``plaintext`` so status reports the truth.
    loaded = auth._secure_store.load()
    assert loaded == legacy
    assert auth._secure_store.backend_label() == "plaintext"

    # Subsequent save: keyring wins, plaintext is gone
    merged = {**legacy, "allowlist": ["cve"]}
    backend = auth._secure_store.save(merged)
    assert backend == "keyring"
    assert not (tmp_cache_dir / "auth.json").exists()


def test_clear_removes_both_keyring_and_plaintext(fake_keyring, tmp_cache_dir):
    auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    assert fake_keyring._store  # populated
    auth._secure_store.clear()
    assert not fake_keyring._store  # keyring entry deleted
    assert not (tmp_cache_dir / "auth.json").exists()  # plaintext also gone
    assert auth._secure_store.load() is None


def test_locked_keyring_falls_back_silently(fake_keyring, tmp_cache_dir, capsys):
    # A locked/broken keyring backend must fall through to plaintext 0600
    # without printing anything to stderr — the user can discover the
    # active backend any time via `oed auth status`.
    fake_keyring.raise_on["set"] = _FakeErrors.KeyringError("locked")
    auth._secure_store._available = None
    backend = auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    assert backend == "plaintext"
    err = capsys.readouterr().err
    assert err == ""


def test_locked_keyring_only_probed_once(fake_keyring, tmp_cache_dir, capsys):
    """Regression: probe-only-success-then-real-op-failure used to re-fire
    the keyring backend on every save/load because ``_available`` was
    never flipped to False after the failed operation. Even after removing
    the stderr warn, we still want the broken backend cached so we don't
    waste time retrying on every call.
    """
    fake_keyring.raise_on["set"] = _FakeErrors.KeyringError("locked")
    fake_keyring.raise_on["get"] = _FakeErrors.KeyringError("locked")
    auth._secure_store._available = None

    # Three operations in the same process; the broken backend should be
    # remembered (no stderr, and the in-process probe state is False).
    auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    auth._secure_store.save({"msal_cache": "Y", "created_at": 2.0})
    auth._secure_store.load()
    assert auth._secure_store._available is False
    assert capsys.readouterr().err == ""


def test_import_error_falls_back_to_plaintext(monkeypatch, tmp_cache_dir):
    # Block the ``import keyring`` inside ``_resolve`` by stuffing a sentinel
    # that re-raises ImportError on attribute access.
    class _Boom:
        def __getattr__(self, name):  # noqa: D401 — test stub
            raise ImportError("simulated missing keyring")

    # Drop any cached import and substitute a dummy top-level module
    monkeypatch.delitem(sys.modules, "keyring", raising=False)
    fake_pkg = types.ModuleType("keyring")
    fake_pkg.errors = _Boom()
    monkeypatch.setitem(sys.modules, "keyring", fake_pkg)

    auth._secure_store._available = None
    backend = auth._secure_store.save({"msal_cache": "X", "created_at": 1.0})
    assert backend == "plaintext"
    assert (tmp_cache_dir / "auth.json").exists()


# ---------------------------------------------------------------------------
# Public API plumbing (save_auth / load_auth / clear_auth / auth_status)
# ---------------------------------------------------------------------------


def test_save_auth_returns_path_and_uses_keyring(fake_keyring, tmp_cache_dir, monkeypatch):
    # save_auth runs mkdir on parent; tmp_cache_dir handles that.
    monkeypatch.setattr(auth, "_synth_msal_cache_json", lambda token, rt: f"MOCK::{token}::{rt}")
    path = auth.save_auth("MY_TOKEN", cookie="k=v", refresh_token="RT")
    expected = auth.auth_token_path()
    assert path == expected
    loaded = auth._secure_store.load()
    assert loaded["msal_cache"] == "MOCK::MY_TOKEN::RT"
    assert loaded["cookie"] == "k=v"
    assert "created_at" in loaded


def test_load_auth_prefers_keyring(fake_keyring, tmp_cache_dir):
    payload = {"msal_cache": "K", "created_at": 2.0}
    fake_keyring.set_password("oed-cli", "default", json.dumps(payload))
    (tmp_cache_dir / "auth.json").write_text(
        json.dumps({"msal_cache": "PLAINTEXT", "created_at": 1.0}), encoding="utf-8"
    )
    loaded = auth.load_auth()
    assert loaded["msal_cache"] == "K"


def test_clear_auth_returns_true_when_data_existed(fake_keyring, tmp_cache_dir):
    auth.save_auth("T")
    assert auth.clear_auth() is True
    assert auth.clear_auth() is False  # idempotent


def test_save_auth_uses_keyring_backend_when_available(fake_keyring, tmp_cache_dir):
    """With a reachable keyring, save_auth persists via the OS keystore and
    reports it as the active backend (``backend`` was removed from
    ``oed auth status`` output, so we assert at the store layer)."""

    auth.save_auth("T")
    assert auth._secure_store.backend_label() == "keyring"
    # And the stored payload is the real keyring entry, not a plaintext file.
    assert not auth.auth_token_path().is_file()


def test_save_auth_falls_back_to_plaintext_backend(tmp_cache_dir):
    """When no keystore is reachable, save_auth falls back to the 0600
    plaintext file and reports ``plaintext`` as the active backend."""

    # Conftest fixture already pins _available=False → plaintext path.
    auth.save_auth("T")
    assert auth._secure_store.backend_label() == "plaintext"
    assert auth.auth_token_path().is_file()


# ---------------------------------------------------------------------------
# _first_token_secret — pick the freshest entry, not the first by dict order
# ---------------------------------------------------------------------------


def _msal_cache(entries):
    """Build an MSAL cache JSON with the given AccessToken entries."""
    return json.dumps({"AccessToken": {f"k{i}": e for i, e in enumerate(entries)}})


def test_first_token_secret_picks_newest_by_expires_on():
    """Among multiple AccessToken entries (e.g. test-env stale + prod-env fresh),
    the one with the largest ``expires_on`` wins — not the dict-first one."""
    cache = _msal_cache([
        {"secret": "stale-old-token", "expires_on": 1000, "cached_at": 900},
        {"secret": "fresh-new-token", "expires_on": 9000, "cached_at": 8900},
    ])
    assert auth._extract_access_token(cache) == "fresh-new-token"


def test_first_token_secret_falls_back_to_cached_at():
    """When ``expires_on`` is absent, fall back to ``cached_at`` for recency."""
    cache = _msal_cache([
        {"secret": "older", "cached_at": 100},
        {"secret": "newer", "cached_at": 500},
    ])
    assert auth._extract_access_token(cache) == "newer"


def test_first_token_secret_legacy_first_match_without_timestamps():
    """Entries with no timestamp (manually synthesized / legacy cache) keep the
    original first-match behavior — the regression must not break manual tokens."""
    cache = _msal_cache([
        {"secret": "only-token"},
    ])
    assert auth._extract_access_token(cache) == "only-token"


def test_first_token_secret_legacy_field_compat():
    """Old schema wrote the token under ``access_token`` instead of ``secret``;
    that fallback still works and recency still applies."""
    cache = json.dumps({
        "AccessToken": {
            "old": {"access_token": "legacy-old", "expires_on": 100},
            "new": {"access_token": "legacy-new", "expires_on": 9999},
        }
    })
    assert auth._extract_access_token(cache) == "legacy-new"
