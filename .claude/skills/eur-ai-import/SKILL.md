---
name: eur-ai-import
description: "openEuler EUR AI 引包（AI-Import）全流程。当用户说「AI 引包」「帮我引一个包」「把这个上游包引入 openEuler」「eur 提交任务」「查 AI 引包进度 / 额度 / 构建目标」「取消引包」「rework spec」时使用。本 skill 负责：采集并确认引包所需信息、提交前复述确认、提交后按步骤 S0–S6 播报进度、失败后回炉或取消。调用 oed 前先加载 [[oed-cli]]。"
---

# eur-ai-import — EUR AI 引包全流程（带进度标识）

把上游开源包自动引入 openEuler：EUR 后端完成依赖闭包、spec 生成、COPR 构建、
失败重试和源发布，最终在目标机 `dnf install`。用户只需说清需求；AI 通过
`oed eur <operation>` 推进，并在每一步告诉用户「现在走到哪了」。

服务名固定为 `eur`（小写）。所有 API 调用走 `oed`，不要手写 curl 打 APIG/EUR API。
使用前先加载 [[oed-cli]]；本 skill 只覆盖 EUR AI 引包，不重复 oed 的通用命令契约。

## 何时使用

- 用户要把某个上游包（Python / ROS / 普通仓库）引入 openEuler，或问「AI 引包能做什么」。
- 查询 AI 引包资格、每日额度、项目可用的构建目标（chroot）。
- 提交、跟踪、取消、确认（ROS 批量预览）或回炉（rework）AI 引包任务。
- 用户拿着一个 job_id 问「现在到哪一步了 / 还要多久 / 成功了吗」。

不适用：普通 RPM 打包、PR 门禁/CI 排错（用 [[ci-log-analysis]]）、CVE/会议/论坛查询。

## 一句话发起（给用户的示例）

```text
帮我把 litestar 2.25.0 从 GitHub 引入 SP4 x86_64，项目 user_xxx/openEuler_summit，
让同事能直接 dnf install。
```

AI 从这句话里提取包名、版本、上游地址、目标系统、项目路径；缺什么问什么，
**不要一次性抛出一长串问题**。

用户第一次说「我要引包」时，先用这段话收口所需信息：

```text
开始 AI 引包前，请确认 5 项：① 项目路径（username/projectname 或
group/projectname）② 包名 ③ 上游仓库地址 ④ 版本 ⑤ 目标系统（如 SP4 x86_64）。
可以一句话说完；缺的我再逐项问。
```

## 进度标识：S0–S6

每次向用户汇报进度，**第一行必须是步骤标识**，然后再给详情。没有调用
`aiImportGetJob` 就不要报进度。

| 标识 | 阶段 | 谁在做事 | 典型输出 |
| ---- | ---- | -------- | -------- |
| S0 | 环境预检 | AI | 登录状态、可用 chroot、额度 |
| S1 | 需求确认 | 用户 + AI | 提交前确认卡 |
| S2 | 提交任务 | AI（用户已确认） | job_id、queued |
| S3 | 排队 | EUR worker | queue_position |
| S4 | 构建（依赖 / spec / 主包） | EUR worker | built/reused/failed、copr_build_id |
| S5 | 发布完成 | EUR | 产物包、repo 地址 |
| S6 | 安装验证 | 用户目标机 + AI | dnf install、import/运行结果 |

标准进度卡（每 30–60 秒轮询一次，状态或阶段变化时立即汇报）：

```text
【AI 引包 · S4 构建中】
任务 e7e1323414 · 状态 running · 已用时 42m13s
包 litestar 2.25.0 · 目标 openeuler-24.03_LTS_SP4-x86_64
依赖：built 5 · reused 2 · failed 0（downstream 7 个，进度以最新日志为准）
COPR build 113946
最新日志：...
下一步：等待主包构建完成；我会在状态变化时继续汇报。
```

其它阶段的首行示例：

```text
【AI 引包 · S0 预检完成】可用目标 3 个；今日额度已用 1 / 上限 5
【AI 引包 · S1 等待你确认】请回复「确认提交」
【AI 引包 · S2 已提交】job e7e1323414 · queued
【AI 引包 · S3 排队中】队列位置 2
【AI 引包 · S4 等待你确认 ROS 预览】
【AI 引包 · S4 构建失败】failed_pkgs: sniffio
【AI 引包 · S5 已发布】可进入 S6 安装验证
【AI 引包 · S6 安装验证通过】rpm 查询 / import / 最小请求均通过
【AI 引包 · 已取消】任务 e7e1323414 已取消
```

## 前置检查（S0）

```bash
oed info                       # 网关连通、服务发现正常
oed auth status                # logged_in 是否为 true
oed eur --help | jq -r '.operations[] | select(startswith("aiImport"))'
```

- `logged_in: false` → 让用户执行 `oed login`。设备码授权必须用户本人在浏览器确认，
  AI 不能代替；完成后用 `oed auth status` 复查。
- `eur` 服务或 `aiImport*` operation 不存在 → 网关 spec 尚未注册 AI 引包接口，
  停止并向用户说明，**不要发明命令**。
- AI 引包接口对未登录 / 无资格用户统一返回 **404**（有意隐藏），不是「接口不存在」。
- 如果 `aiImportGetAccess` 返回 `enabled: false`，说明账号未开通 AI 引包；
  申请制入口已停用，让用户联系管理员或在 oneid 侧确认。

## 必须向用户收集 / 确认的信息

| 信息 | 何时必填 | 说明与示例 |
| ---- | -------- | ---------- |
| 项目路径 | 提交时始终 | 用户项目 `username/projectname`；组项目 `group_name/projectname`。不知道就让用户发 EUR 项目 URL，从 `/coprs/<username>/<projectname>/` 或 `/coprs/g/<group>/<project>/` 解析。示例值：`user_76c6f026/openEuler_summit` |
| 包名 `pkgname` | normal 必填；ROS 单包 | 上游包名，如 `litestar`。**不是** RPM 名 `python3-litestar` |
| 上游地址 `upstream_url` | normal 必填；ROS 可选 | 必须是 `http(s)://` 地址，如 `https://github.com/litestar-org/litestar` |
| 版本 `version` | 建议必填；ROS 可选 | 如 `2.25.0`；不要替用户猜「最新版」 |
| 目标系统 `chroots` | 提交时始终 | 必须来自 `aiImportGetChroots` 的返回值。用户说「SP4 x86_64」时映射成完整名，如 `openeuler-24.03_LTS_SP4-x86_64` |
| 模式 `mode` | 可选 | `normal`（默认，单包）/ `ros`（ROS 批量） |
| ROS 专属 | `mode=ros` | `pkg_list`、`ros_distro`（当前仅 `humble`）、`deep_dependency`、`ref_type` / `ref` / `ref_branch`、`pkg_sources` |

采集规则：

- 先从用户原话提取，只追问缺少的 2–3 项；已提供的信息不要重复问。
- 目标系统必须用 chroots 接口校验；多个匹配时列出让用户选，**不要猜**。
- 上游地址 / 版本可以由 AI 给出候选，但必须让用户确认后才能提交。
- 不要让用户提供密码、token、Cookie；登录走 `oed login` 设备码流程。
- 用户说不清项目时，给出获取方式：「把 EUR 项目页 URL 发我」或「格式是
  `username/projectname`」。

## 提交前必须复述确认（S1 → S2）

额度在**参数校验之前**扣减：参数写错也会消耗一次。因此提交前必须：

1. 先跑 `--dry-run`，确认 URL / 方法 / body 正确；
2. 打印「提交前确认卡」；
3. 等用户明确回复「确认提交」（或等价肯定）后，才发送真实请求。

```text
【提交前确认 · AI 引包】
项目：user_76c6f026/openEuler_summit（用户项目）
包名：litestar        版本：2.25.0
上游：https://github.com/litestar-org/litestar
目标：openeuler-24.03_LTS_SP4-x86_64
模式：normal（单包）
额度：今日已用 1 / 上限 5，本次将占用 1 次（参数校验失败也扣减）
dry-run：通过
回复「确认提交」后我才发送。
```

## 第一步：预检（chroots / 资格 / 额度）

用户项目：

```bash
oed eur aiImportGetChroots --username <username> --projectname <projectname>
oed eur aiImportGetAccess  --username <username> --projectname <projectname>
oed eur aiImportListJobs   --username <username> --projectname <projectname>
```

组项目（操作名带 `ByGroup`，路径参数是 `--group-name`）：

```bash
oed eur aiImportGetChrootsByGroup --group-name <group> --projectname <projectname>
oed eur aiImportGetAccessByGroup  --group-name <group> --projectname <projectname>
```

解读返回值（以下字段都位于 oed 输出 JSON 的 `.response` 下）：

- `enabled`：是否具备 AI 引包资格；`false` 时不要提交。
- `quota.limit <= 0` 表示不限额；否则看 `used` / `limit`。额度按账号计、跨项目共享。
- chroots 是完整构建目标列表；normal 模式可选任意一项，ROS 模式只支持
  `openeuler-24.03*`。
- 404：未登录或无 AI 引包资格（接口隐藏），先查 `oed auth status`。
- 403（提交时）：当前用户对该项目没有构建权限，换项目或让管理员授权。

## 第二步：提交任务（S2）

normal（常规单包）示例：

```bash
oed eur aiImportSubmitJob \
  --username <username> --projectname <projectname> \
  --json '{
    "mode": "normal",
    "pkgname": "litestar",
    "upstream_url": "https://github.com/litestar-org/litestar",
    "version": "2.25.0",
    "chroots": ["openeuler-24.03_LTS_SP4-x86_64"]
  }' --dry-run
```

用户确认后去掉 `--dry-run` 再执行。组项目用 `aiImportSubmitJobByGroup` +
`--group-name`，body 相同。

ros（ROS 批量）的 body 示例（命令同 normal，把 `--json` 换成下面这段）：

```bash
--json '{
  "mode": "ros",
  "pkg_list": ["rclcpp", "nav2_util"],
  "chroots": ["openeuler-24.03_LTS_SP4-x86_64"],
  "ros_distro": "humble",
  "deep_dependency": true
}'
```

ROS 单包也可以传 `pkgname` + 可选 `upstream_url` / `version` + `ref_type` / `ref` /
`ref_branch`；批量时逐包来源放 `pkg_sources`。

提交规则：

- 请求体只能走 `--json`；`--params` 只用于 query/path 参数。
- 路径参数用 flag：`--username` / `--projectname` / `--group-name` / `--job-id`。
- 返回体在 `.response` 下：`{"job_id": "...", "status": "queued"}`。立即记录
  `.response.job_id`；丢失时用 `aiImportListJobs` 查最近 50 条（按创建时间倒序）。
- ROS 批量任务提交后可能进入 `waiting_confirm`，必须先让用户确认预览（见下）。
- normal 模式提交后自动执行，无需人工预览。

## 第三步：跟踪进度（S3 / S4）

### 接手已有任务

```bash
oed eur aiImportListJobs --username <username> --projectname <projectname> \
  | jq '.response[] | {id, pkgname, status, created_at}'
```

- 用户给了 job_id：先确认它出现在当前项目的 `aiImportListJobs` 结果里，再查详情。
  详情接口不校验 job 归属，不要直接信任外部/别的项目给的 job_id。
- 没给 job_id：列出最近 5 条，让用户选；不要猜，也不要用别的项目的 job_id。
- 任务只保留约 7 天，列表最多 50 条；查不到就让用户提供 job_id 或重新提交。
- 会话中断 / 上下文丢失时，先用列表找回 job_id，**不要重新提交**（会再次扣额度）。

```bash
oed eur aiImportGetJob \
  --username <username> --projectname <projectname> --job-id <job_id> \
  | jq '.response | {id, status, queue_position, created_at,
       last_t: (.log[-1].t // .created_at),
       built_pkgs, failed_pkgs, reused_pkgs, downstream,
       copr_build_id, rework_count, confirm}'
```

组项目用 `aiImportGetJobByGroup --group-name ...`。

任务状态与流程步骤的映射：

| `status` | 字段信号 | 当前步骤 | 怎么对用户说 |
| -------- | -------- | -------- | ------------ |
| `queued` | `queue_position` | S3 排队 | 「队列位置 N（1 = 队首）」 |
| `running` | 无 `copr_build_id`、无 built/reused/failed | S4 分析上游 / 依赖评估 | 「AI 正在分析上游与依赖闭包」 |
| `running` | 有 `built_pkgs` / `reused_pkgs` / `failed_pkgs` / `downstream` | S4 依赖构建 | 报 built / reused / failed 计数与最新日志 |
| `running` | 有 `copr_build_id` | S4 主包 COPR 构建 | 「主包已提交 COPR build N」 |
| `waiting_confirm` | `preview_json`、`confirm != "1"` | S4 等待用户确认 | 展示预览并请求确认 |
| `success` | `status` | S5 发布完成 | 给出产物包与 repo 地址 |
| `failed` | `failed_pkgs`、`log` | S4 构建失败 | 贴关键报错，提出 rework / 取消 |
| `cancelled` | `status` | 已取消 | 确认是否重新提交 |

进度计算与播报规则：

- 已用时 = `last_t - created_at`（都是 Unix epoch 秒，可能是数字或数字字符串，
  计算前先 `tonumber`），格式化成 `XhYmZs`；任务结束后不要再用当前时间估算。
- `built_pkgs` / `failed_pkgs` / `reused_pkgs` 是空格分隔字符串；`downstream`
  是 JSON 编码字符串数组，读取时可能需要 `fromjson`。
- `queue_position` 只在 `queued` 时出现；1 表示队首。
- 阶段判断是启发式：优先看 `copr_build_id`，其次看依赖字段，最后看最新日志；
  每次都要把 `.log[-1].msg` 一起给用户，不要只凭字段猜。
- `failed_pkgs` 非空不一定最终失败：worker 会自动修复/重试依赖，只有
  `status=failed` 才是最终失败；running 期间说「N 个依赖首次失败，正在自动重试」。
- 轮询间隔 30–60 秒；没有变化时每 2–3 分钟给一行心跳，不要高频刷屏。
- 依赖阶段可能持续数十分钟到数小时；只报「已用时 + 当前阶段」，不要给固定 ETA。
- 只有用户明确说「帮我盯着」才持续轮询；否则每次汇报后告诉用户「随时回来问我」，
  不要把轮询写成无上限死循环。
- 到 `success` / `failed` / `cancelled` 停止轮询；不要在没有新调用的前提下
  声称「应该快好了」。

### ROS 批量预览确认（`waiting_confirm`）

```bash
oed eur aiImportGetJob ... \
  | jq '.response | {preview: ((.preview_json // "{}") | fromjson), confirm}'
```

- 把预览里的包名、ref、version 整理成短表，请用户确认或提供 override。
- 纯确认：`--json '{}'`；带覆盖：

```bash
oed eur aiImportConfirmJob \
  --username <username> --projectname <projectname> --job-id <job_id> \
  --json '{"overrides": {"<pkg>": {"ref_type": "tag", "ref": "v1.2.3", "version": "1.2.3"}}}'
```

- `overrides` 的 key 必须来自预览包列表；`ref_type` 为 `tag` / `branch` / `commit`。
- 确认需要项目构建权限；403 表示没有权限，不要让用户反复重试。
- 409 `not_waiting_confirm`：任务已不在预览态，重新查一次详情再决定，不要盲目重试。

## 失败、取消与回炉

- 失败时先看 spec（仅任务提交者本人或管理员可查看）：

```bash
oed eur aiImportGetJobSpec \
  --username <username> --projectname <projectname> --job-id <job_id> \
  [--pkg <name>]
```

  normal 任务可省略 `--pkg`；ROS 任务必须指定，缺省会返回 400 和可选包列表。
  再结合 `failed_pkgs` 与日志尾部 2–5 行给出根因。
- 取消：`aiImportCancelJob ... --job-id <id>`。先让用户确认；排队中会移出队列，
  运行中会清除项目锁并通知 worker。`.response.status` 应为 `cancelled`。
- 回炉：`aiImportReworkJob` 只对**已完成**（`success` / `failed`）的任务有效，
  且只能由任务提交者本人或管理员操作；会创建新的 job_id：

```bash
oed eur aiImportReworkJob \
  --username <username> --projectname <projectname> --job-id <old_job_id> \
  --json '{"feedback": "把 spec 里的 BuildRequires 补上 python3-setuptools-scm >= 6.4"}'
```

回炉规则：

- normal 任务不需要 `pkgs`；ROS 任务必须给 `pkgs`（最多 50 个，且必须在原任务的
  `built_pkgs` / `failed_pkgs` / `reused_pkgs` 集合里）。
- `feedback` 最长 4000 字符；每个原任务最多 5 轮；项目有任务在跑时返回 409。
- 回炉**不消耗**每日额度；成功后旧构建保留，`downstream` 里已成功的依赖不会重建。
- 回炉后跟踪 `.response.job_id`（新任务），进度卡里注明 `.response.round` 和
  `.response.rework_of`。

## 发布与安装验证（S5 → S6）

只有 `status=success` 才能说「已发布」。先取产物：

```bash
oed eur aiImportGetJob ... | jq '.response | {built_pkgs, copr_build_id}'
```

用户项目的 repo 文件 URL（本次实测模式）：

```text
https://eur.openeuler.openatom.cn/coprs/<username>/<projectname>/repo/<chroot>/<username>-<projectname>-<chroot>.repo
```

组项目的 repo 文件不要套用上式，让用户从 EUR 项目页复制。

目标机上（以下命令需要 root / sudo）：

```bash
sudo curl -o /etc/yum.repos.d/eur-<projectname>.repo <repo-file-url>
sudo dnf -y install <built_pkgs 中的实际包名>
rpm -q <pkg> && rpm -qi <pkg> | grep -E 'Version|Vendor|Signature'
```

- 安装包名以 `built_pkgs` 为准，不要从上游包名反推（Python 包常是
  `python3-<name>`，其他语言不同）。
- 在告诉用户「同事可以装」之前，至少确认 repo 文件、repodata 和 GPG key 可访问；
  `status=success` 只代表构建成功，不代表源已经同步到所有节点。
- 验证不只跑 `dnf install`：至少做一次 `rpm -q` + 一次真实 import / 启动 /
  最小请求；失败就回到 S4/S5 报告，不要口头宣称「能跑」。
- 这里的 `curl` 只用于目标机下载 repo 文件；API 调用仍然只能走 `oed`。

## 命令速查（18 个 operation）

| 场景 | 用户项目 | 组项目（`--group-name`） |
| ---- | -------- | ------------------------ |
| 可用构建目标 | `aiImportGetChroots` | `aiImportGetChrootsByGroup` |
| 资格 / 申请 / 额度 | `aiImportGetAccess` | `aiImportGetAccessByGroup` |
| 任务列表（最近 50） | `aiImportListJobs` | `aiImportListJobsByGroup` |
| 提交任务 | `aiImportSubmitJob` | `aiImportSubmitJobByGroup` |
| 任务详情 / 进度 | `aiImportGetJob` | `aiImportGetJobByGroup` |
| 取消 | `aiImportCancelJob` | `aiImportCancelJobByGroup` |
| 确认 ROS 预览 | `aiImportConfirmJob` | `aiImportConfirmJobByGroup` |
| 查看 spec | `aiImportGetJobSpec` | `aiImportGetJobSpecByGroup` |
| Spec 回炉 | `aiImportReworkJob` | `aiImportReworkJobByGroup` |

通用形式：

```text
oed eur <operation> --username <u> --projectname <p> [--job-id <id>] [--json '{...}'] [--dry-run]
oed eur <operation>ByGroup --group-name <g> --projectname <p> ...
```

参数不确定时先跑 `oed eur <operation> --help`，以网关 spec 的 `parameters` /
`body_schema` 为准。

## 常见错误分流

| HTTP | 典型 error / 场景 | 处理 |
| ---- | ----------------- | ---- |
| 400 | 参数校验失败、ref/chroot 非法、ROS spec 缺 `pkg` | 读 message 修 JSON / 补 `--pkg`；注意额度可能已扣 |
| 403 | `insufficient_permissions` / 任务不属于当前用户 | 无项目构建权限或非本人任务；换项目或找管理员 |
| 404 | 未登录、无 AI 引包资格、项目/任务不存在 | 接口有意隐藏；先查 `oed auth status`，不要判定为接口消失 |
| 409 | `not_waiting_confirm` / 项目有任务在跑 | 刷新详情；不要盲目重试 |
| 429 | `daily_quota_exceeded` | 今日额度用完；等次日或联系管理员 |
| 500 | ROS SIG 源挂载/清理失败 | 平台侧问题；稍后重试并反馈 |

## 红线

- **未经用户明确确认，不调用** `aiImportSubmitJob` / `aiImportConfirmJob` /
  `aiImportCancelJob` / `aiImportReworkJob`。这些操作会消耗额度或改变任务状态。
- 额度在参数校验前扣减：不要「先提交试试」，必须先 `--dry-run` + 复述确认。
- 不猜 `pkgname` / `upstream_url` / `version` / `chroots` / `job_id`；chroot 必须来自
  接口，job_id 必须来自本项目提交响应或任务列表。
- 404 不等于接口不存在：先查登录和 AI 引包资格（接口有意隐藏）。
- 请求体只能走 `--json`；`--params` 只放 query/path 参数。
- 所有 API 调用走 `oed`，不要 WebFetch/curl 直连 APIG / EUR API。
- 不展示、记录或传递 token / Cookie；`oed` 会自动注入认证。
- 不伪造进度：每次播报必须基于最新一次 `aiImportGetJob`；调用失败就如实报告错误码。
- 如果 `oed eur --help` 没有 `aiImport*`，说明网关尚未注册，停止并告知用户。
- 修改 oed-cli 本身时，遵循 [[oed-cli]] 的开发约束（hook、不自动 commit）。

## 关联

- [[oed-cli]]：`oed` 的命令契约、`--json` / `--params` 区别、退出码、登录方式。
- [[ci-log-analysis]]：构建产物进入 CI 后，门禁/编译失败的下钻分析。
- 接口字段以 `oed eur <operation> --help` 的 `body_schema` 为准；用户项目与组项目
  两套路径只差 `ByGroup` 与 `--group-name`。
