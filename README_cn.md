# oed-cli

> **oed** —— 一个面向 openEuler 社区服务的命令行工具。自动发现、JSON 优先、AI 友好。为人类和 LLM 智能体而生。

[English](README.md) | [简体中文](README_cn.md)

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org) [![License](https://img.shields.io/badge/license-Apache--2.0-green)](https://gitcode.com/openeuler/oed-cli/tree/master/LICENSE) [![PyPI](https://img.shields.io/pypi/v/oed-cli)](https://pypi.org/project/oed-cli/)

`oed` 并不内置一份静态的命令列表。它在运行时读取 openEuler 基础设施发现服务（Infra Discovery Service），并动态构建出完整的命令接口。当有新服务上线时，`oed` 会自动识别——无需升级。

## 为什么选择 oed？

`oed` 的存在是为了解决一个具体问题：一个要与*众多不断演进的服务*交互的 CLI，而不让这种复杂性成倍增长。为每个服务的每个版本手工编写客户端，正是 Zylos API 版本管理研究中所警告的那种版本压力——而对于 AI 智能体这类使用者来说，这种压力尤为突出：一个被重命名的字段会悄无声息地破坏一次工具调用，一个新增的必填参数会让原本健康的流程直接崩溃。`oed` 通过成为**一个在运行时发现所有服务的客户端**来彻底绕开这个问题，因此唯一需要版本管理的，只有网关自身的 OpenAPI 规范。

- **零样板代码。** 无需复制粘贴的 OpenAPI 客户端，无需针对每个服务的 SDK，无需转义 `--data`，无需记忆 `User-Agent` 请求头。
- **运行时发现。** 你在上面看到的 `oed cve --help` 列表，是每次调用时从 `https://api-gateway.osinfra.cn/discovery/apis` 动态构建的。新服务、新端点、新的 schema 字段都会在无需升级 `oed` 的情况下自动出现。10 分钟的缓存让 CI 中的密集调用保持低成本；当你知道网关刚发布了更新时，可以用 `oed cache refresh` 强制立即重新拉取。
- **AI 友好的输出。** stdout 上输出单个 JSON 对象，确定性的退出码（`0` 成功 · `1` 用户错误 · `2` 网络错误 · `3` 上游错误 · `4` 未找到）。日志和进度信息输出到 stderr，因此 `| jq` 始终安全。
- **开箱即用的 Claude / Cursor 支持。** 自带 `.claude/skills/oed-cli/SKILL.md`，智能体无需自定义提示词即可知道如何使用它。

## 安装

```
pip install oed-cli
```

或者在隔离环境中安装（推荐用于 CI）：

```
pipx install oed-cli
```

从源码安装：

```
git clone https://gitcode.com/openeuler/oed-cli && cd oed-cli
pip install -e .
```

验证安装：

```
oed --version       # → oed, version 0.3.0
```

## 快速开始：安装后即可调用

```
pip install oed-cli
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
```

### （可选）调用 AtomGit 操作 —— 一次性存储令牌

`ag`（AtomGit）服务上的操作通过 `access_token` 查询参数进行认证。一次性存储个人访问令牌（PAT），之后每次 `oed ag ...` 调用都会自动注入该令牌：

```
oed ag login
```

然后 `oed ag listAuthenticatedUserIssues` 就可以直接使用——无需每次调用都加令牌标志。完整的细节（`oed ag login --token <pat> [--no-verify]`、`--status`、`oed ag logout`、令牌的存储方式、自动注入规则）在下方的 AtomGit（`ag`）认证章节中。

就这样。`oed` 从网关发现该服务，拉取其 OpenAPI 规范，从声明的 `query` 参数推导出 `--cve-id`，填充符合 WAF 要求的浏览器请求头，并通过生产网关发送请求：

```
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

输出是 stdout 上的纯 JSON——可以直接管道传入 `jq`：

```
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 \
  | jq '.response.result[0] | {cveId, affectedProduct, affectedComponent}'
```

```
{
  "cveId": "CVE-2019-10082",
  "affectedProduct": "openEuler-20.03-LTS",
  "affectedComponent": "httpd-2.4.34-18"
}
```

### 想先看看有什么？（可选）

```
oed info      # 网关快照：services_total、缓存状态、社区信息
oed services  # 完整服务列表——选一个接着调用
oed <service> --help                       # 列出该服务的所有操作
oed <service> <operation> --help           # 查看某个操作的所有标志
oed <service> <operation> --dry-run --…    # 预览请求，不访问网络
```

安装后第一次执行 `oed <service> <operation>` 调用会拉取最新的发现数据 + 该服务的 OpenAPI 规范；10 分钟内的后续调用复用本地缓存。当你知道网关刚发布了新内容时，运行 `oed cache refresh` 强制重新拉取。

## "这个操作需要哪些标志？" → `--help`

不要靠猜。每个操作都从 OpenAPI schema 自动推导出自己的标志：

```
oed cve getSecurityNoticeByCveId --help
```

```
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

服务层面同理——用一个标志列出每个操作：

```
oed cve --help | jq '.operations | length'
# → 37
```

## 更多示例

```
# 预演（dry-run）——不访问网络，预览请求
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 --dry-run

# 按日期过滤的查询——分页以 total/page/size 形式返回
oed meeting listMeetings --date 2026-07-29
# → response.data: [ { "topic": "安全sig例会", "group_name": "security-committee",
#                     "date": "2026-07-29", "start": "16:00", "end": "18:00",
#                     "join_url": "https://meeting.huaweicloud.com:36443/#/j/985661561", ... } ]

# 论坛——Discourse 的 `/latest.json`；
oed forum listLatestTopics --per-page 2
# → response.topic_list.topics: [ { "title": "《openEuler社区论坛使用指南&规则》",
#                                   "posts_count": 12, "created_at": "2023-01-16T07:53:30.163Z" }, ... ]

# 搜索——POST JSON 请求体；`keyword` + `lang` 为必填，pageSize 必须在 6-49 之间
oed search multisearchDocByKeyword \
  --json '{"keyword":"软件源安装速度慢怎么办","lang":"zh","page":1,"pageSize":10}'
# → response.obj.records: [ { "title": "<span>软件下载慢问题</span>",
#                             "path": "https://eur.openeuler.openatom.cn/coprs/",
#                             "type": "service", "lang": "zh" }, ... ]
```

## oneid 登录（RFC 8628 设备码流程）

openEuler 网关后的大多数服务（eulermaker、pkgcontrib、meeting……）需要一个**用户身份**——不是 AtomGit PAT，而是你的 openEuler oneid 账号。`oed login` 通过 [RFC 8628 设备授权许可（Device Authorization Grant）](https://datatracker.ietf.org/doc/html/rfc8628) 来获取它：无需本地 HTTP 服务器，无需 `redirect_uri`，无需 `client_secret`，无需在防火墙上开放端口。它在笔记本电脑、SSH 远程连接和容器中的工作方式完全一致。

### 登录

```
oed login
```

`oed` 向 openEuler oneid 请求一个设备码，并将**用户码**和**验证 URL**一起打印到 stderr：

```
Open https://omapi.osinfra.cn/oneid/oidc/device in a browser.
Enter the user code: 98R4-GGKW
Waiting for approval...
```
<img width="846" height="98" alt="oed login 触发后终端输出 user code + verification URL" src="docs/images/oed-login-terminal-output.png" />
<img width="601" height="626" alt="oneid 设备码输入页" src="docs/images/oed-login-device-input.png" />
<img width="527" height="712" alt="openEuler 服务授权页（搜索 / CVE / 论坛 / EulerMaker）" src="docs/images/oed-login-approve.png" />
<img width="500" height="205" alt="授权成功，oed 开始轮询后台" src="docs/images/oed-login-success.png" />

在**任意**浏览器中打开该 URL——本机浏览器、手机，或通过 SSH 连接到远程主机的笔记本——输入用户码，并用你的 openEuler 账号批准该请求。`oed` 在后台轮询 oneid，一旦你批准，就自动存储所获得的 `access_token` + `refresh_token`。你无需将令牌粘贴到终端中。

`oed login` 是 `oed auth login` 的顶层快捷方式；两个名称都有效，并运行相同的设备码流程。

### 令牌存储在哪里

成功登录后，当存在可用的操作系统原生凭据存储时，令牌会被写入其中——macOS 钥匙串、Windows DPAPI / 凭据管理器、Linux SecretService（libsecret）。当无法访问任何凭据存储时（无界面的 Linux、CI、锁定的 GNOME 钥匙串），`oed` 会静默回退到 `<OED_CACHE_DIR>/auth.json`，在 POSIX 上权限为 `0600`；无需用户操作。

| 平台               | 后端                                            |
| ---------------------- | ----------------------------------------------- |
| macOS                  | Keychain（加密）                                |
| Windows                | DPAPI / 凭据管理器（加密）                       |
| Linux 桌面             | SecretService / libsecret（加密）               |
| 无界面的 Linux / CI    | `auth.json` 0600 明文回退                        |

> **为什么 `oed auth status` 在 Windows / Linux 上开箱显示 `plaintext`** —— Python 的 `keyring` 库将各平台后端作为*可选*的额外依赖发布（以便在最小化系统上也能安装）。`oed-cli` 仅依赖 `keyring>=24`；要使用 Windows DPAPI / Linux SecretService 后端，你还需要安装以下之一：
>
> ```
> pip install "oed-cli[os-keyring-windows]"   # Windows DPAPI / 凭据管理器
> pip install "oed-cli[os-keyring-linux]"     # Linux SecretService（libsecret）
> pip install "oed-cli[os-keyring]"           # 两者都装
> ```
>
> 如果没有匹配的后端，`oed` 会静默使用 0600 明文回退——没有任何东西会损坏，`oed auth status` 中的 `backend` 字段会显示为 `"plaintext"`。

随时可用 `oed auth status` 查看当前生效的后端——JSON 中包含一个 `"backend"` 字段（`"keyring"` 或 `"plaintext"`）。当使用 keyring 时，不会向磁盘写入任何 `auth.json`。

登录后，后续每次 `oed <service> <operation>` 调用都会自动将令牌作为 `Authorization: Bearer <token>` 发送——无需每次调用的标志。上游返回的 `401` 会以 `UpstreamError(kind="unauthorized")` 的形式呈现，并附带指向 `oed auth status` 的提示。

### 无界面 / SSH / CI 登录

在无界面主机上（无 `DISPLAY` / `WAYLAND_DISPLAY`）`oed login` 依然可用——它将 URL + 用户码打印到 stderr 并持续轮询。你从**任意**其他浏览器批准；当批准到达时，CLI 会获取令牌。服务器上无需运行浏览器。

要完全跳过自动打开浏览器的尝试（CI / 容器），设置：

```
BROWSER=none oed login
```

> **提示** —— 不要在某个码处于待审批状态时再次运行 `oed login`。新的运行会请求一个**新**的设备码，并覆盖你可能已在浏览器中正在审批的那个码。如果第一个码已过期，只需等待——oneid 会使其失效，`oed login` 会提示你重新开始。

### 其他认证命令

```
oed auth status                  # 是否已加载令牌？（令牌以 first3...last2 形式脱敏；显示后端 + 允许列表）
oed login --manual               # 从 TTY 粘贴令牌（在非 TTY 环境中被拒绝）
oed auth token <bearer> [--cookie "k=v"]   # 直接写入令牌——推荐用于智能体 / CI / 管道脚本
oed auth logout                  # 清除已存储的令牌
```

运行时优先级：`OED_TOKEN` 环境变量优先于已存储的令牌；`OED_COOKIE` 对可选的 `Cookie` 请求头同理。

### 按用户的服务允许列表

当你通过 `oed login`（设备码流程）登录时，oneid 会返回你已为此 CLI 授权的服务列表。`oed` 将其本地存储在 `auth.json.allowlist` 中，并用它来管控后续的 `oed <service> ...` 调用——不在列表上的服务会被以 `kind="service_blocked_by_allowlist"`（退出码 1）拒绝。在 oneid 界面中更新你的允许列表，然后重新运行 `oed login` 刷新本地副本。

当 `auth.json` 缺失、没有 `allowlist` 字段（旧版兼容）或列表为 `[]`（你已在 oneid 中显式清空）时，该检查为**失败放行（fail-open）**。保留命令（`auth`、`services`、`info`、`schema`、`cache`、`--help`、`--version`）完全绕过此管控。`oed auth status` 显示当前列表。

## AtomGit（`ag`）认证

AtomGit 操作通过其规范上声明的 `access_token` 查询参数进行认证。一次性存储个人访问令牌，之后每次 `oed ag ...` 调用都会自动使用它：

```
# 交互式（提示输入令牌，绝不回显）
oed ag login

# 非交互式——适合 CI / 脚本
oed ag login --token <pat>

# 存储前跳过向 AtomGit 验证令牌
oed ag login --token <pat> --no-verify

# 仅报告是否已配置令牌（不访问网络，不提示）
oed ag login --status

# 忘记已存储的令牌
oed ag logout
```

令牌存储：

- 与 `oed login` 使用相同的 keyring 后端存储：当可访问操作系统凭据存储时使用 macOS 钥匙串 / Windows DPAPI / Linux SecretService，否则使用 0600 明文回退文件。
- 位于缓存目录下的 `tokens/` 子目录中（keyring 不可用时为 `tokens/ag.json`）；`oed cache clear` 绝不会触碰凭据。
- 存储在独立的 keyring 条目（`ag:ag`）下，与 `oed login` 的 oneid 令牌分开，因此两套凭据绝不会冲突。

实际调用时的自动注入：

- 如果操作声明了 `access_token` 而你未传入，已存储的令牌会被自动填充——`oed ag listAuthenticatedUserIssues` 直接可用。
- 显式的 `--access-token <pat>` 始终优先于已存储的令牌。
- 如果操作需要令牌而任何地方都没有可用的令牌，你会得到一个清晰的 `ag_token_missing` 错误及提示，而不是一个不透明的网关 401。
- `--dry-run` 和请求回显视图会将令牌掩码为 `<stored>`——真实值仅在网络上传输。

## 本地开发

### 克隆并以可编辑模式安装

```
git clone https://gitcode.com/openeuler/oed-cli && cd oed-cli
pip install -e ".[dev]"
```

`pip install -e .` 使得对源码的修改在下次 `oed` 调用时生效。完成后用 `pip uninstall oed-cli` 卸载即可。

### 运行测试（约 0.3 秒，完全离线）

```
python -m pytest -q
```

58 个测试覆盖了 v0.1 + v0.2 分发、操作帮助速查表、各参数标志的强制转换、`API_` 前缀别名、`resolve_runtime_gateway` 的无回退语义、每条退出码路径，以及 v0.4 的 `ag` 令牌存储（DPAPI/base64）+ 自动注入。它们对发现层进行了 monkeypatch，因此不需要访问网关。

### 对接真实网关进行冒烟测试

```
# 1) 健康检查
oed info
# → {"ok": true, "services_total": 8, "community": "openeuler", ...}

# 2) 真实的 CVE 查询（规范的端到端测试）
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082

# 3) dry-run 检查 URL + 请求体，不访问网络
oed cve getSecurityNoticeByCveId --cve-id 1 --dry-run

# 4) 验证规范的 URL 路由
oed cve getSecurityNoticeByCveId --dry-run --cve-id 1 \
  | jq '.url'
# → "https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId"
```

### 缓存调试

```
oed cache show      # 路径、大小、年龄、TTL
oed cache refresh   # 强制重新拉取发现数据
oed cache clear     # 删除缓存文件
```

缓存位于 `~/.cache/oed-cli/`（XDG）或 Windows 上的 `C:\Users\<你>\AppData\Local\oed-cli\cache\`。可用 `OED_CACHE_DIR=...` 覆盖。各服务的 OpenAPI 规范缓存于其旁的 `specs/<community>/<service>.json`，具有相同的 10 分钟 TTL。认证文件（`auth.json`）以及交换端点所内置的 OAuth 客户端凭据也根植于同一个 `OED_CACHE_DIR` 下。

### 与网关保持同步

`oed` **不会**在每次调用时都预热一份新的发现数据——只有 10 分钟窗口内的第一条命令会真正访问网关，其余都从 `~/.cache/oed-cli/discovery.json` 读取。这使得运行数十个 `oed` 调用的 CI 脚本不会反复冲击网关，也意味着 `oed --help` 和 `oed --version` 绝不会访问网络。

当网关新增服务或操作时，手动刷新缓存：

```
# 最快：丢弃 10 分钟 TTL 并重新拉取发现数据
oed cache refresh

# 或者，从已知干净状态彻底清除并重新拉取
oed cache clear && oed info

# 然后确认新服务现在已可见
oed services | jq -r '.[] | .service_name'
```

首次调用一个全新服务时，还会将其 OpenAPI 规范拉取到 `specs/<community>/<service>.json`（同样为 10 分钟 TTL）；之后就像其他任何规范一样被复用。

**为什么这不是自动的。** openEuler 基础设施服务以每周到每季度的节奏通过评审新增，而非每分钟一次。在每次 `oed` 调用时自动刷新，会为每次 CLI 调用白白消耗一次网络往返，毫无实际收益。TTL 的存在是为了吸收 CI 的密集调用，而非延迟新端点的可见性。

**未来计划** —— `oed whatsnew`（规划中）将把新拉取的数据与前一次缓存进行 diff，只打印变化的部分，这样你就不必每周肉眼查看 `oed services` 的输出了。

**缓存完整性。** 本地缓存是纯 JSON，位于用户可写的目录中，因此理论上同用户的恶意进程可以重写它。`oed` 采取了三项防御措施（issue #22）：（1）缓存时间戳若超前当前时间约 5 分钟以上，则被视为投毒并被丢弃——这封堵了"把时间戳设到未来以使条目永不过期"的伎俩；（2）运行时 `base_url` 的协议若非 `https`（例如被投毒成 `http://evil.com`），会在附加任何凭据之前被硬性拒绝，因此被篡改的主机无法通过明文窃取你的 Bearer 令牌 / `ag` PAT；（3）缓存写入是原子的（临时文件 + 替换），因此写入中途崩溃不会留下损坏的文件。如果你看到 `insecure_base_url`，请运行 `oed cache clear` 后重试。请注意这是纵深防御——能写缓存的同用户进程通常也能直接读取你的 keyring。

### 常见错误

| 症状 | 原因 | 解决方法 |
| --- | --- | --- |
| `ModuleNotFoundError: oed_cli` | 环境中未安装 | `pip install -e .` |
| `oed info` 卡住或 `waf_block` 退出码 2 | 网关不可达 / WAF | 确认 `curl https://api-gateway.osinfra.cn`；参见 `context/discoverAPI.md` §6 |
| Windows 上中文输出乱码 | 控制台代码页非 UTF-8 | `chcp 65001`，或管道 `| python`，或 `PYTHONIOENCODING=utf-8 oed …` |
| 已知服务上出现 `error="spec_missing"`（退出码 4） | 上游尚未发布规范 | 等待网关侧的 OpenAPI yaml；oed 侧无需操作 |
| `ag` 调用出现 `error="ag_token_missing"` | 操作需要令牌，但未存储 | `oed ag login`（或传入 `--access-token <pat>`） |
| POST 时出现 `error="body_fields_via_params"`（退出码 1） | 请求体字段通过 `--params` 传入（它只覆盖 query/path） | 改用 `--json '{...}'` 重新发送字段——`oed <service> <op> --help` 会列出请求体 schema |
| `forum` 调用时出现 `error="gateway_managed_param"`（退出码 1） | 传入了 `Api-Key` / `Api-Username`（`--api-key` 或 `--params`） | 删除它们——`oed` 会在每次 `forum` 调用时自动填充这两个占位请求头，由网关转换 |
| 出现 `error="service_blocked_by_allowlist"`（退出码 1） | 该服务不在你的 oneid 允许列表中 | 在 oneid 中更新允许列表，然后 `oed login` 刷新本地副本 |
| 已认证调用出现 `error="unauthorized"`（退出码 3） | 令牌缺失 / 过期 / 角色错误 | `oed auth status`；重新运行 `oed login` |
| `cve` 调用退出码 2（`waf_block`） | 规范指向 `.test.osinfra.cn` 主机 | 已处理——`oed` 从发现数据读取 `base_url`（无回退），并忽略规范中的 `x-apigateway-backend.httpEndpoints.address` 作为主机 |

### 离线模式

`oed --help`、`oed info`（使用缓存数据）、`pytest`，以及任何针对其规范已在本地缓存中的服务的命令，都可以在无网络的情况下工作。要在不安装的情况下从源码运行 `oed`：

```
python -m oed_cli --help
# 或
python -c "from oed_cli.main import main; sys.argv = ['oed','--help']; main()"
```

## 文档

- 设计文档 —— 架构、命令契约、退出码、打包、路线图。
- 本地测试指南 —— 安装、冒烟测试、各参数标志演示。
- 发现 API 参考 —— `oed` 所消费的数据源，以及 WAF 注意事项。

## 贡献

欢迎在 [gitcode.com/openeuler/oed-cli](https://gitcode.com/openeuler/oed-cli) 提交 issue 和补丁。

开发安装：

```
pip install -e ".[dev]"
pytest
ruff check src tests
```

## 许可证

Apache-2.0。详见 [LICENSE](https://gitcode.com/openeuler/oed-cli/tree/master/LICENSE)。
