"""Dynamic OpenAPI → operation-table mapping.

Each operation in an openEuler-gateway OpenAPI doc lives under
``spec.paths[path][verb]`` with an optional
``x-apigateway-backend.httpEndpoints`` block describing the upstream
backend (scheme/address/path/method). :func:`collect_operations` walks
the spec and exposes each (path, verb) pair as a :class:`Operation` so
the dispatcher can build URLs without hand-written client code.

URL construction:

The ``x-apigateway-backend.httpEndpoints.address`` field often points to
a staging / test backend (e.g. ``cvesa.test.osinfra.cn``) that the
gateway's CloudWAF blocks — even with browser-style headers. Each
service's runtime gateway is whatever its discovery feed entry says
(``ServiceMeta.base_url``); :func:`resolve_runtime_gateway` reads that
value, strips whitespace and a trailing ``/``, and returns it verbatim.
The runtime URL is therefore ``resolve_runtime_gateway(service) +
spec.paths[key]``; the backend block is parsed for diagnostics
(method, scheme) only and never trusted for the host. There is **no
fallback** — if the gateway hands back an empty string or the legacy
``$APIG_GROUP_ENTRY_URL`` placeholder, the resulting URL will fail at
HTTP time and surface a clear error.

CLI surface:

Each declared ``query`` / ``path`` parameter on an operation is exposed
as its own ``--<kebab-case>`` flag by :func:`to_flag` / :func:`param_flag_index`,
so users can run ``oed <service> <op> --cve-id CVE-2024-1234`` instead
of stuffing JSON into ``--params``. The legacy ``--params '{...}'``
form is preserved as an escape hatch and is overridden by per-param
flags when both are present.

The most common backend block we still see in specs:

    "x-apigateway-backend": {
        "type": "HTTP",
        "httpEndpoints": {
            "address": "software-pkg.openeuler.org",
            "scheme":  "https",
            "method":  "GET",
            "path":    "/api/v1/cla",
        }
    }

Specs that omit this block still work — the operation is registered
with an empty ``address`` and its method/scheme taken from the OpenAPI
``(path, verb)`` pair. Several openEuler services (``cve``,
``pkgcontrib``, …) publish specs without it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discovery import (
    ServiceMeta,
    _atomic_write_json,
    _cache_dir,
    current_community,
    fetch_discovery,
)
from .errors import NotFoundError, UserError
from .http import fetch_json_with_integrity, spec_url

SPEC_CACHE_TTL_SECONDS = 600  # 10 min, mirrors the discovery feed TTL
# Mirror of ``discovery._CLOCK_SKEW_SECONDS`` — a cached spec whose
# ``__oed_fetched_at`` is newer than ``now + _CLOCK_SKEW_SECONDS`` is treated
# as poisoned (future-timestamp TTL bypass, issue #22) and refetched. Keep in
# sync with the discovery-side constant.
_CLOCK_SKEW_SECONDS = 300
# Spec URLs are built at request time via http.spec_url, which honors the
# OED_GATEWAY / OED_DISCOVERY_PREFIX env vars so the CLI can target a test
# deployment without code changes.

# Runtime base URL for every dynamic call. Each service's gateway host
# is read straight from the discovery feed (``ServiceMeta.base_url``) —
# no fallback constant, no environment override, no derivation from
# the OpenAPI spec. The gateway is the source of truth.
_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}


def resolve_runtime_gateway(service: ServiceMeta) -> str:
    """Return the runtime base URL for ``service``.

    Reads ``service.base_url`` from the discovery feed, stripping
    whitespace and a trailing ``/`` so concatenation with an OpenAPI
    path (``/v1/...``) never produces ``//``. Returns the feed value
    verbatim — if the gateway hands back an empty string or the legacy
    ``$APIG_GROUP_ENTRY_URL`` placeholder, that string flows through
    and the HTTP call fails loudly rather than being silently rewritten.
    """

    candidate = (service.base_url or "").strip()
    return candidate.rstrip("/")

# Flag-name derivation -------------------------------------------------- #


def to_flag(name: str) -> str:
    """Convert a spec parameter name to a kebab-case CLI flag stem.

    Examples:
        ``cveId``        → ``cve-id``
        ``pageNum``      → ``page-num``
        ``count_per_page`` → ``count-per-page``
        ``id``           → ``id``
    """

    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    s = s.replace("_", "-")
    return s.lower()


def param_flag_index(op: Operation) -> dict[str, dict[str, Any]]:
    """Map CLI flag stems to declared query / path parameter defs.

    Each declared parameter is registered twice — once under its
    kebab-case stem (``--cve-id``) and once under the raw spec name
    (``--cveId``) — so users can pick whichever form reads better.
    """

    out: dict[str, dict[str, Any]] = {}
    for p in op.parameters:
        if p.get("in") not in {"query", "path"}:
            continue
        out[to_flag(p["name"])] = p
        out[p["name"]] = p
    return out


@dataclass(frozen=True)
class Backend:
    """The real upstream service backing one OpenAPI operation."""

    scheme: str
    address: str
    path: str
    method: str


@dataclass(frozen=True)
class Operation:
    """One callable API operation, derived from a (path, verb) OpenAPI pair."""

    service_name: str
    path: str
    http_method: str
    backend: Backend
    summary: str = ""
    description: str = ""
    parameters: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    body_required: bool = False
    # Raw ``requestBody`` block (so we don't lose the OpenAPI-level metadata)
    # plus the inlined ``body_schema`` resolved against ``components/schemas``
    # for the ``application/json`` content type — the latter is what agents
    # consume to learn how to build a ``--json`` body. ``None`` when the op
    # has no JSON body.
    request_body: dict[str, Any] | None = None
    body_schema: dict[str, Any] | None = None
    operation_id: str = ""
    # Resolved runtime base URL — read from ``ServiceMeta.base_url`` via
    # :func:`resolve_runtime_gateway`. Empty by default so callers that
    # build Operations without a service still work; ``invoke.py`` reads
    # this field to build every request URL.
    base_url: str = ""

    @classmethod
    def from_openapi(
        cls,
        *,
        service_name: str,
        path: str,
        verb: str,
        op: dict[str, Any],
        backend: Backend,
        base_url: str = "",
        spec: dict[str, Any] | None = None,
    ) -> Operation:
        request_body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else None
        body_schema: dict[str, Any] | None = None
        if request_body is not None and spec is not None:
            body_schema = _resolve_json_body_schema(request_body, spec)
        return cls(
            service_name=service_name,
            path=path,
            http_method=verb.upper(),
            backend=backend,
            summary=op.get("summary", ""),
            description=op.get("description", ""),
            parameters=tuple(op.get("parameters", []) or ()),
            body_required=bool(op.get("requestBody", {}).get("required")),
            request_body=request_body,
            body_schema=body_schema,
            operation_id=op.get("operationId") or f"{verb.upper()} {path}",
            base_url=base_url,
        )

    @property
    def path_params(self) -> list[dict[str, Any]]:
        return [p for p in self.parameters if p.get("in") == "path"]

    @property
    def query_params(self) -> list[dict[str, Any]]:
        return [p for p in self.parameters if p.get("in") == "query"]

    @property
    def display_name(self) -> str:
        """User-facing operation name. Strips the ``API_`` prefix that
        Huawei APIG auto-appends to every operationId on services like
        ``software-package-server`` — see ``operations_table`` for the
        lookup alias that keeps the raw form working too."""

        if self.operation_id.startswith("API_") and len(self.operation_id) > 4:
            return self.operation_id[4:]
        return self.operation_id


def _parse_backend(op: dict[str, Any], *, path: str, verb: str) -> Backend:
    """Return the :class:`Backend` for an operation.

    When the spec omits ``x-apigateway-backend`` (or types it as something
    other than ``HTTP``) we synthesize one from the OpenAPI ``(path, verb)``
    pair with an empty ``address``. The block is diagnostics-only — the
    runtime host always comes from :func:`resolve_runtime_gateway` — so a
    missing block must not make the operation disappear from the command
    table. Several openEuler services (``cve``, ``pkgcontrib``, …) ship
    specs without it.
    """

    raw = op.get("x-apigateway-backend")
    if not isinstance(raw, dict) or raw.get("type") != "HTTP":
        return Backend(scheme="https", address="", path=path, method=verb.upper())
    eps = raw.get("httpEndpoints") or {}
    return Backend(
        scheme=eps.get("scheme", "https"),
        address=eps.get("address", ""),
        path=eps.get("path", "/"),
        method=(eps.get("method") or verb).upper(),
    )


# --------------------------------------------------------------------------- #
# $ref resolver for request-body schemas
# --------------------------------------------------------------------------- #


def _inline_refs(node: Any, *, spec: dict[str, Any], _seen: frozenset[str] = frozenset()) -> Any:
    """Recursively inline ``$ref: #/components/schemas/X`` in ``node``.

    Only resolves refs the openEuler gateway actually emits (a single schema
    bag under ``components.schemas``); external ``$ref``s and non-JSON-Reference
    forms are left alone so the original spec stays recoverable for
    diagnostics. Recursion depth is bounded by the ref-graph so a cyclic
    schema can't blow the stack.
    """

    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]
            if ref.startswith("#/components/schemas/") and ref not in _seen:
                key = ref.rsplit("/", 1)[-1]
                schemas = (spec.get("components") or {}).get("schemas") or {}
                target = schemas.get(key)
                if isinstance(target, dict):
                    return _inline_refs(
                        target, spec=spec, _seen=_seen | {ref}
                    )
            return node
        return {k: _inline_refs(v, spec=spec, _seen=_seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, spec=spec, _seen=_seen) for v in node]
    return node


def _resolve_json_body_schema(
    request_body: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the inlined JSON-body schema for a requestBody block.

    Prefers the ``application/json`` content type; falls back to the first
    declared content type when JSON isn't there. Returns ``None`` if the
    block has no schema to resolve.
    """

    content = request_body.get("content") or {}
    media = content.get("application/json") or next(iter(content.values()), None)
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    resolved = _inline_refs(schema, spec=spec)
    return resolved if isinstance(resolved, dict) else None


def collect_operations(
    spec: dict[str, Any], service_name: str, *, base_url: str = ""
) -> list[Operation]:
    """Walk ``spec.paths`` and return every operation.

    ``base_url`` is the resolved runtime URL (output of
    :func:`resolve_runtime_gateway`); it is baked into every returned
    :class:`Operation` so :mod:`oed_cli.invoke` does not need to know
    which service produced the operation. The ``spec`` is also passed
    through so each operation's ``requestBody`` can be resolved against
    ``components/schemas``.
    """

    out: list[Operation] = []
    paths = spec.get("paths") or {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb.lower() not in _HTTP_VERBS or not isinstance(op, dict):
                continue
            out.append(
                Operation.from_openapi(
                    service_name=service_name,
                    path=path,
                    verb=verb,
                    op=op,
                    backend=_parse_backend(op, path=path, verb=verb),
                    base_url=base_url,
                    spec=spec,
                )
            )
    return out


def operations_table(
    spec: dict[str, Any], service_name: str, *, base_url: str = ""
) -> dict[str, Operation]:
    """Build a name → :class:`Operation` lookup. Each operationId is the primary
    key; secondary aliases are ``"<VERB> <path>"`` (e.g. ``"GET /v1/cla"``)
    and — for specs whose operationIds carry an APIG-generated ``API_``
    prefix — the prefix-stripped form (``API_listFoo`` → ``listFoo``)."""

    table: dict[str, Operation] = {}
    for op in collect_operations(spec, service_name, base_url=base_url):
        primary = op.operation_id
        table[primary] = op
        table[f"{op.http_method} {op.path}"] = op
        stripped = op.display_name
        if stripped != primary and stripped not in table:
            table[stripped] = op
    return table


def resolve_operation(table: dict[str, Operation], name: str) -> Operation:
    """Look up an operation by ``name`` (case-insensitive), with helpful errors."""

    if name in table:
        return table[name]
    lowered = name.lower()
    for key, op in table.items():
        if key.lower() == lowered:
            return op
    raise NotFoundError(
        f"no operation matches '{name}'",
        kind="method_not_found",
        hint="Run `oed <service>` to list every operationId for this service.",
    )


def parse_json_arg(blob: str | None, *, flag: str) -> dict[str, Any] | None:
    """Parse a JSON flag with a precise error pointing at the flag name."""

    if blob is None or blob == "":
        return None
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise UserError(
            f"--{flag} is not valid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})",
            kind="invalid_json",
        ) from exc
    if not isinstance(obj, dict):
        raise UserError(
            f"--{flag} must decode to a JSON object, got {type(obj).__name__}",
            kind="invalid_json",
        )
    return obj


def coerce_param_types(op: Operation, params: dict[str, Any]) -> dict[str, Any]:
    """Cast well-known string params to int/float based on the OpenAPI schema.

    openEuler specs declare ``schema.type: integer`` for fields like
    ``page_num``/``count_per_page``; many callers pass them as strings.
    Without coercion the backend rejects them. We only convert ints the
    spec actually declares, so we never mis-cast user data.
    """

    out: dict[str, Any] = {}
    declared_ints = {
        p["name"]
        for p in op.parameters
        if p.get("schema", {}).get("type") == "integer" and p.get("name") in params
    }
    declared_numbers = {
        p["name"]
        for p in op.parameters
        if p.get("schema", {}).get("type") == "number" and p.get("name") in params
    }
    for k, v in params.items():
        if k in declared_ints and isinstance(v, str) and v.lstrip("-").isdigit():
            out[k] = int(v)
        elif k in declared_numbers and isinstance(v, str):
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def coerce_flag_value(param_def: dict[str, Any], value: str) -> Any:
    """Coerce one CLI string flag to the spec-declared type.

    CLI flags arrive as strings (``--page-num 3`` → ``"3"``), but the
    OpenAPI schema often declares ``integer``. Callers can also pass
    already-typed values via ``--params '{...}'``; those bypass this
    helper and go through :func:`coerce_param_types` instead.
    """

    schema_type = (param_def.get("schema") or {}).get("type", "string")
    if schema_type == "integer":
        stripped = value.lstrip("-")
        if stripped.isdigit():
            return int(value)
        return value
    if schema_type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if schema_type == "boolean":
        lowered = value.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return value


# --------------------------------------------------------------------------- #
# Local spec cache (per-service file, mirrors discovery.py layout)
# --------------------------------------------------------------------------- #


def _spec_cache_path(community: str, service_name: str) -> Path:
    # ``_cache_dir`` is imported at module scope so tests can monkeypatch it on
    # this module (``setattr(dyn, "_cache_dir", ...)``) and actually redirect
    # the spec cache. A deferred ``from .discovery import _cache_dir`` here
    # would bypass that patch and silently write to the real cache dir.
    return _cache_dir() / "specs" / community / f"{service_name}.json"


def _integrity_index_path() -> Path:
    """Path to the integrity ledger holding per-spec ``repr_digest`` values.

    The digest is stored SEPARATELY from the spec cache file (and at 0600) so
    that editing the visible ``spec`` field cannot, on its own, make a
    tampered spec pass local verification — the attacker must also write this
    protected ledger. Verification reads the digest ONLY from here, with a
    one-time fallback to the legacy inline field for pre-hardening caches.
    (Plan A: local-cache anti-tamper hardening.)
    """
    return _cache_dir() / ".integrity.json"


def _cache_key_for_path(path: Path) -> str:
    """Derive ``"community/service"`` from a spec cache path.

    The spec cache path is ``<cache_dir>/specs/<community>/<service>.json``,
    so ``parent.name`` is the community and the stem is the service name.
    This lets the read helpers keep their ``path``-only signatures while
    looking the digest up in the ledger.
    """
    return f"{path.parent.name}/{path.stem}"


def _read_integrity_index() -> dict[str, str]:
    """Load the integrity ledger, or ``{}`` when absent / unreadable."""
    path = _integrity_index_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def _write_integrity_index(index: dict[str, str]) -> None:
    path = _integrity_index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, index, mode=0o600)
    except OSError:
        return  # best-effort


def _lookup_digest(path: Path) -> str | None:
    """Return the stored ``repr_digest`` for the spec at ``path``, or None."""
    val = _read_integrity_index().get(_cache_key_for_path(path))
    return val if isinstance(val, str) and val else None


def _store_digest(path: Path, digest: str) -> None:
    """Record ``digest`` for the spec at ``path`` in the protected ledger."""
    index = _read_integrity_index()
    index[_cache_key_for_path(path)] = digest
    _write_integrity_index(index)


def _spec_repr_digest(spec: dict[str, Any]) -> str:
    """Compute the RFC 9530 base64 sha-256 digest of a spec dict.

    Serializes with the same parameters as the discovery service's
    ``_send_integrity_response`` (``ensure_ascii=False, indent=2``), so the
    digest matches the server's ``Repr-Digest`` of the spec body. Used to
    verify a locally-cached spec against its stored ``repr_digest`` — detects
    local-cache tampering (a spec edited on disk without updating the digest).
    """
    import base64
    payload = json.dumps(spec, ensure_ascii=False, indent=2).encode("utf-8")
    return base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")


def _read_spec_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = float(raw.get("__oed_fetched_at", 0))
    # Poisoned cache (issue #22): a future timestamp makes the TTL check
    # below never expire the entry. Reject and refetch.
    if fetched_at > time.time() + _CLOCK_SKEW_SECONDS:
        return None
    age = time.time() - fetched_at
    if age >= SPEC_CACHE_TTL_SECONDS:
        return None
    spec = raw.get("spec")
    if not isinstance(spec, dict):
        return None
    # Local-integrity check: verify the on-disk spec still hashes to its
    # stored repr_digest. The digest lives in the protected integrity ledger
    # (separate 0600 file) so editing the spec file alone can't make a
    # tampered spec pass — the attacker must also forge the ledger. Legacy
    # caches (pre-hardening) still carry the digest inline; read it from there
    # as a one-time fallback so existing caches migrate on next write.
    cached_digest = _lookup_digest(path) or raw.get("repr_digest")
    if isinstance(cached_digest, str) and cached_digest and not hmac.compare_digest(
        _spec_repr_digest(spec), cached_digest
    ):
        return None
    return spec


def _read_cached_etag(path: Path) -> str | None:
    """Return the ETag stored alongside a (possibly stale) cached spec.

    Used to drive a conditional ``If-None-Match`` request when the cache has
    expired: the spec on disk may be stale but its ETag is still valid for
    revalidation, letting the server answer ``304`` and save a full re-download.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    etag = raw.get("etag")
    return etag if isinstance(etag, str) and etag else None


def _cached_repr_digest(path: Path) -> str | None:
    """Return the stored ``repr_digest`` for the spec at ``path``.

    Read from the protected integrity ledger; fall back to the legacy inline
    field for caches written before the ledger existed (one-time migration).
    """
    digest = _lookup_digest(path)
    if digest:
        return digest
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    legacy = raw.get("repr_digest")
    return legacy if isinstance(legacy, str) and legacy else None


def _read_cached_spec_raw(path: Path) -> dict[str, Any] | None:
    """Return the cached spec dict regardless of TTL, IF it passes the local
    integrity check (its sha-256 matches the stored ``repr_digest``).

    Used to reuse a spec on a ``304 Not Modified``. The integrity guard closes
    the "304 reuses a tampered local cache" blind spot: if the on-disk spec was
    edited without updating its digest, the caller must NOT reuse it — return
    ``None`` so ``fetch_service_spec`` falls through to a full re-download.
    """

    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    spec = raw.get("spec")
    if not isinstance(spec, dict):
        return None
    cached_digest = _lookup_digest(path) or raw.get("repr_digest")
    if isinstance(cached_digest, str) and cached_digest and not hmac.compare_digest(
        _spec_repr_digest(spec), cached_digest
    ):
        return None  # tampered — refuse to reuse, force re-download
    return spec


def _write_spec_cache(
    path: Path,
    spec: dict[str, Any],
    etag: str | None = None,
    repr_digest: str | None = None,
) -> None:
    """Write the spec cache file at 0600; store ``repr_digest`` in the
    SEPARATE protected ledger rather than in this file.

    ``etag`` and ``fetched_at`` stay here: the ETag is a cache token, not
    security-sensitive (forging it only causes a safe 304-miss → refetch).
    The ``repr_digest`` is what gates local-cache trust, so it must NOT live
    next to the spec it guards — otherwise a single file edit can re-stamp
    both. The spec file is 0600 so non-owners can't write it at all.
    """
    payload: dict[str, Any] = {
        "__oed_fetched_at": time.time(),
        "spec": spec,
    }
    if etag:
        payload["etag"] = etag
    # repr_digest intentionally NOT written here — see _integrity_index_path.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, payload, mode=0o600)
    except OSError:
        return  # best-effort
    if repr_digest:
        _store_digest(path, repr_digest)


def fetch_service_spec(
    service: ServiceMeta, *, force_refresh: bool = False
) -> dict[str, Any]:
    """Load one service's OpenAPI, using a file cache when fresh.

    Beyond the TTL cache, this drives content-checksum verification and
    conditional requests against the discovery service's integrity headers
    (issue #15 F-03 + checksum hardening): a cached ETag is sent as
    ``If-None-Match`` so an unchanged spec returns ``304`` (no body) and the
    cache is merely re-stamped instead of re-downloaded. The response body's
    SHA256 is checked against the ``Repr-Digest`` header (RFC 9530) when present;
    a mismatch raises :class:`UpstreamError` (``integrity_mismatch``).

    Local-cache integrity is also enforced on read: a cached spec whose SHA256
    no longer matches its stored ``repr_digest`` (edited on disk) is treated as
    invalid and refetched — both on TTL-hit reads and on ``304`` reuse.
    """

    cache_path = _spec_cache_path(service.community, service.service_name)

    # Fresh cache → return immediately, no network. (Integrity-checked inside.)
    if not force_refresh:
        cached = _read_spec_cache(cache_path)
        if cached is not None:
            return cached

    url = spec_url(service.community, service.service_name)
    stale_etag = _read_cached_etag(cache_path)

    # Conditional request when we hold an ETag; the server may answer 304.
    data, etag, repr_digest = fetch_json_with_integrity(url, if_none_match=stale_etag)

    if data is None:
        # 304 Not Modified — the cached spec is still current. Reuse it rather
        # than re-downloading, but only if it passes the local integrity check
        # (``_read_cached_spec_raw`` verifies the on-disk spec against its
        # stored ``repr_digest``; a tampered cache returns None here).
        stale_spec = _read_cached_spec_raw(cache_path)
        if stale_spec is not None:
            _write_spec_cache(
                cache_path, stale_spec,
                etag=etag or stale_etag,
                repr_digest=repr_digest or _cached_repr_digest(cache_path),
            )
            return stale_spec
        # Stale spec missing or tampered — refetch unconditionally.
        data, etag, repr_digest = fetch_json_with_integrity(url)

    if not isinstance(data, dict) or "openapi" not in data:
        raise NotFoundError(
            f"GET {url} did not return an OpenAPI document",
            kind="spec_missing",
            hint="The discovery feed lists this service but its OpenAPI spec is not available.",
        )
    _write_spec_cache(cache_path, data, etag=etag, repr_digest=repr_digest)
    return data


def resolve_service(community: str | None = None, force_refresh: bool = False) -> ServiceMeta:
    """Find the service named by the ``OED_SERVICE`` env var (used in tests).

    Real CLI dispatch uses :func:`resolve_service_by_name`. This helper exists
    so that scripts / tests can still drive ``invoke.call_operation`` without
    going through the full dispatch table.
    """

    name = os.environ.get("OED_SERVICE")
    if not name:
        raise UserError(
            "OED_SERVICE env var is not set; pass service_name explicitly.",
            kind="missing_service",
        )
    return resolve_service_by_name(name, community=community, force_refresh=force_refresh)


def resolve_service_by_name(
    service_name: str,
    *,
    community: str | None = None,
    force_refresh: bool = False,
) -> ServiceMeta:
    """Locate a :class:`ServiceMeta` by ``service_name`` in the given/active community."""

    feed = fetch_discovery(force_refresh=force_refresh)
    target_community = community or current_community()
    matches = [s for s in feed.services if s.service_name == service_name]
    if not matches:
        other = sorted({s.community for s in feed.services})
        raise NotFoundError(
            f"service '{service_name}' is not registered",
            kind="service_not_found",
            hint=(
                f"Available communities: {other}. "
                "Run `oed services` to list registered services."
            ),
        )
    if len(matches) == 1:
        return matches[0]
    for s in matches:
        if s.community == target_community:
            return s
    other_communities = sorted({s.community for s in matches})
    raise UserError(
        f"service '{service_name}' is registered in multiple communities {other_communities}; "
        f"set OED_COMMUNITY to pick one (current: {target_community}).",
        kind="ambiguous_service",
    )


def operations_for(
    service_name: str,
    *,
    community: str | None = None,
    force_refresh: bool = False,
) -> tuple[ServiceMeta, dict[str, Operation], dict[str, Any]]:
    """One-shot helper: resolve a service, fetch its spec, build the table.

    Returns ``(service_meta, operations_table, raw_spec)`` so callers can
    inspect both the structured view and the raw OpenAPI doc. Each
    operation in the returned table has ``base_url`` set to
    :func:`resolve_runtime_gateway` of the resolved service.
    """

    service = resolve_service_by_name(
        service_name, community=community, force_refresh=force_refresh
    )
    spec = fetch_service_spec(service, force_refresh=force_refresh)
    base_url = resolve_runtime_gateway(service)
    return service, operations_table(spec, service.service_name, base_url=base_url), spec


__all__ = [
    "Backend",
    "Operation",
    "collect_operations",
    "coerce_flag_value",
    "coerce_param_types",
    "fetch_service_spec",
    "operations_for",
    "operations_table",
    "param_flag_index",
    "parse_json_arg",
    "resolve_operation",
    "resolve_runtime_gateway",
    "resolve_service_by_name",
    "to_flag",
]
