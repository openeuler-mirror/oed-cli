<h1 align="center">oed-cli</h1>

<p align="center">
  <strong>一条命令，打通 openEuler 全部在线服务 —— 自动发现、JSON 优先、为人和 AI Agent 而生。</strong>
  <br />
  <em>支持 Claude Code、Cursor、VS Code、Codex、Gemini CLI、OpenClaw、Kimi Code、Kiro、扣子等 17+ 个客户端，以及普通的 shell 管道。</em>
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_cn.md">简体中文</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/oed-cli/"><img src="https://img.shields.io/pypi/v/oed-cli" alt="PyPI" /></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" /></a>
  <a href="https://gitcode.com/openeuler/oed-cli/tree/master/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="License: Apache-2.0" /></a>
  <a href="#一键安装-cli--mcp"><img src="https://img.shields.io/badge/MCP-oed--mcp-6f42c1" alt="MCP: oed-mcp" /></a>
  <a href=".claude/skills/oed-cli/SKILL.md"><img src="https://img.shields.io/badge/Agent_Skill-bundled-0969da" alt="Agent Skill: bundled" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/平台-17%2B-orange" alt="平台: 17+" /></a>
</p>

<p align="center">
  <a href="#支持平台"><img src="https://img.shields.io/badge/Claude_Code-D97757" alt="Claude Code" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Cursor-000000" alt="Cursor" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Trae-7e22ce" alt="Trae" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Codex-1F2937" alt="Codex" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Gemini_CLI-4285F4" alt="Gemini CLI" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Qwen_Code-615CED" alt="Qwen Code" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/dsh-24292e" alt="dsh" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/OpenClaw-DC2626" alt="OpenClaw" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Hermes_Agent-BE185D" alt="Hermes Agent" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Kimi_Code-0F766E" alt="Kimi Code" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/VS_Code-007ACC" alt="VS Code" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Copilot_CLI-1F6FEB" alt="Copilot CLI" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Antigravity-8B5CF6" alt="Antigravity" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Pi-F59E0B" alt="Pi" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Nanobot-10B981" alt="Nanobot" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Kiro-FF9900" alt="Kiro" /></a>
  <a href="#支持平台"><img src="https://img.shields.io/badge/Coze-4D6BFE" alt="Coze" /></a>
</p>

---

**`oed` 并不内置一份静态的命令列表。** 它在运行时读取 openEuler 基础设施发现服务（Infra Discovery Service），并动态构建出完整的命令接口。当有新服务上线时，`oed` 会自动识别——无需升级。

## 为什么选择 oed？

`oed` 的存在是为了解决一个具体问题：一个要与*众多不断演进的服务*交互的 CLI，而不让这种复杂性成倍增长。为每个服务的每个版本手工编写客户端，正是 Zylos API 版本管理研究中所警告的那种版本压力——而对于 AI 智能体这类使用者来说，这种压力尤为突出：一个被重命名的字段会悄无声息地破坏一次工具调用，一个新增的必填参数会让原本健康的流程直接崩溃。`oed` 通过成为**一个在运行时发现所有服务的客户端**来彻底绕开这个问题，因此唯一需要版本管理的，只有网关自身的 OpenAPI 规范。

- **零样板代码。** 无需复制粘贴的 OpenAPI 客户端，无需针对每个服务的 SDK，无需转义 `--data`，无需记忆 `User-Agent` 请求头。
- **运行时发现。** 你在上面看到的 `oed cve --help` 列表，是每次调用时从 `https://api-gateway.osinfra.cn/discovery/apis` 动态构建的。新服务、新端点、新的 schema 字段都会在无需升级 `oed` 的情况下自动出现。10 分钟的缓存让 CI 中的密集调用保持低成本；当你知道网关刚发布了更新时，可以用 `oed cache refresh` 强制立即重新拉取。
- **AI 友好的输出。** stdout 上输出单个 JSON 对象，确定性的退出码（`0` 成功 · `1` 用户错误 · `2` 网络错误 · `3` 上游错误 · `4` 未找到）。日志和进度信息输出到 stderr，因此 `| jq` 始终安全。
- **开箱即用的 Claude / Cursor 支持。** 自带 `.claude/skills/oed-cli/SKILL.md`，智能体无需自定义提示词即可知道如何使用它。
- **MCP 就绪。** 同一套工具通过 [`oed-mcp`](https://github.com/Hourunze1997/oed-mcp)（`npx -y oed-mcp`）触达所有 MCP 客户端——下面的 `install.sh`（Windows 用 `install.ps1`）一行接入。

## 安装

三种安装方式，按使用场景选一种即可。

**方式一：pip（日常使用）**

```
pip install oed-cli
```

**方式二：pipx（隔离环境，推荐用于 CI）**

```
pipx install oed-cli
```

**方式三：源码安装（本地开发、调试）**

```
git clone https://gitcode.com/openeuler/oed-cli && cd oed-cli
pip install -e .
```

**方式四：脚本一键安装（CLI + MCP + Skills打包安装，推荐）**

linux客户端:
```bash
# 自动探测客户端，或显式指定
curl -fsSL https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.sh | bash
curl -fsSL https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.sh | bash -s cursor
```
注意：受 PEP 668 限制（Ubuntu 23.04+/Debian 12+ 禁止用系统 pip 装包），该脚本执行时需要安装pipx，然后再将`oed` 装进 pipx 的独立 venv。


windows客户端:
```powershell
# 自动探测客户端，或显式指定
powershell -c "irm https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.ps1 | iex"

powershell -c "& ([scriptblock]::Create((irm https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.ps1))) cursor"
```

安装完成后，验证是否成功：

```
oed --version       # → oed, version 0.3.1
```

## 快速开始(试一句)

在执行以下涉及到读写的场景时，请确保已登录。
```
oed login     # 登录openEuler OneID
oed ag login  # 登录atomgit
```

```
帮我分析这个pr为什么失败了https://atomgit.com/src-openeuler/gstreamer1-plugins-bad-free/pull/162
```

```
帮我修复这个CVE https://atomgit.com/src-openeuler/dbus/issues/48
```

```
帮我归纳整理openEuler社区TC近期的会议内容
```

需要登录才能调用的场景：

- 调用需要用户身份的服务（`eulermaker`、`pkgcontrib`、`meeting` 等）：先执行 `oed login`，见 [oneid 登录](docs/login_cn.md)。
- 调用 AtomGit（`ag`）相关操作：先执行 `oed ag login` 一次性存储个人访问令牌，见 [AtomGit（`ag`）认证](docs/atomgit_auth_cn.md)。

想不通过 Agent、自己动手调用？完整的调用示例（CVE 查询、找 SIG 组、看例会、查仓库、查软件包制品、查 Issue、分析 PR 门禁 CI 失败）见 [调用场景示例](docs/scenarios_cn.md)；命令速查表见 [oed-cli 常用命令解析](docs/commands_cn.md)。


## 支持平台

| 平台 | 状态 | 安装方式 |
|----------|--------|----------------|
| Claude Code | Shell-install | `install.sh claude` · 或 `claude mcp add -s user oed -- npx -y oed-mcp` · 或安装自带 [Agent Skill](#安装-agent-skill) |
| Cursor | Shell-install | `install.sh cursor` |
| Trae / Trae 中国版 | Shell-install | `install.sh trae` / `install.sh trae-cn` |
| Codex CLI | Shell-install | `install.sh codex` |
| Gemini CLI | Shell-install | `install.sh gemini` |
| Qwen Code | Native | `qwen extensions install oed-mcp`（npm 渠道）· 或 `install.sh qwen` |
| dsh | Shell-install | `install.sh dsh` |
| OpenClaw | Shell-install | `install.sh openclaw` · 或 `openclaw mcp add oed --command npx --arg -y --arg oed-mcp` |
| Hermes Agent | Shell-install | `install.sh hermes` · 或在 `~/.hermes/config.yaml` 的 `mcp_servers` 下加 `oed` |
| Kimi Code CLI | Shell-install | `install.sh kimi` · 或在 `~/.kimi-code/mcp.json` 的 `mcpServers` 里加 `oed` · 或 TUI 里 `/mcp-config` |
| VS Code（含 Copilot Chat） | Shell-install | `install.sh vscode` · 或 `code --add-mcp '{"name":"oed","command":"npx","args":["-y","oed-mcp"]}'` |
| Copilot CLI | Shell-install | `install.sh copilot` · 或 `copilot mcp add oed -- npx -y oed-mcp` |
| Antigravity | Shell-install | `install.sh antigravity`（写 `~/.gemini/antigravity/mcp_config.json`） |
| Pi | Shell-install | `install.sh pi` · 或 `pi mcp add oed -- npx -y oed-mcp` |
| Nanobot | Shell-install | `install.sh nanobot`（写 `~/.nanobot/config.json` 的 `tools.mcpServers`） |
| Kiro CLI / IDE | Shell-install | `install.sh kiro`（写 `~/.kiro/settings/mcp.json`，两个产品共用） |
| Cline / 其他 MCP 客户端 | Shell-install | 在其配置中加入 `npx -y oed-mcp` |
| 扣子 Coze（云端） | Native | 扣子插件商店搜索 **oed** |

> **状态的含义** —— 状态词中英文 README 统一写法，不做翻译。
>
> - **Native** —— 平台用自己的机制就能发现并添加 `oed`，无需任何脚本：应用内商店搜索（扣子——插件商店搜 **oed**），或官方包渠道按名安装（Qwen Code——`qwen extensions install oed-mcp` 从 npm 拉取，前提是 npm 包里带上 `qwen-extension.json` 清单）。
> - **Auto-discover** —— 平台目录自动收录，无需人工提交：要么是官方爬虫（Gemini CLI 的 [extension gallery](https://geminicli.com/extensions) 盯着 `gemini-cli-extension` GitHub topic），要么是自动爬 npm 的目录站（下面的 Glama）。目前还没有哪个 Agent 平台对我们生效——Gemini 的 gallery 尚未索引到仓库，收录后 Gemini CLI 即转为 Auto-discover。
> - **Shell-install** —— 平台任何目录都还没有收录 `oed`，用一条命令接入：`install.sh <client>`，或表中等价的手动命令。Gemini CLI 目前落在这里——它原生的 `extensions install` 机制通过脚本完全可用，只是官方 gallery 还没列出我们。

MCP 需要 Node ≥ 18（`npx`）；CLI 本身需要 Python ≥ 3.10。MCP server 首次调用会自动引导安装 `oed` CLI，所以客户端侧只需要做接入这一步。

> **MCP 目录收录** —— `oed-mcp` 目前能被搜到的地方。Glama 是自动爬 npm 收录的（真正的自动发现）；npm 是我们自己发布的；其余尚未收录、计划提交：

| 目录 | 收录页 | 来源 |
|----------|--------|------|
| npm | [npmjs.com/package/oed-mcp](https://www.npmjs.com/package/oed-mcp) | 我们发布 |
| Glama | [glama.ai/mcp/servers/Hourunze1997/oed-mcp](https://glama.ai/mcp/servers/Hourunze1997/oed-mcp) | 自动爬取 |
| Smithery | — | 尚未收录（计划提交） |
| MCP Registry（官方） | — | 尚未收录（计划提交） |
| Cline 插件市场 | — | 尚未收录（计划提交） |

无论从 npm 还是哪个自动收录的目录跳过来，安装方式都是同一条：`npx -y oed-mcp`。

## 更多场景

- [oed-cli 常用命令解析](docs/commands_cn.md) —— 全局命令、服务 / 操作发现、参数传法、缓存与认证命令、退出码，以及一张按需求查命令的速查表。
- [调用场景示例](docs/scenarios_cn.md) —— 不经 Agent、自己手动调用的完整示例：查 CVE 公告、按关键词找 SIG 组、看 SIG 例会、查 SIG 相关仓库、查软件包跨版本制品、查仓库 Issue 列表、分析 PR 门禁 CI 失败原因、会议 / 论坛 / 搜索。
- [oneid 登录（RFC 8628 设备码流程）](docs/login_cn.md) —— 需要用户身份的服务怎么登录、令牌存在哪、无界面 / SSH / CI 场景怎么处理。
- [AtomGit（`ag`）认证](docs/atomgit_auth_cn.md) —— 存储个人访问令牌，`oed ag ...` 调用自动注入。
- [本地开发](docs/local_dev_cn.md) —— 克隆安装、跑测试、冒烟测试、缓存调试、常见错误排查、离线模式。

## 贡献

欢迎在 [gitcode.com/openeuler/oed-cli](https://gitcode.com/openeuler/oed-cli) 提交 issue 和补丁，开发安装步骤见[本地开发](docs/local_dev_cn.md)。

## 许可证

Apache-2.0。详见 [LICENSE](https://gitcode.com/openeuler/oed-cli/tree/master/LICENSE)。

---

<p align="center">
  <strong>一个客户端，全部 openEuler 服务。人和 Agent 都好用。</strong>
</p>
