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


# A trimmed snapshot of the real ``pkgcontrib`` OpenAPI spec (the service that
# replaced the old ``software-package-server`` name). operationIds are the real
# wire values — no ``API_`` prefix, because the gateway stopped appending that
# prefix when the service was renamed. See ``PREFIXED_SPEC`` below for the
# dedicated coverage of the (now historical) ``API_`` prefix-stripping path.
SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
        "version": "1.0.0",
        "description": "openEuler 软件包引入管理服务",
    },
    "servers": [{"url": "https://apig.osinfra.cn"}],
    "paths": {
        "/v1/sig": {
            "get": {
                "summary": "获取 SIG 列表",
                "operationId": "listSigs",
                "responses": {"default": {"description": "SIG 列表"}},
            }
        },
        "/v1/softwarepkg": {
            "get": {
                "summary": "获取软件包列表",
                "operationId": "listSoftwarePackages",
                "parameters": [
                    {"in": "query", "name": "phase", "schema": {"type": "string"}},
                    {"in": "query", "name": "pkg_name", "schema": {"type": "string"}},
                    {"in": "query", "name": "importer", "schema": {"type": "string"}},
                    {"in": "query", "name": "platform", "schema": {"type": "string"}},
                    {"in": "query", "name": "last_id", "schema": {"type": "string"}},
                    {"in": "query", "name": "count", "schema": {"type": "string"}},
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
                "responses": {"default": {"description": "软件包列表"}},
            },
            "post": {
                "summary": "申请新软件包引入",
                "operationId": "applyNewSoftwarePackage",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "responses": {"default": {"description": "申请结果"}},
            },
        },
        "/v1/softwarepkg/{id}": {
            "get": {
                "summary": "获取软件包详情",
                "operationId": "getSoftwarePackage",
                "parameters": [
                    {
                        "in": "path",
                        "name": "id",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {"in": "query", "name": "language", "schema": {"type": "string"}},
                ],
                "responses": {"default": {"description": "软件包详情"}},
            }
        },
    },
}


# Separate fixture that carries the historical ``API_`` operationId prefix the
# gateway used to append (only on the old ``software-package-server`` name).
# Real ``pkgcontrib`` operationIds have no prefix; this exists solely to keep
# covering ``Operation.display_name`` prefix-stripping + the case-insensitive
# lookup alias in :func:`operations_table`, so that code path is not left
# untested now that the live spec no longer exercises it.
PREFIXED_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER", "version": "1.0.0"},
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
                ],
                "responses": {},
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
                    {
                        "in": "path",
                        "name": "id",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {},
            }
        },
    },
}


SAMPLE_FEED = {
    "kind": "discovery#servicesListByCommunity",
    "communities": {
        "openeuler": [
            {
                "name": "openeuler/pkgcontrib",
                "service_name": "pkgcontrib",
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
    return CliRunner()


@pytest.fixture()
def patched(monkeypatch):
    """Stub out discovery + HTTP so dynamic.py thinks the gateway is local."""

    from oed_cli import cli as cli_mod
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

    def _fake_feed_service(name, svcs):
        for s in svcs:
            if s.service_name == name:
                return s
        from oed_cli.errors import NotFoundError
        raise NotFoundError(f"unknown {name}")

    # ``main`` does ``from .dynamic import fetch_service_spec`` at module
    # load, so any test that runs AFTER another module has imported
    # ``oed_cli.main`` (e.g. ``tests/test_allowlist.py``) will see a
    # stale ``main.fetch_service_spec`` binding. Patch every known
    # re-export so the spec stays a fake regardless of import order.
    monkeypatch.setattr(cli_mod, "fetch_spec", lambda svc: SAMPLE_SPEC)
    from oed_cli import main as main_mod
    monkeypatch.setattr(main_mod, "fetch_service_spec", lambda svc, **_: SAMPLE_SPEC)
    # ``main`` also uses ``resolve_service_by_name`` (imported from
    # ``dynamic``) — its re-export also needs patching.
    monkeypatch.setattr(
        main_mod, "resolve_service_by_name",
        lambda name, **kw: _fake_feed_service(name, services),
    )

    # Stub http request so we can verify URL building without going to network

    captured = {}

    def _fake_do_call(
        method,
        url,
        params=None,
        body=None,
        timeout=30.0,
        headers=None,
        user_agent=None,
        token=None,
        cookie=None,
        service_name="",
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
        captured["token"] = token
        captured["cookie"] = cookie
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

    ops = collect_operations(SAMPLE_SPEC, "pkgcontrib")
    assert len(ops) == 4
    methods = {(o.http_method, o.path) for o in ops}
    assert ("GET", "/v1/sig") in methods
    assert ("GET", "/v1/softwarepkg/{id}") in methods
    assert ("POST", "/v1/softwarepkg") in methods


def test_backend_extraction():
    from oed_cli.dynamic import collect_operations

    # PREFIXED_SPEC carries an x-apigateway-backend block (the historical form);
    # real pkgcontrib specs omit it — see test_collect_operations_without_backend_block.
    ops = collect_operations(PREFIXED_SPEC, "x")
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
    op = next(o for o in ops if o.operation_id == "listSoftwarePackages")
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


# ---------- cache poisoning guards (issue #22) ----------


def test_discovery_cache_future_timestamp_is_rejected(monkeypatch, tmp_path):
    """A cache whose ``__oed_fetched_at`` is far in the future (poisoned to
    defeat the TTL — ``now - fetched_at`` would always be negative) is treated
    as corrupt and refetched."""

    import json

    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    from oed_cli import discovery as disc

    cache_path = disc._cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "__oed_fetched_at": disc.time.time() + 9999,
        "services": [{"name": "x/y", "service_name": "y", "community": "x",
                      "base_url": "http://evil.com"}],
    }), encoding="utf-8")

    # _read_cache sees a future timestamp → returns None (poisoned).
    assert disc._read_cache(cache_path) is None

    # And fetch_discovery refetches instead of trusting the poisoned entry.
    refetched = {}

    def _fake_remote(community=None):
        refetched["called"] = True
        return disc.DiscoveryFeed(
            fetched_at=disc.time.time(),
            raw={"services": []},
            services=[],
        )

    monkeypatch.setattr(disc, "_fetch_remote", _fake_remote)
    disc.fetch_discovery()
    assert refetched.get("called") is True


def test_spec_cache_future_timestamp_is_rejected(monkeypatch, tmp_path):
    """Same guard on the per-service spec cache (``dynamic._read_spec_cache``)."""

    import json

    from oed_cli import dynamic as dyn

    path = tmp_path / "specs" / "openeuler" / "cve.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "__oed_fetched_at": dyn.time.time() + 9999,
        "spec": {"openapi": "3.0.3", "paths": {}},
    }), encoding="utf-8")

    assert dyn._read_spec_cache(path) is None


# ---------- invoke.py (call construction + response shape) ----------


def test_call_operation_rejects_insecure_base_url_scheme(patched):
    """A non-https ``base_url`` (poisoned cache → plaintext token exfil,
    issue #22) is hard-rejected at request-build time, never sent on the wire."""

    from oed_cli.dynamic import operations_table
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "pkgcontrib", base_url="http://evil.com")
    op = table["getSoftwarePackage"]

    with pytest.raises(UserError) as exc_info:
        call_operation(op, params={"id": "1"}, token="secret-bearer")
    assert exc_info.value.kind == "insecure_base_url"
    # The request must never have reached the (stubbed) HTTP layer.
    assert "captured" not in patched or "url" not in patched.get("captured", {})
    # And the token must not appear in the error payload.
    assert "secret-bearer" not in str(exc_info.value.to_dict())


def test_call_operation_allows_schemeless_base_url(patched):
    """An empty / scheme-less ``base_url`` (legacy ``$APIG_GROUP_ENTRY_URL``
    placeholder, or empty feed value) is NOT rejected by the scheme guard —
    it flows through to HTTP time per the ``resolve_runtime_gateway``
    passthrough contract."""

    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "pkgcontrib", base_url="$APIG_GROUP_ENTRY_URL")
    op = table["getSoftwarePackage"]
    # No insecure_base_url raise — the stubbed HTTP layer handles it.
    call_operation(op, params={"id": "1"})
    assert patched["captured"]["url"] == "$APIG_GROUP_ENTRY_URL/v1/softwarepkg/1"


def test_call_operation_url_built_with_filled_path(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "pkgcontrib",
                             base_url="https://apig.osinfra.cn")
    op = table["getSoftwarePackage"]

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
        SAMPLE_SPEC, "pkgcontrib", base_url="https://custom.example.com"
    )
    op = table["getSoftwarePackage"]

    call_operation(op, params={"id": "7"})
    assert patched["captured"]["url"] == "https://custom.example.com/v1/softwarepkg/7"

    # And dry-run surfaces the same per-service URL in the JSON view.
    dry = call_operation(op, params={"id": "7"}, dry_run=True)
    assert dry["dry_run"] is True
    assert dry["request"]["url"] == "https://custom.example.com/v1/softwarepkg/7"


def test_call_operation_post_sends_body(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "pkgcontrib")
    op = table["applyNewSoftwarePackage"]
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
    op = table["getSoftwarePackage"]

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
    op = table["getSoftwarePackage"]

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
        SAMPLE_SPEC, "pkgcontrib", base_url="https://apig.osinfra.cn"
    )
    op = table["getSoftwarePackage"]

    call_operation(op, params={"id": "1"})
    headers = patched["captured"]["headers"]
    assert "Api-Key" not in headers
    assert "Api-Username" not in headers


def test_call_operation_missing_path_param_raises_user_error(patched):
    from oed_cli.dynamic import operations_table
    from oed_cli.errors import UserError
    from oed_cli.invoke import call_operation

    table = operations_table(SAMPLE_SPEC, "pkgcontrib")
    op = table["getSoftwarePackage"]
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
    code = oed_main.main(["does-not-exist", "nonexistentOp"])
    assert code == 4


def test_dispatch_unknown_method(patched, runner, monkeypatch):
    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(["pkgcontrib", "DOES_NOT_EXIST"])
    assert code == 4


def test_dispatch_invalid_json_flag(patched, runner, monkeypatch):
    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(
        ["pkgcontrib", "listSoftwarePackages", "--params", "not-json"]
    )
    assert code == 1


def test_service_level_help_with_method_rejected(patched, monkeypatch, capsys):
    """`oed <service> <method> --help` now resolves the operation first and
    shows its per-parameter flag cheatsheet. With an unknown method it
    surfaces ``method_not_found`` (exit 4) — no more ``too_many_positional``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(["pkgcontrib", "nonexistentOp", "--help"])
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
    code = oed_main.main(["pkgcontrib", "nonexistentOp"])
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
    assert {op["operation_id"] for op in doc["operations"]} >= {
        "listSoftwarePackages", "getSoftwarePackage", "listSigs",
    }
    help_doc = describe_operation_help(
        operations_table(SAMPLE_SPEC, svc.service_name)["listSoftwarePackages"], svc
    )
    flags = {p["flag"] for p in help_doc["parameters"]}
    assert {"--phase", "--page-num", "--count-per-page"} <= flags
    assert help_doc["usage"] and help_doc["examples"]
    table = operations_table(SAMPLE_SPEC, svc.service_name)
    assert call_operation(table["listSigs"], dry_run=True)["dry_run"] is True
    assert call_operation(table["listSigs"], params={"nope": 1})["unused_params"] == ["nope"]

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    assert oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
        "--id", "42", "--language", "zh_CN",
    ]) == 0
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    assert patched["captured"]["params"] == {"language": "zh_CN"}
    # Case-insensitive lookup still resolves regardless of prefix.
    assert resolve_operation(
        operations_table(SAMPLE_SPEC, "x"), "listsoftwarepackages"
    ).operation_id == "listSoftwarePackages"

    code = oed_main.main([
        "pkgcontrib", "listSoftwarePackages", "--bogus", "x",
    ])
    captured = capsys.readouterr()
    assert code == 1 and "unknown_flag" in (captured.out + captured.err)
    code = oed_main.main(["pkgcontrib", "getSoftwarePackage", "--help"])
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


def test_api_prefix_stripping_and_alias():
    """The gateway used to append an ``API_`` prefix to operationIds (only on
    the old ``software-package-server`` name). ``Operation.display_name``
    strips it, and ``operations_table`` keeps both the raw and stripped forms
    as lookup aliases. Real ``pkgcontrib`` operationIds have no prefix; this
    covers the historical path so it is not left untested."""

    from oed_cli.dynamic import operations_table, resolve_operation

    table = operations_table(PREFIXED_SPEC, "x")
    # Stripped form is the user-facing display name.
    assert table["API_getSoftwarePackage"].display_name == "getSoftwarePackage"
    # Both the raw (API_getSoftwarePackage) and stripped (getSoftwarePackage)
    # forms resolve to the same operation.
    resolved = resolve_operation(table, "getSoftwarePackage")
    assert resolved.operation_id == "API_getSoftwarePackage"
    assert (
        resolve_operation(table, "API_getSoftwarePackage").operation_id
        == "API_getSoftwarePackage"
    )
    # Case-insensitive lookup still works on the prefixed form.
    assert (
        resolve_operation(table, "api_getsoftwarepackage").operation_id
        == "API_getSoftwarePackage"
    )


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

    svc, table, spec = operations_for("pkgcontrib")
    assert svc.service_name == "pkgcontrib"
    assert spec is SAMPLE_SPEC
    op = table["getSoftwarePackage"]
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
    op = operations_table(SAMPLE_SPEC, svc.service_name)["applyNewSoftwarePackage"]
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

    code = _dispatch_dynamic_help("pkgcontrib", [])
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
    oed_main.main(["pkgcontrib", "getSoftwarePackage", "--id=42"])
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    code = oed_main.main(["pkgcontrib", "listSoftwarePackages", "-x"])
    assert code == 1 and "unknown short flag" in (capsys.readouterr().err)
    code = oed_main.main(["pkgcontrib", "listSoftwarePackages",
                          "--params", "bad"])
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    code = oed_main.main(["pkgcontrib", "listSoftwarePackages",
                          "--params", "[]"])
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    code = oed_main.main(
        ["pkgcontrib", "applyNewSoftwarePackage", "--json", "bad"]
    )
    out = capsys.readouterr()
    assert code == 1 and "invalid_json" in (out.out + out.err)
    oed_main.main(["pkgcontrib", "getSoftwarePackage",
                   "--params", '{"id":"42"}', "--language", "zh_CN"])
    assert patched["captured"]["url"].endswith("/v1/softwarepkg/42")
    patched["captured"].clear()
    code = oed_main.main(["pkgcontrib", "getSoftwarePackage",
                          "--id", "1", "--dry-run"])
    assert code == 0 and "method" not in patched["captured"]
    code = oed_main.main(["pkgcontrib"])
    out = capsys.readouterr()
    assert code == 0 and "listSoftwarePackages" in (out.out + out.err)
    code = oed_main.main(["pkgcontrib", "--help"])
    out = capsys.readouterr()
    assert code == 0 and "listSoftwarePackages" in (out.out + out.err)
    # -- separator (covers _split_dispatch_argv -- passthrough)
    code = oed_main.main(["pkgcontrib", "getSoftwarePackage",
                          "--", "ignored"])
    out = capsys.readouterr()
    assert code == 1 and "missing_path_param" in (out.out + out.err)
    monkeypatch.setattr(http_mod, "get_request",
        lambda *a, **k: (_ for _ in ()).throw(NetworkError("x", kind="network_error")))
    code = oed_main.main(["pkgcontrib", "getSoftwarePackage", "--id", "1"])
    out = capsys.readouterr()
    assert code == 2 and "network_error" in (out.out + out.err)
    monkeypatch.setattr(
        http_mod,
        "get_request",
        lambda *a, **k: type(
            "R", (), {"status_code": 503, "text": "", "content": b"", "headers": {}}
        )(),
    )
    code = oed_main.main(["pkgcontrib", "getSoftwarePackage", "--id", "1"])
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

    table = operations_table(SAMPLE_SPEC, "pkgcontrib")
    op = table["getSoftwarePackage"]
    call_operation(op, params={"id": "1"}, user_agent="override/1.0")
    assert patched["captured"]["user_agent"] == "override/1.0"


def test_dispatch_user_agent_flag_flows_to_http(patched, monkeypatch, capsys):
    """End-to-end: ``--user-agent foo`` reaches ``get_request(user_agent=...)``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
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
        "pkgcontrib", "getSoftwarePackage",
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
        "pkgcontrib", "getSoftwarePackage",
        "--id", "1", "--user-agent=arg/2.0",
    ])
    assert code == 0
    assert patched["captured"]["user_agent"] == "arg/2.0"


def test_dispatch_user_agent_in_dry_run(patched, monkeypatch, capsys):
    """Dry-run surfaces the resolved User-Agent under ``request.headers``."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
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
        "pkgcontrib", "getSoftwarePackage",
        "--id", "1", "--user-agent",
    ])
    out = capsys.readouterr()
    assert code == 1 and "missing_flag_value" in (out.out + out.err)


# ---------- v0.4 auth token injection ----------


def test_dispatch_includes_oed_token_env(patched, monkeypatch):
    """``OED_TOKEN`` env var is forwarded as Authorization: Bearer <token>."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.setenv("OED_TOKEN", "from-env-token")
    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage", "--id", "1",
    ])
    assert code == 0
    assert patched["captured"]["token"] == "from-env-token"


def test_dispatch_no_token_omits_authorization_header(patched, monkeypatch):
    """No env, no auth.json → token kwarg is None (caller does not add header)."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.delenv("OED_TOKEN", raising=False)
    monkeypatch.delenv("OED_COOKIE", raising=False)
    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage", "--id", "1",
    ])
    assert code == 0
    assert patched["captured"]["token"] is None
    assert patched["captured"]["cookie"] is None


def test_dispatch_dry_run_shows_authorization_header(patched, monkeypatch, capsys):
    """``--dry-run`` request-view includes the Authorization header (redacted)."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.setenv("OED_TOKEN", "dry-run-token")
    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
        "--id", "1", "--dry-run",
    ])
    assert code == 0
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    # The header is present but the live token is redacted in the echo view
    # so a shared log never leaks it — the real token still goes on the wire.
    assert payload["request"]["headers"]["Authorization"] == "Bearer <stored>"
    assert "dry-run-token" not in _json.dumps(payload)


def test_dispatch_reserves_auth_subgroup(patched, monkeypatch, capsys):
    """``oed auth --help`` routes to click tree, not interpreted as a service."""

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    code = oed_main.main(["auth", "--help"])
    assert code == 0
    out = capsys.readouterr().out
    assert "auth" in out
    # Help rendered by click, not by dynamic dispatch (no 'service:' payload).
    assert "service" not in out or "Commands" in out


def test_401_raises_upstream_error_with_unauthorized_kind(patched, monkeypatch):
    """A 401 response from the upstream raises UpstreamError(kind='unauthorized')."""

    from oed_cli import http as http_mod
    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")

    def _fake_401(*args, **kwargs):
        class _R:
            status_code = 401
            content = b'{"error":"unauthorized"}'
            text = content.decode()
            headers = {"content-type": "application/json"}

            def json(self):
                return {"error": "unauthorized"}

        return _R()

    monkeypatch.setattr(http_mod, "get_request", _fake_401)

    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage", "--id", "1",
    ])
    assert code == 3


def test_dispatch_captures_set_cookie_from_backend(monkeypatch, patched, tmp_path):
    """End-to-end: a Set-Cookie in the backend response updates local auth.json.

    Lives in test_dynamic.py because the ``patched`` fixture already patches
    ``dyn.fetch_service_spec`` BEFORE ``main`` imports it, which is the only
    way to keep ``main.fetch_service_spec`` pointing at a fake spec.
    """

    from oed_cli import auth as auth_mod
    from oed_cli import http as http_mod

    # Override the patched fixture's fake response: include a Set-Cookie header.
    captured: dict = {}

    def _fake_do_call_with_set_cookie(
        method,
        url,
        *,
        params=None,
        body=None,
        headers=None,
        timeout=30.0,
        user_agent=None,
        token=None,
        cookie=None,
        service_name="",
    ):
        captured["token"] = token
        captured["cookie"] = cookie

        class _R:
            status_code = 200
            content = b'{"ok":true}'
            text = '{"ok":true}'
            headers = {
                "Content-Type": "application/json",
                "Set-Cookie": "session=rotated-by-backend; Path=/; HttpOnly",
            }

            def json(self):
                return {"ok": True}

        return _R()

    monkeypatch.setattr(http_mod, "get_request", _fake_do_call_with_set_cookie)

    # Seed auth.json with a known token + cookie so the dispatcher has
    # something to send + to overwrite.
    monkeypatch.setenv("OED_CACHE_DIR", str(tmp_path))
    auth_mod.save_auth("seed-token", cookie="session=old")

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    from oed_cli import main as oed_main

    code = oed_main.main([
        "pkgcontrib", "listSoftwarePackages",
    ])
    assert code == 0, code

    # The dispatch sent our seeded Bearer token + cookie
    assert captured["token"] == "seed-token"
    assert captured["cookie"] == "session=old"

    # Backend rotated the cookie → auth.json now has the new one
    stored = auth_mod.load_auth()
    assert stored["cookie"] == "session=rotated-by-backend"
    # Token is stored inside the MSAL cache blob, not as a top-level key.
    assert auth_mod.get_token() == "seed-token"


def test_dispatch_dry_run_includes_x_oed_headers(patched, monkeypatch, capsys):
    """Authenticated dispatch emits plaintext x-oed-source / x-oed-target headers."""

    import json as json_mod

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.setenv("OED_TOKEN", "dispatch-tok")

    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
        "--id", "1", "--dry-run",
    ])
    assert code == 0, code

    payload = json_mod.loads(capsys.readouterr().out)
    headers = payload["request"]["headers"]

    # Authorization is present (redacted in the echo view — the real token still
    # goes on the wire) + plaintext identity/routing headers are emitted.
    assert headers["Authorization"] == "Bearer <stored>"
    assert "dispatch-tok" not in json_mod.dumps(payload)
    assert headers["x-oed-source"] == "oed-cli"
    assert headers["x-oed-target"] == "pkgcontrib"

    # No signed/encrypted header is sent anymore — the HMAC layer was removed.
    assert "x-secret-token" not in headers


def test_dispatch_omits_x_oed_headers_when_no_token(patched, monkeypatch, capsys):
    """Without a token, no x-oed-* headers are sent (public endpoints stay public)."""

    import json as _json

    from oed_cli import main as oed_main

    monkeypatch.setenv("OED_COMMUNITY", "openeuler")
    monkeypatch.delenv("OED_TOKEN", raising=False)
    monkeypatch.delenv("OED_COOKIE", raising=False)

    code = oed_main.main([
        "pkgcontrib", "getSoftwarePackage",
        "--id", "1", "--dry-run",
    ])
    assert code == 0, code

    headers = _json.loads(capsys.readouterr().out)["request"]["headers"]
    assert "Authorization" not in headers
    assert "x-oed-source" not in headers
    assert "x-oed-target" not in headers

