"""Run a real :class:`Operation` against its backend.

This module is what the CLI dispatches to when a user runs
``oed <service> <method> --params ... --json ...``. It fills path/query
params on the OpenAPI ``paths`` template, attaches the JSON body, and
sends the request through the production gateway via
``oed_cli.http.get_request``.

URL construction note: the spec's ``x-apigateway-backend.httpEndpoints``
block is parsed for ``method`` / ``scheme`` but **not** trusted for the
host — those ``address`` values are routinely staging hosts
(``*.test.osinfra.cn``) that CloudWAF blocks. The runtime URL is built
as ``op.base_url + op.path``, where ``op.base_url`` was filled in by
:func:`oed_cli.dynamic.resolve_runtime_gateway` from the discovery feed
(``ServiceMeta.base_url``). ``op.path`` is the OpenAPI ``paths`` key
(e.g. ``/cve-security-notice-server/securitynotice/getByCveId``).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import httpx

from . import http as http_mod
from .auth import read_token
from .dynamic import Operation, coerce_param_types, to_flag
from .errors import NetworkError, UserError
from .http import _is_waf_block, _resolve_user_agent


def _fill_path(template: str, params: dict[str, Any]) -> tuple[str, list[str]]:
    """Substitute ``{name}`` placeholders in ``template`` from ``params``.

    Returns the rendered path and the list of placeholder names that were
    not provided, so the caller can raise a precise error.

    Huawei APIG marks required path params with a trailing ``+`` in the
    template (``/t/{id+}``); the ``+`` is a spec-side marker that never
    appears in the real request URL or in the declared
    ``parameters[].name``. Strip it so the lookup matches what the
    caller actually provided under ``--<flag>`` / ``--params``.
    """

    missing: list[str] = []
    parts: list[str] = []
    rest = template
    while True:
        head, sep, chunk = rest.partition("{")
        if not sep:
            parts.append(head)
            break
        name_end = chunk.find("}")
        if name_end == -1:
            parts.append(head + sep + chunk)
            break
        raw_name = chunk[:name_end]
        name = raw_name.rstrip("+")
        parts.append(head)
        if name in params:
            parts.append(str(params[name]))
        else:
            missing.append(name)
            parts.append("{" + raw_name + "}")
        rest = chunk[name_end + 1 :]
    return "".join(parts), missing


def _select_params(
    op: Operation, raw: dict[str, Any] | None
) -> tuple[dict, dict, list[str]]:
    """Split ``raw`` into (path_params, query_params, unused_keys)."""

    raw = raw or {}
    path_names = {p["name"] for p in op.path_params}
    query_names = {p.get("name") for p in op.query_params}
    declared = path_names | query_names

    path_params = {k: raw[k] for k in path_names if k in raw}
    query_params = {k: raw[k] for k in raw if k in query_names and k not in path_params}
    unused = sorted(k for k in raw if k not in declared)
    return path_params, query_params, unused


def _body_field_summary(
    schema: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Project a resolved body schema down to ``[{name, type, required}]``.

    Listing views use this so the per-operation summary stays compact
    while still telling an agent which fields exist and which are
    mandatory. The full schema is exposed separately under
    ``body_schema`` on ``oed <service> <op> --help``.
    """

    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    out: list[dict[str, Any]] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        entry: dict[str, Any] = {
            "name": name,
            "type": prop.get("type", "object"),
            "required": name in required,
        }
        if prop.get("description"):
            entry["description"] = prop["description"]
        out.append(entry)
    return out


def _render_response(resp: httpx.Response) -> Any:
    """Parse the JSON body of ``resp``, returning a wrapped string for non-JSON."""

    if not resp.content:
        return None
    try:
        return resp.json()
    except Exception:
        return {
            "_non_json_body": resp.text[:8192],
            "_content_type": resp.headers.get("content-type", ""),
        }


# Api-Key / Api-Username are Discourse auth headers that the gateway (APIG)
# injects during its header conversion. oed must neither send them nor let
# callers supply them — surfacing them makes agents hunt for credentials that
# are already handled upstream.
GATEWAY_MANAGED_PARAMS = frozenset({"Api-Key", "Api-Username"})


def _reject_gateway_managed_params(params: dict[str, Any] | None) -> None:
    """Refuse Api-Key / Api-Username supplied by the caller.

    ``forum`` authenticates at the gateway layer; the spec still declares
    these as required header params, so agents reading the raw spec keep
    trying to "fill" them. An explicit error is the strongest signal that
    the value is injected upstream and must not be supplied.
    """

    supplied = sorted(k for k in (params or {}) if k in GATEWAY_MANAGED_PARAMS)
    if not supplied:
        return
    raise UserError(
        f"{', '.join(supplied)} are injected by the gateway and must not be supplied",
        kind="gateway_managed_param",
        hint=(
            "Api-Key / Api-Username are auto-filled by oed as placeholder "
            "headers on every forum call, then converted by the gateway — "
            "do not supply them."
        ),
    )


def _inject_ag_token(op: Operation, query_params: dict[str, Any]) -> None:
    """Auto-fill the ``access_token`` query param for AtomGit operations.

    ``ag`` authenticates every request through the ``access_token`` query
    parameter declared on its spec (用户授权码). When the caller didn't
    pass one explicitly, fall back to the locally stored token from
    ``oed ag login``. A required token that isn't available anywhere
    raises a hint instead of failing inside the gateway; optional ones
    simply go without.
    """

    declared = next(
        (p for p in op.query_params if p.get("name") == "access_token"), None
    )
    if op.service_name != "ag" or declared is None or "access_token" in query_params:
        return
    token = read_token("ag")
    if token:
        query_params["access_token"] = token
    elif declared.get("required"):
        raise UserError(
            "this AtomGit operation requires an access_token",
            kind="ag_token_missing",
            hint="Store one with `oed ag login`, or pass `--access-token <pat>`.",
        )


def _reject_body_fields_via_params(op: Operation, unused: list[str]) -> None:
    """Turn a common misuse into an actionable error instead of an opaque 400.

    ``--params`` only carries query/path parameters. Body fields passed that
    way land in ``unused`` and the request goes out with ``body: null``, which
    the gateway rejects with a generic 400. When those keys line up with the
    operation's request-body schema, raise a hint that names the right flag.
    """

    if op.body_schema is None or not unused:
        return
    body_props = set(op.body_schema.get("properties") or {})
    body_keys = sorted(k for k in unused if k in body_props)
    if not body_keys:
        return
    raise UserError(
        f"{', '.join(body_keys)} match request-body fields for this operation "
        "but no body was sent",
        kind="body_fields_via_params",
        hint=(
            "`--params` only covers query/path parameters; request bodies go "
            f"through `--json '{{...}}'`. See `oed <service> {op.display_name} --help`."
        ),
    )


def call_operation(
    op: Operation,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    dry_run: bool = False,
    timeout: float = 30.0,
    include_request: bool = False,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Invoke ``op`` and return a structured JSON dict suitable for stdout.

    ``ok`` is ``True`` for any 2xx/3xx response. Non-success still surfaces
    the body and status under ``response`` / ``status``; the caller is
    responsible for the exit code.

    ``user_agent`` overrides the default User-Agent header; falls back to
    ``OED_USER_AGENT`` env, then the bundled default.
    """

    path_params, query_params, unused = _select_params(op, params)
    _reject_gateway_managed_params(params)
    query_params = coerce_param_types(op, query_params)
    if body is None:
        _reject_body_fields_via_params(op, unused)

    filled_path, missing = _fill_path(op.path, path_params)
    if missing:
        raise UserError(
            f"missing path params: {missing}",
            kind="missing_path_param",
            hint=f"Provide them via --params: {', '.join(missing)}",
        )

    query_params = {k: v for k, v in query_params.items() if v is not None}
    _inject_ag_token(op, query_params)
    url = f"{op.base_url}{filled_path}"

    # Discourse (forum) authenticates via Api-Key / Api-Username headers. oed
    # fills them with the gateway's expected placeholder — the APIG header
    # conversion swaps them for the real credentials at the edge, so oed MUST
    # send them or the gateway rejects the call. Callers never supply values
    # themselves; _reject_gateway_managed_params enforces that.
    api_headers: dict[str, str] = {}
    if op.service_name == "forum":
        api_headers = {"Api-Key": "oed-placeholder", "Api-Username": "oed-placeholder"}

    request_headers: dict[str, str] = {"User-Agent": _resolve_user_agent(user_agent)}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    # Mask credentials in any echoed request view (dry-run / include_request):
    # the real token still goes out on the wire, it just never round-trips
    # back to the terminal.
    request_view: dict[str, Any] = {
        "method": op.backend.method,
        "url": url,
        "query": {
            k: ("<stored>" if k == "access_token" else v) for k, v in query_params.items()
        },
        "headers": {**request_headers, **api_headers},
        "body": body,
    }

    if dry_run:
        out = {
            "ok": True,
            "dry_run": True,
            "service": op.service_name,
            "operation": op.display_name,
            "method": op.backend.method,
            "url": url,
            "request": request_view,
            "note": "request was not sent.",
        }
        if op.display_name != op.operation_id:
            out["operation_id_raw"] = op.operation_id
        return out

    resp = http_mod.get_request(
        op.backend.method,
        url,
        params=query_params if query_params else None,
        body=body,
        headers=api_headers,
        timeout=timeout,
        user_agent=user_agent,
    )

    if _is_waf_block(resp.text):
        raise NetworkError(
            f"Backend WAF blocked {op.backend.method} {url}",
            kind="waf_block",
            hint=(
                "Some APIG backends reject the bundled oed/x.y.z User-Agent. "
                "Retry with `--user-agent 'Mozilla/5.0 ...'` or set "
                "OED_USER_AGENT in the environment."
            ),
        )

    out = {
        "ok": 200 <= resp.status_code < 400,
        "service": op.service_name,
        "operation": op.display_name,
        "method": op.backend.method,
        "url": url,
        "status": resp.status_code,
        "response": _render_response(resp),
    }
    if include_request:
        out["request"] = request_view
    if op.display_name != op.operation_id:
        out["operation_id_raw"] = op.operation_id
    if unused:
        out["unused_params"] = unused
    return out


def describe_operation(op: Operation) -> dict[str, Any]:
    """Render a single :class:`Operation` for ``oed <service>`` listing."""

    rendered, _missing = _fill_path(op.path, {})
    out: dict[str, Any] = {
        "operation_id": op.display_name,
        "method": op.http_method,
        "path": op.path,
        "url": f"{op.base_url}{rendered}",
        "backend_declared": (
            f"{op.backend.scheme}://{op.backend.address}{op.backend.path}"
            if op.backend.address
            else None
        ),
        "summary": op.summary,
        "description": op.description,
        "path_params": [p["name"] for p in op.path_params],
        "query_params": [p["name"] for p in op.query_params],
        "has_body": op.body_schema is not None,
        "body_required": op.body_required,
    }
    if op.body_schema is not None:
        out["body_required_fields"] = _body_field_summary(op.body_schema)
    if op.display_name != op.operation_id:
        out["operation_id_raw"] = op.operation_id
    return out


def describe_service(service, ops: list) -> dict[str, Any]:
    """Bundle service metadata + operations into one JSON document."""

    return {
        "service": {
            "name": service.name,
            "service_name": service.service_name,
            "community": service.community,
            "title": service.title,
            "version": service.version,
            "base_url": service.base_url,
        },
        "operations": [describe_operation(op) for op in ops],
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


def describe_operation_help(op: Operation, service) -> dict[str, Any]:
    """Render ``oed <service> <operation> --help``: every per-parameter flag
    plus a copy-pasteable usage example."""

    rendered, _missing = _fill_path(op.path, {})
    params: list[dict[str, Any]] = []
    for p in op.parameters:
        if p.get("in") not in {"query", "path"}:
            continue
        flag_stem = to_flag(p["name"])
        schema = p.get("schema") or {}
        entry: dict[str, Any] = {
            "name": p["name"],
            "in": p["in"],
            "required": bool(p.get("required")),
            "flag": f"--{flag_stem}",
            "alt_flag": f"--{p['name']}",
            "type": schema.get("type", "string"),
        }
        if p.get("description"):
            entry["description"] = p["description"]
        params.append(entry)

    name = op.display_name
    out: dict[str, Any] = {
        "ok": True,
        "help_for": name,
        "service": {
            "name": service.name,
            "service_name": service.service_name,
            "title": service.title,
        },
        "method": op.http_method,
        "path": op.path,
        "url": f"{op.base_url}{rendered}",
        "summary": op.summary,
        "description": op.description,
        "has_body": op.body_schema is not None,
        "body_required": op.body_required,
        "parameters": params,
        "usage": _usage_example(op),
        "examples": _usage_examples(op),
    }
    if name != op.operation_id:
        out["operation_id_raw"] = op.operation_id
    if op.body_schema is not None:
        out["body_required_fields"] = _body_field_summary(op.body_schema)
        out["body_schema"] = op.body_schema
    return out


def _usage_example(op: Operation) -> str:
    """Single-line copy-pasteable invocation string."""

    parts = [f"oed <service> {op.display_name}"]
    for p in op.parameters:
        if p.get("in") not in {"query", "path"}:
            continue
        flag = f"--{to_flag(p['name'])} <value>"
        parts.append(flag if p.get("required") else f"[{flag}]")
    if op.body_schema is not None:
        parts.append("--json '{...}'")
    parts.append("[--dry-run]")
    return " ".join(parts)


def _usage_examples(op: Operation) -> list[str]:
    """A couple of ready-to-paste example commands.

    The ``--params`` example only makes sense when the operation declares
    query/path params. A body-only operation gets a ``--json`` example
    instead — otherwise agents get steered into sending body fields via
    ``--params``, which silently drops them (``--params`` never carries a
    request body).
    """

    name = op.display_name
    examples: list[str] = []
    has_params = any(p.get("in") in {"query", "path"} for p in op.parameters)
    required = [
        p
        for p in op.parameters
        if p.get("in") in {"query", "path"} and p.get("required")
    ]

    if required:
        cmd = f"oed <service> {name}"
        cmd += "".join(f" --{to_flag(p['name'])} <value>" for p in required)
        cmd += " [--dry-run]"
        examples.append(cmd)
    if op.body_schema is not None:
        examples.append(f"oed <service> {name} --json '{{\"...\"}}'")
    if has_params:
        examples.append(f"oed <service> {name} --params '{name}_PARAMS_JSON'")
    if not examples:
        examples.append(f"oed <service> {name} [--dry-run]")
    return examples


__all__ = [
    "call_operation",
    "describe_operation",
    "describe_operation_help",
    "describe_service",
]
