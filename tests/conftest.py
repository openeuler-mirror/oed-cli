"""Shared pytest fixtures.

The auth storage layer writes to OS-native credential stores via ``keyring``
(macOS Keychain / Windows DPAPI / Linux SecretService). These tests must
never touch a real keyring — both for sandbox safety and to keep runs
deterministic across developer machines.

The autouse fixture below forces ``_secure_store`` into plaintext mode so
``load_auth`` / ``save_auth`` etc. read/write a 0600 ``auth.json`` under
``tmp_path`` (configured by each test's own ``OED_CACHE_DIR`` setup).
Tests that need to exercise keyring specifically (``tests/test_secure_storage.py``)
unset / override this fixture locally.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def mock_secure_store(monkeypatch, tmp_path):
    """Force plaintext mode + sandbox auth.json under tmp_path for all auth tests.

    Without these two redirects the suite would touch the developer's real
    ``~/.cache/oed-cli/auth.json`` (a real oneid login with an allow-list that
    gates dispatch tests), and probe the real OS keychain — both must never
    happen from CI. The ``tmp_path`` redirect is harmless for tests that don't
    read auth: ``auth_token_path()`` returns a path that just doesn't exist.
    """

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))

    from oed_cli import auth

    # Short-circuit the lazy probe: pretend keyring was already resolved
    # as unavailable. ``_resolve`` checks ``self._available is not None``
    # first, so this skips the import + probe roundtrip entirely.
    monkeypatch.setattr(auth._secure_store, "_available", False)
    # ``_last_backend`` flips back to ``unprobed`` on every clear; reset it
    # to ``plaintext`` so status-output assertions don't see stale labels
    # when a test runs save+status+clear in sequence.
    monkeypatch.setattr(auth._secure_store, "_last_backend", "plaintext")
    # Force the per-service AtomGit stores (created lazily by
    # ``auth._ag_store``) into plaintext mode too, so ``oed ag login`` tests
    # never touch the real OS keychain. Done by neutering the factory's
    # probe at the class level — covers store instances not yet created.
    # ``test_secure_storage.py`` restores the real ``_resolve`` via its
    # ``fake_keyring`` fixture when it needs to exercise the keyring path.
    if not hasattr(auth._SecureStore, "_original_resolve"):
        auth._SecureStore._original_resolve = auth._SecureStore._resolve
    monkeypatch.setattr(auth._SecureStore, "_resolve", lambda self: False)
    yield
