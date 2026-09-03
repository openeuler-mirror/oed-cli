# oed-cli 设计文档

> openEuler development command line tool — AI-friendly、自动发现、pip 一键安装。
> 受 [`googleworkspace/cli (gws)`](https://github.com/googleworkspace/cli) 启发，本文对照 gws 的设计哲学。
> 设计日期：2026-07-25

---

## 1. 目标 & 解决的问题

`oed-cli` 是面向 openEuler Infra 网关下一组在线服务的统一命令行入口。开发、CI、LLM Agent 不再需要手写 `curl`、`--data`、`x-apigateway-*` 字段，就能调用每个服务的 OpenAPI 端点。

核心理念与 gws 一致：

1. **运行时不预置命令清单**。`oed` 启动时（或首次需要时）拉取一次 `discovery/apis`，按规格动态构建整张命令表。服务增删，网关规则变，CLI 自适应——下一版本无感。
2. **为 LLM 而生**。默认输出就是结构化 JSON、可 `jq` 流式管道输出；附带 Claude / Cursor Skill，让 Agent 无需特殊 prompt 也能像人一样调用。
3. **为人类可读**。`--help` 全程有、shell 补全全平台（bash/zsh/fish/PowerShell）有、`--dry-run` 永远可见地预览。

## 2. 对照表（oed vs gws）

| 维度 | gws | oed |
|---|---|---|
| 语言 | Rust（前端）+ Node 包 | Python 3.10+ |
| 安装 | npm / brew / 二进制 / cargo / nix | **pip install oed-cli** / pipx / uv |
| 二进制名 | `gws` | **`oed`** |
| 数据源 | Google Discovery Service | `https://api-gateway.osinfra.cn/discovery/apis` |
| 资源定位 | `<service> <resource> <method>`（多段嵌套） | `<service> <method>`（更扁平） |
| 鉴权 | OAuth + Service Account + 预签 token | **MVP 阶段透传**（网关侧策略由调用方注入） |
| 输出 | JSON | **JSON**（默认）+ `--human` 可选美化 |
| Agent Skill | `.agent/` + `.claude/` + `.gemini/` | `.claude/skills/oed-cli/` |

---

## 3. 命令形态

### 3.1 顶层命令树

```
oed --help
oed --version
oed info                                   # 网关连通性 + 当前社区/服务一览
oed services [--community X] [--refresh]   # 当前社区下服务清单（JSON）
oed schema <service>[.<method>]            # 打印 OpenAPI 3.0.3 规格
oed cache {show,clear,refresh}             # 缓存管理
oed auth {status,token,logout,login,login --manual}   # 鉴权管理 (v0.4)
oed login [--manual]                       # 顶层快捷方式 = oed auth login
oed <service>                              # 列服务的全部 operation
oed <service> <method> [--params JSON] [--json BODY] [--dry-run]
# 未来: --page-all / --human (v0.4, 未开始), shell completion (v1.0, 未开始)
```

`<service>` 命中已注册服务后，整条三段命令进入动态分发通道；保留命令与发现结果冲突时优先保留命令。

### 3.2 调用示例

```bash
# 看网关状态
oed info
# [stdout]: {"ok": true, "gateway": "https://api-gateway.osinfra.cn", "community": "openeuler",
#            "services_total": 8, "cache": {"ttl_seconds": 600, "stale": false, ...}}

# 列出 openEuler 社区下所有服务
oed services | jq '[.[] | {name, title, version}]'

# 看 service 的全部 operation（动态；不依赖 click 注册子命令）
oed software-package-server

# 单接口的请求/响应 schema
oed schema software-package-server.listSoftwarePackages

# 真实调用：列出 phase=accepted 的前 5 条软件包
oed software-package-server listSoftwarePackages \
  --phase accepted --page-num 1 --count-per-page 5

# 提交软件包申请
oed software-package-server applyNewSoftwarePackage \
  --json '{"pkg_name":"demo","version":"1.0.0","community":"openEuler"}'

# 预览（不发请求，把构造的请求晒出来）
oed software-package-server listSoftwarePackages \
  --phase accepted --dry-run

# 流式分页，NDJSON 输出（v0.4 计划中，未实现）
oed software-package-server listSoftwarePackages \
  --count 100 --page-all | jq -c '.'
```

> **v0.3.0 已实现**：`oed <service> [<method>] [--params ...] [--json ...] [--dry-run]` 全部可用；每个声明参数自动展开为 `--<kebab-case>` 强类型 flag（如 `cveId` → `--cve-id`），operation 级 `--help`，APIG `API_` 前缀自动剥离并保留为别名。**0.3.0 新增**：
> - **oneid RFC 8628 device flow** 登录（`oed login` / `oed auth login`），无需本地 HTTP server，headless / SSH / CI 可用，客户端 `client_id` 经 `OED_APP_ID` 可覆盖；
> - **OS-native credential storage**（`_SecureStore`）：macOS Keychain / Windows DPAPI / Linux SecretService 可达时直接走 keystore，否则 `auth.json` 0600 fallback；`keyring>=24` 由 `pip install oed-cli` 自动拉；
> - **Per-user service allow-list**：设备流登录后一次性拉 `/oneid/oidc/device/user-data`，存 `auth.json.allowlist`，后续每次 dispatch 只查本地，缺/空/类型异常 fail-open，非空命中外 `kind="service_blocked_by_allowlist"`；
> - **HMAC 签名头移除**：CLI 二进制分发的对称密钥无法保密，`x-secret-token` HMAC 改为明文 `x-oed-source: oed-cli` + `x-oed-target: <service>` 身份/路由头；gateway custom-auth 直接从 Bearer JWT 解 username + 角色，不再依赖 CLI 端加密；
> - **spec fixture 对齐生产**：`software-package-server` → `pkgcontrib`（在线改名），operationId 不再带 `API_` 前缀，`test_dynamic` 全面对齐；
> - **omapi 走生产域**：`omapi.osinfra.cn` 替换 `omapi.test.osinfra.cn`（omapi 已上线生产）。

### 3.3 参数约定（已实现）

| Flag / 形式 | 含义 |
|---|---|
| `--<kebab-case> <value>` | 每个声明的 query / path 参数自动展开成 `--<kebab-case>` 强类型 flag；原始 spec 名（`--cveId`）与 kebab 形式（`--cve-id`）都接受，整数 / 数字类型自动强转 |
| `--params '{...}'` | 批量传 query/path 参数的 JSON 字典（**不含 request body**）；与 per-param flag 共存时 flag 覆盖同名键；path 中的 `{id}` 自动从 `params.id` 提取。若调用没传 `--json` 而 `--params` 里的键命中 requestBody 字段名，报 `body_fields_via_params` + 提示改用 `--json` |
| `--json '{...}'` | request body（同时隐式声明 `Content-Type: application/json`）。只要 spec 声明了 `requestBody`（**无论是否标记 required**），schema 就会被内联解析后写到 `oed <service> <op> --help` 的 `body_schema` + `body_required_fields` 里，usage/examples 也会给出 `--json` 示例；Agent 可据此自动构造 body。`body_required` 仅反映 spec 的 `requestBody.required` 标记 |
| `--dry-run` | 仅打印待执行的 HTTP 详情，不发请求 |

参数解析容错：JSON 解析失败时给出行号、可粘贴的修复建议。未知 `--<flag>` 与声明参数对齐失败时报 `unknown_flag` + 提示该 operation 声明了哪些参数。

**计划中（未实现）**：`--page-all` NDJSON 流式分页、`--human` 美化输出（v0.4）、`-H` 自定义 header、`-o/--output` 写文件、`--quiet` 关闭 stderr（v0.4+）。

---

## 4. 架构

```
┌─────────────────────┐
│        oed (CLI)    │   click-based entry, dispatch table
└─────────┬───────────┘
          │
          ├──► discovery.py   拉取 /discovery/apis，缓存到 ~/.cache/oed-cli/（Windows: %LOCALAPPDATA%\oed-cli\cache\）
          │
          ├──► http.py        WAF-safe HTTP client（带伪装 UA/Referer）
          │
          ├──► dynamic.py     把 OpenAPI paths → 调度表，per-param flag 推导
          │
          ├──► invoke.py      实际调用：拼 URL → 发请求 → 封 JSON
          │
          ├──► auth.py        本地 token 存储（`_SecureStore` keyring + oneid device flow）+ ag 凭证自动注入
          │
          ├──► main.py        顶层 dispatcher（保留命令 vs 动态分发）
          │
          └──► errors.py      退出码（0/1/2/3/4）+ 友好异常
```

### 4.1 包结构（最终态）

```
oed-cli/
├── pyproject.toml                  # PEP 621，entry: oed = oed_cli.main:main
├── README.md
├── LICENSE
├── context/discoverAPI.md          # 人类可读的发现服务使用说明（已存在）
├── docs/cli-design.md              # 本文档
├── .claude/skills/oed-cli/         # Claude Code Skill 包
│   ├── SKILL.md
│   └── references/discovery-api.md # 引用 context/discoverAPI.md
├── src/
│   └── oed_cli/
│       ├── __init__.py
│       ├── __main__.py             # 支持 python -m oed_cli
│       ├── py.typed                # PEP 561 类型标记
│       ├── main.py                 # 顶层 dispatcher（v0.2+）
│       ├── cli.py                  # click 命令（保留命令 + --help 装饰）
│       ├── http.py                 # WAF-safe client
│       ├── discovery.py            # 拉取 + 缓存
│       ├── dynamic.py              # OpenAPI → 调度表 + per-param flag 推导（v0.2+）
│       ├── invoke.py               # 实际调用 / 描述 service & operation（v0.2+）
│       ├── auth.py                 # 本地 token 存储（`_SecureStore` keyring）+ oneid device flow（v0.5+）+ ag 自动注入（v0.4+）
│       └── errors.py               # 退出码 + OedError 体系
└── tests/
    ├── test_cli.py                 # v0.1 保留命令 + --help 装饰
    ├── test_discovery.py           # discovery 缓存
    ├── test_dynamic.py             # v0.2 调度 + per-param flag + API_ 前缀
    ├── test_invoke.py              # ag access_token 注入 + body 可见性 + result_root 修正
    └── test_auth.py                # ag/oneid token 存储（`_SecureStore`）单元测试
```

最小脚手架阶段先交付：pyproject + __init__ + __main__ + cli + http + discovery + errors。main / dynamic / invoke 在 v0.2 PR 完成。

### 4.2 关键模块职责

**`http.py`**
- 强制带浏览器头：`User-Agent: oed/<version>`、`Accept: application/json, text/plain, */*`、`Accept-Language: zh-CN,zh;q=0.9,en;q=0.8`。
- **不**带默认 `Referer`：openEuler APIG 在 `easysearch` 等端点上对 `Referer: https://api-gateway.osinfra.cn/` 返回 HTTP 401（实测 2026-07-28）；不带时 `/discovery/apis` 也仍能过 CloudWAF。调用方仍可通过 `headers={"Referer": ...}` 显式注入。
- 默认 30s 超时、自动遵循重定向（`follow_redirects=True`）。
- 暴露 `get_json()` / `request_json()` 与通用 `get_request(method, url, *, params, body, headers, timeout, user_agent, token, cookie, service_name)`（全 keyword-only），后者允许任意动词 + body。`token`/`cookie` 注入 `Authorization: Bearer <token>` / `Cookie: <cookie>` 副标头；`token` + `service_name` 同时存在时额外发明文身份/路由头 `x-oed-source: oed-cli` / `x-oed-target: <service>`（供 APIG custom-auth 做 per-service 头转发，非机密，见 §7「认证」行）。
- `_decode` 统一校验：5xx → `UpstreamError`；CloudWAF HTML 块 → `NetworkError(kind="waf_block")`；空 body 在 `spec_endpoint=True`（`get_json`）时 → `NotFoundError(kind="spec_missing")`，否则视为成功空 payload（`null`）。

**`discovery.py`**
- `fetch_discovery(*, community=None, force_refresh=False) -> DiscoveryFeed`：先看本地缓存的 discovery 文件（TTL 10 分钟），过期再拉。缓存根目录：POSIX `~/.cache/oed-cli/`，Windows `%LOCALAPPDATA%\oed-cli\cache\`；`OED_CACHE_DIR` 可整体覆盖。
- 模型（dataclass）：`ServiceMeta(name, service_name, community, title, version, description, base_url)`（frozen）、`DiscoveryFeed(fetched_at, raw, services)`。spec 本身不建模，以原始 dict 传递。
- 凭证路径：`auth_token_path()` → `<OED_CACHE_DIR>/auth.json`；`ag_token_path(service)` → `<OED_CACHE_DIR>/tokens/<service>.json`。`oed cache clear` 只删 discovery 缓存文件，**不会**误清 auth.json / tokens/。
- 缓存防毒化（issue #22）：`_materialize` 对 `__oed_fetched_at` 未来时间戳（超过 `now + _CLOCK_SKEW_SECONDS=300`，容忍 NTP 漂移）直接丢弃重拉；写入用 `_atomic_write_json`（tmp + replace），崩溃半写入不留 corrupt 文件。

**`dynamic.py`** (v0.2 新增)
- 把 OpenAPI 文档编成一张 `OperationsTable`：
  - 每个 (path, verb) 抽成 `Operation(scheme, address, path, method, parameters, body_required, request_body, body_schema, operation_id, base_url)`。`body_schema` 是 `requestBody` 里 `application/json` 的 schema，对 `$ref: #/components/schemas/X` 做内联解析（带 cycle 防护）；`oed <service> <op> --help` 把字段名 / 类型 / 是否必填透传给 Agent，方便自动构造 `--json` body。
  - 表的主键是 `operationId`；别名是 `"<VERB> <path>"`。
  - 如果 `operationId` 带 APIG 自动加的 `API_` 前缀（目前只有 `software-package-server` 命中），还会注册一个去掉前缀的别名（`API_xxx` ↔ `xxx`），方便用户少敲四个字符。`Operation.display_name` 是剥掉前缀后的形式，`Operation.operation_id` 保留 spec 原文。
  - `Operation.path` 来自 OpenAPI `paths` 块的 key；`Operation.backend.{scheme, address, path, method}` 来自 `x-apigateway-backend.httpEndpoints`。该块**可选** —— 缺失（或 `type != "HTTP"`）时合成 `Backend(scheme="https", address="", path=<OpenAPI path>, method=<OpenAPI verb>)`，operation 照常进命令表。网关重发布的 spec（`cve`、`pkgcontrib` 等）已不带这个块，按它过滤会让整个服务的命令消失。
  - `Operation.base_url` 是该服务解析后的运行时 base URL（在 `collect_operations` 时烘焙进去；`invoke.py` 直接读 `op.base_url` 拼 URL）。
- 路径占位符自动替换 + 类型强转（如 `page_num: integer` 接 `string` 自动 `int(...)`）。
- `to_flag(name)` 把 spec 里的参数名转成 kebab-case CLI flag（`cveId` → `cve-id`）。
- `param_flag_index(op)` 把 `Operation.parameters` 编成 `{flag_stem: param_def}`，让每个声明参数都能直接当 `--<flag>` 用。
- `resolve_runtime_gateway(service)` 决定每个服务的运行时 base URL：读 `ServiceMeta.base_url`（来自 discovery），去掉尾部 `/`、空白，原样返回。**无 fallback** —— 若 gateway 返回空或 `$APIG_GROUP_ENTRY_URL` 占位符，HTTP 调用会直接报错而不是被静默重写。`Operation.base_url` 在 `collect_operations` / `operations_table` 时一次性烘焙进去，`invoke.py` 直接读 `op.base_url` 拼 URL。
- 单服务 spec 在 `<OED_CACHE_DIR>/specs/<community>/<service>.json` 内 10 分钟缓存。

**`invoke.py`** (v0.2 新增，v0.4 扩展鉴权)
- `call_operation(op, *, params, body, dry_run, timeout, include_request, user_agent, token, cookie)`（全 keyword-only）：拼 URL → `http.get_request` → 封装 `ok / status / response / request` JSON。
- **URL 规则**：每个 `Operation.base_url`（在 `dynamic.py` 由 `resolve_runtime_gateway(service)` 一次性填好） + `op.path`。**不信任** `x-apigateway-backend.httpEndpoints.address`（spec 里常填测试域如 `cvesa.test.osinfra.cn`，会被 CloudWAF 拦截）。该块只用于推断 method / scheme。拼好后 `_reject_insecure_base_url` 校验 scheme：非空且非 `https` 抛 `UserError(kind="insecure_base_url")`（issue #22 缓存防毒化，hint `oed cache clear`）。
- **参数三分类与预检**：`_select_params` 把 `params` 拆成 path / query / unused；缺 path 占位符抛 `kind="missing_path_param"`；`--params` 命中 requestBody 字段且未传 body 抛 `kind="body_fields_via_params"`（hint 指向 `--json`）。
- **forum 占位符鉴权**：`GATEWAY_MANAGED_PARAMS = {"Api-Key","Api-Username"}`。forum 调用时 oed 自动填 `oed-placeholder` 头（网关边缘 header 转换替换成真实凭证）；调用方自行传这些键 → `UserError(kind="gateway_managed_param")`。`--dry-run` / 回显视图可见占位符，这是 oed 的自动填充，不是缺口。
- `describe_service(service, ops)`:  `oed <service>` 单参时打印的清单，包含 `url`（真实调用地址，已带服务自身的 `base_url`）和 `backend_declared`（spec 写的后端，仅供诊断）。
- `describe_operation(op)`:  `oed <service>` 列表里单个 operation 的摘要（path / url / path_params / query_params / has_body / body_required_fields）。
- `describe_operation_help(op, service)`:  `oed <service> <op> --help` 输出的 JSON，含每个参数的 `--<flag>` 形式 + 可粘贴的 usage 行。
- **ag 凭证自动注入**：`ag`（AtomGit）的每个请求通过 spec 声明的 `access_token` query 参数鉴权。`call_operation` 在调用前调用 `_inject_ag_token`：若操作声明了 `access_token` 且调用方没有显式传，就从本地存储（`auth.read_token("ag")`，由 `oed ag login` 写入）自动填充；声明为必填但没有任何 token 时抛 `kind="ag_token_missing"` 的 `UserError`（hint 提示 `oed ag login` 或 `--access-token`）。任何回显的 `request` 视图把 `access_token` 掩码成 `<stored>`，真实 token 只出现在实际发出的请求里。
- **`eulermaker getJobLog` 的 `result_root` 自动修正**：jobs 搜索 API 返回的 `result_root` 是相对路径，spec 要求以 `/dmesg` 结尾。Agent 常传原始值（缺尾部 `/dmesg`，且常带多余前导 `/`）。`call_operation` 通过 `_normalize_result_root` **只**对这个已知 path 参数做修正（去前导 `/`、补 `/dmesg`），并在输出里加 `param_corrections` 数组说明每处修改。其他任何调用都不被静默改写。
- **非 JSON 响应**：`_render_response` 对非 JSON body（HTML WAF 页、明文 traceback 等）原样包成 `{"_non_json_body": ..., "_content_type": ...}`，stdout 保持单个 JSON 对象，`| jq` 管道不破。
- **回显与条件字段**：`request` 视图由 `include_request` 开关控制（`main.py` 真实调用总是 `True`，故 dry-run 与真实调用都会出现）；`unused_params` 在传了多余参数时出现；`operation_id_raw` 仅在剥过 `API_` 前缀时出现；`param_corrections` 仅在修正过参数时出现。
- **Cookie 轮转**：2xx/3xx 且带 token 时捕获后端 `Set-Cookie`（`update_auth_from_response_headers`）持久化；best-effort，失败不阻断调用。
- WAF 拦截被识别为 `kind="waf_block"` 的 `NetworkError`。
- 401 响应被识别为 `kind="unauthorized"` 的 `UpstreamError`（exit 3），hint 引导用户跑 `oed auth status` / `oed auth login`。

**`auth.py`** (v0.4 新增，v0.6.2 存储重写)
- AtomGit（`ag`）PAT 通过第二个 `_SecureStore` 实例持久化：`username="ag:ag"` 命名空间，与 oneid 的 `"default"` 条目物理隔离；明文 fallback 落 `<OED_CACHE_DIR>/tokens/<service>.json`（继承 `OED_CACHE_DIR`；`oed cache clear` 只删 discovery.json，不会误清凭证）。
- 存储语义与 oneid 凭证一致：OS keystore 可达（macOS Keychain / Windows DPAPI / Linux SecretService）→ 走 keyring；否则 0600 明文 fallback。`keyring>=24` 依赖由 `pip install oed-cli` 自动安装。
- API：`store_token(token, service)` / `read_token(service)` / `clear_token(service)` / `token_info(service)`（返回 `{"encryption": "keyring"|"plaintext", "created_at"}`）/ `token_path(service)`。读取失败（缺文件 / 坏 JSON / 无 keyring 条目）一律静默返回 `None`，让调用方落到 missing-token 路径。

**`main.py`** — 顶层 dispatcher（v0.2 起为入口）
- `main()` 是 `oed` 的 entry point（pyproject 的 `oed = oed_cli.main:main`）；`__main__.py` 把 `python -m oed_cli` 也指过来。
- 顶层 dispatch：`_looks_like_reserved` 把保留命令（`info` / `services` / `schema` / `cache` / `auth` / `login` / `completion` / `help` / `--version` / `--help`）路由到 click；其他进 `_dispatch_dynamic`。`RESERVED_FIRST_TOKENS` 还含空串 `""`（裸 `oed` 无参数时直接进 click 帮助）；保留 token 在列即不进动态分发通道（避免被当成 `oed <service=login>`）。
- `_check_allowlist(service_name)` —— 本地 per-user service allow-list 闸门（v0.6）。读 `auth.json.allowlist`，service 不在列则抛 `UserError(kind="service_blocked_by_allowlist")`。**fail-open** 语义：无 auth.json / 缺 `allowlist` 字段 / `allowlist=[]` / 字段类型异常 全部放行（详见 §4.3）。
- `_split_dispatch_argv` 第一遍解析：把 `--<flag> value`、裸 `--<flag>`、`--help`/`-h`、positional 分桶；未知 `--<flag>` 暂存等操作解析后再校验。
- 操作解析（service → spec → operation）完成后，未知 `--<flag>` 通过 `param_flag_index` 跟声明参数对齐；匹配不上且不在 `_VALUE_FLAGS={"params","json","path","user-agent"}` / `_BOOL_FLAGS={"dry-run"}` 内则报 `unknown_flag` + 提示声明了哪些参数。
- per-param flag 与 `--params` JSON 共存：flag 覆盖 `--params` 同名字段；类型由 `coerce_flag_value` + `coerce_param_types` 双层强转。
- 真实调用：`_resolve_auth()` 解析 token/cookie（env `OED_TOKEN` / `OED_COOKIE` 优先，其次 auth.json），然后以 `include_request=True` 调 `call_operation` —— 所以真实调用也会返回 `request` 回显视图（凭证已掩码）。
- **保留子命令**：`service_name == "ag"` 且 method 为 `login` / `logout` 时走本地凭证管理，不进动态分发 —— `oed ag login`（交互式 `--token` 或静默提示，默认向 AtomGit 校验 token，`--no-verify` 跳过，`--status` 只查不写）、`oed ag logout`。见 §4.2 `auth.py`。
- 操作级 `--help` 短路在 flag 校验之前：未知 flag + `--help` 仍展示帮助，方便探索。
- 任何 `OedError` 都被 catch 后以 `{"ok":false,"code":N,"error":...}` 形式写到 stderr 并返回对应退出码；非 2xx/3xx 时 stdout 也写响应体但退出码仍非零。

**`cli.py`**
- 用 `click` 解析保留命令；`OedCli` 自定义 group 在标准 `--help` 之外追加「Auto-discovered services」section（拉 discovery 失败时静默降级）。
- 顶层保留命令：`info` / `services [--community X] [--refresh]` / `schema <service>[.method] [--refresh]` / `cache {show,clear,refresh}` / `auth {status,token,logout,login,login --manual}` / `--version`。
- `auth` 子组从 `oed_cli.auth` 注册；入口 `auth_group` 被 `cli.add_command(auth_group)` 联入主树。
- `shell completion` 子命令计划在 v1.0 由 click 原生支持，**当前未实现**（`main.py` 已在 `RESERVED_FIRST_TOKENS` 里保留 `completion`，避免被当成服务名；键入会落到 click 树报未知命令）。

**`auth.py`** (v0.5 重写，v0.6 扩展 allowlist + 平台原生加密)
- `save_auth()` / `load_auth()` / `clear_auth()` —— 通过 `_SecureStore` facade 写入或读取 `<OED_CACHE_DIR>/auth.json`（POSIX 0600 fallback）；当 OS keystore 可达时走 Keychain / DPAPI / SecretService。Schema：`{msal_cache, created_at, cookie?, allowlist?}`；`msal_cache` 是 MSAL 序列化的 token 缓存（包含 `access_token` + `refresh_token` + account）。
- `_SecureStore` —— 内部 facade，`save()` / `load()` / `clear()` 三方法 + lazy 探测 keyring + 单 backend 标签 `_last_backend`。keyring 写成功后删 plaintext；plaintext 仅作迁移期临时 / 无 keystore fallback。`auth_status` 输出里的 `backend` 字段读 `backend_label()`（`"keyring"` 或 `"plaintext"`）。
- `save_msal_auth(cache, cookie=, allowlist=)` —— 设备流使用；`cache.has_data_changed` 时持久化。`allowlist=None` 不写字段（兼容老 auth.json）；显式传 list 时覆盖。
- `_fetch_user_allowlist(access_token)` —— **一次性** HTTP GET `/oneid/oidc/device/user-data`，带 `Authorization: Bearer <token>`。返回 `list[str]` 或 `None`（fail-open：网络/5xx/JSON 解析/字段缺失/URL 未配置 全部 `None`）。URL 从 `defaults.toml [oauth].cli_user_data_url` 读，env `OED_USER_DATA_URL` 可覆盖。仅在 `_finalize_device_result` token 拿到之后调一次，结果写入 auth.json 缓存。
- `auth_headers_from_storage()` —— 拼 `Authorization: Bearer <token>` / 可选 `Cookie` 头；`OED_TOKEN` / `OED_COOKIE` 环境变量优先级高于文件。
- `is_token_expired()` —— 基于 `created_at + 24h` 的最佳估算（网关侧可能提前失效，401 是权威信号）。
- `device_login()` —— **RFC 8628 Device Authorization Grant**，通过 MSAL Python 实现。调用 `msal.PublicClientApplication.initiate_device_flow()` 拿 `user_code` + `verification_uri`，打印到 stderr，自动尝试开浏览器（best-effort），自动复制 `user_code` 到剪贴板（best-effort），然后 `acquire_token_by_device_flow()` 轮询拿 token。MSAL 内部处理 `slow_down` / `authorization_pending` / 网络错误重试。**token 拿到后自动调用 `_fetch_user_allowlist()` 把 allowlist 写入 auth.json**。
- `manual_login()` —— TTY 粘贴流（与 v0.4 兼容）。非 TTY 抛 `UserError(kind="not_tty")`。不触发 allowlist fetch（用户主动 paste 的 token 通常跨身份复用，allowlist 留给下一次设备流登录刷新）。
- `load_defaults()` / `get_client_id()` / `get_device_url()` / `_get_user_data_url()` —— 从 wheel 内 `defaults.toml` 读 `[oauth]`，env `OED_APP_ID` / `OED_DEVICE_URL` / `OED_USER_DATA_URL` 可覆盖。
- `extract_set_cookie()` / `update_auth_from_response_headers()` —— 业务请求返回 `Set-Cookie` 时轮转 cookie（保留 MSAL 缓存 + allowlist）。
- `encrypt_api_data(data)` —— HMAC 签名 + base64 编码，给 APIG custom-auth 拼 `x-secret-token` 头。
- `_try_open_browser()` / `_has_display()` / `_copy_to_clipboard()` —— 辅助；后两个 stdlib only（`subprocess` 调用 `wl-copy` / `xclip` / `xsel` / `pbcopy` / `clip`）。
- `auth_group` click 子组：`status`（JSON，含 `token_fingerprint` / `username` / `allowlist`）、`token <value> [--cookie]`（粘贴）、`logout`、`login`（设备流）、`login --manual`（TTY 粘贴）。

**`errors.py`** — 退出码

| Code | 含义 |
|---|---|
| 0 | 成功（HTTP 2xx/3xx） |
| 1 | 用户错误（参数无效、JSON 解析失败、未知 flag） |
| 2 | 网络 / WAF 拦截 |
| 3 | 上游 API 错误（含 4xx/5xx） |
| 4 | 未发现（service/method 不在 gateway 中 / 空 spec） |

### 4.3 Per-user service allow-list（v0.6）

`oed <service> ...` 每次执行前查 `auth.json.allowlist`（一个 list[str]），service_name 不在则抛 `UserError(kind="service_blocked_by_allowlist")` 退出码 1。**关键设计**：

1. **不在每次 CLI 调用时 fetch**。只在 `oed auth login` 设备流程**轮询到成功**那一刻，发一次 `GET /oneid/oidc/device/user-data`（带 `Authorization: Bearer <access_token>`）把 allowlist 拉下来，写到 auth.json 的 `allowlist` 字段。
2. **本地校验**：之后每次 CLI 调用只看 `auth.json` 本地这份，**不再发请求**。
3. **保留命令不受影响**：`oed login` / `oed auth login` / `oed auth status` / `oed services` / `oed info` 等走 click 树，根本不进 dispatcher，所以 allowlist 不会拦它们。
4. **`ag` 豁免**：`ag`（AtomGit）用自有 PAT 鉴权（`oed ag login`），与 oneid 无关，不归 oneid allow-list 管。`_check_allowlist` 对 `service_name == "ag"` 直接放行；`ag login`/`ag logout` 更在 gate 之前短路。

**Fail-open 矩阵**（`auth.json` 状态 → 行为）：

| `auth.json` | `allowlist` 字段 | 行为 |
|---|---|---|
| 不存在 | — | **fail-open**：放行 |
| 存在 | 缺字段 | **fail-open**：兼容老 auth.json |
| 存在 | `null` | **fail-open** |
| 存在 | `[]` | **fail-open**：用户主动清空（保守） |
| 存在 | `["a","b"]` | service_name 在 → 放行；不在 → `UserError` |

`allowlist=[]` 选 fail-open 而不是 fail-closed：网络/服务异常可能也会返回空，严格 fail-closed 会把 CLI 锁死。需要 fail-closed 时把 `main._check_allowlist` 里的 `if isinstance(allowlist, list) and allowlist:` 改为 `if isinstance(allowlist, list):` 即可（one-liner 改动）。

**数据流**：

```
oed login
  ↓
POST /oidc/device/code                 → user_code
  ↓ user approves in browser
POST /oidc/token (polling)             → access_token
  ↓
GET  /oidc/device/user-data            → {"data": "a,b,c"}
  ↓ parse + sort
save auth.json with allowlist field
  ↓
oed <service> <op> ...                 → load_auth() → check service_name
                                         - 在：放行
                                         - 不在：UserError(kind="service_blocked_by_allowlist")
```

**URL 配置**：`defaults.toml [oauth].cli_user_data_url`，env `OED_USER_DATA_URL` 可覆盖。

---

## 5. 安装 & 发布

### 5.1 用户侧

```bash
# 推荐（与 gws 单条命令一致）
pip install oed-cli

# 想要隔离环境（CI 更稳）
pipx install oed-cli

# 使用 uv 时
uv tool install oed-cli
```

打包边界：
- `Python >= 3.10`
- 仅强依赖：`click>=8.1`, `httpx>=0.27`
- 无本地编译；wheel only。

### 5.2 开发侧

```bash
git clone https://atomgit.com/openeuler/oed-cli
cd oed-cli
pip install -e ".[dev]"
pytest
```

### 5.3 发布流水线（计划）

1. `git tag vX.Y.Z && git push --tags`
2. GitHub Actions：跑 `pytest` + `python -m build` → 上传 PyPI（OIDC trusted publisher）
3. 自动生成 release notes（changesets-style，由 `.changeset/*.md` 驱动）

---

## 6. AI 友好

### 6.1 默认 JSON、确定性 stdout

- 永远 JSON 到 stdout（`ensure_ascii=False`，中文友好），stderr 单独承载错误和日志。
- 颜色/交互提示关闭（`NO_COLOR=1` 且 `click.echo(..., err=True, color=False)`）。
- 成功调用返回字段稳定：`ok`、`status`、`url`、`method`、`service`、`operation`、`response`；`request` 仅在 dry-run 或被显式开启 `include_request` 时出现；`operation_id_raw` 仅在 spec 的 `operationId` 被剥过 `API_` 前缀时出现；`param_corrections` 仅在本次调用修正过参数时出现（如 `eulermaker getJobLog` 的 `result_root` 自动补 `/dmesg`）。
- 错误也是 JSON（写到 stderr）：
  ```json
  {"ok": false, "code": 4, "error": "method_not_found",
   "message": "service 'xxx' has no method 'yyy'", "available_methods": [...], "hint": "..."}
  ```

### 6.2 Claude Code Skill

`.claude/skills/oed-cli/SKILL.md` 内容：
- 简介 `oed`，告诉 Agent 该用它来做什么。
- 列出 `oed info / services / schema / <service> <method>` 三段式。
- 给 Agent 「先 `oed info` 探测 → `oed schema` 取规格 → `oed <svc> <method>` 调用」的标准工作流。
- 引用 `context/discoverAPI.md` 作为参数语义权威源。

### 6.3 可移植到其它 Agent

- 同时保留 `.agent/skills/oed-cli/` 与 `.gemini/extensions/oed-cli/` 的占位结构，未来补齐即可。
- 命令文法本身用 BNF-like 块写在 SKILL.md，避免 Agent 误解。

---

## 7. 已知风险 & 决策记录

| 主题 | 决策 | 备注 |
|---|---|---|
| **WAF 拦截** | 内置浏览器请求头（`User-Agent`、`Accept`、`Accept-Language`）；**不**带默认 `Referer`（`easysearch` 等端点对其返回 401） | 详见 `context/discoverAPI.md` 第 6 节；具体头由 `http.py` 维护 |
| **`$APIG_GROUP_ENTRY_URL` 占位** | 每个服务的运行时 base URL = `resolve_runtime_gateway(service) + op.path`。解析器读 `ServiceMeta.base_url`（来自 discovery feed），去掉尾部 `/` 和空白后原样返回；**无 fallback**。spec 里的 `servers[0].url` 不参与。 | 当下所有 openeuler 服务在 feed 里都返回真实的 `base_url`（如 `https://apig.osinfra.cn`）；若 gateway 在过渡期返回空或占位符，HTTP 调用会直接失败暴露问题，而不是被静默重写到旧常量。`x-apigateway-backend.httpEndpoints.address` 常指向测试域 `*.test.osinfra.cn`，会被 CloudWAF 拦截，因此该字段仅用于推断 `method/scheme`，不用于 host。 |
| **多社区支持** | MVP 默认 `openeuler`；`OED_COMMUNITY` 环境变量切换；`oed services --community X` 限定一个社区 | 与 gws 的 `project` 选择类似 |
| **缓存失效** | discovery + 单服务 spec 都 TTL 10 分钟；`oed cache refresh` 强制刷 discovery；`oed cache show/clear` 看 / 清 | 避免动态命令表抖动，详情见 README "Keeping in sync with the gateway" |
| **缓存防毒化（v0.6.3，issue #22）** | 三层纵深防御，全环境生效、无密钥管理面：(1) **未来时间戳拒绝** —— `discovery._materialize` 与 `dynamic._read_spec_cache` 见 `fetched_at > now + 300s`（`_CLOCK_SKEW_SECONDS`，容忍 NTP 漂移）即视为损坏、丢弃重拉，堵住「把 `__oed_fetched_at` 改成未来值让 `now - fetched_at` 恒负、TTL 永不过期」的绕过；(2) **base_url 仅 https** —— `invoke.call_operation` 在拼 URL 后、附加 token 前用 `urlparse` 校验 scheme，非空且非 `https` 抛 `UserError(kind="insecure_base_url")` + hint「`oed cache clear`」，堵住把 `base_url` 改成 `http://evil.com` 让 Bearer token/ag PAT 明文出网。**不拦** scheme 为空的占位符/空串（`$APIG_GROUP_ENTRY_URL`、empty feed），保留 `resolve_runtime_gateway` 原样穿透到 HTTP 层失败的契约；(3) **原子写** —— `discovery._atomic_write_json`（tmp+replace）供 discovery 与 spec 缓存共用，崩溃半写入不再留 corrupt 文件。**不做** HMAC 完整性签名：plaintext-fallback（常见 CI/headless）下密钥与缓存同目录、可被同一攻击者读写，边际价值低；issue 自承同用户进程本可读 keyring，属纵深防御而非闭合新洞。三层不闭合 `https://evil.com` 外泄（需 HMAC/hostname 白名单），hostname 白名单因现有测试用 `https://custom.example.com` 等非 osinfra 主机而过脆，故未采用 | 缓存是公开数据，**加密不是正确工具**（issue 明确反对）；host 必须来自 feed 的约束不变（scheme 校验是 hard-fail 而非 silent rewrite，不违反「无 fallback」） |
| **认证（v0.5+）** | `oed login`/`oed auth login` 走 RFC 8628 device flow（MSAL），token 经 `_SecureStore` 持久化（见下「平台原生加密」行）；调用时自动注入 `Authorization: Bearer <token>` + `x-secret-token` HMAC 头。`ag`（AtomGit）走独立鉴权：spec 声明的 `access_token` query 参数，`oed ag login/logout/status` 管理本地 PAT，真实调用自动注入已存 token、回显视图掩码为 `<stored>`，显式 `--access-token` 优先。其他服务暂无本地凭证 | v0.4 的 `oed login` + 自动注入已落地；`OED_TOKEN`/`OED_COOKIE` 环境变量覆盖仍未做 |
| **forum 占位符鉴权** | `forum`（Discourse）的 `Api-Key` / `Api-Username` 由 **oed 在请求时自动填充占位符头**（`oed-placeholder`），网关 APIG 的 header 转换规则在边缘把它们替换成真实凭证 —— 因此 oed 必须发送这些头，否则网关直接拒绝。spec 仍把它们声明为 `required` header 参数，但 oed 从 flag/help 面剔除；调用方若通过 `--params` 或 `--api-key` 传入会报 `gateway_managed_param`（exit 1）+ hint「oed 自动填，别传」 | 目标是「让 agent 相信 oed 会自动填占位符、不去检查」——`--dry-run` 的 headers 里会显示 `Api-Key: oed-placeholder`，这是 oed 的自动填充，不是缺口 |
| **body 可见性** | help 里 body 信息（`body_schema` / `body_required_fields` / usage 的 `--json`）以「存在 requestBody」为准展示，而非 `body_required`——网关常只写 schema 级 `required`，`requestBody.required` 缺失会误判「无 body」。`--params` 示例仅在操作声明了 query/path 参数时才给出；body 型操作给 `--json` 示例 | 修正了 `forum createTopicPostPM` 事件：按 help 用 `--params` 传 body 字段 → 静默落入 `unused`、`body: null` → 网关 400。现在调用时若 `--params` 命中 body 字段名且未传 `--json`，直接报 `body_fields_via_params` |
| **shell 补全** | 计划由 click 原生支持（`oed completion {bash,zsh,fish,powershell}`），尚未实现 | v1.0 路线图 |
| **错误处理** | 任何 `OedError` 被顶层 catch 后以 JSON 形式写 stderr 并返回对应退出码（0/1/2/3/4）；stdout 在出错时仍为空，方便 `\| jq` 安全管道 | 详见 §4.2 错误码表 |
| **Per-user allow-list（v0.6）** | 设备流登录成功后，**一次性** GET `/oneid/oidc/device/user-data`（带 Bearer token）把 user 勾选过的服务列表拉下来，存进 `auth.json.allowlist`。后续每次 `oed <service> ...` 只查本地，**不**发 HTTP。Fail-open（无 auth.json / 缺字段 / `[]` 全部放行）；非空列表里没命中 → `UserError(kind="service_blocked_by_allowlist")`（exit 1）。URL 配置在 `defaults.toml [oauth].cli_user_data_url`，env `OED_USER_DATA_URL` 可覆盖 | 详见 §4.3。手动粘贴 (`auth token`) 不触发 fetch（粘的 token 通常跨身份复用，allowlist 留给下一次设备流登录刷新）；token 静默刷新 (`acquire_token_silent`) 也不重 fetch（allowlist 与 user identity 绑定，不与 token lifetime 绑定） |
| **平台原生加密（v0.6）** | `auth.json` 通过 `_SecureStore` facade 写入。优先级：OS keystore 可达 → macOS Keychain / Windows DPAPI / Linux SecretService；否则 fallback `<OED_CACHE_DIR>/auth.json` + `0600`。Runtime dep `keyring>=24`，由 `pip install oed-cli` 自动安装；用户无手动步骤。`oed auth status` 输出 `backend` 字段标识当前后端 | 详见 `docs/oed-cli-token-lifecycle-and-storage.md` §5.3 |

---

## 8. 路线图

| 版本 | 范围 | 状态 |
|---|---|---|
| v0.1 | 设计文档 + 最小脚手架 + Claude Skill + `oed info/services/schema/cache` | ✅ 已发布 |
| v0.2 | 动态方法分发：`oed <service> <method> --params ... --json ... --dry-run`，输出 `request` 视图，禁用 OedError 转 JSON 返回 | ✅ 已发布 |
| v0.2.3 | Per-parameter flag 表面：每个声明参数自动转 `--<kebab-case>`（`--cve-id` 而非 `--params '{"cveId":…}'`），operation 级 `--help`，APIG `API_` 前缀自动剥离并保留为别名 | ✅ 已发布 |
| v0.3.0 | oneid RFC 8628 device flow 登录（`oed login` / `oed auth login`，headless/SSH/CI 可用）；OS-native credential storage（`_SecureStore` → macOS Keychain / Windows DPAPI / Linux SecretService，0600 fallback）；Per-user service allow-list（设备流登录后一次性拉 `/oneid/oidc/device/user-data`，本地拦截未授权 service，fail-open 兼容老 `auth.json`）；`x-secret-token` HMAC 移除改用明文 `x-oed-source` / `x-oed-target` 身份头（CLI 二进制分发对称密钥无法保密）；gateway custom-auth 从 Bearer JWT 解 username + 角色；spec fixture 与生产 `pkgcontrib` 对齐（`software-package-server` 在线改名，operationId 不再带 `API_` 前缀）；omapi 切生产域 `omapi.osinfra.cn` | ✅ 已发布 |
| v0.4 | `--page-all`、NDJSON 流式分页、`--human` 美化输出 | 未开始 |
| v0.6.1 | `auth.json` 平台原生加密：macOS Keychain / Windows DPAPI / Linux SecretService（`keyring>=24` 依赖），无 keystore 时回退 0600；`oed auth status` 增加 `backend` 字段 | ✅ 已发布 |
| v0.6.2 | AtomGit（`ag`）PAT：`oed ag login/logout/status` + `ag` 请求自动注入 `access_token`（显式传参优先、回显掩码）；PAT 存储复用 `_SecureStore`（独立 keyring 条目），与 oneid 凭证隔离 | 部分落地（仅 `ag` 服务） |
| v0.6.3 | 缓存防毒化（issue #22）：未来时间戳拒绝、base_url 仅 https、原子写缓存；`oed auth status` 的 `has_refresh_token` 从 MSAL cache 读取（修历史 bug） | ✅ 已发布 |
| v1.0 | PyPI 首发 + 完整测试 + 跨平台 shell 补全 + 文档站 | 未开始 |

> v0.2 解锁了**直接调用云服务**的核心承诺（`oed <service> <method> ...`）。
> 用户从此 `oed` 一下就能调 openEuler 任意已挂载到网关的服务，无需手写 `curl` 也无需手填 WAF 头。

---

## 9. 参考

- [`googleworkspace/cli`](https://github.com/googleworkspace/cli)（gws，本工具的设计灵感源）
- [`context/discoverAPI.md`](../context/discoverAPI.md)（网关发现服务使用文档，本 CLI 的数据源）
- OpenAPI 3.0.3 规范（`https://spec.openapis.org/oas/v3.0.3`）
