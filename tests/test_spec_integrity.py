"""Tests for OpenAPI spec integrity verification + If-None-Match/304 negotiation.

Covers the CLI side of issue #15 F-03 + checksum hardening:
:func:`oed_cli.http.fetch_json_with_integrity`,
:func:`oed_cli.http._verify_content_integrity` / :func:`parse_repr_digest`, and
the 304 revalidation path + local-cache integrity guards in
:func:`oed_cli.dynamic.fetch_service_spec`.

Integrity uses the RFC 9530 ``Repr-Digest`` header
(``sha-256=<base64(sha256(body))>``); ``ETag`` (hex) is retained purely for
``If-None-Match`` / 304 negotiation.

All tests run offline by monkeypatching ``oed_cli.http.get_request``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from oed_cli import dynamic as dyn  # noqa: E402
from oed_cli import http as http_mod  # noqa: E402
from oed_cli.discovery import ServiceMeta  # noqa: E402
from oed_cli.errors import UpstreamError  # noqa: E402

# --------------------------------------------------------------------------- #
# Fake response builder
# --------------------------------------------------------------------------- #


def _resp(status: int, body: bytes, headers: dict | None = None):
    """Build a minimal stand-in for ``httpx.Response``."""

    body = body or b""

    class _R:
        def __init__(self) -> None:
            self.status_code = status
            self.content = body
            self.text = body.decode("utf-8", "replace")
            self.headers = headers or {}
            self.url = "https://example.test/spec"
            self.request = type("Req", (), {"method": "GET"})()

        def json(self):
            return json.loads(self.text)

    return _R()


def _spec_body() -> bytes:
    """Body bytes the discovery service would emit (same serialization)."""
    return json.dumps(
        {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {}},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def _digest_b64(body: bytes) -> str:
    """The Repr-Digest base64 value the server would compute for ``body``."""
    return base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")


def _digest_header(body: bytes) -> str:
    return f"sha-256={_digest_b64(body)}"


@pytest.fixture(autouse=True)
def _no_waf(monkeypatch):
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)


# --------------------------------------------------------------------------- #
# parse_repr_digest
# --------------------------------------------------------------------------- #


def test_parse_repr_digest_single():
    assert http_mod.parse_repr_digest("sha-256=YWFhYQ==") == "YWFhYQ=="


def test_parse_repr_digest_multiple_algos_picks_sha256():
    assert http_mod.parse_repr_digest("md5=xx==, sha-256=YmJiYg==, sha-512=zz==") == "YmJiYg=="


def test_parse_repr_digest_case_insensitive():
    assert http_mod.parse_repr_digest("SHA-256=YWFhYQ==") == "YWFhYQ=="


def test_parse_repr_digest_none_when_absent():
    assert http_mod.parse_repr_digest("md5=xx==") is None
    assert http_mod.parse_repr_digest("") is None
    assert http_mod.parse_repr_digest(None) is None


# --------------------------------------------------------------------------- #
# _verify_content_integrity
# --------------------------------------------------------------------------- #


def test_integrity_match_returns_digest():
    body = _spec_body()
    digest = _digest_b64(body)
    # Returns the base64 digest value when it matches.
    assert http_mod._verify_content_integrity(body, {"Repr-Digest": _digest_header(body)}) == digest


def test_integrity_mismatch_raises():
    body = _spec_body()
    with pytest.raises(UpstreamError) as exc_info:
        http_mod._verify_content_integrity(body, {"Repr-Digest": "sha-256=AAAAAAAA"})
    assert exc_info.value.kind == "integrity_mismatch"


def test_integrity_missing_header_skips():
    # No Repr-Digest header → skip (graceful), return None. Must NOT raise.
    assert http_mod._verify_content_integrity(_spec_body(), {}) is None
    assert http_mod._verify_content_integrity(b"{}", {"Content-Type": "application/json"}) is None


def test_integrity_malformed_header_skips():
    # Header present but no sha-256 algo → skip, not raise.
    assert http_mod._verify_content_integrity(_spec_body(), {"Repr-Digest": "md5=xx=="}) is None


# --------------------------------------------------------------------------- #
# fetch_json_with_integrity  (returns data, etag, repr_digest)
# --------------------------------------------------------------------------- #


def _install_get_request(monkeypatch, responder):
    captured = {}

    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        return responder(method, url, dict(headers or {}))

    monkeypatch.setattr(http_mod, "get_request", _fake)
    return captured


def test_fetch_integrity_304_returns_none_etag_digest(monkeypatch):
    body = _spec_body()
    digest_b64 = _digest_b64(body)
    etag_hex = hashlib.sha256(body).hexdigest()
    captured = _install_get_request(
        monkeypatch,
        lambda m, u, h: _resp(304, b"", {"ETag": etag_hex, "Repr-Digest": _digest_header(body)}),
    )
    data, etag, repr_digest = http_mod.fetch_json_with_integrity(
        "https://x/spec", if_none_match=etag_hex
    )
    assert data is None
    assert etag == etag_hex
    assert repr_digest == digest_b64
    # The cached ETag was forwarded as If-None-Match.
    assert captured["headers"].get("If-None-Match") == etag_hex


def test_fetch_integrity_200_match(monkeypatch):
    body = _spec_body()
    digest_b64 = _digest_b64(body)
    etag_hex = hashlib.sha256(body).hexdigest()
    _install_get_request(
        monkeypatch,
        lambda m, u, h: _resp(200, body, {"ETag": etag_hex, "Repr-Digest": _digest_header(body)}),
    )
    data, etag, repr_digest = http_mod.fetch_json_with_integrity("https://x/spec")
    assert data == json.loads(body)
    assert etag == etag_hex
    assert repr_digest == digest_b64


def test_fetch_integrity_200_mismatch_raises(monkeypatch):
    body = _spec_body()
    _install_get_request(
        monkeypatch,
        lambda m, u, h: _resp(200, body, {"ETag": "deadbeef", "Repr-Digest": "sha-256=AAAAAAAA"}),
    )
    with pytest.raises(UpstreamError) as exc_info:
        http_mod.fetch_json_with_integrity("https://x/spec")
    assert exc_info.value.kind == "integrity_mismatch"


def test_fetch_integrity_200_no_header_skips(monkeypatch):
    body = _spec_body()
    _install_get_request(
        monkeypatch,
        lambda m, u, h: _resp(200, body, {"Content-Type": "application/json"}),
    )
    data, etag, repr_digest = http_mod.fetch_json_with_integrity("https://x/spec")
    assert data == json.loads(body)
    assert etag is None
    assert repr_digest is None


def test_fetch_integrity_no_etag_omits_if_none_match(monkeypatch):
    body = _spec_body()
    captured = _install_get_request(
        monkeypatch,
        lambda m, u, h: _resp(304, b"", {"ETag": "abc", "Repr-Digest": _digest_header(body)}),
    )
    http_mod.fetch_json_with_integrity("https://x/spec")  # no if_none_match
    assert "If-None-Match" not in captured["headers"]


# --------------------------------------------------------------------------- #
# dynamic.fetch_service_spec — 304 revalidation + local-cache integrity guards
# --------------------------------------------------------------------------- #


def _service() -> ServiceMeta:
    return ServiceMeta.from_raw({
        "name": "openeuler/cve",
        "service_name": "cve",
        "community": "openeuler",
        "title": "t",
        "version": "1",
        "description": "",
        "base_url": "https://apig.osinfra.cn",
    })


def _spec_dict() -> dict:
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {}}


def _install_resp(monkeypatch, status, body, etag_hex, digest_header):
    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        return _resp(status, body, {"ETag": etag_hex, "Repr-Digest": digest_header})
    monkeypatch.setattr(http_mod, "get_request", _fake)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)


def test_fetch_spec_304_reuses_valid_cache(monkeypatch, tmp_path):
    """Expired cache + matching ETag → 304 → reuse spec (re-stamped), no re-download."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)

    spec = _spec_dict()
    body = json.dumps(spec, ensure_ascii=False, indent=2).encode("utf-8")
    etag_hex = hashlib.sha256(body).hexdigest()
    digest_header = _digest_header(body)

    # Seed a stale, integrity-valid cache entry.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time() - dyn.SPEC_CACHE_TTL_SECONDS - 1,
        "spec": spec,
        "etag": etag_hex,
        "repr_digest": _digest_b64(body),
    }), encoding="utf-8")

    _install_resp(monkeypatch, 304, b"", etag_hex, digest_header)

    got = dyn.fetch_service_spec(svc, force_refresh=True)
    assert got == spec  # reused the revalidated spec
    # Cache re-stamped fresh + carries both fields.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["spec"] == spec
    assert raw["etag"] == etag_hex
    assert "repr_digest" not in raw  # digest now lives in the separate ledger
    assert dyn._lookup_digest(path) == _digest_b64(body)
    assert dyn._read_spec_cache(path) is not None


def test_fetch_spec_304_tampered_local_cache_refetches(monkeypatch, tmp_path):
    """Guard 2: 304 hit but the on-disk spec was tampered (digest mismatch) →
    do NOT reuse it; fall through to a full re-download."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)

    good_spec = _spec_dict()
    good_body = json.dumps(good_spec, ensure_ascii=False, indent=2).encode("utf-8")
    etag_hex = hashlib.sha256(good_body).hexdigest()
    digest_header = _digest_header(good_body)

    # Stale cache whose stored repr_digest does NOT match the on-disk spec
    # (the spec was edited on disk without updating the digest).
    import copy
    tampered_spec = copy.deepcopy(good_spec)
    tampered_spec["paths"]["/FAKE"] = {"get": {"summary": "evil"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time() - dyn.SPEC_CACHE_TTL_SECONDS - 1,
        "spec": tampered_spec,
        "etag": etag_hex,
        "repr_digest": _digest_b64(good_body),  # matches GOOD body, not tampered
    }), encoding="utf-8")

    calls = {"n": 0}

    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        calls["n"] += 1
        # First call (If-None-Match) → 304. Second (fallback) → 200 with good body.
        if calls["n"] == 1:
            return _resp(304, b"", {"ETag": etag_hex, "Repr-Digest": digest_header})
        return _resp(200, good_body, {"ETag": etag_hex, "Repr-Digest": digest_header})

    monkeypatch.setattr(http_mod, "get_request", _fake)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)

    got = dyn.fetch_service_spec(svc, force_refresh=True)
    assert got == good_spec  # NOT the tampered spec
    assert "/FAKE" not in got.get("paths", {})
    assert calls["n"] == 2  # 304 then a full 200 refetch
    # Cache overwritten with the good spec.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "/FAKE" not in raw["spec"].get("paths", {})


def test_fetch_spec_ttl_hit_tampered_cache_refetches(monkeypatch, tmp_path):
    """Guard 1: a fresh (TTL-hit) cache whose spec was tampered → not returned;
    instead refetched from the server."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)

    good_spec = _spec_dict()
    good_body = json.dumps(good_spec, ensure_ascii=False, indent=2).encode("utf-8")
    etag_hex = hashlib.sha256(good_body).hexdigest()
    digest_header = _digest_header(good_body)

    # Fresh cache (within TTL) but the spec on disk was tampered.
    import copy
    tampered_spec = copy.deepcopy(good_spec)
    tampered_spec["paths"]["/FAKE"] = {"get": {"summary": "evil"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time(),  # fresh
        "spec": tampered_spec,
        "etag": etag_hex,
        "repr_digest": _digest_b64(good_body),  # matches GOOD, not tampered
    }), encoding="utf-8")

    calls = {"n": 0}

    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        calls["n"] += 1
        return _resp(200, good_body, {"ETag": etag_hex, "Repr-Digest": digest_header})

    monkeypatch.setattr(http_mod, "get_request", _fake)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)

    got = dyn.fetch_service_spec(svc)  # force_refresh=False: should hit cache...
    # ...but the tampered cache is rejected, so it refetched the good spec.
    assert got == good_spec
    assert "/FAKE" not in got.get("paths", {})
    assert calls["n"] == 1


def test_fetch_spec_caches_etag_and_digest_and_resends(monkeypatch, tmp_path):
    """A 200 with integrity headers stores both etag + repr_digest; the next
    (forced) fetch re-sends the ETag as If-None-Match."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)
    assert not path.exists()

    body = _spec_body()
    etag_hex = hashlib.sha256(body).hexdigest()
    digest_header = _digest_header(body)
    call = {"n": 0, "headers": None}

    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        call["n"] += 1
        call["headers"] = dict(headers or {})
        return _resp(200, body, {"ETag": etag_hex, "Repr-Digest": digest_header})

    monkeypatch.setattr(http_mod, "get_request", _fake)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)

    first = dyn.fetch_service_spec(svc, force_refresh=True)
    assert first == json.loads(body)
    assert call["n"] == 1
    assert "If-None-Match" not in (call["headers"] or {})  # nothing cached yet

    # Both fields persisted: etag in the spec file, digest in the ledger.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["etag"] == etag_hex
    assert "repr_digest" not in raw  # digest no longer stored in the spec file
    assert dyn._lookup_digest(path) == _digest_b64(body)

    # Force a refetch: the cached ETag must now be sent conditionally.
    dyn.fetch_service_spec(svc, force_refresh=True)
    assert call["n"] == 2
    assert call["headers"].get("If-None-Match") == etag_hex


def test_fetch_spec_mismatch_does_not_poison_cache(monkeypatch, tmp_path):
    """A tampered 200 body raises and leaves the prior good cache untouched."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)
    good_spec = _spec_dict()
    good_body = json.dumps(good_spec, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time(),  # fresh → returned without network
        "spec": good_spec,
        "etag": "GOOD",
        "repr_digest": _digest_b64(good_body),
    }), encoding="utf-8")

    # Force a refetch that returns a body whose Repr-Digest does not match.
    spec_body = _spec_body()
    wrong_digest_header = "sha-256=AAAAAAAA"

    def _fake(method, url, *, params=None, req_body=None, headers=None,
              timeout=30.0, user_agent=None, token=None, cookie=None,
              service_name=""):
        return _resp(200, spec_body, {"ETag": "BAD", "Repr-Digest": wrong_digest_header})

    monkeypatch.setattr(http_mod, "get_request", _fake)
    monkeypatch.setattr(http_mod, "_is_waf_block", lambda text: False, raising=False)

    with pytest.raises(UpstreamError) as exc_info:
        dyn.fetch_service_spec(svc, force_refresh=True)
    assert exc_info.value.kind == "integrity_mismatch"
    # Good cache still intact.
    assert json.loads(path.read_text(encoding="utf-8"))["spec"] == good_spec


def test_repr_digest_lives_in_separate_0600_ledger(monkeypatch, tmp_path):
    """Plan A: a fetched spec's repr_digest is NOT in the spec file (so editing
    the spec file can't re-stamp it) but in a separate 0600 ledger; both the
    spec file and the ledger are owner-only."""
    import os

    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)
    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)

    body = _spec_body()
    etag_hex = hashlib.sha256(body).hexdigest()
    _install_resp(monkeypatch, 200, body, etag_hex, _digest_header(body))

    dyn.fetch_service_spec(svc, force_refresh=True)

    # Spec file carries spec + etag, but NOT repr_digest.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "repr_digest" not in raw
    # The digest lives in the separate ledger and matches the body.
    assert dyn._lookup_digest(path) == _digest_b64(body)
    # Both files are owner-only (0600) — a non-owner can't forge either.
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert (os.stat(dyn._integrity_index_path()).st_mode & 0o777) == 0o600


def test_ledger_separates_digest_from_spec_tamper(monkeypatch, tmp_path):
    """Plan A core guarantee: with the digest in the ledger (new format),
    editing ONLY the spec file — leaving the ledger untouched — is rejected,
    even though the spec file no longer carries any digest to mismatch against.
    The attacker must write the protected ledger too."""
    import copy

    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)
    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)

    good_spec = _spec_dict()
    good_body = json.dumps(good_spec, ensure_ascii=False, indent=2).encode("utf-8")

    # Seed a fresh cache the new way: spec file (no inline digest) + ledger.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time(),
        "spec": good_spec,
        "etag": "etag1",
    }), encoding="utf-8")
    dyn._store_digest(path, _digest_b64(good_body))

    # Attacker edits ONLY the spec file (adds a fake path), leaving the ledger.
    tampered = copy.deepcopy(good_spec)
    tampered["paths"]["/FAKE"] = {"get": {"summary": "evil"}}
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time(),
        "spec": tampered,
        "etag": "etag1",
    }), encoding="utf-8")

    # TTL-fresh read must reject: the on-disk spec no longer hashes to the
    # ledger digest, and there is no inline digest to "fix" by editing one file.
    assert dyn._read_spec_cache(path) is None
    # 304 reuse must also reject the tampered spec → caller would refetch.
    assert dyn._read_cached_spec_raw(path) is None


# --------------------------------------------------------------------------- #
# Integrity ledger robustness + fetch_spec delegation (coverage)
# --------------------------------------------------------------------------- #


def test_read_integrity_index_handles_malformed_files(monkeypatch, tmp_path):
    """The ledger read helpers must degrade to empty/None on a corrupt or
    mistyped file — never raise. Covers the JSONDecodeError and non-dict
    branches of _read_integrity_index (and transitively _lookup_digest)."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)
    idx_path = dyn._integrity_index_path()
    idx_path.parent.mkdir(parents=True, exist_ok=True)

    # Corrupt JSON → empty index, no digest found.
    idx_path.write_text("{not valid json", encoding="utf-8")
    assert dyn._read_integrity_index() == {}
    assert dyn._lookup_digest(tmp_path / "x" / "y.json") is None

    # Valid JSON but not a dict → empty index.
    idx_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert dyn._read_integrity_index() == {}

    # Dict with non-string entries → those filtered out, strings kept.
    idx_path.write_text(json.dumps({"cve/service": "abc", "bad": 5, "n": None}),
                        encoding="utf-8")
    assert dyn._read_integrity_index() == {"cve/service": "abc"}


def test_write_integrity_index_is_best_effort_on_oserror(monkeypatch, tmp_path):
    """_write_integrity_index must swallow OSError (best-effort), not raise.

    Triggered by making _atomic_write_json raise — e.g. when the ledger parent
    cannot be created (read-only filesystem). Ensures a cache-write failure on
    the ledger never crashes a normal CLI run.
    """
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)

    def _boom(path, data, *, mode=None):
        raise OSError("simulated read-only fs")

    monkeypatch.setattr(dyn, "_atomic_write_json", _boom)
    # Must not raise.
    dyn._write_integrity_index({"cve/service": "abc"})


def test_cached_repr_digest_prefers_ledger_then_legacy(monkeypatch, tmp_path):
    """_cached_repr_digest reads from the ledger first (new format); when the
    ledger has no entry it falls back to the legacy inline field so existing
    pre-hardening caches keep working during migration."""
    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)
    svc = _service()
    path = dyn._spec_cache_path(svc.community, svc.service_name)
    path.parent.mkdir(parents=True, exist_ok=True)

    # No file at all → None.
    assert dyn._cached_repr_digest(path) is None

    # Ledger-only (new format): spec file without inline digest, ledger has it.
    path.write_text(json.dumps({"__oed_fetched_at": 0, "spec": {}}), encoding="utf-8")
    dyn._store_digest(path, "LEDGERDIGEST")
    assert dyn._cached_repr_digest(path) == "LEDGERDIGEST"  # ledger branch (598-600)

    # Legacy-only (old format): no ledger entry, inline repr_digest present.
    ledger = dyn._read_integrity_index()
    ledger.pop(dyn._cache_key_for_path(path), None)
    dyn._write_integrity_index(ledger)
    path.write_text(json.dumps({"repr_digest": "LEGACYDIGEST"}), encoding="utf-8")
    assert dyn._cached_repr_digest(path) == "LEGACYDIGEST"  # legacy branch (607-608)

    # Corrupt spec file with no ledger entry → None, no raise.
    path.write_text("{bad json", encoding="utf-8")
    assert dyn._cached_repr_digest(path) is None


def test_fetch_spec_in_discovery_delegates_to_cached_fetcher(monkeypatch, tmp_path):
    """discovery.fetch_spec must delegate to dynamic.fetch_service_spec (which
    adds caching + integrity), rather than fetching fresh and discarding the
    ETag. Covers the delegation body (discovery.py:237,239)."""
    import oed_cli.discovery as disc

    monkeypatch.setattr(dyn, "_cache_dir", lambda: tmp_path, raising=False)
    svc = _service()
    body = _spec_body()
    etag_hex = hashlib.sha256(body).hexdigest()
    _install_resp(monkeypatch, 200, body, etag_hex, _digest_header(body))

    got = disc.fetch_spec(svc)
    assert got == json.loads(body)
    # Proves delegation wired the cache (not a fresh-each-call path): a second
    # call serves from the TTL cache without hitting the network.
    call = {"n": 0}

    def _counting(method, url, *, params=None, req_body=None, headers=None,
                  timeout=30.0, user_agent=None, token=None, cookie=None,
                  service_name=""):
        call["n"] += 1
        return _resp(200, body, {"ETag": etag_hex, "Repr-Digest": _digest_header(body)})

    monkeypatch.setattr(http_mod, "get_request", _counting)
    disc.fetch_spec(svc)
    assert call["n"] == 0  # served from cache, no network


