---
name: oed-cli
description: openEuler Infra command line (oed). Use when the task touches openEuler community services (software-package-server, easysearch, cve, discourse, mailman, etherpad, copr, app-meeting-server) and you need to discover, list, or call their REST endpoints without hand-rolling curl.
---

# oed-cli — openEuler Infra command line

`oed` is a Python CLI that auto-discovers openEuler community services from
the gateway's Discovery Service and exposes them as a single, JSON-first CLI.
It is designed for both humans and AI agents (Claude Code, Cursor, etc).

## When to use this skill

Invoke `oed` whenever the task requires any of:

- Listing registered openEuler community services.
- Inspecting a service's OpenAPI 3 schema (paths, parameters, request bodies).
- Making an HTTP call to one of those services from a script or agent loop.
- Verifying connectivity to the openEuler gateway.

Do **not** hand-write `curl` against `api-gateway.osinfra.cn` when `oed`
covers the call — `oed` handles the browser-style headers needed to bypass
the gateway's WAF automatically, and routes each call through the
service's `base_url` from the discovery feed verbatim (no fallback
constant or env-var override).

## Installation

```bash
pip install oed-cli
# or, isolated:
pipx install oed-cli
```

## Canonical example: query a CVE security notice

```bash
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
```

That's the entire user-facing surface for one real call:

- `<service>` = `cve` (resolved from the discovery feed)
- `<operation>` = `getSecurityNoticeByCveId` (resolved from the spec's
  `operationId`; the APIG-generated `API_` prefix is auto-stripped where
  present)
- `--cve-id <value>` = auto-derived from the spec's `query.cveId`
  parameter (camelCase → kebab-case). Both `--cve-id` and `--cveId` work;
  string→int coercion is applied to declared `integer` / `number` params.

The response is a single JSON object on stdout:

```json
{
  "ok": true,
  "status": 200,
  "url": "https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId",
  "response": {
    "code": 0,
    "result": [
      { "cveId": "CVE-2019-10082", "affectedProduct": "openEuler-20.03-LTS", ... }
    ]
  }
}
```

Pipe through `jq` to extract fields:

```bash
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 \
  | jq '.response.result[0] | {cveId, affectedProduct, affectedComponent}'
```

## Recommended agent workflow

1. **Probe first**: `oed info` — fail-fast on gateway outage; surface
   `services_total` and `cache.stale` as soon as possible.
2. **Discover services**:
   - `oed --help` — enumerated list of every discovered service with
     descriptions and a quickstart (best for first contact).
   - `oed services | jq -r '.[] | .service_name'` — machine-readable.
3. **List operations** for the service you care about:
   - `oed <service> --help` — JSON list of operationIds + aliases.
   - `oed <service>` — same data plus per-operation path / params detail.
4. **Inspect flags** for a specific operation:
   `oed <service> <operation> --help` — JSON with every auto-derived
   `--<flag>`, required markers, and a copy-pasteable usage line.
5. **Dry-run** (cheap, no network):
   `oed <service> <operation> --<flag> <value> --dry-run`.
6. **Call for real**:
   `oed <service> <operation> --<flag> <value>`.

If `service` is missing → exit `4`, `error="service_not_found"`.
If `method` is missing → exit `4`, `error="method_not_found"`.
If spec wasn't published upstream → exit `4`, `error="spec_missing"`.
If `--params` / `--json` is malformed → exit `1`, `error="invalid_json"`.
If an unknown flag is passed → exit `1`, `error="unknown_flag"` (hint lists
declared params).
If the upstream returns non-2xx → exit `3`.

## Command surface (v0.2)

```bash
oed --version                            # show version

# Reserved top-level commands
oed info                                 # gateway snapshot + cache stats
oed services                             # services in the current community
oed schema <service>                     # full OpenAPI 3.x doc
oed schema <service>.<method>            # one operation by operationId
oed cache {show,clear,refresh}
oed completion {bash,zsh,fish,powershell}

# Dynamic dispatch — the killer feature
oed <service>                            # list every operation
oed <service> --help                     # service-level cheatsheet
oed <service> <method> --help            # per-operation flag cheatsheet
oed <service> <method>                   # call the operation (use --help to see its flags)
oed <service> <method> --<flag> <value>  # pass a declared parameter as its own flag
oed <service> <method> --params '{...}'  # bulk JSON for query / path params
oed <service> <method> --json   '{...}'  # JSON request body (POST/PUT/PATCH)
oed <service> <method> --dry-run         # preview request, no network
```

### Per-parameter flag derivation

For every declared `query` / `path` parameter on an operation, `oed`
auto-derives a `--<kebab-case>` flag from the spec name:

| Spec name          | Flag                |
| ------------------ | ------------------- |
| `cveId`            | `--cve-id`          |
| `page_num`         | `--page-num`        |
| `count_per_page`   | `--count-per-page`  |
| `countPerPage`     | `--count-per-page`  |
| `id`               | `--id`              |

Both kebab-case (`--cve-id`) and the raw spec name (`--cveId`) are accepted.
Integer / number parameters get string→int / string→float coercion. `--params`
remains as an escape hatch; per-parameter flags override matching keys from
`--params`.

### APIG-generated `API_` prefix

Huawei APIG auto-appends `API_` to every operationId on some services
(currently `software-package-server`). `oed` strips it for display and
registers the prefix-less form as a lookup alias — both forms work, the
user-facing form is what shows up in `--help`, usage examples and call
output. The raw spec form is preserved under `operation_id_raw` for
traceability.

### URL construction

The dispatcher builds URLs as
`resolve_runtime_gateway(service) + spec.paths.<key>`. The resolver
reads `ServiceMeta.base_url` from the discovery feed verbatim (after
stripping whitespace and a trailing `/`) — there is no fallback constant
or env-var override; if the feed hands back an empty string or the legacy
`$APIG_GROUP_ENTRY_URL` placeholder, the HTTP call fails loudly. The
spec's `x-apigateway-backend.httpEndpoints.address` field is **ignored
for the host** — it often points to staging hosts (`*.test.osinfra.cn`)
that the gateway's CloudWAF blocks. Only `method` / `scheme` are read
from that block.

## JSON output contract

Every command writes a single JSON object (or array) to **stdout**. Errors
also go to stdout as JSON, with the documented exit codes
(`docs/cli-design.md` section 4.2):

| Code | Meaning                              |
| ---- | ------------------------------------ |
| 0    | Success                              |
| 1    | User error                           |
| 2    | Network / WAF                        |
| 3    | Upstream API error                   |
| 4    | Not found (service / method unknown) |

Logs / progress go to **stderr** — pipe stdout straight into `jq`.

## Data source

The full discovery surface is documented in
[`context/discoverAPI.md`](../../../context/discoverAPI.md). Use it as the
authoritative reference when `oed` output is ambiguous.

## Configuration

| Env var           | Purpose                                                |
| ----------------- | ------------------------------------------------------ |
| `OED_COMMUNITY`   | Default community (currently only `openeuler`)         |
| `OED_CACHE_DIR`   | Override the cache directory (default: platform XDG)   |
| `OED_GATEWAY_URL` | Override the gateway origin (v0.2+)                    |
| `NO_COLOR=1`      | Disable ANSI in output (already default in v0.1)       |

---

## DEVELOPING oed-cli

If you are an agent **modifying** oed-cli (not just calling it), this
section applies to you — the rest of this file is for *callers* of `oed`.

### Where to look first

- **[`CLAUDE.md`](../../../CLAUDE.md)** — project entry point. Read it at
  the start of every session. It has the directory map, the developer loop,
  agent red-lines, and a self-check checklist.
- **[`docs/cli-design.md`](../../../docs/cli-design.md)** — source of
  truth for design decisions, module boundaries, and the roadmap. **Update
  this file in the same commit as any code change** that affects modules,
  exit codes, or the roadmap.

### Dev loop (mandatory)

```bash
pip install -e ".[dev]"     # pytest + ruff
ruff check src tests        # lint (E,F,W,I,B,UP,SIM, line-length=100)
pytest -q                   # 41 tests, monkeypatch discovery, < 1s
oed --version               # confirm entry point works
```

A `.claude/hooks/post-edit-check.py` PostToolUse hook runs `ruff check
src tests && pytest -q` after **every** Edit/Write/MultiEdit on a `.py`
file. A failure is blocking — fix it before continuing. There is no
`--no-verify` escape hatch for Claude Code hooks.

### Where to put new code

| If you're adding…                       | Edit…                        |
| --------------------------------------- | ---------------------------- |
| a reserved command (`oed foo`)          | `src/oed_cli/cli.py`         |
| a top-level flag / dispatch rule        | `src/oed_cli/main.py`        |
| OpenAPI → Operation parsing             | `src/oed_cli/dynamic.py`     |
| a real HTTP call / output shape         | `src/oed_cli/invoke.py`      |
| HTTP client behaviour (retries, etc.)   | `src/oed_cli/http.py`        |
| a new exit code or error kind           | `src/oed_cli/errors.py`      |
| a test                                  | `tests/test_<area>.py`       |

Add a new `.py` file to `src/oed_cli/` only as a last resort — the seven
modules above have stable roles.

### Don'ts (see CLAUDE.md for the full list)

- ❌ Hardcode endpoints — read `base_url` from the resolved `ServiceMeta` via `resolve_runtime_gateway(service)`. No fallback constant, no env-var override.
- ❌ Add runtime dependencies without asking — add to `[project.optional-dependencies].dev`.
- ❌ Auto-commit. Report changes to the user; wait for the word "commit".
- ❌ Push to `master`, force-push, amend, or rewrite git history.
- ❌ Skip the hook by any means.