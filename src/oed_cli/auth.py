"""openEuler credential storage and login flows (v0.5).

v0.5 replaces the v0.4 browser-callback flow with **RFC 8628 Device
Authorization Grant**, backed by the MSAL Python library. The CLI never opens
a local HTTP server; authentication works in headless / CI / SSH / agent
contexts without modification.

Storage:
    ``<OED_CACHE_DIR>/auth.json`` (POSIX 0600, atomic write) holds a
    serialized MSAL token cache plus an optional ``cookie`` field. The MSAL
    cache contains ``access_token`` + ``refresh_token`` + account metadata,
    which is what enables silent refresh on subsequent calls.

Public surface:
    - :func:`save_auth` / :func:`load_auth` / :func:`clear_auth` — manual token
      persistence (also synthesizes a minimal MSAL cache for backward compat)
    - :func:`get_token` / :func:`get_cookie` — read fields out of auth.json
    - :func:`auth_headers_from_storage` — build ``Authorization`` / ``Cookie``
      headers, preferring the env overrides ``OED_TOKEN`` / ``OED_COOKIE``
    - :func:`device_login` — RFC 8628 device authorization grant (MSAL)
    - :func:`manual_login` — TTY paste of a token (unchanged from v0.4)
    - :func:`load_defaults` / :func:`get_client_id` / :func:`get_device_url` —
      read bundled ``defaults.toml`` with env-var override
    - :func:`_try_open_browser` / :func:`_has_display` — auto-open the
      verification URL when a display is available
    - :func:`_copy_to_clipboard` — stdlib ``subprocess`` against
      ``wl-copy`` / ``xclip`` / ``pbcopy`` / ``clip.exe``
    - :data:`auth_group` — click sub-group wired into :mod:`oed_cli.cli`
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import webbrowser
from base64 import b64decode
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import click
import httpx  # used by _fetch_user_allowlist (one HTTP GET after token success)
import msal  # RFC 8628 device authorization grant + token cache
import tomllib

from .discovery import ag_token_path, auth_token_path

# Best-effort token lifetime — the openEuler gateway does not publish a TTL.
TOKEN_MAX_AGE = 24 * 60 * 60

# Credential store identifiers for the OS-native backend (keyring).
# Single-account for v0.6; multi-account support can namespace by MSAL
# ``home_account_id`` later without breaking compatibility (keyring
# tolerates multiple usernames under one service).
_KEYRING_SERVICE = "oed-cli"
_KEYRING_USERNAME = "default"


# ---------------------------------------------------------------------------
# Defaults (bundled in the wheel at oed_cli/defaults.toml)
# ---------------------------------------------------------------------------


def load_defaults() -> dict[str, Any]:
    """Read bundled ``defaults.toml``. Returns parsed dict."""

    text = (
        files("oed_cli").joinpath("defaults.toml").read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def get_client_id() -> str:
    """OAuth ``client_id``. Env ``OED_APP_ID`` overrides bundled default."""

    defaults = load_defaults()
    return os.environ.get("OED_APP_ID") or defaults["oauth"]["client_id"]


def get_device_url() -> str:
    """openEuler usercenter base URL. Env ``OED_DEVICE_URL`` overrides bundled default."""

    defaults = load_defaults()
    return os.environ.get("OED_DEVICE_URL") or defaults["oauth"]["device_url"]


def _get_device_authorization_url() -> str | None:
    """Device authorization endpoint from defaults.toml or ``None``."""

    defaults = load_defaults()
    return defaults["oauth"].get("device_authorization_url")


def _get_user_data_url() -> str | None:
    """CLI user-data (allow-list) endpoint from defaults.toml or ``None``."""

    defaults = load_defaults()
    return defaults["oauth"].get("cli_user_data_url")


# MSAL reserved scopes — it auto-appends these and refuses them if passed
# explicitly (ValueError: "cannot use any scope value that is reserved").
_RESERVED_SCOPES = {"openid", "profile", "offline_access"}

# Hardcoded base OAuth scopes. These are part of the wire contract with
# openEuler oneid and intentionally NOT read from defaults.toml:
#   - `email`: makes /userinfo return the user's email.
#   - `id_token`: oneid's `OidcService.getOidcTokenByDeviceCode` only emits
#     an `id_token` field in the /token response when the requested scopes
#     literally contain the string "id_token" (see
#     scopes.contains("id_token") gate). Non-standard OIDC (where `openid`
#     alone would trigger id_token issuance), but required by this server.
#     MSAL keys its silent-refresh Account map on id_token claims — without
#     id_token, refresh_token is unusable and the 120s access_token TTL
#     forces re-login every 2 minutes.
#
# MSAL will auto-prepend the reserved set on top of these.
_BASE_SCOPES: tuple[str, ...] = ("email", "id_token")


def get_scopes() -> list[str]:
    """OAuth scopes to request at device-flow initiation.

    Returns the hardcoded base set (``email`` + ``id_token``) unless
    ``OED_SCOPES`` env var is set, in which case a comma-separated value
    overrides. MSAL auto-appends ``openid`` / ``profile`` / ``offline_access``;
    any reserved scope passed via env is filtered out to avoid MSAL's
    reserved-scope error.
    """

    raw = os.environ.get("OED_SCOPES")
    if raw is not None:
        scopes = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        scopes = list(_BASE_SCOPES)
    return [s for s in scopes if s and s not in _RESERVED_SCOPES]


# ---------------------------------------------------------------------------
# Token storage (keyring primary + plaintext fallback)
# ---------------------------------------------------------------------------
#
# v0.6+: tokens are persisted into the OS-native credential store via the
# ``keyring`` library when an accessible backend is detected (macOS Keychain,
# Windows DPAPI / Credential Manager, Linux SecretService). On environments
# where keyring is unreachable (headless Linux / CI / Docker without
# libsecret) or when keyring raises on read/write, the implementation falls
# back to a 0600-mode plaintext file. The plaintext file is deleted after a
# successful keyring write so the OS keystore is the single source of truth.


class _SecureStore:
    """Thin facade over :mod:`keyring` with a transparent plaintext fallback.

    Resolution is lazy: each call probes the current best backend and caches
    the verdict. Any ``keyring`` failure (``NoKeyringError``, locked
    credential DB, ``OSError`` from libsecret, sandbox restrictions) causes
    a silent fallback to ``auth.json`` (mode 0600) and a one-line stderr
    note — never a hard error, so silent refresh keeps working.
    """

    def __init__(self, *, username: str = _KEYRING_USERNAME,
                 fallback_path: Callable[[], Path] = auth_token_path) -> None:
        # ``username`` namespaces the keyring entry so a second credential
        # (e.g. the AtomGit PAT) can share the ``_KEYRING_SERVICE`` without
        # colliding with the oneid ``default`` entry. ``fallback_path`` picks
        # the plaintext 0600 file used when keyring is unreachable — the ag
        # PAT lives under ``tokens/ag.json``, separate from ``auth.json``.
        self._username = username
        self._fallback_path = fallback_path
        self._keyring: Any | None = None
        # ``None`` = not probed yet, ``True``/``False`` = resolved verdict
        self._available: bool | None = None
        self._last_backend: str = "unprobed"

    # -- internal ----------------------------------------------------------

    def _resolve(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import keyring  # type: ignore[import-untyped]
            import keyring.errors  # type: ignore[import-untyped]

            self._keyring = keyring
            # Cheapest probe: a single get. Side-effect free — we don't want
            # to write a probe entry into the user's real keychain.
            # NoKeyringError is the canonical "no backend" signal; other
            # exceptions mean the backend exists but is broken/locked, in
            # which case we silently fall through to plaintext 0600.
            try:
                keyring.get_password(_KEYRING_SERVICE, self._username)
            except keyring.errors.NoKeyringError:
                self._available = False
                return False
            except Exception:  # noqa: BLE001 — best-effort probe
                # Locked credential DB, libsecret not running, sandbox block,
                # GNOME Keyring not unlocked — module loaded but unusable.
                # Surface the backend via `oed auth status`, not stderr noise.
                self._available = False
                return False
            self._available = True
            return True
        except ImportError:
            self._available = False
            return False

    # -- public API ---------------------------------------------------------

    def save(self, payload: dict[str, Any]) -> str:
        """Persist ``payload`` via the best available backend.

        Returns the backend label (``"keyring"`` or ``"plaintext"``) so the
        caller can surface it (``oed auth status``).
        """

        path = self._fallback_path()
        if self._resolve():
            try:
                blob = json.dumps(payload, ensure_ascii=False)
                self._keyring.set_password(_KEYRING_SERVICE, self._username, blob)  # type: ignore[union-attr]
                # keyring is the source of truth now — drop the plaintext copy
                # so a later read cannot see stale data from a previous
                # plaintext-only session.
                with contextlib.suppress(OSError):
                    path.unlink()
                self._last_backend = "keyring"
                return "keyring"
            except Exception:  # noqa: BLE001 — best-effort fallback
                # Backend reported as available by the probe but the real
                # write raised (locked keyring, etc.). Mark broken for this
                # process so subsequent operations go straight to plaintext
                # instead of re-probing keyring on every call.
                self._available = False
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json_plaintext(path, payload)
        self._last_backend = "plaintext"
        return "plaintext"

    def load(self) -> dict[str, Any] | None:
        """Read ``payload`` from the best available backend.

        Prefers keyring when reachable; falls back to the plaintext file
        (used both for migration of legacy auth.json and for headless
        environments without an OS keystore).
        """

        if self._resolve():
            try:
                raw = self._keyring.get_password(_KEYRING_SERVICE, self._username)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 — best-effort fallback
                # Same as save(): mark broken so we don't keep retrying
                # keyring (and burning time on a locked backend) every read.
                self._available = False
                raw = None
            if raw:
                try:
                    self._last_backend = "keyring"
                    return json.loads(raw)
                except json.JSONDecodeError:
                    # corrupt entry — drop it and let the caller treat as
                    # unauthenticated rather than crash
                    with contextlib.suppress(Exception):
                        self._keyring.delete_password(_KEYRING_SERVICE, self._username)  # type: ignore[union-attr]
        # Fallback: plaintext file (migration or no-keyring envs).
        existing = _read_existing(self._fallback_path())
        self._last_backend = "plaintext" if existing else "unprobed"
        return existing or None

    def clear(self) -> None:
        """Remove the payload from both backends (idempotent)."""

        if self._resolve():
            with contextlib.suppress(Exception):
                self._keyring.delete_password(_KEYRING_SERVICE, self._username)  # type: ignore[union-attr]
        with contextlib.suppress(OSError):
            self._fallback_path().unlink()
        self._last_backend = "unprobed"

    def backend_label(self) -> str:
        """Last backend actually used (``"keyring"`` / ``"plaintext"`` / ``"unprobed"``)."""

        return self._last_backend


# Module-level singleton — writers/readers delegate to this.
_secure_store = _SecureStore()


def save_auth(
    token: str,
    *,
    cookie: str | None = None,
    refresh_token: str | None = None,
) -> Path:
    """Persist a token (+ optional cookie / refresh_token).

    Manual ``oed auth token <bearer>`` flow. Synthesizes a minimal MSAL cache
    so :func:`get_token` and :func:`auth_headers_from_storage` work uniformly
    for both manual and device-flow logins.

    Writes go through :class:`_SecureStore`, which prefers the OS keyring
    when reachable (macOS Keychain / Windows DPAPI / Linux SecretService)
    and falls back to a 0600 plaintext ``auth.json`` otherwise.
    """

    if not token:
        raise ValueError("token must be a non-empty string")
    path = auth_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _secure_store.load() or {}
    data: dict[str, Any] = dict(existing)
    data["msal_cache"] = _synth_msal_cache_json(token, refresh_token)
    data["created_at"] = existing.get("created_at", time.time())
    if cookie is not None:
        data["cookie"] = cookie
    _secure_store.save(data)
    return path


def save_msal_auth(
    cache: Any,
    *,
    cookie: str | None = None,
    allowlist: list[str] | None = None,
) -> Path:
    """Persist an MSAL :class:`SerializableTokenCache` (+ optional cookie / allowlist).

    Used by the device-flow path. ``cache`` is a ``msal.SerializableTokenCache``
    whose ``.serialize()`` returns the MSAL JSON string. Pass ``allowlist``
    to also persist the per-user service allow-list (typically fetched by
    :func:`_fetch_user_allowlist` right after token success); pass ``None``
    to leave the existing field untouched (legacy auth.json compat).

    Writes go through :class:`_SecureStore` (keyring primary, plaintext fallback).
    """

    path = auth_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _secure_store.load() or {}
    data: dict[str, Any] = dict(existing)
    if cache.has_state_changed:
        data["msal_cache"] = cache.serialize()
    if not data.get("msal_cache"):
        # Nothing to persist (cache was empty + no cookie change). Keep existing.
        return path
    # A fresh device-flow token just landed — refresh the login timestamp so
    # ``is_token_expired`` / ``oed auth status`` reflect *this* login rather
    # than the previous one. The old ``if "created_at" not in data`` guard
    # left the stale timestamp from a prior login in place, so a re-login on
    # an existing auth.json kept reporting ``expired: true`` even though the
    # new token was valid. See ``oed auth status`` / :func:`is_token_expired`.
    data["created_at"] = time.time()
    if cookie is not None:
        data["cookie"] = cookie
    if allowlist is not None:
        data["allowlist"] = allowlist
    _secure_store.save(data)
    return path


def load_auth() -> dict[str, Any] | None:
    """Return the parsed auth-store contents, or ``None`` if missing / corrupt.

    Reads from keyring when reachable; falls back to the plaintext file
    for migration of legacy auth.json or for environments without an OS
    keystore.
    """

    return _secure_store.load()


def clear_auth() -> bool:
    """Remove the stored credentials (keyring + plaintext). Idempotent.

    Returns True if any data was removed from either backend.
    """

    had_data = load_auth() is not None
    _secure_store.clear()
    return had_data


def get_token() -> str | None:
    """Return the active ``access_token`` from auth.json, or ``None``.

    If the cached access token is present but expired, attempts a silent
    refresh via MSAL ``acquire_token_silent`` (uses the cached refresh_token).
    On success the updated cache is persisted automatically.
    """

    data = load_auth()
    if not data:
        return None
    cache_json = data.get("msal_cache") or ""
    token = _extract_access_token(cache_json)
    if token:
        return token

    # access_token missing or expired — try silent refresh from MSAL cache
    refreshed = _try_silent_refresh(data)
    if refreshed:
        return refreshed
    return None


def _try_silent_refresh(stored: dict[str, Any]) -> str | None:
    """Attempt MSAL ``acquire_token_silent`` to refresh an expired access token.

    Reconstructs the ``PublicClientApplication`` with the persisted cache and
    calls ``acquire_token_silent``. If a new access token is obtained, the
    updated cache is written back to ``auth.json``.
    """

    cache_json = stored.get("msal_cache") or ""
    if not cache_json:
        return None

    cache = msal.SerializableTokenCache()
    with contextlib.suppress(Exception):
        cache.deserialize(cache_json)

    # Check if there's a refresh_token in the cache — no point trying without one.
    rt_entries = None
    with contextlib.suppress(Exception):
        rt_entries = list(cache.search("RefreshToken"))
    if not rt_entries:
        return None

    client_id = get_client_id()
    device_url = get_device_url()

    app = msal.PublicClientApplication(
        client_id=client_id,
        oidc_authority=device_url,  # non-Azure OIDC discovery
        token_cache=cache,
        instance_discovery=False,  # openEuler is not Azure; skip Azure domain check
    )

    accounts = app.get_accounts()
    if not accounts:
        return None

    with contextlib.suppress(Exception):
        result = app.acquire_token_silent(
            scopes=[],  # reserved scopes (openid/profile/offline_access) added by MSAL
            account=accounts[0],
        )
        if result and "access_token" in result:
            # Persist the refreshed cache back to disk.
            save_msal_auth(cache)
            return result["access_token"]
    return None


def get_cookie() -> str | None:
    """Return the stored ``cookie`` value, or ``None``."""

    data = load_auth()
    return data.get("cookie") if data else None


def is_token_expired(stored: dict[str, Any], *, max_age: int = TOKEN_MAX_AGE) -> bool:
    """Best-effort age check based on local ``created_at`` timestamp."""

    created_at = float(stored.get("created_at", 0))
    return (time.time() - created_at) > max_age


def auth_headers_from_storage() -> dict[str, str]:
    """Build ``Authorization`` / ``Cookie`` headers from env (priority) or auth.json."""

    token = os.environ.get("OED_TOKEN") or None
    cookie = os.environ.get("OED_COOKIE") or None
    if not token:
        stored = load_auth()
        if stored:
            token = token or get_token()
            cookie = cookie or stored.get("cookie")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    return headers


# ---------------------------------------------------------------------------
# Cookie capture from backend responses (unchanged contract)
# ---------------------------------------------------------------------------


def extract_set_cookie(headers: Any) -> str | None:
    """Return first ``Set-Cookie`` ``name=value`` from a header mapping."""

    candidates: list[str] = []
    if hasattr(headers, "items"):
        for key, value in headers.items():
            if str(key).lower() == "set-cookie" and value:
                candidates.append(str(value))
    elif hasattr(headers, "get_list"):
        candidates.extend(headers.get_list("set-cookie"))
    for raw in candidates:
        first = raw.split(";", 1)[0].strip()
        if "=" in first:
            return first
    return None


def update_auth_from_response_headers(headers: Any) -> bool:
    """Persist a rotated ``Set-Cookie`` from the backend into the auth store.

    Preserves the existing MSAL cache (and any refresh_token it carries) —
    only the ``cookie`` field is overwritten. Goes through :class:`_SecureStore`
    so the cookie lands in the same backend (keyring or plaintext) as the
    existing MSAL cache.
    """

    new_cookie = extract_set_cookie(headers)
    if not new_cookie:
        return False
    existing = _secure_store.load() or {}
    if existing.get("cookie") == new_cookie:
        return False
    existing["cookie"] = new_cookie
    existing.setdefault("created_at", time.time())
    _secure_store.save(existing)
    return True


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _token_fingerprint(token: str) -> str:
    """Return ``first3...last2`` for display. Never the full token."""

    if len(token) <= 6:
        return "***"
    return f"{token[:3]}...{token[-2:]}"


# ---------------------------------------------------------------------------
# Browser / display helpers (reused for verification_uri)
# ---------------------------------------------------------------------------


def _has_display() -> bool:
    """Linux: ``DISPLAY`` (X11) or ``WAYLAND_DISPLAY`` (Wayland). macOS/Win: True."""

    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # macOS / Windows are assumed to have a display; tests can override via
    # BROWSER=none if they need to.
    return True


def _try_open_browser(url: str) -> bool:
    """Open ``url`` in default browser. On failure, print JSON to stderr.

    Honors ``BROWSER=none|echo|off|print`` → skip webbrowser entirely.
    """

    browser_env = os.environ.get("BROWSER", "").strip().lower()
    if browser_env in {"none", "echo", "off", "print"}:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "stage": "browser_skipped",
                    "reason": f"BROWSER={browser_env}",
                    "login_url": url,
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return False

    if not _has_display():
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "stage": "no_display",
                    "platform": sys.platform,
                    "login_url": url,
                    "hint": (
                        "Headless Linux detected (no DISPLAY). Open this URL "
                        "in a browser on any device, then return here to "
                        "continue polling. Or press Ctrl-C and re-run with "
                        "`oed auth login --manual`."
                    ),
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return False

    try:
        opened = webbrowser.open(url)
    except webbrowser.Error as exc:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "stage": "browser_failed",
                    "error": str(exc),
                    "login_url": url,
                    "hint": (
                        "Could not auto-open the browser. Open this URL "
                        "manually, or run `oed auth login --manual`."
                    ),
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return False

    if not opened:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "stage": "browser_unavailable",
                    "login_url": url,
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Clipboard helper (RFC 8628 §3.3 user_code copy)
# ---------------------------------------------------------------------------


def _copy_to_clipboard(text: str) -> bool:
    """Copy ``text`` to the OS clipboard. Returns True on success.

    Tries in order: ``wl-copy`` (Wayland), ``xclip -sel c`` (X11), ``xsel``
    (X11 fallback), ``pbcopy`` (macOS), ``clip`` (Windows). Stdlib only.
    """

    candidates: list[list[str]] = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
        ["clip"],
    ]
    encoded = text.encode("utf-8")
    for cmd in candidates:
        try:
            subprocess.run(cmd, input=encoded, check=True, timeout=2)
            return True
        except (
            FileNotFoundError,
            subprocess.SubprocessError,
            subprocess.TimeoutExpired,
        ):
            continue
    return False


# ---------------------------------------------------------------------------
# Device-flow login (RFC 8628 via MSAL Python)
# ---------------------------------------------------------------------------


def device_login(
    *,
    scope: list[str] | None = None,
    open_browser: bool = True,
    copy_code: bool = True,
) -> dict[str, Any] | None:
    """Run RFC 8628 device authorization grant.

    Returns dict with ``access_token`` (and optional ``refresh_token``) on
    success; ``None`` on failure or cancellation. Prints stage JSON to stdout
    and human prompts to stderr. The caller is responsible for persisting the
    resulting MSAL token cache via :func:`save_msal_auth`.
    """

    client_id = get_client_id()
    device_url = get_device_url()

    cache = msal.SerializableTokenCache()
    existing = load_auth()
    if existing and existing.get("msal_cache"):
        with contextlib.suppress(Exception):
            cache.deserialize(existing["msal_cache"])

    app = msal.PublicClientApplication(
        client_id=client_id,
        oidc_authority=device_url,  # non-Azure OIDC discovery
        token_cache=cache,
        instance_discovery=False,  # openEuler is not Azure; skip Azure domain check
    )

    # If OIDC discovery didn't provide device_authorization_endpoint,
    # inject it from defaults.toml (oneid doesn't publish it in discovery).
    # MSAL stores it in two places: authority attribute AND client.configuration
    # dict (the latter is what initiate_device_flow actually reads).
    device_auth_url = _get_device_authorization_url()
    if device_auth_url:
        authority = getattr(app, "authority", None)
        if authority and not getattr(authority, "device_authorization_endpoint", None):
            authority.device_authorization_endpoint = device_auth_url
        client = getattr(app, "client", None)
        cfg = getattr(client, "configuration", None)
        if isinstance(cfg, dict) and not cfg.get("device_authorization_endpoint"):
            cfg["device_authorization_endpoint"] = device_auth_url

    flow = app.initiate_device_flow(scopes=scope if scope is not None else get_scopes())
    if not flow or "user_code" not in flow:
        err = (flow or {}).get("error_description") or (flow or {}).get("error") or "unknown"
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "stage": "device_flow_init_failed",
                    "error": str(err),
                    "hint": (
                        "openEuler usercenter may not yet support device "
                        "authorization grant. Try `oed auth login --manual` "
                        "or set OED_TOKEN=<bearer>."
                    ),
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return None

    user_code = flow["user_code"]
    verification_uri = flow.get("verification_uri", "")
    expires_in = int(flow.get("expires_in", 1800))
    interval = int(flow.get("interval", 5))

    click.echo(
        json.dumps(
            {
                "ok": True,
                "stage": "device_code_issued",
                "user_code": user_code,
                "verification_uri": verification_uri,
                "verification_uri_complete": flow.get("verification_uri_complete"),
                "expires_in": expires_in,
                "interval": interval,
            },
            ensure_ascii=False,
        )
    )

    # stderr human prompts
    click.echo(
        flow.get("message")
        or f"Visit {verification_uri} and enter code: {user_code}",
        err=True,
    )
    if copy_code and _copy_to_clipboard(user_code):
        click.echo("(user_code copied to clipboard)", err=True)

    # Best-effort auto-open the verification URL.
    if open_browser and verification_uri:
        _try_open_browser(verification_uri)

    # MSAL handles slow_down + anti-fast-poll + 5xx-keep-polling internally.
    # Its default exit_condition uses flow["expires_at"], set in
    # initiate_device_flow as ``time.time() + expires_in``.
    result = app.acquire_token_by_device_flow(flow)
    # Thread user_code through so _fetch_user_allowlist can send it as
    # ?user_code=... (oneid-specific, see _fetch_user_allowlist).
    return _finalize_device_result(result, cache, user_code=user_code)


def _summarize_token_response(result: dict[str, Any]) -> dict[str, Any]:
    """Project MSAL device-flow result to a minimal user-facing dict.

    Surfaces only the fields a user / agent cares about at login time:
    whether the server granted a token, what scopes survived round-trip,
    and when it expires. Token values stay in ``auth.json`` only — see
    ``oed auth status`` for a redacted fingerprint.
    """
    return {
        "ok": True,
        "stage": "token_received",
        "token_type": result.get("token_type"),
        "scope": result.get("scope"),
        "expires_in": result.get("expires_in"),
        "has_access_token": bool(result.get("access_token")),
        "has_refresh_token": bool(result.get("refresh_token")),
        "has_id_token": bool(result.get("id_token")),
    }


def _fetch_user_allowlist(
    access_token: str,
    user_code: str | None = None,
) -> list[str] | None:
    """GET the per-user CLI service allow-list from oneid.

    Called ONCE per login (right after /token returns) by
    :func:`_finalize_device_result`. The response is cached in
    ``auth.json`` and consulted locally on every subsequent CLI invocation —
    no per-call HTTP. ``access_token`` is sent as ``Authorization: Bearer``.

    ``user_code`` (when supplied) is sent as a ``user_code`` query param.
    This is a **oneid-specific deviation** from RFC 8628: oneid's
    ``/device/user-data`` test endpoint uses the public user-facing code
    (the ABCD-EFGH string shown in the browser) to look up the user, rather
    than the bearer token's ``sub`` claim. RFC 8628 §3.5 considers user_code
    a public display value (it can be shown to other humans at a shared
    device), so passing it over a TLS connection is acceptable, but the
    bearer token alone should normally suffice. Kept as a temporary
    accommodation for the test deployment; revisit when oneid aligns with
    RFC 8628 (drop the ``user_code`` param).

    Returns:
        ``list[str]`` of allowed service names on success.
        ``None`` on any failure (network / non-2xx / parse / missing field) —
        the caller treats ``None`` as "no allow-list available, don't block"
        (fail-open).

    Response shape tolerance:
        oneid's current envelope wraps the payload as
        ``{"msg": {...}, "code": 200, "data": {"data": "a,b,c"}}``
        (two layers of ``data``). The parser peels both layers and also
        accepts the flat shape ``{"data": "a,b,c"}`` in case the envelope
        is dropped in a future revision. Anything else fails open.

    The endpoint URL comes from ``defaults.toml [oauth].cli_user_data_url``
    with an ``OED_USER_DATA_URL`` env override (useful for fork builds).

    Each branch emits a one-line diagnostic to stderr (keyed by ``stage``)
    so an operator running ``oed auth login`` can see what the server
    returned without grepping the source. Stdout stays reserved for the
    success-JSON envelope — these prints never break ``| jq``.
    """
    url = os.environ.get("OED_USER_DATA_URL") or _get_user_data_url()
    if not url:
        click.echo(
            json.dumps(
                {"ok": True, "stage": "user_data_skipped", "reason": "no_user_data_url"},
                ensure_ascii=False,
            ),
            err=True,
        )
        return None
    # oneid /device/user-data expects ?user_code=<ABCD-EFGH> as a query param.
    # When ``user_code`` is None (manual flow path), we still call without it —
    # manual-login tokens are unlikely to be recognized by oneid's user-data
    # endpoint, but sending the call surfaces a server-side error rather than
    # silently skipping.
    params: dict[str, str] = {}
    if user_code:
        params["user_code"] = user_code
    try:
        resp = httpx.get(
            url,
            params=params or None,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Server returned 4xx/5xx — surface status + a short body preview so
        # an operator can tell apart auth (401/403) from missing endpoint
        # (404) from upstream errors (5xx) without re-running the curl.
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "stage": "user_data_fetch_failed",
                    "url": url,
                    "status": exc.response.status_code,
                    "reason": exc.response.reason_phrase,
                    "body_preview": exc.response.text[:512],
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return None
    except httpx.HTTPError:
        # Connect / read / DNS / timeout — no response object.
        click.echo(
            json.dumps(
                {"ok": False, "stage": "user_data_fetch_failed", "url": url},
                ensure_ascii=False,
            ),
            err=True,
        )
        return None

    raw_body = resp.text
    try:
        body = resp.json()
    except ValueError:
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "stage": "user_data_parse_failed",
                    "url": url,
                    "status": resp.status_code,
                    "body_preview": raw_body[:512],
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return None
    # oneid's /device/user-data wraps the payload in an envelope:
    #     {"msg": {...}, "code": 200, "data": {"data": "search,cve"}}
    # The CSV string lives at ``body.data.data`` (two levels). Tolerate the
    # flat shape ``{"data": "search,cve"}`` too in case the envelope is
    # dropped in a future revision. Anything else → None (fail-open).
    data: Any = None
    if isinstance(body, dict):
        outer = body.get("data")
        if isinstance(outer, str):
            data = outer  # flat shape
        elif isinstance(outer, dict):
            inner = outer.get("data")
            if isinstance(inner, str):
                data = inner  # enveloped shape
    if not isinstance(data, str):
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "stage": "user_data_unexpected_shape",
                    "url": url,
                    "status": resp.status_code,
                    "body": body,
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return None
    parsed = [s.strip() for s in data.split(",") if s.strip()]
    click.echo(
        json.dumps(
            {
                "ok": True,
                "stage": "user_data_fetched",
                "url": url,
                "status": resp.status_code,
                "raw_data_field": data,
                "parsed_allowlist": parsed,
                "count": len(parsed),
                "sent_user_code_param": bool(user_code),
                "response_envelope": "wrapped" if isinstance(body.get("data"), dict) else "flat",
            },
            ensure_ascii=False,
        ),
        err=True,
    )
    # Empty list is preserved (caller treats [] the same as None — fail-open).
    return parsed


def _finalize_device_result(
    result: dict[str, Any] | None,
    cache: Any,
    user_code: str | None = None,
) -> dict[str, Any] | None:
    """Process MSAL device-flow result: persist cache, emit stages, return dict.

    ``user_code`` (the ABCD-EFGH display string from the device flow response)
    is forwarded to :func:`_fetch_user_allowlist` as a query param because
    oneid's /device/user-data endpoint uses it for user lookup. See
    :func:`_fetch_user_allowlist` for the full note on this deviation from
    RFC 8628.
    """

    if not result:
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "stage": "device_code_timeout",
                    "hint": "Run `oed auth login` again.",
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return None

    if "access_token" in result:
        # Fetch per-user service allow-list from oneid (one-shot at login
        # success). Persist alongside the MSAL cache. ``None`` (network /
        # parse failure, or endpoint not configured) is a fail-open signal
        # — caller will not block any service.
        allowlist = _fetch_user_allowlist(result["access_token"], user_code=user_code)
        save_msal_auth(cache, allowlist=allowlist)
        summary = _summarize_token_response(result)
        summary["allowlist"] = allowlist
        click.echo(
            json.dumps(
                summary,
                ensure_ascii=False,
            )
        )
        return result

    err = result.get("error") or "unknown"
    desc = result.get("error_description") or ""
    click.echo(
        json.dumps(
            {
                "ok": False,
                "stage": "device_token_error",
                "error": str(err),
                "error_description": str(desc),
                "hint": "Run `oed auth login` again.",
            },
            ensure_ascii=False,
        ),
        err=True,
    )
    return None


# ---------------------------------------------------------------------------
# Manual login (TTY paste; unchanged behavior from v0.4)
# ---------------------------------------------------------------------------


def _is_tty() -> bool:
    """True when stdout is a real terminal (not piped / redirected)."""

    try:
        return os.isatty(1)
    except (ValueError, OSError):
        return False


def manual_login() -> dict[str, Any] | None:
    """Prompt for a token + cookie on stdin. Fail to ``UserError`` when not a TTY."""

    from .errors import UserError

    if not _is_tty():
        raise UserError(
            "`oed auth login --manual` requires an interactive terminal. "
            "Pipe mode should use `oed auth token <token>` instead.",
            kind="not_tty",
            hint=(
                "Use `oed auth token <token>` to set the token directly from "
                "browser DevTools (Network → request headers → token)."
            ),
        )

    click.echo("Manual login: paste credentials from your browser's DevTools.")
    click.echo("Network tab → request headers → look for `token` and `Cookie`.")
    token = click.prompt("token", hide_input=True)
    cookie = click.prompt("cookie (optional)", default="", hide_input=True)
    return {"token": token, "cookie": cookie or None}


# ---------------------------------------------------------------------------
# Internal helpers (file IO + MSAL cache extraction)
# ---------------------------------------------------------------------------


def _read_existing(path: Path) -> dict[str, Any]:
    """Read existing ``auth.json``; return empty dict if missing or corrupt."""

    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Corrupt file — wrong encoding / truncated / half-written. Drop it
        # rather than crash the login flow. Caller treats {} as
        # unauthenticated, then a fresh save() overwrites the bad file.
        with contextlib.suppress(OSError):
            path.unlink()
        return {}


def _atomic_write_json_plaintext(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``path`` atomically with 0600 perms.

    Plaintext fallback path used by :class:`_SecureStore` when ``keyring``
    is unreachable. Production callers go through
    :func:`save_auth` / :func:`save_msal_auth` / :func:`update_auth_from_response_headers`,
    not this helper directly.
    """

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(path)


def _synth_msal_cache_json(token: str, refresh_token: str | None) -> str:
    """Build a minimal MSAL cache JSON containing a single access_token.

    MSAL's ``AccessToken`` map is keyed by a composite string (type, home
    account, env, client, scope) and the value is the entry dict directly —
    no extra nesting. Used by :func:`save_auth` so manual
    ``oed auth token <bearer>`` produces a cache file readable by
    :func:`get_token` / :func:`auth_headers_from_storage`.

    Token values are written under the ``secret`` key to match the current
    MSAL :class:`SerializableTokenCache` schema (older MSAL used the
    map-specific ``access_token`` / ``refresh_token`` field names; the
    readers in :func:`_first_token_secret` accept both for back-compat).
    """

    expires_on = int(time.time()) + TOKEN_MAX_AGE
    cache_dict: dict[str, Any] = {
        "AccessToken": {
            "manual::oed-cli:": {
                "credential_type": "AccessToken",
                "secret": token,
                "token_type": "Bearer",
                "expires_on": expires_on,
                "extended_expires_on": expires_on,
            }
        },
        "Account": {},
        "IdToken": {},
        "AppMetadata": {},
    }
    if refresh_token:
        cache_dict["RefreshToken"] = {
            "manual::oed-cli:": {"credential_type": "RefreshToken", "secret": refresh_token}
        }
    return json.dumps(cache_dict)


def _first_token_secret(
    cache_json: str, map_name: str, legacy_field: str | None = None
) -> str | None:
    """Pull the freshest token value out of an MSAL cache ``map_name`` section.

    MSAL's :class:`SerializableTokenCache` stores the token value under the
    key ``secret`` in every entry of ``AccessToken`` / ``RefreshToken`` /
    ``IdToken`` maps (this is the current MSAL schema — older versions used
    the map-specific field name ``access_token`` / ``refresh_token`` /
    ``id_token``). Read ``secret`` first, then ``legacy_field`` for backward
    compat with caches / test fixtures written to the old schema.

    The cache accumulates one entry per (home_account, environment, client_id,
    scope) tuple — a re-login against a different environment (e.g. oneid test
    vs prod) leaves the old entry in place. Returning the *first* entry (dict
    insertion order) could therefore surface a stale token from the wrong
    environment. Pick the entry with the newest ``expires_on`` (fallback
    ``cached_at``) so the just-issued token wins; entries without a timestamp
    (legacy / manually synthesized caches) keep the original first-match
    behavior.
    """

    if not cache_json:
        return None
    try:
        cache = json.loads(cache_json)
    except (json.JSONDecodeError, TypeError):
        return None
    section = cache.get(map_name)
    if not isinstance(section, dict):
        return None

    def _token_of(entry: dict[str, Any]) -> str | None:
        token = entry.get("secret")
        if not isinstance(token, str) or not token:
            if legacy_field:
                token = entry.get(legacy_field)
            if not isinstance(token, str) or not token:
                return None
        return token

    def _sort_key(entry: dict[str, Any]) -> float:
        # MSAL writes ``expires_on`` / ``cached_at`` as Unix-second ints (or
        # numeric strings). Anything unparseable sorts as -inf so timestamped
        # entries always win; an all-untimestamped section degrades to taking
        # the first entry (``max`` is stable on ties → first wins).
        for field in ("expires_on", "cached_at"):
            val = entry.get(field)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return float("-inf")

    best_token: str | None = None
    best_key = float("-inf")
    for entry in section.values():
        if not isinstance(entry, dict):
            continue
        token = _token_of(entry)
        if token is None:
            continue
        key = _sort_key(entry)
        if best_token is None or key > best_key:
            best_token = token
            best_key = key
    return best_token


def _extract_access_token(cache_json: str) -> str | None:
    """Pull the freshest ``access_token`` out of an MSAL cache JSON blob."""

    return _first_token_secret(cache_json, "AccessToken", legacy_field="access_token")


def _extract_refresh_token(cache_json: str) -> str | None:
    """Pull the first ``refresh_token`` out of an MSAL cache JSON blob.

    Mirrors :func:`_extract_access_token` but reads the ``RefreshToken`` map.
    Used by ``oed auth status`` — the refresh_token never lives at the
    auth.json top level, only inside the serialized MSAL cache.
    """

    return _first_token_secret(cache_json, "RefreshToken", legacy_field="refresh_token")


def _decode_jwt_claims(jwt: str) -> dict[str, Any] | None:
    """Decode the payload claims of a JWT without verifying signature."""

    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(b64decode(payload.encode("ascii")).decode("utf-8"))
    except (IndexError, ValueError, TypeError):
        return None


def _extract_id_token_claims(cache_json: str) -> dict[str, Any] | None:
    """Decode ``id_token`` claims from the MSAL cache, or ``None`` if absent.

    Mirrors :func:`_extract_access_token` but reads the ``IdToken`` map MSAL
    populates when the token endpoint response carries an ``id_token``. When
    the IdP omits ``id_token`` (as oneid does unless OIDC id-token signing is
    enabled for the client), this map is empty and we return ``None``.
    """

    id_tok = _first_token_secret(cache_json, "IdToken", legacy_field="id_token")
    if not id_tok:
        return None
    return _decode_jwt_claims(id_tok)


def _msal_username() -> str | None:
    """Best-effort: read MSAL cache from disk, return ``username`` if any.

    Falls back to the ``id_token`` claims (``preferred_username`` / ``email``)
    when MSAL did not populate the ``Account`` map — which happens when the
    IdP returns an ``id_token`` but MSAL's account extraction is skipped for
    non-Azure authorities.
    """

    cache = msal.SerializableTokenCache()
    data = load_auth()
    if not (data and data.get("msal_cache")):
        return None
    with contextlib.suppress(Exception):
        cache.deserialize(data["msal_cache"])
    try:
        accounts = list(cache.search("Account"))
    except Exception:
        accounts = []
    if accounts:
        return accounts[0].get("username")
    # Fallback: pull a human-readable identifier straight out of the id_token.
    claims = _extract_id_token_claims(data.get("msal_cache") or "")
    if claims:
        return claims.get("preferred_username") or claims.get("email") or claims.get("name")
    return None


# ---------------------------------------------------------------------------
# Click sub-group
# ---------------------------------------------------------------------------


@click.group(name="auth")
def auth_group() -> None:
    """Manage openEuler authentication state.

    Default ``auth login`` uses RFC 8628 device authorization grant — no
    local HTTP server, works headless. Use ``--manual`` to paste a token
    from a terminal.
    """


@auth_group.command("status")
def auth_status() -> None:
    """Print login status as JSON. Token is redacted to a fingerprint."""

    stored = load_auth()
    if not stored:
        click.echo(
            json.dumps(
                {"ok": True, "logged_in": False},
                ensure_ascii=False,
            )
        )
        return

    token = get_token() or ""
    created_at = float(stored.get("created_at", 0))
    allowlist = stored.get("allowlist") if isinstance(stored, dict) else None
    if not isinstance(allowlist, list):
        allowlist = None
    payload = {
        "ok": True,
        "logged_in": bool(token),
        "token_present": bool(token),
        "token_fingerprint": _token_fingerprint(token) if token else "",
        "created_at": created_at,
        "expired": is_token_expired(stored),
        # None  → never fetched (legacy auth.json, or fetch failed at login)
        # []    → user explicitly cleared in oneid (fail-open in dispatcher)
        # [...] → per-user allow-list, dispatcher blocks any service_name
        #         not in the list.
        "allowlist": allowlist,
    }
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@auth_group.command("token")
@click.argument("token_value")
@click.option(
    "--cookie",
    "-c",
    default="",
    help="Optional cookie string (e.g. 'name1=value1; name2=value2').",
)
def auth_token_cmd(token_value: str, cookie: str) -> None:
    """Save a token (and optionally a cookie) to the local auth file."""

    path = save_auth(token_value, cookie=cookie or None)
    click.echo(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "has_cookie": bool(cookie),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@auth_group.command("logout")
def auth_logout_cmd() -> None:
    """Clear the stored auth.json (logout)."""

    cleared = clear_auth()
    click.echo(
        json.dumps(
            {
                "ok": True,
                "cleared": cleared,
                "path": str(auth_token_path()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run_login(manual: bool) -> None:
    """Public entry for the top-level ``oed login`` shortcut.

    Thin wrapper over :func:`_run_login` so :mod:`oed_cli.cli` can register
    a ``login`` command on the root group without reaching into private API.
    """

    _run_login(manual)


def _run_login(manual: bool) -> None:
    """Shared body for ``oed auth login`` and the top-level ``oed login`` shortcut.

    Device-flow login (default) or manual paste (``manual=True``). Both entry
    points funnel through here so the stage JSON, persistence, and exit codes
    stay identical regardless of which command name the user typed.
    """

    from .errors import UserError

    if manual:
        try:
            creds = manual_login()
        except UserError as exc:
            click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
            raise SystemExit(exc.code) from None
        if not creds:
            click.echo(
                json.dumps({"ok": False, "error": "manual_login_aborted"}, ensure_ascii=False),
                err=True,
            )
            raise SystemExit(1)
        token = creds.get("token")
        cookie = creds.get("cookie")
        refresh_token = None
        id_token = None
    else:
        # device_login() reads get_scopes() internally; pass nothing and let
        # it fall back to _BASE_SCOPES. Passing scope=[] would override the
        # default with an empty list — MSAL would then decorate it to just
        # the reserved 'openid profile offline_access' set, dropping email
        # and id_token from the wire request. Oneid would still accept the
        # flow, but the resulting token would have no id_token, no email
        # claim, and no refresh_token (offline_access comes from MSAL
        # decoration but the cached account metadata would be incomplete).
        result = device_login()
        if not result:
            click.echo(
                json.dumps({"ok": False, "error": "device_login_failed"}, ensure_ascii=False),
                err=True,
            )
            raise SystemExit(1)
        token = result.get("access_token")
        cookie = None
        refresh_token = result.get("refresh_token")
        id_token = result.get("id_token")

    if not token:
        click.echo(
            json.dumps({"ok": False, "error": "no_token_in_response"}, ensure_ascii=False),
            err=True,
        )
        raise SystemExit(1)

    # Both branches already persisted to auth.json; this is informational.
    path = auth_token_path()

    click.echo(
        json.dumps(
            {
                "ok": True,
                "stage": "saved",
                "path": str(path),
                "has_cookie": bool(cookie),
                "has_refresh_token": bool(refresh_token),
                "has_id_token": bool(id_token),
                "token_fingerprint": _token_fingerprint(token),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@auth_group.command("login")
@click.option(
    "--manual",
    "-m",
    is_flag=True,
    help="Paste token + cookie from terminal instead of using device-flow.",
)
def auth_login_cmd(manual: bool) -> None:
    """Device-flow login (default) or manual paste (``--manual``)."""

    _run_login(manual)


__all__ = [
    "TOKEN_MAX_AGE",
    "_copy_to_clipboard",
    "_fetch_user_allowlist",
    "_has_display",
    "_token_fingerprint",
    "_try_open_browser",
    "auth_group",
    "auth_headers_from_storage",
    "clear_auth",
    "device_login",
    "extract_set_cookie",
    "get_client_id",
    "get_cookie",
    "get_device_url",
    "get_token",
    "is_token_expired",
    "load_auth",
    "load_defaults",
    "manual_login",
    "save_auth",
    "save_msal_auth",
    "run_login",
    "update_auth_from_response_headers",
]


# ---------------------------------------------------------------------------
# AtomGit (``ag``) personal-access-token storage
# ---------------------------------------------------------------------------
#
# The AtomGit PAT is persisted through a *second* :class:`_SecureStore` instance
# namespaced by ``username="ag"`` (the oneid credential uses ``"default"``), so
# the two secrets never collide in the OS keystore and fall back to separate
# plaintext files (``tokens/ag.json`` vs ``auth.json``). Storage semantics are
# identical to the oneid credential: keyring (macOS Keychain / Windows DPAPI /
# Linux SecretService) preferred, 0600 plaintext fallback otherwise. The public
# API (``store_token`` / ``read_token`` / ``token_info`` / ``clear_token`` /
# ``token_path``) stays stable so :mod:`oed_cli.main` and
# :mod:`oed_cli.invoke` need no changes.

# Per-service AtomGit stores, keyed by ``service`` so a future general token
# store can share this code. ``"ag"`` is the only wired service today.
_ag_stores: dict[str, _SecureStore] = {}


def _ag_store(service: str = "ag") -> _SecureStore:
    """Return the namespaced :class:`_SecureStore` for ``service``."""

    store = _ag_stores.get(service)
    if store is None:
        store = _SecureStore(
            username=f"ag:{service}",
            fallback_path=lambda s=service: ag_token_path(s),
        )
        _ag_stores[service] = store
    return store


def token_path(service: str = "ag") -> Path:
    """Return the plaintext-fallback path for ``service``'s AtomGit token.

    The real secret prefers the OS keystore (see :class:`_SecureStore`); this
    path is only the 0600 fallback when keyring is unreachable. Exposed for
    diagnostics (``oed ag login --status``) and for ``oed cache`` isolation.
    """

    return ag_token_path(service)


def store_token(token: str, *, service: str = "ag") -> Path:
    """Persist ``token`` for ``service`` via :class:`_SecureStore`.

    Writes through the keyring when reachable (OS-encrypted), otherwise a
    0600 plaintext file. Returns the fallback path. Never raises on a
    keystore failure — it degrades to plaintext so ``oed ag login`` keeps
    working in headless / CI / Docker.
    """

    if not token:
        from .errors import UserError

        raise UserError("token must be a non-empty string", kind="missing_token")
    payload: dict[str, Any] = {
        "service": service,
        "access_token": token,
        "created_at": time.time(),
    }
    store = _ag_store(service)
    store.save(payload)
    return token_path(service)


def read_token(service: str = "ag") -> str | None:
    """Return the stored AtomGit token for ``service``, or ``None``.

    Any read failure (no token stored, corrupt entry, undecryptable blob)
    degrades silently to ``None`` so callers fall through to the
    missing-token path rather than crashing.
    """

    stored = _ag_store(service).load()
    if not isinstance(stored, dict):
        return None
    token = stored.get("access_token")
    return token if isinstance(token, str) else None


def token_backend(service: str = "ag") -> str:
    """The storage backend actually used for ``service``'s last write/read.

    Reflects the cached verdict from the most recent :meth:`_SecureStore.save`
    / :meth:`_SecureStore.load` (``"keyring"`` / ``"plaintext"`` / ``"unprobed"``)
    without re-probing. Use it right after :func:`store_token` to tell the user
    *where* their token landed — the OS credential manager (no on-disk file) or
    the 0600 plaintext fallback (the file at :func:`token_path`).
    """

    return _ag_store(service).backend_label()


def token_info(service: str = "ag") -> dict[str, Any] | None:
    """Return ``{encryption, created_at}`` metadata for ``service``'s token.

    ``encryption`` reports the backend actually used (``"keyring"`` or
    ``"plaintext"``), not a hand-rolled cipher marker. Returns ``None`` when
    no token is stored.
    """

    store = _ag_store(service)
    stored = store.load()
    if not isinstance(stored, dict):
        return None
    return {
        "encryption": store.backend_label(),
        "created_at": stored.get("created_at"),
    }


def clear_token(service: str = "ag") -> bool:
    """Delete ``service``'s stored token from both backends; return if set."""

    store = _ag_store(service)
    had_data = store.load() is not None
    store.clear()
    return had_data
