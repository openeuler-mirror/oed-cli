# oed-cli

> **oed** — one CLI for openEuler community services. Auto-discovered,
> JSON-first, AI-friendly. Built for humans and LLM agents.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/oed-cli)](https://pypi.org/project/oed-cli/)

`oed` doesn't ship a static list of commands. It reads the openEuler Infra
[Discovery Service](https://api-gateway.osinfra.cn/discovery/apis) at runtime
and builds its entire command surface dynamically. When a new service ships,
`oed` picks it up automatically — no upgrade required.
## Why oed?

`oed` exists to solve one specific problem: a CLI that talks to *many
evolving services* without multiplying that complexity. Shipping a
hand-written client per service per version is exactly the versioning
pressure that the
[Zylos API versioning research](https://zylos.ai/research/2026-05-20-api-versioning-strategies-multi-agent-platforms/)
warns against — and for AI agent consumers that pressure is acute: a
renamed field silently breaks a tool call, a new required parameter
crashes an otherwise healthy workflow. `oed` sidesteps the whole problem
by being one client that **discovers every service at runtime**, so the
only thing that has to be versioned is the gateway's OpenAPI spec itself.

- **Zero boilerplate.** No copy-pasted OpenAPI clients, no per-service
  SDKs, no `--data` to escape, no `User-Agent` headers to remember.
- **Runtime discovery.** The `oed cve --help` list you saw
  above is built from `https://api-gateway.osinfra.cn/discovery/apis` on
  every call. New services, new endpoints, and new schema fields show up
  without an `oed` upgrade. A 10-minute cache keeps CI bursts cheap;
  `oed cache refresh` forces an immediate re-fetch when you know the
  gateway just shipped.
- **AI-friendly output.** Single JSON object on stdout, deterministic
  exit codes (`0` success · `1` user · `2` network · `3` upstream · `4`
  not found). Logs and progress go to stderr so `| jq` is always safe.
- **Claude / Cursor ready.** Ships with
  [`.claude/skills/oed-cli/SKILL.md`](.claude/skills/oed-cli/SKILL.md)
  so agents know how to use it without a custom prompt.

---

## Install

```bash
pip install oed-cli
```

Or in an isolated environment (recommended for CI):

```bash
pipx install oed-cli
```

From source:

```bash
git clone https://atomgit.com/openeuler/oed-cli && cd oed-cli
pip install -e .
```

Verify:

```bash
oed --version       # → oed, version 0.1.5
```

---

## Quick start: install, then call

```bash
pip install oed-cli
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
```
#### (optional) Call an AtomGit operation — store a token once

Operations on the `ag` (AtomGit) service authenticate via an `access_token`
query parameter. Store a personal access token once, and every `oed ag ...`
call injects it automatically:

```bash
oed ag login
```

Then `oed ag listAuthenticatedUserIssues` just works — no per-call token
flag needed. Full details (`oed ag login --token <pat> [--no-verify]`,
`--status`, `oed ag logout`, how the token is stored, auto-injection rules)
are in the [AtomGit (`ag`) authentication](#atomgit-ag-authentication)
section below.


That's it. `oed` discovers the service from the gateway, pulls its OpenAPI
spec, derives `--cve-id` from the declared `query` parameter, fills WAF-safe
browser headers, and ships the request through the production gateway:

```json
{
  "ok": true,
  "status": 200,
  "url": "https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId",
  "response": {
    "code": 0,
    "result": [
      {
        "cveId": "CVE-2019-10082",
        "affectedProduct": "openEuler-20.03-LTS",
        "affectedComponent": "httpd-2.4.34-18",
        "announcementTime": "2020-05-13",
        ...
      }
    ]
  }
}
```

Output is plain JSON on stdout — pipe straight into `jq`:

```bash
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 \
  | jq '.response.result[0] | {cveId, affectedProduct, affectedComponent}'
```

```json
{
  "cveId": "CVE-2019-10082",
  "affectedProduct": "openEuler-20.03-LTS",
  "affectedComponent": "httpd-2.4.34-18"
}
```

### Want to look around first? (optional)

```bash
oed info      # gateway snapshot: services_total, cache status, community
oed services  # full service list — pick one to call next
oed <service> --help                       # list that service's operations
oed <service> <operation> --help           # see every flag for one operation
oed <service> <operation> --dry-run --…    # preview the request, no network
```

The first `oed <service> <operation>` call after install will pull a fresh
discovery feed + that service's OpenAPI spec; subsequent calls within
10 minutes reuse the local cache. Run `oed cache refresh` to force a
re-fetch when you know the gateway just shipped something new.

---

## "What flags does this operation take?" → `--help`

Don't guess. Each operation auto-derives its own flags from the OpenAPI
schema:

```bash
oed cve getSecurityNoticeByCveId --help
```

```json
{
  "help_for": "getSecurityNoticeByCveId",
  "operation_id_raw": "getSecurityNoticeByCveId",
  "method": "GET",
  "path": "/cve-security-notice-server/securitynotice/getByCveId",
  "url": "https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId",
  "parameters": [
    {
      "name": "cveId",
      "in": "query",
      "required": true,
      "flag": "--cve-id",
      "alt_flag": "--cveId",
      "type": "string",
      "description": "CVE ID"
    }
  ],
  "usage": "oed <service> getSecurityNoticeByCveId --cve-id <value> [--dry-run]"
}
```

Same idea at the service level — list every operation with one flag each:

```bash
oed cve --help | jq '.operations | length'
# → 37
```

---

## More examples

```bash
# Dry-run — preview the request without hitting the network
oed cve getSecurityNoticeByCveId --cve-id 1 --dry-run

# Path placeholder — `--id` is a path param, auto-substituted into the URL
oed software-package-server getSoftwarePackage --id 12345 --language zh_CN

# Integer / number flags auto-coerce from string
oed software-package-server listSoftwarePackages --page-num 1 --count-per-page 5

# POST with JSON body — Content-Type auto-set
oed software-package-server applyNewSoftwarePackage \
  --json '{"pkg_name":"demo","version":"1.0.0"}'

# Bulk JSON for scripts — `--params` is the escape hatch when you have many fields
oed cve getSecurityNoticeByCveId --params '{"cveId":"1"}'

# Raw OpenAPI spec, for debugging
oed schema cve | jq '.paths | keys'
```

---

## AtomGit (`ag`) authentication

AtomGit operations authenticate through the `access_token` query parameter
declared on their spec. Store a personal access token once, and every
`oed ag ...` call uses it automatically:

```bash
# Interactive (prompts for the token, never echoes it back)
oed ag login

# Non-interactive — good for CI / scripts
oed ag login --token <pat>

# Skip validating the token against AtomGit before storing
oed ag login --token <pat> --no-verify

# Just report whether a token is configured (no network, no prompt)
oed ag login --status

# Forget the stored token
oed ag logout
```

Token storage:

- Windows: encrypted at rest for the current user via DPAPI (no extra deps).
- Elsewhere: base64, which is documented obfuscation, **not** encryption.
- Lives under the cache dir (`tokens/ag.json`); `oed cache clear` never
  touches credentials.

Auto-injection on real calls:

- If the operation declares `access_token` and you don't pass one, the stored
  token is filled in automatically — `oed ag listAuthenticatedUserIssues` just
  works.
- An explicit `--access-token <pat>` always wins over the stored one.
- If the operation requires a token and none is available anywhere, you get a
  clear `ag_token_missing` error with a hint, instead of an opaque gateway 401.
- `--dry-run` and request echo views mask the token as `<stored>` — the real
  value only ever goes out on the wire.

---

## Local development

### Clone and install (editable)

```bash
git clone https://atomgit.com/openeuler/oed-cli && cd oed-cli
pip install -e ".[dev]"
```

`pip install -e .` makes source edits take effect on the next `oed`
invocation. Drop it with `pip uninstall oed-cli` when you're done.

### Run the tests (~0.3 s, fully offline)

```bash
python -m pytest -q
```

58 tests cover v0.1 + v0.2 dispatch, the operation-help cheatsheet,
per-parameter flag coercion, the `API_`-prefix alias, the
`resolve_runtime_gateway` no-fallback semantics, every exit code path, and
v0.4's `ag` token store (DPAPI/base64) + auto-injection.
They monkeypatch the discovery layer so no gateway access is needed.

### Smoke-test against the live gateway

```bash
# 1) Health check
oed info
# → {"ok": true, "services_total": 8, "community": "openeuler", ...}

# 2) Real CVE query (canonical end-to-end test)
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082

# 3) Dry-run to inspect URL + body without hitting the network
oed cve getSecurityNoticeByCveId --cve-id 1 --dry-run

# 4) Verify the canonical URL routing
oed cve getSecurityNoticeByCveId --dry-run --cve-id 1 \
  | jq '.url'
# → "https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId"
```

### Cache debugging

```bash
oed cache show      # path, size, age, TTL
oed cache refresh   # force re-fetch the discovery feed
oed cache clear     # delete the cache file
```

Cache lives at `~/.cache/oed-cli/` (XDG) or
`C:\Users\<you>\AppData\Local\oed-cli\cache\` on Windows. Override with
`OED_CACHE_DIR=...`. Per-service OpenAPI specs are cached next to it
under `specs/<community>/<service>.json` with the same 10-minute TTL.

### Keeping in sync with the gateway

`oed` does **not** warm up a fresh discovery feed on every invocation — only
the first command in a 10-minute window actually hits the gateway, the rest
read from `~/.cache/oed-cli/discovery.json`. That keeps CI scripts that run
dozens of `oed` calls from hammering the gateway, and it means `oed --help`
and `oed --version` never touch the network.

When the gateway adds a new service or operation, refresh the cache by hand:

```bash
# Fastest: drop the 10-minute TTL and re-pull the discovery feed
oed cache refresh

# Or, nuke and re-fetch from a known-clean state
oed cache clear && oed info

# Then confirm the new service is now visible
oed services | jq -r '.[] | .service_name'
```

The first call to a brand-new service will additionally pull its OpenAPI
spec into `specs/<community>/<service>.json` (also 10-minute TTL); after
that it's reused like any other spec.

**Why this isn't automatic.** openEuler Infra services are added on a
weekly-to-quarterly cadence via review, not minute-to-minute. Auto-refreshing
on every `oed` invocation would burn a network round-trip per CLI call for
no practical benefit. The TTL exists to absorb CI bursts, not to delay
visibility of new endpoints.

**Future** — `oed whatsnew` (planned) will diff the freshly-pulled feed
against the previous cache and print only what changed, so you don't have
to eyeball `oed services` output every week.

### Common errors

| Symptom                                            | Cause                          | Fix                                     |
| -------------------------------------------------- | ------------------------------ | --------------------------------------- |
| `ModuleNotFoundError: oed_cli`                     | not installed in env           | `pip install -e .`                      |
| `oed info` hangs or `waf_block` exit 2             | gateway unreachable / WAF      | confirm `curl https://api-gateway.osinfra.cn`; see `context/discoverAPI.md` §6 |
| Chinese output garbled on Windows                  | console codepage not UTF-8     | `chcp 65001`, or pipe `\| python`, or `PYTHONIOENCODING=utf-8 oed …` |
| `error="spec_missing"` (exit 4) on a known service | upstream hasn't published the spec yet | wait for the gateway-side OpenAPI yaml; nothing to do on the oed side |
| `error="ag_token_missing"` on an `ag` call | operation needs a token, none stored | `oed ag login` (or pass `--access-token <pat>`) |
| A `cve` call exits 2 (`waf_block`)      | spec points to a `.test.osinfra.cn` host | already handled — `oed` reads `base_url` from the discovery feed (no fallback) and ignores the spec's `x-apigateway-backend.httpEndpoints.address` for the host |

### Offline mode

`oed --help`, `oed info` (uses cached feed), `pytest`, and any
command against a service whose spec is in the local cache all work
without network. To run `oed` from source without installing:

```bash
python -m oed_cli --help
# or
python -c "from oed_cli.main import main; sys.argv = ['oed','--help']; main()"
```

---

## Documentation

- [Design doc](docs/cli-design.md) — architecture, command contract, exit
  codes, packaging, roadmap.
- [Local testing guide](docs/local-testing.md) — install, smoke test, every
  per-parameter flag demo.
- [Discovery API reference](context/discoverAPI.md) — the data source `oed`
  consumes, plus WAF caveats.

---

## Contributing

Issues and patches welcome on
[atomgit.com/openeuler/oed-cli](https://atomgit.com/openeuler/oed-cli).

Dev install:

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

## License

Apache-2.0. See [LICENSE](LICENSE).