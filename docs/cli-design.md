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
oed <service>                              # 列服务的全部 operation
oed <service> <method> [--params JSON] [--json BODY] [--dry-run]
# 未来: --page-all (v0.3), shell completion (v1.0)
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

# 流式分页，NDJSON 输出（v0.3）
oed software-package-server listSoftwarePackages \
  --count 100 --page-all | jq -c '.'
```

> **v0.2 已实现**：`oed <service> [<method>] [--params ...] [--json ...] [--dry-run]` 全部可用，包括 path 占位符（如 `--id 12345` → 自动填到 `/api/v1/softwarepkg/{id}`）。**v0.2.3 新增**：每个声明参数自动展开为 `--<kebab-case>` 强类型 flag（如 `cveId` → `--cve-id`），并对 APIG 自动加的 `API_` 前缀做透明剥离（`API_listSoftwarePackages` ↔ `listSoftwarePackages` 都可调用，展示名是剥前缀的）。

### 3.3 参数约定（已实现）

| Flag / 形式 | 含义 |
|---|---|
| `--<kebab-case> <value>` | 每个声明的 query / path 参数自动展开成 `--<kebab-case>` 强类型 flag；原始 spec 名（`--cveId`）与 kebab 形式（`--cve-id`）都接受，整数 / 数字类型自动强转 |
| `--params '{...}'` | 批量传 query/path 参数的 JSON 字典（**不含 request body**）；与 per-param flag 共存时 flag 覆盖同名键；path 中的 `{id}` 自动从 `params.id` 提取。若调用没传 `--json` 而 `--params` 里的键命中 requestBody 字段名，报 `body_fields_via_params` + 提示改用 `--json` |
| `--json '{...}'` | request body（同时隐式声明 `Content-Type: application/json`）。只要 spec 声明了 `requestBody`（**无论是否标记 required**），schema 就会被内联解析后写到 `oed <service> <op> --help` 的 `body_schema` + `body_required_fields` 里，usage/examples 也会给出 `--json` 示例；Agent 可据此自动构造 body。`body_required` 仅反映 spec 的 `requestBody.required` 标记 |
| `--dry-run` | 仅打印待执行的 HTTP 详情，不发请求 |

参数解析容错：JSON 解析失败时给出行号、可粘贴的修复建议。未知 `--<flag>` 与声明参数对齐失败时报 `unknown_flag` + 提示该 operation 声明了哪些参数。

**计划中（未实现）**：`--page-all` NDJSON 流式分页（v0.3）、`-H` 自定义 header、`-o/--output` 写文件、`--quiet` 关闭 stderr（v0.4+）。

---

## 4. 架构

```
┌─────────────────────┐
│        oed (CLI)    │   click-based entry, dispatch table
└─────────┬───────────┘
          │
          ├──► discovery.py   拉取 /discovery/apis，缓存到 ~/.cache/oed-cli/
          │
          ├──► http.py        WAF-safe HTTP client（带伪装 UA/Referer）
          │
          ├──► dynamic.py     把 OpenAPI paths → 调度表，per-param flag 推导
          │
          ├──► invoke.py      实际调用：拼 URL → 发请求 → 封 JSON
          │
          ├──► auth.py        本地 token 存储（Windows DPAPI / 其余 base64）+ ag 凭证自动注入
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
│       ├── main.py                 # 顶层 dispatcher（v0.2+）
│       ├── cli.py                  # click 命令（保留命令 + --help 装饰）
│       ├── http.py                 # WAF-safe client
│       ├── discovery.py            # 拉取 + 缓存
│       ├── dynamic.py              # OpenAPI → 调度表 + per-param flag 推导（v0.2+）
│       ├── invoke.py               # 实际调用 / 描述 service & operation（v0.2+）
│       ├── auth.py                 # 本地 token 存储（DPAPI/base64）+ ag 自动注入（v0.4+）
│       └── errors.py               # 退出码 + OedError 体系
└── tests/
    ├── test_cli.py                 # v0.1 保留命令 + --help 装饰
    ├── test_discovery.py           # discovery 缓存
    ├── test_dynamic.py             # v0.2 调度 + per-param flag + API_ 前缀
    ├── test_invoke.py              # v0.4 ag access_token 自动注入
    └── test_auth.py                # v0.4 token 存储（DPAPI/base64）单元测试
```

最小脚手架阶段先交付：pyproject + __init__ + __main__ + cli + http + discovery + errors。main / dynamic / invoke 在 v0.2 PR 完成。

### 4.2 关键模块职责

**`http.py`**
- 强制带浏览器头：`User-Agent: oed/<version>`、`Accept: application/json`、`Accept-Language: zh-CN,zh;q=0.9,en;q=0.8`。
- **不**带默认 `Referer`：openEuler APIG 在 `easysearch` 等端点上对 `Referer: https://api-gateway.osinfra.cn/` 返回 HTTP 401（实测 2026-07-28）；不带时 `/discovery/apis` 也仍能过 CloudWAF。调用方仍可通过 `headers={"Referer": ...}` 显式注入。
- 30s 超时、自动遵循重定向、不跟随非标端口重定向。
- 暴露 `get_json()` 与通用 `get_request(method, url, params, body, headers)`，后者允许任意动词 + body。

**`discovery.py`**
- `fetch_discovery(force_refresh=False) -> DiscoveryDoc`：先看 `~/.cache/oed-cli/discovery.json`（TTL 10 分钟），过期再拉。
- 模型（dataclass）：
  - `ServiceMeta(name, service_name, community, title, version, description, base_url)`
  - `Spec(openapi, info, servers, paths)`：单个服务的 OpenAPI。

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
- 单服务 spec 在 `~/.cache/oed-cli/specs/<community>/<service>.json` 内 10 分钟缓存。

**`invoke.py`** (v0.2 新增)
- `call_operation(op, params, body, dry_run)`:  拼 URL → `http.get_request` → 封装 `ok / status / response / request` JSON。
- **URL 规则**：每个 `Operation.base_url`（在 `dynamic.py` 由 `resolve_runtime_gateway(service)` 一次性填好） + `op.path`。**不信任** `x-apigateway-backend.httpEndpoints.address`（spec 里常填测试域如 `cvesa.test.osinfra.cn`，会被 CloudWAF 拦截）。该块只用于推断 method / scheme。
- `describe_service(service, ops)`:  `oed <service>` 单参时打印的清单，包含 `url`（真实调用地址，已带服务自身的 `base_url`）和 `backend_declared`（spec 写的后端，仅供诊断）。
- `describe_operation_help(op, service)`:  `oed <service> <op> --help` 输出的 JSON，含每个参数的 `--<flag>` 形式 + 可粘贴的 usage 行。
- **ag 凭证自动注入**：`ag`（AtomGit）的每个请求通过 spec 声明的 `access_token` query 参数鉴权。`call_operation` 在调用前调用 `_inject_ag_token`：若操作声明了 `access_token` 且调用方没有显式传，就从本地存储（`auth.read_token("ag")`，由 `oed ag login` 写入）自动填充；声明为必填但没有任何 token 时抛 `kind="ag_token_missing"` 的 `UserError`（hint 提示 `oed ag login` 或 `--access-token`）。任何回显的 `request` 视图（`--dry-run` / `--include-request`）把 `access_token` 掩码成 `<stored>`，真实 token 只出现在实际发出的请求里。
- WAF 拦截被识别为 `kind="waf_block"` 的 `NetworkError`。

**`auth.py`** (v0.4 新增)
- 本地 secret 存储，按 `service` 分文件存于缓存目录下 `tokens/<service>.json`（继承 `OED_CACHE_DIR`；`oed cache clear` 只删 discovery.json，不会误清凭证）。
- Windows 上经 DPAPI（`CryptProtectData`/`CryptUnprotectData`，ctypes 调用）加密，加密边界真实且零运行时依赖；非 Windows 平台退化为 base64（文档化混淆，**非**加密）。
- 磁盘 schema 带 `version` 字段（当前 `1`）+ `service` 键，未来通用 `oed login` 可复用同一存储。
- API：`store_token(token, service)` / `read_token(service)` / `clear_token(service)` / `token_info(service)` / `token_path(service)`。读取失败（缺文件 / 坏 JSON / 无法解密）一律静默返回 `None`，让调用方落到 missing-token 路径。

**`main.py`** — 顶层 dispatcher（v0.2 起为入口）
- `main()` 是 `oed` 的 entry point（pyproject 的 `oed = oed_cli.main:main`）；`__main__.py` 把 `python -m oed_cli` 也指过来。
- 顶层 dispatch：`_looks_like_reserved` 把保留命令（`info` / `services` / `schema` / `cache` / `--version` / `--help`）路由到 click；其他进 `_dispatch_dynamic`。
- `_split_dispatch_argv` 第一遍解析：把 `--<flag> value`、裸 `--<flag>`、`--help`/`-h`、positional 分桶；未知 `--<flag>` 暂存等操作解析后再校验。
- 操作解析（service → spec → operation）完成后，未知 `--<flag>` 通过 `param_flag_index` 跟声明参数对齐；匹配不上且不在 `_VALUE_FLAGS={"params","json","path"}` / `_BOOL_FLAGS={"dry-run"}` 内则报 `unknown_flag` + 提示声明了哪些参数。
- per-param flag 与 `--params` JSON 共存：flag 覆盖 `--params` 同名字段；类型由 `coerce_flag_value` + `coerce_param_types` 双层强转。
- **保留子命令**：`service_name == "ag"` 且 method 为 `login` / `logout` 时走本地凭证管理，不进动态分发 —— `oed ag login`（交互式 `--token` 或静默提示，默认向 AtomGit 校验 token，`--no-verify` 跳过，`--status` 只查不写）、`oed ag logout`。见 §4.2 `auth.py`。
- 操作级 `--help` 短路在 flag 校验之前：未知 flag + `--help` 仍展示帮助，方便探索。
- 任何 `OedError` 都被 catch 后以 `{"ok":false,"code":N,"error":...}` 形式写到 stderr 并返回对应退出码；非 2xx/3xx 时 stdout 也写响应体但退出码仍非零。

**`cli.py`**
- 用 `click` 解析保留命令；`OedCli` 自定义 group 在标准 `--help` 之外追加「Auto-discovered services」section（拉 discovery 失败时静默降级）。
- 顶层保留命令：`info` / `services [--community X] [--refresh]` / `schema <service>[.method] [--refresh]` / `cache {show,clear,refresh}` / `--version`。
- `shell completion` 子命令计划在 v1.0 由 click 原生支持，**当前未实现**。

**`errors.py`** — 退出码

| Code | 含义 |
|---|---|
| 0 | 成功（HTTP 2xx/3xx） |
| 1 | 用户错误（参数无效、JSON 解析失败、未知 flag） |
| 2 | 网络 / WAF 拦截 |
| 3 | 上游 API 错误（含 4xx/5xx） |
| 4 | 未发现（service/method 不在 gateway 中 / 空 spec） |

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
- 成功调用返回字段稳定：`ok`、`status`、`url`、`method`、`service`、`operation`、`response`；`request` 仅在 dry-run 或被显式开启 `include_request` 时出现；`operation_id_raw` 仅在 spec 的 `operationId` 被剥过 `API_` 前缀时出现。
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
| **认证** | `ag`（AtomGit）操作经 spec 声明的 `access_token` query 参数鉴权；`oed ag login/logout/status` 管理本地 token（Windows DPAPI 加密 / 其余平台 base64 混淆），真实调用自动注入已存 token，回显视图掩码为 `<stored>`。显式 `--access-token` 优先于本地存储。其他服务暂无本地凭证，仍走网关侧已有策略 | v0.4 的 `oed login` 子命令 + 自动注入已为 `ag` 落地（见 §8）；`OED_TOKEN` 环境变量 / 通用 `Authorization` 注入未做 |
| **forum 占位符鉴权** | `forum`（Discourse）的 `Api-Key` / `Api-Username` 由 **oed 在请求时自动填充占位符头**（`oed-placeholder`），网关 APIG 的 header 转换规则在边缘把它们替换成真实凭证 —— 因此 oed 必须发送这些头，否则网关直接拒绝。spec 仍把它们声明为 `required` header 参数，但 oed 从 flag/help 面剔除；调用方若通过 `--params` 或 `--api-key` 传入会报 `gateway_managed_param`（exit 1）+ hint「oed 自动填，别传」 | 目标是「让 agent 相信 oed 会自动填占位符、不去检查」——`--dry-run` 的 headers 里会显示 `Api-Key: oed-placeholder`，这是 oed 的自动填充，不是缺口 |
| **body 可见性** | help 里 body 信息（`body_schema` / `body_required_fields` / usage 的 `--json`）以「存在 requestBody」为准展示，而非 `body_required`——网关常只写 schema 级 `required`，`requestBody.required` 缺失会误判「无 body」。`--params` 示例仅在操作声明了 query/path 参数时才给出；body 型操作给 `--json` 示例 | 修正了 `forum createTopicPostPM` 事件：按 help 用 `--params` 传 body 字段 → 静默落入 `unused`、`body: null` → 网关 400。现在调用时若 `--params` 命中 body 字段名且未传 `--json`，直接报 `body_fields_via_params` |
| **shell 补全** | 计划由 click 原生支持（`oed completion {bash,zsh,fish,powershell}`），尚未实现 | v1.0 路线图 |
| **错误处理** | 任何 `OedError` 被顶层 catch 后以 JSON 形式写 stderr 并返回对应退出码（0/1/2/3/4）；stdout 在出错时仍为空，方便 `\| jq` 安全管道 | 详见 §4.2 错误码表 |

---

## 8. 路线图

| 版本 | 范围 | 状态 |
|---|---|---|
| v0.1 | 设计文档 + 最小脚手架 + Claude Skill + `oed info/services/schema/cache` | ✅ 已发布 |
| v0.2 | 动态方法分发：`oed <service> <method> --params ... --json ... --dry-run`，输出 `request` 视图，禁用 OedError 转 JSON 返回 | ✅ 已发布 |
| v0.2.3 | Per-parameter flag 表面：每个声明参数自动转 `--<kebab-case>`（`--cve-id` 而非 `--params '{"cveId":…}'`），operation 级 `--help`，APIG `API_` 前缀自动剥离并保留为别名 | ✅ 已发布 |
| v0.3 | `--page-all`、NDJSON 流式分页、`--human` 美化输出 | 未开始 |
| v0.4 | 鉴权插件：`oed ag login/logout/status` 本地 token 存储（DPAPI/base64）+ `ag` 请求自动注入 `access_token`（显式传参优先、回显掩码） | 部分落地（仅 `ag` 服务；`OED_TOKEN` 环境变量、通用 `Authorization` 注入未做） |
| v1.0 | PyPI 首发 + 完整测试 + 跨平台 shell 补全 + 文档站 | 未开始 |

> v0.2 解锁了**直接调用云服务**的核心承诺（`oed <service> <method> ...`）。
> 用户从此 `oed` 一下就能调 openEuler 任意已挂载到网关的服务，无需手写 `curl` 也无需手填 WAF 头。

---

## 9. 参考

- [`googleworkspace/cli`](https://github.com/googleworkspace/cli)（gws，本工具的设计灵感源）
- [`context/discoverAPI.md`](../context/discoverAPI.md)（网关发现服务使用文档，本 CLI 的数据源）
- OpenAPI 3.0.3 规范（`https://spec.openapis.org/oas/v3.0.3`）
