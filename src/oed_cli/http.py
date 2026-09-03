"""WAF-safe HTTP client for the openEuler Infra gateway.

The gateway sits behind a CloudWAF that returns a Chinese-language HTML block
page when the request lacks browser-style headers (see ``context/discoverAPI.md``
section "注意事项 & 已知限制"). This module bakes those headers in so every call
made by ``oed`` succeeds without users tweaking curl flags.

Note on ``Referer``: openEuler APIG rejects requests that carry a
``Referer: https://api-gateway.osinfra.cn/`` header with HTTP 401 on at
least one production path (``easysearch`` / ``sigsearch/docs``). The
``/discovery/apis`` feed does not need Referer to pass the CloudWAF
either — empirically verified 2026-07-28. So ``_headers()`` does NOT
emit a default Referer. Callers can still pass one explicitly via the
``headers=`` argument to :func:`get_request` if a future endpoint
demands it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

import httpx

from . import __version__
from .errors import NetworkError, NotFoundError, UpstreamError, UserError

DEFAULT_GATEWAY = "https://api-gateway.osinfra.cn"
# Path prefix under the gateway that serves the discovery API. Production routes
# the feed through a reverse proxy at ``/discovery/apis``; a discovery service
# reached directly (e.g. a test deployment) serves ``/apis``. Override the
# gateway host and/or this prefix via env to point the CLI at a non-production
# deployment without code changes:
#   OED_GATEWAY=https://apig-discovery.test.osinfra.cn
#   OED_DISCOVERY_PREFIX=/apis
DEFAULT_DISCOVERY_PREFIX = "/discovery/apis"
_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = f"oed/{__version__} (+https://atomgit.com/openeuler/oed-cli)"


def resolve_gateway() -> str:
    """Return the gateway host: ``OED_GATEWAY`` env > :data:`DEFAULT_GATEWAY`.

    Strip a trailing ``/`` so concatenation with an absolute path never yields
    ``//``. Used at request-build time (not import time) so flipping the env
    takes effect without reloading the module.
    """
    gw = os.environ.get("OED_GATEWAY") or DEFAULT_GATEWAY
    return gw.rstrip("/")


def resolve_discovery_prefix() -> str:
    """Return the discovery API path prefix: env > default.

    ``OED_DISCOVERY_PREFIX=/apis`` points at a directly-reached discovery
    service; the default ``/discovery/apis`` goes through the gateway reverse
    proxy. Leading slash enforced, trailing slash stripped.
    """
    p = os.environ.get("OED_DISCOVERY_PREFIX") or DEFAULT_DISCOVERY_PREFIX
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def apis_url() -> str:
    """Full URL of the discovery feed list endpoint."""
    return f"{resolve_gateway()}{resolve_discovery_prefix()}"


def spec_url(community: str, service_name: str) -> str:
    """Full URL of one service's OpenAPI document endpoint."""
    return f"{resolve_gateway()}{resolve_discovery_prefix()}/{community}/{service_name}"


def _resolve_user_agent(user_agent: str | None) -> str:
    """Resolve the User-Agent: explicit arg > ``OED_USER_AGENT`` env > default.

    An empty string falls through to the next source — passing ``--user-agent ""``
    is treated the same as not passing it.
    """

    if user_agent:
        return user_agent
    env = os.environ.get("OED_USER_AGENT")
    if env:
        return env
    return DEFAULT_USER_AGENT


def _headers(
    extra: dict[str, str] | None = None,
    *,
    user_agent: str | None = None,
    token: str | None = None,
    cookie: str | None = None,
    service_name: str = "",
) -> dict[str, str]:
    h = {
        "User-Agent": _resolve_user_agent(user_agent),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
        # Plaintext identity/routing headers for the APIG frontend custom-auth
        # function (see ``context/custom-auth.py``). These are NOT a secret —
        # they only declare the calling client (``source``) and the target
        # service (``target``) so APIG can apply per-service header forwarding.
        # Real authorization comes from the Bearer token above + the
        # server-side role lookup; a signed/HMAC header was removed because a
        # secret baked into a distributed CLI cannot be kept secret.
        if service_name:
            h["x-oed-source"] = "oed-cli"
            h["x-oed-target"] = service_name
    if cookie:
        h["Cookie"] = cookie
    if extra:
        h.update(extra)
    return h


def _is_waf_block(html: str) -> bool:
    lowered = html[:512].lower()
    return (
        "<!doctype html" in lowered
        and ("cloudwaf" in lowered or "访问被拦截" in html or "requestid" in lowered)
    )


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> Any:
    """GET ``url`` and return parsed JSON.

    Raises:

      - :class:`NetworkError` (exit 2) for connectivity / WAF failures
      - :class:`UpstreamError` (exit 3) for genuine 5xx / unexpected payloads
      - :class:`NotFoundError` (exit 4) when the upstream reports an empty spec
    """

    return _decode(get_request("GET", url, params=params, headers=headers), spec_endpoint=True)


def get_request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    body: Any = None,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_SECONDS,
    user_agent: str | None = None,
    token: str | None = None,
    cookie: str | None = None,
    service_name: str = "",
) -> httpx.Response:
    """Run an arbitrary HTTP request and return the raw :class:`httpx.Response`.

    Adds the WAF-safe browser headers; auto-declares ``Content-Type: application/json``
    when ``body`` is set and the caller has not overridden it. Surfaces
    connectivity failures as :class:`NetworkError`; WAF blocks as
    :class:`NetworkError` (kind ``waf_block``); 5xx as :class:`UpstreamError`.

    ``user_agent`` overrides the default User-Agent header — useful when a
    specific APIG backend's WAF rejects the default ``oed/x.y.z`` UA with a
    misleading 401 (e.g. ``easysearch``). Falls back to ``OED_USER_AGENT`` env,
    then the bundled default.

    ``token`` adds an ``Authorization: Bearer <token>`` header (only when set).
    ``cookie`` adds a ``Cookie: <cookie>`` header (only when set). Both are
    added by :func:`_headers` before the caller's ``headers=`` extras merge,
    so caller-supplied ``Authorization`` / ``Cookie`` override the kwargs.

    ``service_name`` (when both ``token`` and ``service_name`` are set) emits
    plaintext ``x-oed-source: oed-cli`` and ``x-oed-target: <service>``
    headers — the APIG frontend custom-auth function reads them to pick
    per-service header forwarding rules. They carry no secret; authorization
    is done by the Bearer token plus a server-side role lookup.
    """

    method = method.upper()
    extra = dict(headers or {})
    if body is not None and not any(h.lower() == "content-type" for h in extra):
        extra["Content-Type"] = "application/json"

    try:
        try:
            client_cm = httpx.Client(timeout=timeout, follow_redirects=True)
        except ImportError as exc:
            # httpx reads *_PROXY env vars (trust_env defaults True). A
            # ``socks5://`` proxy makes it lazily import ``socksio``; without
            # the extra installed the Client() constructor raises ImportError
            # rather than an httpx.HTTPError. Surface a readable message.
            if "socksio" in str(exc).lower():
                raise UserError(
                    "A SOCKS proxy is set (via *_PROXY env vars) but the "
                    "'socksio' package is not installed.",
                    kind="socks_dependency_missing",
                    hint="Install the SOCKS transport: pip install 'httpx[socks]'.",
                ) from exc
            raise
        with client_cm as client:
            return client.request(
                method,
                url,
                params=params if params else None,
                json=body if body is not None else None,
                headers=_headers(
                    extra,
                    user_agent=user_agent,
                    token=token,
                    cookie=cookie,
                    service_name=service_name,
                ),
            )
    except httpx.HTTPError as exc:
        raise NetworkError(f"{method} {url} failed: {exc}", kind="network_error") from exc


def _decode(
    resp: httpx.Response,
    *,
    spec_endpoint: bool = False,
) -> Any:
    """Validate ``resp`` and return parsed JSON.

    ``spec_endpoint=True`` treats an empty body as :class:`NotFoundError`
    (used for spec discovery). For runtime service calls an empty body is
    considered a successful empty payload (``null``).
    """

    if resp.status_code >= 500:
        raise UpstreamError(
            f"{resp.request.method} {resp.url} returned {resp.status_code}",
            kind="upstream_error",
            hint="Check gateway status; retry shortly.",
        )

    if _is_waf_block(resp.text):
        raise NetworkError(
            f"Gateway WAF blocked the request to {resp.url}",
            kind="waf_block",
            hint=(
                "Some APIG backends reject the bundled oed/x.y.z User-Agent. "
                "Retry with `--user-agent 'Mozilla/5.0 ...'` or set "
                "OED_USER_AGENT in the environment."
            ),
        )

    if not resp.content:
        if spec_endpoint:
            raise NotFoundError(
                f"{resp.request.method} {resp.url} "
                f"returned an empty body (HTTP {resp.status_code})",
                kind="spec_missing",
                hint=(
                    "The discovery feed lists this service but its OpenAPI "
                    "spec has not been published yet."
                ),
            )
        return None

    try:
        return resp.json()
    except Exception as exc:
        raise UpstreamError(
            f"{resp.request.method} {resp.url} returned non-JSON body (HTTP {resp.status_code})",
            kind="upstream_error",
        ) from exc


def request_json(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    body: Any = None,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> Any:
    """Run a request and return parsed JSON (empty body → ``None``)."""

    resp = get_request(method, url, params=params, body=body, headers=headers, timeout=timeout)
    return _decode(resp)


def _normalize_etag(etag: str | None) -> str | None:
    """Strip the ``W/`` weak prefix and surrounding quotes from an ETag value.

    The discovery service emits the raw SHA256 hex as both ``ETag`` and
    ``X-Content-SHA256`` (no quotes, no weak prefix), but a generic gateway
    may re-quote or weaken it. Return the bare hex for comparison, or
    ``None`` for an empty / malformed value.
    """
    if not etag:
        return None
    e = etag.strip()
    if e.startswith("W/"):
        e = e[2:]
    if len(e) >= 2 and e.startswith('"') and e.endswith('"'):
        e = e[1:-1]
    return e or None


def parse_repr_digest(header: str | None) -> str | None:
    """Parse an RFC 9530 ``Repr-Digest`` header, return the sha-256 base64 value.

    Header form ``sha-256=<base64>``; may carry multiple algorithms comma-
    separated. Returns the sha-256 value or ``None`` when absent / not found.
    Algorithm name is case-insensitive, spaces around ``=`` tolerated.
    """
    if not header:
        return None
    for item in header.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        algo, _, value = item.partition("=")
        if algo.strip().lower() != "sha-256":
            continue
        value = value.strip()
        if not value:
            return None
        return value
    return None


def _verify_content_integrity(content: bytes, headers: httpx.Headers) -> str | None:
    """Verify the response body against its ``Repr-Digest`` header, when present.

    The discovery service stamps the response with an RFC 9530 ``Repr-Digest``
    header carrying ``sha-256=<base64(sha256(body))>``. When present we
    recompute the SHA256 locally (base64-encoded) and compare constant-time; a
    mismatch means the body was altered in transit (tampering / a transparent
    proxy rewriting content) and is raised as :class:`UpstreamError`.

    When no ``Repr-Digest`` header is present (the gateway strips it, or the
    deployment predates this feature) we **skip** verification and return
    ``None`` — verify-when-present, never fail-when-absent.

    Returns the base64 digest value (for local-cache integrity checks) when the
    check passes or was skipped-with-a-header; ``None`` otherwise.
    """
    raw = headers.get("Repr-Digest")
    if not raw:
        return None
    expected = parse_repr_digest(raw)
    if not expected:
        return None
    actual = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    if not hmac.compare_digest(actual, expected):
        raise UpstreamError(
            "OpenAPI spec integrity check failed: body SHA256 does not match "
            "the Repr-Digest header",
            kind="integrity_mismatch",
            hint=(
                "The spec body was altered between the discovery service and "
                "this client (transparent proxy rewrite or tampering). Refusing "
                "to load the spec; retry, and report if persistent."
            ),
        )
    return expected


def fetch_json_with_integrity(
    url: str,
    *,
    if_none_match: str | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> tuple[Any | None, str | None, str | None]:
    """GET ``url`` with WAF-safe headers and return ``(data, etag, repr_digest)``.

    Combines :func:`get_request`, :func:`_decode`, and content-checksum
    verification for the OpenAPI spec endpoints:

    - Sends ``If-None-Match`` when the caller has a cached ETag, so an unchanged
      spec comes back as ``304`` (no body) and the caller can reuse its cache.
    - On ``304`` returns ``(None, etag, repr_digest)`` — ``data`` is ``None``;
      the response's ``ETag`` (hex, for 304 negotiation) and ``Repr-Digest``
      (base64, for local-cache integrity) are returned for the caller to store.
    - Otherwise decodes + checksum-verifies the body and returns
      ``(parsed_json, etag, repr_digest)``. ``repr_digest`` is ``None`` when the
      endpoint did not emit a ``Repr-Digest`` header (verification skipped).
    """
    req_headers = dict(headers or {})
    if if_none_match:
        req_headers["If-None-Match"] = if_none_match

    # Force an uncompressed body. The discovery service stamps ``ETag`` and
    # ``Repr-Digest`` over the *uncompressed* bytes; the front-line WAF/CDN
    # transparently gzips large responses and then strips the (now-mismatched)
    # ``ETag`` header on the compressed path — leaving only ``Repr-Digest``
    # (a non-standard header the WAF does not rewrite). For an uncompressed
    # body both headers survive, so ``If-None-Match`` / 304 negotiation works.
    # ``Repr-Digest`` verification is unaffected either way: httpx always
    # decompresses before exposing ``resp.content``. Specs are small (~tens of
    # KB) and fetched at most every cache TTL, so the bandwidth cost is moot.
    req_headers.setdefault("Accept-Encoding", "identity")

    resp = get_request("GET", url, params=params, headers=req_headers, timeout=timeout)
    if resp.status_code == 304:
        repr_digest = parse_repr_digest(resp.headers.get("Repr-Digest"))
        return None, resp.headers.get("ETag"), repr_digest

    data = _decode(resp, spec_endpoint=True)
    etag = resp.headers.get("ETag")
    repr_digest = _verify_content_integrity(resp.content, resp.headers)
    return data, etag, repr_digest
