# oed-cli

> **oed** —— 一个面向 openEuler 社区服务的命令行工具。自动发现、JSON 优先、AI 友好。为开发者和Agent协作贡献提供辅助。

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

安装完成后，验证是否成功：

```
oed --version       # → oed, version 0.3.0
```

## 快速开始

```
pip install oed-cli
```

打开你的 Agent（OpenCode / Claude / Cursor 等），输入：

```
请学习 oed --help 有哪些命令？
```

Agent 会从网关读取实时服务列表，自己学习有哪些服务、每个操作需要什么参数，不需要提前记命令，也不用先读文档。之后直接用自然语言告诉 Agent 你要做什么，比如“查一下 CVE-2019-10082 的公告”，Agent 会自动选服务、按 `--help` 推导出的参数拼出调用。

需要登录才能调用的场景：

- 调用需要用户身份的服务（`eulermaker`、`pkgcontrib`、`meeting` 等）：先执行 `oed login`，见 [oneid 登录](docs/login_cn.md)。
- 调用 AtomGit（`ag`）相关操作：先执行 `oed ag login` 一次性存储个人访问令牌，见 [AtomGit（`ag`）认证](docs/atomgit_auth_cn.md)。

想不通过 Agent、自己动手调用？完整的调用示例（CVE 查询、找 SIG 组、看例会、查仓库、查软件包制品、查 Issue、分析 PR 门禁 CI 失败）见 [调用场景示例](docs/scenarios_cn.md)；命令速查表见 [oed-cli 常用命令解析](docs/commands_cn.md)。

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
