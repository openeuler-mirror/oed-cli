---
name: ci-log-analysis
description: 定位 openEuler AtomGit PR 门禁/CI 构建失败根因。输入 PR 链接（或 owner/repo/pr），按「ag 门禁概览 → CI Jenkins 控制台日志 → eulermaker CBS 构建记录」三级下钻，直达编译/日志报错并给出修复建议。全程只走 `oed` 命令（WebFetch/curl 访问 atomgit.com、log-ci.*、eulermaker.* 常被网关/WAF 拦截）。调用 `oed` 前先加载 [[oed-cli]] skill。
---

# ci-log-analysis — openEuler PR 门禁/构建失败日志定位

用于回答「这个 PR 为什么失败」。核心思路是**三级下钻**：从 PR 门禁汇总（`ag`）→ Jenkins 门禁控制台日志（`CI`）→ CBS 单包构建记录（`eulermaker`），每一级都从上一级日志里提取下一级的定位锚点（构建号、jobId、result_root），直到看到真实报错。

## 何时使用

- 用户给了一个 openEuler PR 链接（`https://atomgit.com/src-openeuler/<repo>/pull/<n>` 或 gitcode 镜像）问为什么 CI 失败。
- 需要确认失败的是哪个门禁项（check_build / check_install / CLA …）、哪两个架构、以及底层编译/打包报错。
- 门禁打了 `ci_failed` 标签，需要定位具体原因。

## 前置检查

```bash
oed info                       # 网关连通、服务发现正常
oed ag login --status          # ag token 已配置（否则 oed ag login）
```

**关键事实：`--pr` 是 Jenkins 构建号，不是 PR 号。** PR 评论里的日志链接
`.../lrzsz/12/` 末尾的 `12` 才是 `getPRCILogs --pr` 要填的值；而
`eulermaker` 的 `os_project`（`atomgit:master:x86_64:lrzsz:16`）里那个
`16` 才是真实 PR 号。两个号别搞混。

## 第一级：PR 门禁概览（`ag` 服务）

```bash
AG="oed ag"
OWNER=src-openeuler; REPO=<repo>; PR=<真实PR号>

# 1. 门禁状态与标签：state / labels(ci_failed, openeuler-cla/no…) / mergeable_state
$AG getPullRequest --owner $OWNER --repo $REPO --number $PR
#   → jq 关注: .response.state, .response.labels[].name, .response.mergeable_state, .response.head.sha

# 2. 门禁汇总表 + 日志链接 + CLA 提示（openeuler-bot 的评论）
$AG listPullRequestComments --owner $OWNER --repo $REPO --number $PR
#   → 找到含 "Check Name / Build Result" 的评论，得到每个检查项 SUCCESS/FAILED/SKIPPED，
#     以及 x86-64 / aarch64 的日志链接（末尾数字=构建号）、trigger 链接
#   → 找含 "CLA" 的评论：提交邮箱是否关联已签 CLA

# 3. 标签变更时间线（ci_processing → ci_failed 的落点）
$AG listPullRequestOperateLogs --owner $OWNER --repo $REPO --number $PR
```

从汇总表即可判断「哪个检查失败」；若 `check_build` FAILED（通常 x86_64、aarch64 一起挂，
`check_install`/`check_license` 随之 SKIPPED），进入第二级。

## 第二级：Jenkins 门禁控制台日志（`CI` 服务）

```bash
# 先发现操作（只需一次）
oed CI | jq -r '.operations[] | .operation_id'        # 看到 getPRCILogs
oed CI getPRCILogs --help                             # 确认参数: arch/repo/pr

# 拉某个架构的原始控制台文本（arch 用连字符写法 x86-64 / aarch64）
oed CI getPRCILogs --arch x86-64 --repo $REPO --pr <构建号> \
  | jq -r '.response._non_json_body' > /tmp/ci.log
```

> **踩坑：日志文本在 `.response._non_json_body`，不在顶层** —— 顶层是
> `{ok,status,response:{_non_json_body,_content_type}}`，直接 `jq '.response'`
> 拿不到正文。它是一整条 JSON 字符串，`jq -r` 会把 `\n` 还原成换行。

```bash
# 关键结论在末尾：谁失败、下一级锚点（CBS jobId / os_project）
grep -nE "Package build failed|log_url|current_result|Finished:" /tmp/ci.log

# 提取 CBS 构建记录锚点
grep -oE "jobId=[A-Za-z0-9._-]+" /tmp/ci.log | head -1       # jobId
grep -oE "atomgit:master:(x86_64|aarch64):[^']+" /tmp/ci.log | head -1  # os_project
```

若门禁日志直接含编译报错（如源码 lint 类失败）可在此定案；否则把 `jobId` 带进第三级。

## 第三级：CBS 单包构建记录（`eulermaker` 服务）

```bash
# 用 jobId 从 jobs 索引拿 result_root
JOBID=<cbs.xxx>
RR=$(oed eulermaker searchData --json "{\"index\":\"jobs\",\"query\":{\"size\":1,\"_source\":[\"result_root\"],\"query\":{\"bool\":{\"must\":[{\"term\":{\"id\":\"$JOBID\"}}]}}}}" \
  | jq -r '.response.hits.hits[0]._source.result_root')

# 拉该 job 完整日志（oed 会自动补 /dmesg，别再手写后缀）
oed eulermaker getJobLog --result-root "$RR" \
  | jq -r '.response._non_json_body' > /tmp/build.log

# 命中真实报错
grep -nE "error: |cc1: all warnings|make\[[0-9]+\].*Error [0-9]|error: Bad exit status|failed to build rpms" /tmp/build.log
```

> **要点：getJobLog 返回的 dmesg 其实是整个容器的完整日志**，rpmbuild 的
> `%build` 编译输出、`%check`、打包错误全在里面，直接当构建日志 grep 即可。

可选：用 `builds` 索引确认整体状态码（`104` = rpmbuild 失败）：

```bash
oed eulermaker searchData --json '{"index":"builds","query":{"size":1,"_source":["build_packages"],"query":{"bool":{"must":[{"term":{"os_project":"<os_project>"}}]}}}}'
```

## 输出约定

按以下结构收敛，先结论后证据：

1. **门禁结果**：哪几个 check FAILED / SKIPPED，ci_failed 标签落点。
2. **根因**：贴 2~5 行真实报错（文件:行号、错误类型，如 `[-Werror=maybe-uninitialized]`）。
3. **关联改动**：这个报错跟 PR 的什么改动相关（升级版本/删补丁/改 spec）。
4. **修复建议**：1~3 条可执行动作（恢复补丁、初始化变量、补签 CLA + `/check-cla` 等）。

## 踩坑记录（重要）

- **WebFetch/curl 到 atomgit.com、log-ci.openeuler.openatom.cn、eulermaker.* 均可能被拦截**；`log-ci` 是 JS SPA，`/consoleText` 直连返回空壳页。一律走 `oed CI getPRCILogs`。
- `jobs` 索引**没有 `create_time` 映射**，`sort:[{create_time:...}]` 会报 `query_shard_exception`；要么不排序，要么按返回顺序取第一条。
- `eulermaker searchData` 的 body 走 `--json`，`index` + `query` 是顶层字段（见 [[oed-cli]]）。
- 两架构（x86_64/aarch64）常同根因；先深挖一个架构，另一个用
  `grep -E "error: |cc1: all warnings"` 快速确认即可。
- `ag`/`CI`/`eulermaker` 服务名大小写敏感（`oed CI` 大写，`oed ag` 小写）。

## 关联

- [[oed-cli]]：所有 `oed` 命令的完整参考（参数推导、错误码、auth）。
- [[cve-verify]]：RPM 修复后的构建可靠性验证（修复方案落地后的验收环节）。
