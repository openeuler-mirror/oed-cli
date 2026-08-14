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

import os
from typing import Any

import httpx

from . import __version__
from .errors import NetworkError, NotFoundError, UpstreamError

DEFAULT_GATEWAY = "https://api-gateway.osinfra.cn"
_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = f"oed/{__version__} (+https://atomgit.com/openeuler/oed-cli)"


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
    extra: dict[str, str] | None = None, *, user_agent: str | None = None
) -> dict[str, str]:
    h = {
        "User-Agent": _resolve_user_agent(user_agent),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
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
    """

    method = method.upper()
    extra = dict(headers or {})
    if body is not None and not any(h.lower() == "content-type" for h in extra):
        extra["Content-Type"] = "application/json"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            return client.request(
                method,
                url,
                params=params if params else None,
                json=body if body is not None else None,
                headers=_headers(extra, user_agent=user_agent),
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
