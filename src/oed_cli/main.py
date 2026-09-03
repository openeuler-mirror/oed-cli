"""Entry point for the ``oed`` console script.

This module implements the top-level dispatch:

* Reserved sub-commands (``info``, ``services``, ``schema``, ``cache``,
  ``completion``, ``--version``, ``--help`` …) go to the click tree in
  :mod:`oed_cli.cli`.
* Anything else is treated as ``oed <service> [<method>] [flags]`` and
  resolved dynamically via :mod:`oed_cli.dynamic`.

Dynamic dispatch is what makes ``oed`` interesting — a new service shows
up in the discovery feed, and ``oed <new-service> -- ...`` works on the
next TTL refresh without any code change.

Per-parameter flag surface
--------------------------

For each declared ``query`` / ``path`` parameter on an operation,
``oed`` exposes a dedicated ``--<kebab-case>`` flag — derived from the
spec name (``cveId`` → ``--cve-id``, ``page_num`` → ``--page-num``).
``oed <service> <operation> --help`` enumerates them all.

The legacy ``--params '{...}'`` JSON form is preserved as an escape
hatch: useful for rarely-used parameters and for piping bulk data.
Per-parameter flags and ``--params`` can be mixed; per-parameter flags
override matching keys from ``--params``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

import click

from .auth import clear_token, read_token, store_token, token_backend, token_info, token_path
from .cli import cli as click_cli
from .dynamic import (
    coerce_flag_value,
    coerce_param_types,
    collect_operations,
    fetch_service_spec,
    operations_table,
    param_flag_index,
    parse_json_arg,
    resolve_operation,
    resolve_runtime_gateway,
    resolve_service_by_name,
)
from .errors import NotFoundError, OedError
from .invoke import (
    GATEWAY_MANAGED_PARAMS,
    call_operation,
    describe_operation_help,
    describe_service,
)


def _resolve_auth() -> tuple[str | None, str | None]:
    """Resolve the runtime token + cookie from env (priority) or auth.json.

    Mirrors :func:`oed_cli.http._resolve_user_agent` precedence: explicit env
    vars (``OED_TOKEN`` / ``OED_COOKIE``) win over the stored auth file.
    Returns ``(None, None)`` when no credential is available.
    """

    from .auth import auth_headers_from_storage

    headers = auth_headers_from_storage()
    token = headers.get("Authorization")
    if token and token.startswith("Bearer "):
        token = token[len("Bearer "):]
    cookie = headers.get("Cookie")
    return token, cookie

# Tokens that always go through the click sub-tree, regardless of whether
# they happen to match a discovered service. Recognised single tokens:
RESERVED_FIRST_TOKENS: frozenset[str] = frozenset(
    {
        "info",
        "services",
        "schema",
        "cache",
        "auth",
        "completion",
        "help",
        "login",
        # the user typed only the binary with no args
        "",
        # passthrough flags
    }
)
# Click passes these verbatim when they're the first argv item
LEADING_FLAGS: frozenset[str] = frozenset({"-h", "--help", "-V", "--version"})

# Services that are always allowed, regardless of the per-user oneid
# allow-list. These are public / non-oneid-gated services the CLI must keep
# usable without a login: ``ag`` authenticates with its own PAT (AtomGit),
# the rest are open community services. Matched by ``ServiceMeta.service_name``
# (case-sensitive). See issue: allowlist 两来源（登录来源 + 默认放行）.
DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "search",
        "cve",
        "easysoftware",
        "mailman",
        "ag",
        "CI",
        "meeting"
    }
)

# Built-in control flags handled by the dispatcher itself. Everything
# else is treated as a candidate per-parameter flag, validated later
# against the resolved operation's declared parameters.
_VALUE_FLAGS: frozenset[str] = frozenset({"params", "json", "path", "user-agent"})
_BOOL_FLAGS: frozenset[str] = frozenset({"dry-run"})


def _looks_like_reserved(argv: Sequence[str]) -> bool:
    if not argv:
        return True
    head = argv[0]
    if head in LEADING_FLAGS:
        return True
    if head.startswith("-"):
        return True
    return head in RESERVED_FIRST_TOKENS


def _split_dispatch_argv(rest: list[str]) -> tuple[dict[str, str], set[str], bool, list[str]]:
    """First-pass split of argv (after ``service_name``) into:

    - ``raw_flags``: ``--key value`` (or ``--key=value``) pairs
    - ``bool_flags``: ``--key`` with no value (e.g. ``--dry-run``)
    - ``help_requested``: ``--help`` / ``-h`` was seen
    - ``positional``: non-flag arguments

    Unknown ``--<key>`` flags are kept in ``raw_flags`` so they can be
    matched against the resolved operation's declared parameters before
    being rejected.
    """

    raw_flags: dict[str, str] = {}
    bool_flags: set[str] = set()
    help_requested = False
    positional: list[str] = []

    while rest:
        tok = rest.pop(0)
        if tok == "--":
            positional.extend(rest)
            break
        if tok in ("-h", "--help"):
            help_requested = True
            continue
        if tok.startswith("--"):
            body = tok[2:]
            if "=" in body:
                k, v = body.split("=", 1)
                if k in _BOOL_FLAGS:
                    bool_flags.add(k)
                else:
                    raw_flags[k] = v
                continue
            if body in _VALUE_FLAGS:
                if not rest:
                    raise OedError(f"--{body} requires a value", kind="missing_flag_value")
                raw_flags[body] = rest.pop(0)
                continue
            if body in _BOOL_FLAGS:
                bool_flags.add(body)
                continue
            # Candidate per-parameter flag: consume the next token as its
            # value unless that token is itself a flag.
            if rest and not rest[0].startswith("-"):
                raw_flags[body] = rest.pop(0)
            else:
                bool_flags.add(body)
            continue
        if tok.startswith("-"):
            raise OedError(f"unknown short flag: {tok}", kind="unknown_flag")
        positional.append(tok)

    return raw_flags, bool_flags, help_requested, positional


def _merge_params(
    op,
    raw_flags: dict[str, str],
) -> tuple[dict, dict | None, int]:
    """Build the final params + body from ``--params`` JSON and per-param flags.

    Returns ``(params, body, exit_code)`` — exit_code is non-zero when a
    JSON parse error short-circuits the call.
    """

    params: dict = {}
    if "params" in raw_flags:
        try:
            parsed = parse_json_arg(raw_flags["params"], flag="params") or {}
        except OedError as exc:
            return {}, None, exc.code
        if not isinstance(parsed, dict):
            return {}, None, 1
        params.update(parsed)

    body = None
    if "json" in raw_flags:
        try:
            body = parse_json_arg(raw_flags["json"], flag="json")
        except OedError as exc:
            return params, None, exc.code

    # Per-parameter flags override matching keys from --params.
    declared: dict[str, dict] = param_flag_index(op)
    declared_names = {
        p["name"] for p in op.parameters if p.get("in") in {"query", "path"}
    }
    for flag_key, value in raw_flags.items():
        if flag_key in _VALUE_FLAGS or flag_key in _BOOL_FLAGS:
            continue
        if flag_key not in declared:
            if flag_key.lower() in {p.lower() for p in GATEWAY_MANAGED_PARAMS}:
                raise OedError(
                    f"--{flag_key} is injected by the gateway for forum calls",
                    kind="gateway_managed_param",
                    hint=(
                        "Do not pass Api-Key / Api-Username — oed auto-fills "
                        "the placeholder headers on every forum call and the "
                        "gateway converts them."
                    ),
                )
            raise OedError(
                f"unknown flag: --{flag_key}",
                kind="unknown_flag",
                hint=(
                    f"Declared parameters for {op.operation_id}: "
                    f"{sorted(declared_names) or '(none)'}. "
                    "Use `--params '{...}'` for arbitrary JSON."
                ),
            )
        param_def = declared[flag_key]
        params[param_def["name"]] = coerce_flag_value(param_def, value)

    # Final pass for any --params-derived values that need string→int coercion.
    params = coerce_param_types(op, params)
    return params, body, 0


def _check_allowlist(service_name: str) -> None:
    """Local per-user allow-list gate (no HTTP).

    Two sources of "allowed" (union, either suffices):

    1. :data:`DEFAULT_ALLOWLIST` — services the CLI always permits regardless
       of login state (public / non-oneid-gated services, incl. ``ag`` which
       authenticates with its own PAT via ``oed ag login``).
    2. The per-user ``allowlist`` field on ``auth.json`` (populated by
       :func:`oed_cli.auth._fetch_user_allowlist` at login success).

    Raises :class:`UserError` when the service is in neither source. Fail-open
    when no per-user allow-list is available (not logged in, or missing/empty
    field) — never blocks unconfigured / legacy state. The default list still
    applies in every case, so a default-listed service is never blocked.
    """

    from .auth import load_auth
    from .errors import UserError

    if service_name in DEFAULT_ALLOWLIST:
        return  # always allowed (public service / PAT-authed `ag`)

    stored = load_auth()
    if not stored:
        return  # not logged in → don't block (reserved cmds would also miss this)
    allowlist = stored.get("allowlist")
    if not isinstance(allowlist, list) or not allowlist:
        # None (never fetched), [] (user cleared), or non-list (malformed) →
        # all treated as fail-open. The "no allowlist configured" hint in
        # `oed auth status` makes this discoverable.
        return
    if service_name in allowlist:
        return
    raise UserError(
        f"service '{service_name}' is not in your CLI allow-list",
        kind="service_blocked_by_allowlist",
        hint=(
            f"Allowed services: {sorted(allowlist)}. "
            "Update your allow-list in oneid, then re-run `oed auth login` "
            "to refresh the local copy."
        ),
    )


_AG_LOGIN_INSTRUCTIONS = (
    "AtomGit login\n"
    "=============\n"
    "1. Open https://atomgit.com and sign in.\n"
    "2. Create a personal access token under Settings "
    "(https://atomgit.com/setting/token-classic/create)\n"
    "3. Paste the token below. It is stored encrypted for this Windows/Linux user "
    "and is never echoed back.\n"
)


def _ag_login_help() -> int:
    click.echo(
        json.dumps(
            {
                "ok": True,
                "help_for": "ag login",
                "usage": "oed ag login [--token <pat>] [--no-verify] [--status]",
                "flags": {
                    "--token": "provide the AtomGit personal access token non-interactively",
                    "--no-verify": "skip validating the token against AtomGit before storing",
                    "--status": "report whether a token is configured, without prompting",
                },
                "examples": [
                    "oed ag login",
                    "oed ag login --token <pat> --no-verify",
                    "oed ag login --status",
                    "oed ag logout",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _ag_login(raw_flags: dict[str, str], bool_flags: set[str], help_requested: bool) -> int:
    """Configure the AtomGit access token used by ``oed ag`` requests."""

    if help_requested:
        return _ag_login_help()

    if "status" in bool_flags:
        return _ag_status()

    token = raw_flags.pop("token", None)
    if token == "":
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "code": 1,
                    "error": "missing_flag_value",
                    "message": "--token requires a non-empty value",
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return 1

    if token is None:
        click.echo(_AG_LOGIN_INSTRUCTIONS, err=True)
        try:
            token = click.prompt("AtomGit personal access token", hide_input=True, err=True)
        except click.Abort:
            click.echo(
                json.dumps(
                    {"ok": False, "code": 1, "error": "aborted", "message": "login cancelled"},
                    ensure_ascii=False,
                ),
                err=True,
            )
            return 1

    token = token.strip()
    if not token:
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "code": 1,
                    "error": "missing_token",
                    "message": "no token provided",
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return 1

    if "no-verify" not in bool_flags:
        verify_code = _verify_ag_token(token)
        if verify_code != 0:
            return verify_code

    path = store_token(token)
    backend = token_backend("ag")
    if backend == "keyring":
        # The token landed in the OS credential manager (Keychain / DPAPI /
        # SecretService); the plaintext fallback file was deleted on the
        # successful keyring write. Pointing the user at that (now absent)
        # file path would be misleading — say where it actually is.
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "service": "ag",
                    "configured": True,
                    "backend": "keyring",
                    "note": "token saved to the OS credential manager; "
                    "no local file is written, and the token is never echoed back",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        # Plaintext fallback (headless / CI / Docker without an OS keystore):
        # the 0600 file at `path` is the real store, so its path is accurate.
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "service": "ag",
                    "configured": True,
                    "backend": "plaintext",
                    "token_path": str(path),
                    "note": "token stored locally (0600 file); it is never echoed back",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def _verify_ag_token(token: str) -> int:
    """Best-effort validation: hit an authenticated ``ag`` endpoint with ``token``.

    Returns ``0`` when the token looks valid (or validation is unavailable for
    the current spec); a nonzero exit code when the token was rejected or the
    check itself failed.
    """

    try:
        service = resolve_service_by_name("ag")
        spec = fetch_service_spec(service)
        base_url = resolve_runtime_gateway(service)
        table = operations_table(spec, service.service_name, base_url=base_url)
        op = resolve_operation(table, "listAuthenticatedUserIssues")
    except NotFoundError:
        return 0
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    try:
        result = call_operation(op, params={"access_token": token}, include_request=False)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    if not result.get("ok"):
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "code": 1,
                    "error": "invalid_token",
                    "message": "AtomGit rejected the token",
                    "hint": (
                        "Check the token is active, or create a new one at "
                        "AtomGit > Settings > Access Tokens, then run `oed ag login` again."
                    ),
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return 1
    return 0


def _ag_status() -> int:
    """Report whether a token is configured, without prompting or network access."""

    configured = read_token("ag") is not None
    meta = token_info("ag") or {}
    encryption = meta.get("encryption")
    status: dict[str, Any] = {
        "ok": True,
        "service": "ag",
        "configured": configured,
        "encryption": encryption,
        "created_at": meta.get("created_at"),
        "token": None,
    }
    # Show the on-disk path only when plaintext is the real backend; in keyring
    # mode that file does not exist (it is deleted on a successful keyring write),
    # so reporting it would mislead the user about where the token lives.
    if encryption != "keyring":
        status["token_path"] = str(token_path("ag"))
    click.echo(
        json.dumps(status, ensure_ascii=False, indent=2)
    )
    return 0


def _ag_logout(help_requested: bool) -> int:
    """Delete the stored ``ag`` token."""

    if help_requested:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "help_for": "ag logout",
                    "usage": "oed ag logout",
                    "examples": ["oed ag logout"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    was_set = clear_token("ag")
    click.echo(
        json.dumps(
            {"ok": True, "service": "ag", "configured": False, "was_set": was_set},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _dispatch_dynamic(argv: Sequence[str]) -> int:
    """Parse ``oed <service> [<method>] [flags]`` and run the resolved call."""

    if not argv:
        click.echo(_usage_dynamic(), err=True)
        return 1

    service_name = argv[0]
    rest = list(argv[1:])

    try:
        raw_flags, bool_flags, help_requested, positional = _split_dispatch_argv(rest)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    user_agent = raw_flags.pop("user-agent", None)
    method = positional[0] if positional else None

    # `ag login`/`ag logout` are credential-management commands that must not
    # reach the dispatch backend. They run before the allow-list gate (which
    # exempts `ag` anyway — ag is PAT-authenticated, not oneid-governed).
    if service_name == "ag" and method in ("login", "logout"):
        if len(positional) > 1:
            click.echo(
                json.dumps(
                    {
                        "ok": False,
                        "code": 1,
                        "error": "too_many_positional",
                        "message": "`oed ag login` takes flags, not extra positionals",
                        "extra_args": positional[1:],
                    },
                    ensure_ascii=False,
                ),
                err=True,
            )
            return 1
        if method == "login":
            return _ag_login(raw_flags, bool_flags, help_requested)
        return _ag_logout(help_requested)

    # Per-user allow-list gate (local check against auth.json, no HTTP).
    # Runs only for real service calls — after the ag credential-management
    # short-circuit above, which must stay outside the gate.
    try:
        _check_allowlist(service_name)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    try:
        service = resolve_service_by_name(service_name)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    try:
        spec = fetch_service_spec(service)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    base_url = resolve_runtime_gateway(service)
    ops = collect_operations(spec, service.service_name, base_url=base_url)

    if not method:
        # ``oed <service>`` alone → list every available operation.
        if help_requested:
            click.echo(
                json.dumps(_service_help_payload(service, ops), ensure_ascii=False, indent=2)
            )
            return 0
        click.echo(json.dumps(describe_service(service, ops), ensure_ascii=False, indent=2))
        return 0

    table = operations_table(spec, service.service_name, base_url=base_url)
    if method not in table:
        for key in table:
            if key.lower() == method.lower():
                method = key
                break

    try:
        op = resolve_operation(table, method)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    if help_requested:
        click.echo(
            json.dumps(describe_operation_help(op, service), ensure_ascii=False, indent=2)
        )
        return 0

    try:
        params, body, exit_code = _merge_params(op, raw_flags)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code
    if exit_code:
        # Re-parse the params we tried to load so the user sees the error.
        if "params" in raw_flags:
            try:
                parse_json_arg(raw_flags["params"], flag="params")
            except OedError as exc:
                click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
                return exc.code
        if "json" in raw_flags:
            try:
                parse_json_arg(raw_flags["json"], flag="json")
            except OedError as exc:
                click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
                return exc.code
        return exit_code

    dry_run = "dry-run" in bool_flags

    try:
        token, cookie = _resolve_auth()
        result = call_operation(
            op,
            params=params,
            body=body,
            dry_run=dry_run,
            include_request=True,
            user_agent=user_agent,
            token=token,
            cookie=cookie,
        )
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


def _usage_dynamic() -> str:
    return json.dumps(
        {
            "ok": False,
            "code": 1,
            "error": "missing_service",
            "message": "dynamic dispatch: pass <service> as the first argument",
            "examples": [
                "oed <service>                                  # list operations",
                "oed <service> <operation>                      # operation-level help",
                "oed <service> <operation> --<flag> <value>     # call with one flag",
                "oed <service> <operation> --params '{...}'     # bulk JSON params",
                "oed <service> <operation> --dry-run            # preview only",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _service_help_payload(service, ops: list) -> dict:
    """Payload for ``oed <service> --help``: enumerate operations + flag cheatsheet."""

    return {
        "ok": True,
        "help_for": service.name,
        "title": service.title,
        "operations": [op.display_name for op in ops],
        "operation_aliases": {
            op.display_name: op.operation_id
            for op in ops
            if op.display_name != op.operation_id
        } or None,
        "usage": (
            "oed <service> <operation> --<flag> <value>\n"
            "Each declared query / path parameter is exposed as its own "
            "--<kebab-case> flag. Use `oed <service> <operation> --help` "
            "to list them, or pass `--params '{...}'` for bulk JSON."
        ),
        "examples": [
            f"oed {service.service_name} {ops[0].display_name} --help",
            f"oed {service.service_name} {ops[0].display_name} --dry-run",
        ],
    }


def _dispatch_dynamic_help(service_name: str, positional: list[str]) -> int:
    """Legacy ``oed <service> --help`` entry point — kept for tests.

    New code path goes through :func:`_service_help_payload` directly
    inside :func:`_dispatch_dynamic`; this thin wrapper preserves the
    public function name so existing tests stay in sync.
    """

    if positional:
        click.echo(
            json.dumps(
                {
                    "ok": False,
                    "code": 1,
                    "error": "too_many_positional",
                    "message": "`oed <service> --help` lists operations; remove the extra argument",
                    "extra_args": positional,
                },
                ensure_ascii=False,
            ),
            err=True,
        )
        return 1

    try:
        service = resolve_service_by_name(service_name)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code
    try:
        spec = fetch_service_spec(service)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code

    base_url = resolve_runtime_gateway(service)
    ops = collect_operations(spec, service.service_name, base_url=base_url)
    click.echo(json.dumps(_service_help_payload(service, ops), ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level entry point. Returns a Unix-style exit code."""

    raw = list(argv if argv is not None else sys.argv[1:])
    try:
        if _looks_like_reserved(raw):
            try:
                click_cli.main(args=raw, standalone_mode=False)
            except click.exceptions.ClickException as exc:
                exc.show()
                return exc.exit_code
            except SystemExit as exc:
                return int(exc.code or 0)
            return 0
        return _dispatch_dynamic(raw)
    except OedError as exc:
        click.echo(json.dumps(exc.to_dict(), ensure_ascii=False), err=True)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
