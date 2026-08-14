"""Discovery feed retrieval, parsing and local caching.

Reads ``https://api-gateway.osinfra.cn/discovery/apis`` (per ``context/discoverAPI.md``)
and caches the parsed feed under ``~/.cache/oed-cli/discovery.json``. A fresh
fetch happens only when the cache is older than :data:`CACHE_TTL_SECONDS`.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import NotFoundError
from .http import DEFAULT_GATEWAY, get_json

CACHE_TTL_SECONDS = 600  # 10 minutes
DEFAULT_COMMUNITY = "openeuler"
APIS_URL = f"{DEFAULT_GATEWAY}/discovery/apis"
SPEC_URL_TEMPLATE = f"{DEFAULT_GATEWAY}/discovery/apis/{{community}}/{{service_name}}"


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


def _read_cache(path: Path) -> DiscoveryFeed | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _materialize(raw)


def _materialize(raw: dict[str, Any]) -> DiscoveryFeed:
    fetched_at = float(raw.get("__oed_fetched_at", 0))
    services = [ServiceMeta.from_raw(s) for s in raw.get("services", [])]
    return DiscoveryFeed(fetched_at=fetched_at, raw=raw, services=services)


def _write_cache(path: Path, feed: DiscoveryFeed) -> None:
    payload = {
        "__oed_fetched_at": feed.fetched_at,
        "services": [s.__dict__ for s in feed.services],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Cache is best-effort; failures here are not fatal.
        pass


def _fetch_remote(community: str | None) -> DiscoveryFeed:
    """Hit the gateway. If ``community`` is provided, request that variant so the
    payload layout matches the per-community view; otherwise request the global
    payload which is keyed by community."""

    if community:
        data = get_json(APIS_URL, params={"community": community})
        services: list[ServiceMeta] = []
        if isinstance(data, list):
            services = [ServiceMeta.from_raw(s) for s in data]
    else:
        data = get_json(APIS_URL)
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
    """Return the OpenAPI 3.x spec for one service, parsed JSON."""

    url = SPEC_URL_TEMPLATE.format(community=service.community, service_name=service.service_name)
    data = get_json(url)
    if not isinstance(data, dict) or "openapi" not in data:
        raise NotFoundError(
            f"spec at {url} did not return an OpenAPI document",
            kind="spec_not_found",
            hint="The registered service may be missing its openapi.yaml upstream.",
        )
    return data


def current_community() -> str:
    return os.environ.get("OED_COMMUNITY", DEFAULT_COMMUNITY)
