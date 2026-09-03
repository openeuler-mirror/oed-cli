"""Discovery feed retrieval, parsing and local caching.

Reads ``https://api-gateway.osinfra.cn/discovery/apis`` (per ``context/discoverAPI.md``)
and caches the parsed feed under ``~/.cache/oed-cli/discovery.json``. A fresh
fetch happens only when the cache is older than :data:`CACHE_TTL_SECONDS`.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import NotFoundError
from .http import apis_url, get_json

CACHE_TTL_SECONDS = 600  # 10 minutes
# Tolerance for clock skew when judging cache freshness. A cached
# ``__oed_fetched_at`` strictly newer than ``now + _CLOCK_SKEW_SECONDS`` is
# treated as a poisoned cache (issue #22): an attacker who can write the
# cache file can set a future timestamp so ``now - fetched_at`` is always
# negative and the TTL check never expires the entry. Rejecting future
# timestamps closes the "never-expires" bypass. 300s absorbs legitimate NTP
# drift between the writing process and a later reader. NOTE: kept in sync
# with ``dynamic._CLOCK_SKEW_SECONDS`` — both must use the same value.
_CLOCK_SKEW_SECONDS = 300
DEFAULT_COMMUNITY = "openeuler"


@dataclass(frozen=True)
class ServiceMeta:
    name: str
    service_name: str
    community: str
    title: str
    version: str
    description: str
    base_url: str

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ServiceMeta:
        return cls(
            name=raw["name"],
            service_name=raw["service_name"],
            community=raw["community"],
            title=raw.get("title", ""),
            version=raw.get("version", ""),
            description=raw.get("description", ""),
            base_url=raw.get("base_url", ""),
        )


@dataclass
class DiscoveryFeed:
    fetched_at: float
    raw: dict[str, Any]
    services: list[ServiceMeta] = field(default_factory=list)

    def for_community(self, community: str) -> list[ServiceMeta]:
        return [s for s in self.services if s.community == community]

    def find_service(self, community: str, service_name: str) -> ServiceMeta:
        for s in self.services:
            if s.community == community and s.service_name == service_name:
                return s
        raise NotFoundError(
            f"service '{service_name}' not found in community '{community}'",
            kind="service_not_found",
            hint="Run `oed services` to see the registered services.",
        )


def _cache_dir() -> Path:
    override = os.environ.get("OED_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "oed-cli" / "cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "oed-cli"


def _cache_path() -> Path:
    return _cache_dir() / "discovery.json"


def auth_token_path() -> Path:
    """Path to the auth.json file (sibling of ``discovery.json``).

    Lives under the same XDG / Windows-LOCALAPPDATA cache directory as the
    discovery feed, so a single ``OED_CACHE_DIR`` override covers both.
    """
    return _cache_dir() / "auth.json"


def ag_token_path(service: str = "ag") -> Path:
    """Path to an AtomGit (``ag``) PAT plaintext-fallback file.

    Lives under a ``tokens/`` subdir of the cache dir so ``oed cache clear``
    (which only unlinks ``discovery.json``) can never wipe credentials. The
    real secret prefers the OS keystore via :class:`oed_cli.auth._SecureStore`;
    this file is only the 0600 fallback when keyring is unreachable.
    """

    return _cache_dir() / "tokens" / f"{service}.json"


def _read_cache(path: Path) -> DiscoveryFeed | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _materialize(raw)


def _materialize(raw: dict[str, Any]) -> DiscoveryFeed | None:
    """Build a :class:`DiscoveryFeed` from a raw cache dict.

    Returns ``None`` (→ caller refetches) when the cache is poisoned: a
    ``__oed_fetched_at`` timestamp in the future beyond :data:`_CLOCK_SKEW_SECONDS`
    would otherwise make the TTL check never expire the entry (issue #22).
    """

    fetched_at = float(raw.get("__oed_fetched_at", 0))
    if fetched_at > time.time() + _CLOCK_SKEW_SECONDS:
        return None
    services = [ServiceMeta.from_raw(s) for s in raw.get("services", [])]
    return DiscoveryFeed(fetched_at=fetched_at, raw=raw, services=services)


def _atomic_write_json(path: Path, data: dict[str, Any], *, mode: int | None = None) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tmp + replace).

    Cache files are public data (discovery feed / OpenAPI specs), so by
    default no restrictive perms are forced — only the torn-write race is
    closed. Used by both the discovery feed cache and the per-service spec
    cache (see :mod:`oed_cli.dynamic`) so a crash mid-write never leaves a
    half-written file that a later read would parse as corrupt.

    When ``mode`` is given (e.g. ``0o600``) the temp file is chmod'd before
    the atomic replace, so the final file honors it. This is used for the
    spec cache and the integrity ledger: a non-owner must NOT be able to
    write a forged ``repr_digest`` (that would let a tampered spec pass local
    verification). The brief pre-chmod window only exposes a public hash, so
    read-leakage is harmless; what matters is blocking non-owner writes.
    """

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if mode is not None:
        with contextlib.suppress(OSError):
            os.chmod(tmp, mode)
    try:
        tmp.replace(path)
    except OSError:
        # Best-effort: a failed replace (e.g. cross-device tmp) leaves the
        # .tmp behind but never corrupts the existing cache. Callers treat
        # cache write failures as non-fatal.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _write_cache(path: Path, feed: DiscoveryFeed) -> None:
    payload = {
        "__oed_fetched_at": feed.fetched_at,
        "services": [s.__dict__ for s in feed.services],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, payload)
    except OSError:
        # Cache is best-effort; failures here are not fatal.
        pass


def _fetch_remote(community: str | None) -> DiscoveryFeed:
    """Hit the gateway. If ``community`` is provided, request that variant so the
    payload layout matches the per-community view; otherwise request the global
    payload which is keyed by community."""

    if community:
        data = get_json(apis_url(), params={"community": community})
        services: list[ServiceMeta] = []
        if isinstance(data, list):
            services = [ServiceMeta.from_raw(s) for s in data]
    else:
        data = get_json(apis_url())
        services = []
        communities = data.get("communities", {}) if isinstance(data, dict) else {}
        if isinstance(communities, dict):
            for arr in communities.values():
                if isinstance(arr, list):
                    services.extend(ServiceMeta.from_raw(s) for s in arr)

    raw_payload = data if isinstance(data, dict) else {"services": data}
    return DiscoveryFeed(
        fetched_at=time.time(), raw=raw_payload, services=services
    )


def fetch_discovery(*, community: str | None = None, force_refresh: bool = False) -> DiscoveryFeed:
    """Return the discovery feed, using cache when fresh."""

    cache_file = _cache_path()
    cached = None if force_refresh else _read_cache(cache_file)
    if cached is not None and (time.time() - cached.fetched_at) < CACHE_TTL_SECONDS:
        return cached

    feed = _fetch_remote(community)
    _write_cache(cache_file, feed)
    return feed


def fetch_spec(service: ServiceMeta) -> dict[str, Any]:
    """Return the OpenAPI 3.x spec for one service, parsed JSON.

    Delegates to :func:`oed_cli.dynamic.fetch_service_spec`, which adds a
    per-service file cache, ``If-None-Match``/304 negotiation, and
    ``Repr-Digest`` verification (plus the local-cache integrity guards).
    Previously this entry point fetched fresh on every call and discarded the
    returned ETag, so 304 never fired and the spec body was re-downloaded each
    time — the cached fetcher fixes that without changing this function's
    contract (parsed JSON, or raises :class:`NotFoundError`). The import is
    deferred to avoid a discovery ↔ dynamic import cycle.
    """

    from .dynamic import fetch_service_spec  # deferred: avoid import cycle

    return fetch_service_spec(service)


def current_community() -> str:
    return os.environ.get("OED_COMMUNITY", DEFAULT_COMMUNITY)
