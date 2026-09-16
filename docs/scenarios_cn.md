# 调用场景示例

以下示例假设已完成安装（见 [README](../README_cn.md) 的安装章节）。

如果调用是交给 Agent（Claude / Cursor 等）执行的，通常不需要背下这些命令：按 README「快速开始」里说的跑一次 `oed --help` 交给 Agent 学习即可，Agent 会自己探索出可用服务和参数。这份文档是给需要手动调用、或者想看清楚 `oed` 具体怎么工作的人准备的参考。

## 查询 CVE 公告

```
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
```

`oed` 从网关发现该服务，拉取其 OpenAPI 规范，从声明的 `query` 参数推导出 `--cve-id`，填充符合 WAF 要求的浏览器请求头，并通过生产网关发送请求，stdout 上输出纯 JSON：

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

可以直接接 `jq`：

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

## 查看某个操作需要哪些参数

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

服务层面同理，用一个标志列出每个操作：

```
oed cve --help | jq '.operations | length'
# → 37
```

## 摸底：有哪些服务和操作可以调用

```
oed info      # 网关快照：services_total、缓存状态、社区信息
oed services  # 完整服务列表，选一个接着调用
oed <service> --help                       # 列出该服务的所有操作
oed <service> <operation> --help           # 查看某个操作的所有标志
oed <service> <operation> --dry-run --…    # 预览请求，不访问网络
```

安装后第一次执行 `oed <service> <operation>` 调用会拉取最新的发现数据和该服务的 OpenAPI 规范；10 分钟内的后续调用复用本地缓存。当已知网关刚发布了新内容时，运行 `oed cache refresh` 强制重新拉取。

## 更多示例

```
# 预演（dry-run），不访问网络，预览请求
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 --dry-run

# 按日期过滤的查询，分页以 total/page/size 形式返回
oed meeting listMeetings --date 2026-07-29
# → response.data: [ { "topic": "安全sig例会", "group_name": "security-committee",
#                     "date": "2026-07-29", "start": "16:00", "end": "18:00",
#                     "join_url": "https://meeting.huaweicloud.com:36443/#/j/985661561", ... } ]

# 论坛，Discourse 的 `/latest.json`
oed forum listLatestTopics --per-page 2
# → response.topic_list.topics: [ { "title": "《openEuler社区论坛使用指南&规则》",
#                                   "posts_count": 12, "created_at": "2023-01-16T07:53:30.163Z" }, ... ]

# 搜索，POST JSON 请求体；`keyword` + `lang` 为必填，pageSize 必须在 6-49 之间
oed search multisearchDocByKeyword \
  --json '{"keyword":"软件源安装速度慢怎么办","lang":"zh","page":1,"pageSize":10}'
# → response.obj.records: [ { "title": "<span>软件下载慢问题</span>",
#                             "path": "https://eur.openeuler.openatom.cn/coprs/",
#                             "type": "service", "lang": "zh" }, ... ]
```

## 找出正在推进某个方向工作的 SIG 组

`quickissue` 的 PR 看板接口可以按关键词模糊匹配 SIG 组名，只返回有 PR 活动的组，信噪比比全量列表高：

```
oed quickissue listPullSigs --keyword AI
```

```
{ "code": 200, "msg": "Success", "data": ["ai"] }
```

```
oed quickissue listPullSigs --keyword intelligence
```

```
{ "code": 200, "msg": "Success", "data": ["sig-intelligence"] }
```

想看不区分是否有 PR 活动的完整 SIG 名单，换成 `oed search searchSigName`，返回一份包含 `sig-intelligence`、`ai`、`sig-CloudNative` 等上百个组名的数组。

## 查看某个 SIG 组的例会内容

拿到 SIG 组名（即上一步返回的 `group_name`，带不带 `sig-` 前缀视具体组而定）之后：

```
oed meeting listSigMeetings --gn sig-intelligence --size 3
```

```
{
  "code": 200,
  "msg": "success",
  "data": [
    {
      "group_name": "sig-intelligence",
      "topic": "sig-intelligence 例会",
      "date": "2026-09-11",
      "start": "10:30",
      "end": "11:30",
      "agenda": "1. 申请在 src-openEuler 建仓 python-annotated-doc -- 赵家麒\n2. 申请新增 maintainer -- 赵家麒",
      "join_url": "https://us06web.zoom.us/j/89204968325?pwd=...",
      "etherpad": "https://etherpad.openeuler.org/p/sig-intelligence-meetings",
      ...
    }
  ],
  "total": 44
}
```

`--gn` 必须是 `group_name` 这个内部标识，不是页面上展示的中文名。`etherpad` 字段指向该 SIG 的例会纪要合集，比单条会议记录信息量更大。按日期过滤全社区会议用 `oed meeting listMeetings --date 2026-07-29`（见下方「更多示例」）。

## 查询某个 SIG 相关的仓库

```
oed quickissue listRepos --sig sig-intelligence --per-page 5
```

```
{
  "total": 157,
  "page": 1,
  "per_page": 5,
  "data": [
    { "repo": "openeuler/aa-ui", "sig": "sig-intelligence" },
    { "repo": "openeuler/AcTrail", "sig": "sig-intelligence" },
    { "repo": "openeuler/agent-insight", "sig": "sig-intelligence" },
    { "repo": "openeuler/AgentBoost", "sig": "sig-intelligence" },
    { "repo": "openeuler/agentic-engineering-team", "sig": "sig-intelligence" }
  ]
}
```

`repo` 字段已经带 `org/` 前缀，可以直接拼到 AtomGit 域名下打开，比如 `https://atomgit.com/openeuler/aa-ui`。想按关键字而不是 SIG 名过滤，用 `--keyword` 代替 `--sig`。

## 查询软件包在哪些 openEuler 版本上有发布制品

```
oed easysoftware queryRpmEulerVersions --name redis
```

```
{
  "code": 200,
  "msg": "OK",
  "data": {
    "list": [
      { "os": "openEuler-25.09", "arch": "x86_64", "pkgId": "openEuler-25.09EPOLmainx86_64redis8.2.1-2.oe2509x86_64" },
      { "os": "openEuler-24.03-LTS-SP4", "arch": "x86_64", "pkgId": "openEuler-24.03-LTS-SP4everythingx86_64redis7.2.14-1.oe2403sp4x86_64" },
      { "os": "openEuler-22.03-LTS", "arch": "aarch64", "pkgId": "openEuler-22.03-LTSeverythingaarch64redis4.0.14-1.oe2203aarch64" },
      ...
    ],
    "total": 62
  }
}
```

`--name` 要求精确匹配包名。每条记录的 `pkgId` 尾部编码了该版本 + 架构下的确切 rpm 版本号，直接读出来即可，不用再解析文件名。想看某一条记录的完整详情（下载地址、安装命令、维护者），把 `pkgId` 原样传给 `searchRPMPkg`：

```
oed easysoftware searchRPMPkg --pkg-id "openEuler-24.03-LTS-SP4everythingx86_64redis7.2.14-1.oe2403sp4x86_64"
```

```
{
  "data": {
    "list": [{
      "name": "redis",
      "version": "7.2.14-1.oe2403sp4",
      "os": "openEuler-24.03-LTS-SP4",
      "arch": "x86_64",
      "binDownloadUrl": "https://repo.openeuler.org/openEuler-24.03-LTS-SP4/everything/x86_64/Packages/redis-7.2.14-1.oe2403sp4.x86_64.rpm",
      "installation": "- 添加源\n  ```\n  dnf config-manager --add-repo https://repo.openeuler.org/openEuler-24.03-LTS-SP4/everything/x86_64\n  ```\n...",
      ...
    }],
    "total": 1
  }
}
```

## 查询仓库的 Issue 列表

```
oed quickissue listIssues --repo openeuler/kernel --state open --per-page 3
```

```
{
  "total": 2039,
  "page": 1,
  "per_page": 3,
  "data": [
    {
      "repo": "openeuler/kernel",
      "sig": "Kernel",
      "link": "https://atomgit.com/openeuler/kernel/issues/9989",
      "state": "open",
      "issue_type": "内核需求",
      "issue_state": "新建",
      "title": "[OLK-6.6] sched_ext: Slove the failed to register kfunc sets  issue.",
      "author": "phytium_xuxian",
      "created_at": "2026-09-16 17:05:02",
      ...
    }
  ]
}
```

`--repo` 要传 `org/repo` 全名，只传仓库名（如 `kernel`）会查不到任何结果。注意 `issue_state`（社区自定义的中文状态，如「新建」「待办的」）和 `state`（`open`/`closed`）是两个不同字段；按 GitHub 风格的开关状态过滤用 `--state open`，按社区工作流状态过滤用 `--issue-state`。还可以叠加 `--sig`、`--author`、`--label`、`--milestone` 等参数缩小范围。

## 分析 PR 门禁 CI 失败原因

`CI` 服务只有一个操作 `getPRCILogs`，它要的是 Jenkins 的架构目录、仓库名、构建号，不是 PR 链接本身：

```
oed CI getPRCILogs --arch x86-64 --repo openjdk-1.8.0 --pr 1162 --dry-run
```

```
{
  "url": "https://ci.openeuler.openatom.cn/job/multiarch/job/src-openeuler/job/x86-64/job/openjdk-1.8.0/1162/consoleText",
  "note": "request was not sent."
}
```

拿到这三个值的办法：打开 PR 页面（例如 `https://atomgit.com/src-openeuler/openjdk-1.8.0/pull/645`），在门禁检查列表里找到状态为失败的 `x86_64 check_build` / `aarch64 check_build`，点开它链接的 Jenkins 构建页，URL 形如 `.../job/x86-64/job/openjdk-1.8.0/1162/`；把其中的架构目录（`x86-64`/`aarch64`，注意 x86_64 在这里写作 `x86-64`）、仓库名、末尾的构建号分别填进 `--arch`、`--repo`、`--pr`，去掉 `--dry-run` 就能拉到原始控制台日志（`consoleText`，纯文本，可以直接 `grep` 找 `ERROR`/`FAILED`）。构建号不是 PR 号，两者通常不相等。如果构建已经过了 Jenkins 的日志保留期，会返回 404。

完整的命令速查表（含以上全部操作和更多）见 [oed-cli 常用命令解析](commands_cn.md)。

## 需要登录的场景

- 调用需要用户身份的服务（`eulermaker`、`pkgcontrib`、`meeting` 等）：先执行 `oed login`，见 [oneid 登录](login_cn.md)。
- 调用 AtomGit（`ag`）相关操作：先执行 `oed ag login` 一次性存储个人访问令牌，之后每次 `oed ag ...` 调用自动注入，见 [AtomGit（`ag`）认证](atomgit_auth_cn.md)。
