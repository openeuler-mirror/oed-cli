"""Unit tests for v0.2 dynamic dispatch (`oed <service> <method>`).

These tests run entirely offline by monkeypatching the discovery and HTTP
layers, so they need no gateway access.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER", "version": "1.0.0"},
    "servers": [{"url": "$APIG_GROUP_ENTRY_URL"}],
    "paths": {
        "/v1/cla": {
            "get": {
                "summary": "CLA verify",
                "operationId": "API_verifyCla",
                "responses": {},
                "x-apigateway-backend": {
                    "type": "HTTP",
                    "httpEndpoints": {
                        "scheme": "https",
                        "address": "software-pkg.openeuler.org",
                        "path": "/api/v1/cla",
                        "method": "GET",
                    },
                },
            }
        },
        "/v1/softwarepkg": {
            "get": {
                "summary": "list",
                "operationId": "API_listSoftwarePackages",
                "parameters": [
                    {"in": "query", "name": "phase", "schema": {"type": "string"}},
                    {
                        "in": "query",
                        "name": "page_num",
                        "schema": {"type": "integer"},
                    },
                    {
                        "in": "query",
                        "name": "count_per_page",
                        "schema": {"type": "integer"},
                    },
                ],
                "responses": {},
                "x-apigateway-backend": {
                    "type": "HTTP",
                    "httpEndpoints": {
                        "scheme": "https",
                        "address": "software-pkg.openeuler.org",
                        "path": "/api/v1/softwarepkg",
                        "method": "GET",
                    },
                },
            },
            "post": {
                "summary": "apply",
                "operationId": "API_applyNewSoftwarePackage",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": {},
                "x-apigateway-backend": {
                    "type": "HTTP",
                    "httpEndpoints": {
                        "scheme": "https",
                        "address": "software-pkg.openeuler.org",
                        "path": "/api/v1/softwarepkg",
                        "method": "POST",
                    },
                },
            },
        },
        "/v1/softwarepkg/{id}": {
            "get": {
                "summary": "get one",
                "operationId": "API_getSoftwarePackage",
                "parameters": [
                    {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}},
                    {"in": "query", "name": "language", "schema": {"type": "string"}},
                ],
                "responses": {},
                "x-apigateway-backend": {
                    "type": "HTTP",
                    "httpEndpoints": {
                        "scheme": "https",
                        "address": "software-pkg.openeuler.org",
                        "path": "/api/v1/softwarepkg/{id}",
                        "method": "GET",
                    },
                },
            }
        },
    },
}


SAMPLE_FEED = {
    "kind": "discovery#servicesListByCommunity",
    "communities": {
        "openeuler": [
            {
                "name": "openeuler/software-package-server",
                "service_name": "software-package-server",
                "community": "openeuler",
                "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
                "version": "1.0.0",
                "description": "",
                "base_url": "https://apig.osinfra.cn",
            }
        ]
    },
}


@pytest.fixture()
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture()
def patched(monkeypatch):
    """Stub out discovery + HTTP so dynamic.py thinks the gateway is local."""

    from oed_cli import dynamic as dyn

    services = [
        dyn.ServiceMeta.from_raw(s) for s in SAMPLE_FEED["communities"]["openeuler"]
    ]

    class FakeFeed:
        def __init__(self):
            self.services = services
            self.fetched_at = 0

    fake_feed = FakeFeed()

    monkeypatch.setattr(dyn, "fetch_discovery", lambda **_: fake_feed)
    monkeypatch.setattr(dyn, "fetch_service_spec", lambda svc, **_: SAMPLE_SPEC)

    # main.py binds fetch_service_spec by value at import time, so rebind it
    # here too — otherwise these tests only pass if main happens to be imported
    # after the dyn patches above (order-dependent when other test files import
    # main first).
    from oed_cli import main as main_mod

    monkeypatch.setattr(main_mod, "fetch_service_spec", lambda svc, **_: SAMPLE_SPEC)

    # Stub http request so we can verify URL building without going to network

    captured = {}

    def _fake_do_call(
        method, url, params=None, body=None, timeout=30.0, headers=None, user_agent=None
    ):
        class _R:
            status_code = 200
            content = b'{"code":"","msg":"","data":{"ok":true}}'
            text = content.decode()
            headers = {"content-type": "application/json"}

            def json(self):
                return {"code": "", "msg": "", "data": {"ok": True}}

        captured["method"] = method
        captured["url"] = url
        captured["params"] = params
        captured["body"] = body
        captured["headers"] = headers or {}
        captured["user_agent"] = user_agent
        return _R()

    # Replace get_request since invoke calls it
    from oed_cli import http as http_mod

    monkeypatch.setattr(http_mod, "get_request", _fake_do_call)
    monkeypatch.setattr(
        http_mod,
        "_is_waf_block",
        lambda text: False,
        raising=False,
    )
    return {
        "feed": fake_feed,
        "captured": captured,
    }


def _extract_json(text: str) -> str:
    for stream in (text, ""):
        start = stream.find("{") if "{" in stream else stream.find("[")
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(stream)):
            ch = stream[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return stream[start : i + 1]
    return ""


# ---------- dynamic.py (model + spec parsing) ----------


def test_collect_operations_parses_every_http_verb():
    from oed_cli.dynamic import collect_operations

    ops = collect_operations(SAMPLE_SPEC, "software-package-server")
    assert len(ops) == 4
    methods = {(o.http_method, o.path) for o in ops}
    assert ("GET", "/v1/cla") in methods
    assert ("GET", "/v1/softwarepkg/{id}") in methods
    assert ("POST", "/v1/softwarepkg") in methods


def test_backend_extraction():
    from oed_cli.dynamic import collect_operations

    ops = collect_operations(SAMPLE_SPEC, "x")
    op = next(o for o in ops if o.operation_id == "API_applyNewSoftwarePackage")
    assert op.backend.address == "software-pkg.openeuler.org"
    assert op.backend.method == "POST"
    assert op.backend.path == "/api/v1/softwarepkg"
    assert op.body_required is True


def test_collect_operations_without_backend_block():
    """Specs that omit ``x-apigateway-backend`` must still register their
    operations — the openEuler gateway republished ``cve`` / ``pkgcontrib``
    without the block and every command silently vanished."""

    from oed_cli.dynamic import collect_operations

    spec = {
        "openapi": "3.0.1",
        "paths": {
            "/cve-security-notice-server/securitynotice/getByCveId": {
                "get": {
                    "operationId": "getSecurityNoticeByCveId",
                    "parameters": [
                        {"name": "cveId", "in": "query", "required": True,
                         "schema": {"type": "string"}}
                    ],
                }
            }
        },
    }
    ops = collect_operations(spec, "cve", base_url="https://apig.osinfra.cn")
    assert len(ops) == 1
    op = ops[0]
    assert op.operation_id == "getSecurityNoticeByCveId"
    assert op.http_method == "GET"
    assert op.backend.method == "GET"  # taken from the spec verb
    assert op.backend.address == ""  # no declared upstream
    assert op.base_url == "https://apig.osinfra.cn"


def test_request_body_schema_resolves_refs():
    """POST bodies that use ``$ref: #/components/schemas/X`` must surface
    the resolved schema in ``Operation.body_schema`` so ``--help`` can
    tell an agent how to construct the ``--json`` body."""

    from oed_cli.dynamic import collect_operations

    spec = {
        "openapi": "3.0.1",
        "components": {
            "schemas": {
                "SearchCondition": {
                    "type": "object",
                    "required": ["keyword", "lang"],
                    "properties": {
                        "keyword": {"type": "string", "description": "search kw"},
                        "lang": {"type": "string", "description": "zh|en"},
                        "page": {"type": "integer", "default": 1},
                    },
                },
                "Pages": {"type": "object", "properties": {"size": {"type": "integer"}}},
            },
        },
        "paths": {
            "/search/multitimodal": {
                "post": {
                    "operationId": "multitimodalSearchDoc",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SearchCondition"}
                            }
                        },
                    },
                },
            },
            "/cve/findAll": {
                "post": {
                    "operationId": "findAllCVEDatabase",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "keyword": {"type": "string"},
                                        "pages": {"$ref": "#/components/schemas/Pages"},
                                    },
                                }
                            }
                        },
                    },
                },
            },
        },
    }
    ops = collect_operations(spec, "search", base_url="https://apig.osinfra.cn")
    by_op = {op.operation_id: op for op in ops}

    # Top-level $ref resolves to the schema body
    sc = by_op["multitimodalSearchDoc"]
    assert sc.body_required is True
    assert sc.body_schema["required"] == ["keyword", "lang"]
    assert set(sc.body_schema["properties"]) == {"keyword", "lang", "page"}
    # The resolved schema no longer carries the $ref
    assert "$ref" not in sc.body_schema

    # Nested $ref (inside a property) inlines too
    cve = by_op["findAllCVEDatabase"]
    assert cve.body_schema["properties"]["pages"]["type"] == "object"
    assert "size" in cve.body_schema["properties"]["pages"]["properties"]


def test_request_body_schema_cycles_do_not_blow_stack():
    """Cyclic ``$ref`` graphs (rare but possible) must terminate."""

    from oed_cli.dynamic import _inline_refs

    spec = {
        "components": {
            "schemas": {
                "A": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/components/schemas/B"}},
                },
                "B": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/components/schemas/A"}},
                },
            }
        }
    }
    out = _inline_refs({"$ref": "#/components/schemas/A"}, spec=spec)
    # First level inlined; the cycle is broken at A's re-entry
    assert out["type"] == "object"
    assert "next" in out["properties"]


def test_parse_json_arg_rejects_garbage():
    from oed_cli.dynamic import parse_json_arg
    from oed_cli.errors import UserError

    with pytest.raises(UserError):
        parse_json_arg("not-json", flag="params")


def test_coerce_param_types_handles_string_ints():
    from oed_cli.dynamic import coerce_param_types, collect_operations

    ops = collect_operations(SAMPLE_SPEC, "x")
    op = next(o for o in ops if o.operation_id == "API_listSoftwarePackages")
    out = coerce_param_types(op, {"phase": "accepted", "page_num": "3", "count_per_page": 5})
    assert out["phase"] == "accepted"
    assert out["page_num"] == 3  # str→int coercion
    assert out["count_per_page"] == 5  # already int, unchanged


# ---------- resolve_runtime_gateway ----------


def test_resolve_runtime_gateway_returns_base_url():
    from oed_cli.discovery import ServiceMeta
    from oed_cli.dynamic import resolve_runtime_gateway

    svc = ServiceMeta.from_raw({
        "name": "x/y", "service_name": "y", "community": "x",
        "title": "", "version": "", "description": "",
        "base_url": "https://apig.osinfra.cn",
    })
    assert resolve_runtime_gateway(svc) == "https://apig.osinfra.cn"


def test_resolve_runtime_gateway_empty_returns_empty():
    """No fallback: an empty ``base_url`` in the feed flows through verbatim."""

    from oed_cli.discovery import ServiceMeta
    from oed_cli.dynamic import resolve_runtime_gateway

    svc = ServiceMeta.from_raw({
        "name": "x/y", "service_name": "y", "community": "x",
        "title": "", "version": "", "description": "",
        "base_url": "",
    })
    assert resolve_runtime_gateway(svc) == ""


def test_resolve_runtime_gateway_placeholder_flows_through():
    """No fallback: the legacy ``$APIG_GROUP_ENTRY_URL`` placeholder passes through
    unmodified so the HTTP call fails loudly rather than silently rewrites."""

    from oed_cli.discovery import ServiceMeta
    from oed_cli.dynamic import resolve_runtime_gateway

    svc = ServiceMeta.from_raw({
        "name": "x/y", "service_name": "y", "community": "x",
        "title": "", "version": "", "description": "",
        "base_url": "$APIG_GROUP_ENTRY_URL",
    })
    assert resolve_runtime_gateway(svc) == "$APIG_GROUP_ENTRY_URL"


def test_resolve_runtime_gateway_strips_trailing_slash():
    """Trailing slash on ``base_url`` would produce ``//`` when concatenated with the path."""

    from oed_cli.discovery import ServiceMeta
    from oed_cli.dynamic import resolve_runtime_gateway

    svc = ServiceMeta.from_raw({
        "name": "x/y", "service_name": "y", "community": "x",
        "title": "", "version": "", "description": "",
        "base_url": "https://custom.example.com/",
    })
    resolved = resolve_runtime_gateway(svc)
    assert resolved == "https://custom.example.com"
    # Whitespace is also tolerated.
    svc_whitespace = ServiceMeta.from_raw({
        "name": "x/y", "service_name": "y", "community": "x",
        "title": "", "version": "", "description": "",
        "base_url": "  https://other.example.com  ",
    })
    assert resolve_runtime_gateway(svc_whitespace) == "https://other.example.com"


# ---------- invoke.py (call construction + response shape) ----------


def test_call_operation_url_built_with_filled_path(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "software-package-server",
                             base_url="https://apig.osinfra.cn")
    op = table["API_getSoftwarePackage"]

    payload = call_operation(op, params={"id": "42", "language": "zh_CN"})
    assert patched["captured"]["url"] == "https://apig.osinfra.cn/v1/softwarepkg/42"
    assert patched["captured"]["params"] == {"language": "zh_CN"}
    assert payload["ok"] is True
    assert payload["status"] == 200


def test_call_operation_uses_per_service_base_url(patched):
    """Per-service ``base_url`` flows from the discovery feed through
    ``collect_operations`` to ``call_operation`` — each service routes
    via its own gateway, not a shared constant."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(
        SAMPLE_SPEC, "software-package-server", base_url="https://custom.example.com"
    )
    op = table["API_getSoftwarePackage"]

    call_operation(op, params={"id": "7"})
    assert patched["captured"]["url"] == "https://custom.example.com/v1/softwarepkg/7"

    # And dry-run surfaces the same per-service URL in the JSON view.
    dry = call_operation(op, params={"id": "7"}, dry_run=True)
    assert dry["dry_run"] is True
    assert dry["request"]["url"] == "https://custom.example.com/v1/softwarepkg/7"


def test_call_operation_post_sends_body(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "software-package-server")
    op = table["API_applyNewSoftwarePackage"]
    body = {"pkg_name": "demo", "version": "1.0.0"}

    call_operation(op, body=body)
    assert patched["captured"]["method"] == "POST"
    assert patched["captured"]["body"] == body


def test_call_operation_forum_sends_api_headers(patched):
    """Forum (Discourse) requests carry the Api-Key / Api-Username placeholder
    headers on the real request and in the dry-run view — the gateway converts
    them, so oed must send them or the call is rejected."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "forum", base_url="https://apig.osinfra.cn")
    op = table["API_getSoftwarePackage"]

    call_operation(op, params={"id": "1"})
    assert patched["captured"]["headers"] == {
        "Api-Key": "oed-placeholder",
        "Api-Username": "oed-placeholder",
    }

    dry = call_operation(op, params={"id": "1"}, dry_run=True)
    assert dry["request"]["headers"]["Api-Key"] == "oed-placeholder"
    assert dry["request"]["headers"]["Api-Username"] == "oed-placeholder"


def test_call_operation_rejects_gateway_managed_params(patched):
    """Api-Key / Api-Username are injected by the gateway; passing them via
    params is refused with a hint instead of being silently forwarded."""

    from oed_cli.dynamic import operations_table
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "forum", base_url="https://apig.osinfra.cn")
    op = table["API_getSoftwarePackage"]

    with pytest.raises(UserError) as excinfo:
        call_operation(op, params={"id": "1", "Api-Key": "super-secret"})
    err = excinfo.value.to_dict()
    assert err["error"] == "gateway_managed_param"
    assert "injected by the gateway" in err["message"]


def test_call_operation_non_forum_omits_api_headers(patched):
    """Non-forum services get no extra auth headers."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(
        SAMPLE_SPEC, "software-package-server", base_url="https://apig.osinfra.cn"
    )
    op = table["API_getSoftwarePackage"]

    call_operation(op, params={"id": "1"})
    headers = patched["captured"]["headers"]
    assert "Api-Key" not in headers
    assert "Api-Username" not in headers


def test_call_operation_missing_path_param_raises_user_error(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "software-package-server")
    op = table["API_getSoftwarePackage"]
    with pytest.raises(UserError):
        call_operation(op)  # no id


def test_call_operation_strips_trailing_plus_in_path_template(patched):
    """Huawei APIG marks required path params with ``{name+}`` in the path
    template (``/t/{id+}``). The declared ``parameters[].name`` is plain
    ``id`` — the ``+`` is a spec-side marker. ``oed forum getTopic --id 19308``
    must therefore resolve the user's ``--id`` value into the rendered URL,
    not complain that ``id+`` is missing."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    spec = {
        "openapi": "3.0.1",
        "paths": {
            "/t/{id+}": {
                "get": {
                    "operationId": "getTopic",
                    "parameters": [
                        {"in": "path", "name": "id",
                         "required": True, "schema": {"type": "string"}},
                    ],
                }
            }
        },
    }
    table = operations_table(spec, "forum", base_url="https://apig.osinfra.cn")
    op = table["getTopic"]

    payload = call_operation(op, params={"id": "19308"})
    assert patched["captured"]["url"].endswith("/t/19308")
    assert payload["ok"] is True
    assert payload["status"] == 200

    # Missing-param error must use the clean name (what the user types),
    # not the APIG-side ``id+`` form.
    from oed_cli.errors import UserError
    try:
        call_operation(op)
    except UserError as exc:
        assert exc.to_dict()["message"] == "missing path params: ['id']"
    else:
        pytest.fail("expected UserError for missing path param")


# ---------- main.py dispatch ----------


def test_dispatch_unknown_service(patched, runner, monkeypatch):
    from oed_cli import dynamic as dyn
    from oed_cli import main as oed_main

    # Make discovery return empty so unknown service fails fast.
    class _Empty:
        services = []
        fetched_at = 0

    monkeypatch.setattr(dyn, "fetch_discovery", lambda **_: _Empty())
    code = oed_main.main(["does-not-exist", "API_x"])
    assert code == 4


def test_dispatch_unknown_method(patched, runner, monkeypatch):
    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(["software-package-server", "DOES_NOT_EXIST"])
    assert code == 4


def test_dispatch_invalid_json_flag(patched, runner, monkeypatch):
    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(
        ["software-package-server", "API_listSoftwarePackages", "--params", "not-json"]
    )
    assert code == 1


def test_service_level_help_with_method_rejected(patched, monkeypatch, capsys):
    """`oed <service> <method> --help` now resolves the operation first and
    shows its per-parameter flag cheatsheet. With an unknown method it
    surfaces ``method_not_found`` (exit 4) — no more ``too_many_positional``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(["software-package-server", "API_x", "--help"])
    captured = capsys.readouterr()
    assert code == 4
    text = captured.out + captured.err
    assert "method_not_found" in text


def test_spec_missing_returns_exit_4(patched, runner, monkeypatch, capsys):
    from oed_cli import main as oed_main
    from oed_cli.errors import NotFoundError

    def _raise(*a, **kw):
        raise NotFoundError(
            "missing",
            kind="spec_missing",
            hint=(
                "The discovery feed lists this service but its OpenAPI "
                "spec has not been published yet."
            ),
        )

    monkeypatch.setattr(oed_main, "fetch_service_spec", _raise)
    code = oed_main.main(["software-package-server", "API_x"])
    captured = capsys.readouterr()
    assert code == 4 and "spec_missing" in (captured.out + captured.err)


# ---------- describe_* helpers + main dispatch sweep ----------


def test_describe_helpers_and_main_dispatch(patched, monkeypatch, capsys):
    """Compact sweep covering describe_*, main per-param flag dispatch,
    and parse_json_arg / coerce_flag_value edge cases."""
    from oed_cli import main as oed_main
    from oed_cli.discovery import ServiceMeta
    from oed_cli.dynamic import (
        coerce_flag_value,
        collect_operations,
        operations_table,
        parse_json_arg,
        resolve_operation,
    )
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation, describe_operation_help, describe_service

    svc = ServiceMeta.from_raw(SAMPLE_FEED["communities"]["openeuler"][0])
    ops = collect_operations(SAMPLE_SPEC, svc.service_name)
    doc = describe_service(svc, ops)
    assert {op["operation_id"] for op in doc["operations"]} >= {"listSoftwarePackages", "verifyCla"}
    help_doc = describe_operation_help(
        operations_table(SAMPLE_SPEC, svc.service_name)["API_listSoftwarePackages"], svc
    )
    flags = {p["flag"] for p in help_doc["parameters"]}
    assert {"--phase", "--page-num", "--count-per-page"} <= flags
    assert help_doc["usage"] and help_doc["examples"]
    table = operations_table(SAMPLE_SPEC, svc.service_name)
    assert call_operation(table["API_verifyCla"], dry_run=True)["dry_run"] is True
    assert call_operation(table["API_verifyCla"], params={"nope": 1})["unused_params"] == ["nope"]

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    assert oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "42", "--language", "zh_CN",
    ]) == 0
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    assert patched["captured"]["params"] == {"language": "zh_CN"}
    assert oed_main.main([
        "software-package-server", "api_getsoftwarepackage", "--id", "1",
    ]) == 0
    assert resolve_operation(
        operations_table(SAMPLE_SPEC, "x"), "api_listsoftwarepackages"
    ).operation_id == "API_listSoftwarePackages"

    code = oed_main.main([
        "software-package-server", "API_listSoftwarePackages", "--bogus", "x",
    ])
    captured = capsys.readouterr()
    assert code == 1 and "unknown_flag" in (captured.out + captured.err)
    code = oed_main.main(["software-package-server", "API_getSoftwarePackage", "--help"])
    text = capsys.readouterr().out + capsys.readouterr().err
    assert code == 0 and "--id" in text and "language" in text

    assert parse_json_arg("", flag="p") is None
    assert parse_json_arg(None, flag="p") is None
    with pytest.raises(UserError):
        parse_json_arg("[1]", flag="p")
    assert coerce_flag_value({"schema": {"type": "integer"}}, "5") == 5
    assert coerce_flag_value({"schema": {"type": "number"}}, "1.5") == 1.5
    assert coerce_flag_value({"schema": {"type": "boolean"}}, "yes") is True
    assert coerce_flag_value({"schema": {"type": "boolean"}}, "false") is False
    assert parse_json_arg('{"k":1}', flag="p") == {"k": 1}
    assert coerce_flag_value({"schema": {"type": "integer"}}, "abc") == "abc"
    assert coerce_flag_value({"schema": {"type": "number"}}, "abc") == "abc"


def test_resolve_json_body_schema_edge_cases():
    """Non-dict media / schema blocks resolve to ``None`` instead of crashing."""

    from oed_cli.dynamic import _resolve_json_body_schema

    assert _resolve_json_body_schema({"content": {}}, SAMPLE_SPEC) is None
    assert _resolve_json_body_schema(
        {"content": {"application/json": {"schema": "not-a-dict"}}}, SAMPLE_SPEC
    ) is None


def test_operations_for_one_shot_helper(patched):
    """``operations_for`` resolves, fetches, and bakes the runtime base_url in."""

    from oed_cli.dynamic import operations_for

    svc, table, spec = operations_for("software-package-server")
    assert svc.service_name == "software-package-server"
    assert spec is SAMPLE_SPEC
    op = table["API_getSoftwarePackage"]
    assert op.base_url == "https://apig.osinfra.cn"


def test_body_field_summary_edge_cases():
    """Non-dict schema returns [] and non-dict properties are skipped."""

    from oed_cli.invoke import _body_field_summary

    assert _body_field_summary(None) == []
    out = _body_field_summary(
        {
            "properties": {
                "bad": "not-a-dict",
                "good": {"type": "string", "description": "a description"},
                "plain": {"type": "integer"},
            }
        }
    )
    assert [e["name"] for e in out] == ["good", "plain"]
    assert out[0]["description"] == "a description"
    assert "description" not in out[1]


def test_describe_operation_help_with_body(patched):
    """Operation help for a POST with a body exposes params + body field summary."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import describe_operation_help

    svc = patched["feed"].services[0]
    op = operations_table(SAMPLE_SPEC, svc.service_name)["API_applyNewSoftwarePackage"]
    doc = describe_operation_help(op, svc)
    assert doc["ok"] is True
    assert doc["usage"] and doc["examples"]
    assert doc["body_required"] is True
    assert doc["parameters"] == []
    assert doc["body_required_fields"] == []
    assert doc["body_schema"] == {"type": "object"}


def test_dispatch_dynamic_help_legacy_wrapper(patched, capsys):
    """Legacy ``_dispatch_dynamic_help`` resolves + collects and lists operations."""

    from oed_cli.main import _dispatch_dynamic_help

    code = _dispatch_dynamic_help("software-package-server", [])
    out = capsys.readouterr()
    assert code == 0 and "listSoftwarePackages" in (out.out + out.err)


# ---------- main() dispatch edges ----------


def test_main_dispatch_comprehensive(patched, monkeypatch, capsys):
    """Sweep main dispatch surfaces: reserved routes, --key=value, parse-error,
    dry-run short-circuit, service-only listing, and call_operation errors."""
    from oed_cli import http as http_mod
    from oed_cli import main as oed_main
    from oed_cli.errors import NetworkError

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    assert oed_main.main(["--version"]) == 0
    assert oed_main.main(["info"]) == 0
    oed_main.main(["software-package-server", "API_getSoftwarePackage", "--id=42"])
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    code = oed_main.main(["software-package-server", "API_listSoftwarePackages", "-x"])
    assert code == 1 and "unknown short flag" in (capsys.readouterr().err)
    code = oed_main.main(["software-package-server", "API_listSoftwarePackages",
                          "--params", "bad"])
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    code = oed_main.main(["software-package-server", "API_listSoftwarePackages",
                          "--params", "[]"])
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    code = oed_main.main(
        ["software-package-server", "API_applyNewSoftwarePackage", "--json", "bad"]
    )
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    oed_main.main(["software-package-server", "API_getSoftwarePackage",
                   "--params", '{"id":"42"}', "--language", "zh_CN"])
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    patched["captured"].clear()
    code = oed_main.main(["software-package-server", "API_getSoftwarePackage",
                          "--id", "1", "--dry-run"])
    assert code == 0 and "method" not in patched["captured"]
    code = oed_main.main(["software-package-server"])
    out = capsys.readouterr()
    assert code == 0 and "listSoftwarePackages" in (out.out + out.err)
    code = oed_main.main(["software-package-server", "--help"])
    out = capsys.readouterr()
    assert code == 0 and "API_listSoftwarePackages" in (out.out + out.err)
    # -- separator (covers _split_dispatch_argv -- passthrough)
    code = oed_main.main(["software-package-server", "API_getSoftwarePackage",
                          "--", "ignored"])
    out = capsys.readouterr()
    assert code == 1 and "missing_path_param" in (out.out + out.err)
    monkeypatch.setattr(http_mod, "get_request",
        lambda *a, **k: (_ for _ in ()).throw(NetworkError("x", kind="network_error")))
    code = oed_main.main(["software-package-server", "API_getSoftwarePackage", "--id", "1"])
    out = capsys.readouterr()
    assert code == 2 and "network_error" in (out.out + out.err)
    monkeypatch.setattr(
        http_mod,
        "get_request",
        lambda *a, **k: type(
            "R", (), {"status_code": 503, "text": "", "content": b"", "headers": {}}
        )(),
    )
    code = oed_main.main(["software-package-server", "API_getSoftwarePackage", "--id", "1"])
    assert code == 3


# ---------- --user-agent / OED_USER_AGENT override ----------


def test_resolve_user_agent_precedence(monkeypatch):
    """Explicit arg > ``OED_USER_AGENT`` env > bundled default; empty string falls through."""

    from oed_cli.http import DEFAULT_USER_AGENT, _resolve_user_agent

    monkeypatch.delenv("OED_USER_AGENT", raising=False)
    assert _resolve_user_agent(None) == DEFAULT_USER_AGENT
    assert _resolve_user_agent("") == DEFAULT_USER_AGENT
    monkeypatch.setenv("OED_USER_AGENT", "from-env/1.0")
    assert _resolve_user_agent(None) == "from-env/1.0"
    assert _resolve_user_agent("") == "from-env/1.0"
    assert _resolve_user_agent("from-arg/2.0") == "from-arg/2.0"


def test_headers_function_uses_env(monkeypatch):
    """``_headers()`` resolves User-Agent through the same precedence as ``_resolve_user_agent``."""

    from oed_cli.http import _headers

    monkeypatch.setenv("OED_USER_AGENT", "browser-like/1.0")
    h = _headers()
    assert h["User-Agent"] == "browser-like/1.0"
    h2 = _headers(user_agent="from-arg/2.0")
    assert h2["User-Agent"] == "from-arg/2.0"


def test_default_headers_omit_referer():
    """No default ``Referer`` — openEuler APIG rejects ``easysearch`` calls with 401
    when this header is present (verified 2026-07-28). Callers can still pass
    one explicitly via ``extra=`` if a future endpoint needs it."""

    from oed_cli.http import _headers

    h = _headers()
    assert "Referer" not in h
    assert h["User-Agent"].startswith("oed/")
    assert h["Accept"].startswith("application/json")
    assert "zh-CN" in h["Accept-Language"]

    h_with_extra = _headers(extra={"Referer": "https://example.com/"})
    assert h_with_extra["Referer"] == "https://example.com/"


def test_call_operation_forwards_user_agent(patched):
    """``call_operation(user_agent=...)`` propagates to the HTTP layer."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "software-package-server")
    op = table["API_getSoftwarePackage"]
    call_operation(op, params={"id": "1"}, user_agent="override/1.0")
    assert patched["captured"]["user_agent"] == "override/1.0"


def test_dispatch_user_agent_flag_flows_to_http(patched, monkeypatch, capsys):
    """End-to-end: ``--user-agent foo`` reaches ``get_request(user_agent=...)``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "1", "--user-agent", "browser-mock/9.9",
    ])
    assert code == 0
    assert patched["captured"]["user_agent"] == "browser-mock/9.9"


def test_dispatch_user_agent_env_only(patched, monkeypatch, capsys):
    """No --user-agent flag, only OED_USER_AGENT env → env value lands in the
    outbound User-Agent header (resolved downstream in the HTTP layer)."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.setenv("OED_USER_AGENT", "env-only/3.0")
    code = oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "1", "--dry-run",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert '"User-Agent": "env-only/3.0"' in out


def test_dispatch_user_agent_flag_overrides_env(patched, monkeypatch):
    """Flag wins over env when both are set."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.setenv("OED_USER_AGENT", "env/1.0")
    code = oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "1", "--user-agent=arg/2.0",
    ])
    assert code == 0
    assert patched["captured"]["user_agent"] == "arg/2.0"


def test_dispatch_user_agent_in_dry_run(patched, monkeypatch, capsys):
    """Dry-run surfaces the resolved User-Agent under ``request.headers``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "1", "--user-agent", "preview-ua/1.0", "--dry-run",
    ])
    assert code == 0
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["request"]["headers"]["User-Agent"] == "preview-ua/1.0"


def test_dispatch_user_agent_missing_value_errors(patched, monkeypatch, capsys):
    """``--user-agent`` with no following value fails fast with exit 1."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main([
        "software-package-server", "API_getSoftwarePackage",
        "--id", "1", "--user-agent",
    ])
    out = capsys.readouterr()
    assert code == 1 and "missing_flag_value" in (out.out + out.err)
