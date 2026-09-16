# oed-cli 常用命令解析

> 返回 [README](../README_cn.md)

这是一份命令速查表。`oed` 没有固定的子命令列表，下面列出的服务名（`cve`、`meeting`、`quickissue` 等）和操作名都是运行时从网关发现的，随时可能新增；速查表里的条目是当前已验证可用的一批，完整列表以 `oed services` 的实时输出为准。

## 全局命令

| 命令 | 作用 |
| --- | --- |
| `oed --help` | 列出当前从网关发现的全部服务及一句话简介 |
| `oed --version` | 打印版本号 |
| `oed info` | 网关快照：`services_total`、缓存状态、社区信息 |
| `oed services` | 完整服务列表（JSON 数组），逐个给出 `service_name`、`title`、`description` |
| `oed schema <service>` | 打印该服务完整的 OpenAPI 3.x 规范 |

## 发现服务和操作

`oed` 不内置命令表，所有子命令都是从 OpenAPI schema 现场推导出来的：

| 命令 | 作用 |
| --- | --- |
| `oed <service> --help` | 列出该服务下的全部操作名（`operation_id`） |
| `oed <service> <operation> --help` | 列出该操作的方法、路径、每个参数对应的 `--flag`、是否必填 |
| `oed schema <service>.<operation>` | 只打印该操作对应的 schema 片段 |

例如 `oed quickissue listIssues --help` 会把 `org`、`repo`、`sig`、`state`、`label` 等二十来个 query 参数逐个列成 `--org`、`--repo`、`--sig`……不用去翻接口文档背参数名。

## 调用参数怎么传

| 场景 | 写法 |
| --- | --- |
| 一般的 query / path 参数 | 直接用 `--help` 里列出的 `--<kebab-case>` flag，例如 `--cve-id CVE-2019-10082` |
| 一次性传一堆 query / path 参数 | `--params '{"key":"value", ...}'`，只覆盖 query/path，不会写进请求体 |
| POST 请求体（`has_body: true` 的操作） | `--json '{"keyword":"...", "pageSize":10}'`；请求体字段以 `--help` 输出里的 `body_required_fields` 为准 |
| 只想看请求会发成什么样，不真的访问网络 | 加 `--dry-run`，输出里的 `url`、`query`、`body` 就是实际会发送的内容 |

POST 操作如果把请求体字段误传进 `--params`，会拿到 `error="body_fields_via_params"`（退出码 1）；这时改用 `--json` 重新传即可。

## 缓存管理

| 命令 | 作用 |
| --- | --- |
| `oed cache show` | 查看缓存路径、大小、年龄、TTL |
| `oed cache refresh` | 丢弃 10 分钟 TTL，强制重新拉取发现数据 |
| `oed cache clear` | 删除缓存文件（不会动 `auth.json` 或 `tokens/`） |

## 登录和认证

| 命令 | 作用 |
| --- | --- |
| `oed login`（= `oed auth login`） | RFC 8628 设备码流程登录 openEuler oneid |
| `oed login --manual` | 从 TTY 手动粘贴令牌 |
| `oed auth status` | 查看当前令牌是否有效、存储后端、服务允许列表 |
| `oed auth token <bearer> [--cookie "k=v"]` | 直接写入令牌，适合 Agent / CI |
| `oed auth logout` | 清除已存储的 oneid 令牌 |
| `oed ag login [--token <pat>] [--no-verify]` | 存储 AtomGit 个人访问令牌 |
| `oed ag login --status` | 只查是否已配置令牌，不访问网络 |
| `oed ag logout` | 清除已存储的 AtomGit 令牌 |

详见 [oneid 登录](login_cn.md) 和 [AtomGit（`ag`）认证](atomgit_auth_cn.md)。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 用户错误（参数错误、请求体字段传错位置、服务不在允许列表等） |
| `2` | 网络错误（网关不可达、WAF 拦截） |
| `3` | 上游错误（网关或后端服务返回错误，含未授权 `401`） |
| `4` | 未找到（资源不存在、`spec_missing`） |

## 按需求查命令

下表把常见需求直接对应到验证过的真实命令，每一条都在 [调用场景示例](scenarios_cn.md) 里有完整输出可以对照。

| 需求 | 命令 |
| --- | --- |
| 看有哪些服务可用 | `oed services` |
| 看某个服务有哪些操作 | `oed <service> --help` |
| 看某个操作需要哪些参数 | `oed <service> <operation> --help` |
| 查 CVE 公告 | `oed cve getSecurityNoticeByCveId --cve-id <cve-id>` |
| 按关键词找有 PR 活动的 SIG 组 | `oed quickissue listPullSigs --keyword <关键词>` |
| 看全量 SIG 名称列表 | `oed search searchSigName` |
| 看某个 SIG 名下的仓库 | `oed quickissue listRepos --sig <sig-name>` |
| 看某个 SIG 的例会记录 | `oed meeting listSigMeetings --gn <sig-name>` |
| 按日期查会议 | `oed meeting listMeetings --date <YYYY-MM-DD>` |
| 查某个软件包在哪些 openEuler 版本上有制品 | `oed easysoftware queryRpmEulerVersions --name <pkg-name>` |
| 按 pkgId 查软件包详情（下载地址、安装方法） | `oed easysoftware searchRPMPkg --pkg-id <pkg-id>` |
| 查某个仓库的 Issue 列表 | `oed quickissue listIssues --repo <org/repo> --state open` |
| 查某个仓库的 PR 列表 | `oed quickissue listPulls --repo <org/repo>` |
| 拉取某个 PR 门禁失败的 CI 日志 | `oed CI getPRCILogs --arch <arch> --repo <repo> --pr <jenkins-build-no>` |
| 论坛最新话题 | `oed forum listLatestTopics --per-page <n>` |
| 社区统一搜索 | `oed search multisearchDocByKeyword --json '{"keyword":"...","lang":"zh"}'` |
| 登录 oneid | `oed login` |
| 查看登录状态 | `oed auth status` |
| 存 AtomGit 个人访问令牌 | `oed ag login` |
| 强制刷新发现缓存 | `oed cache refresh` |
