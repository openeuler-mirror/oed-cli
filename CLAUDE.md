# CLAUDE.md

> AI 编程助手的项目入口。人类请读 `README.md`。
> 每次新会话开始时读完本文件再行动。

---

## 这是什么

`oed-cli` 是 openEuler Infra 网关下所有在线服务的统一命令行入口。
运行时从 `https://api-gateway.osinfra.cn/discovery/apis` 拉取服务清单
动态构建命令表（设计灵感来自 `googleworkspace/cli`）。

设计哲学、架构、命令契约、退出码的**事实之源**是
[`docs/cli-design.md`](docs/cli-design.md)。本文件不重复 `cli-design.md`
的内容，只写 agent 上手 + 维护约束。

---

## 目录速览

```
src/oed_cli/
  main.py       # 顶层 dispatcher（entry point: oed = oed_cli.main:main）
  cli.py        # click 命令 + --help 装饰（保留命令）
  discovery.py  # discovery feed + 缓存（10 分钟 TTL）
  dynamic.py    # OpenAPI paths → OperationsTable + per-param flag 推导
  invoke.py     # 实际调用：拼 URL、发请求、封 JSON 输出
  http.py       # WAF-safe HTTP client
  errors.py     # OedError 体系 + 退出码 0/1/2/3/4

tests/
  test_cli.py      # 保留命令 + --help 装饰
  test_dynamic.py  # 调度 + per-param flag + API_ 前缀剥离
  test_discovery.py

docs/
  cli-design.md    # 设计文档（事实之源 — 改动要先改这里）
  local-testing.md
context/
  discoverAPI.md   # 网关侧发现服务使用说明

.claude/
  SKILL.md / SKILL.md in skills/oed-cli/   # Agent 使用 oed 的 skill
  settings.json         # 项目级 hooks（已 commit）
  settings.local.json   # 个人权限覆盖（不 commit）
  hooks/                # PostToolUse 校验脚本
```

---

## Quickstart（新人视角）

### 第一次接触本仓库

1. `docs/cli-design.md` §1–§4：搞懂是什么、目录、命令契约、退出码。
2. `docs/cli-design.md` §4.2：模块职责 —— 这是新增功能的归属指南。
3. `README.md`：用户视角（Quickstart / 命令契约 / 缓存同步）。

### 本地开发循环

```bash
pip install -e ".[dev]"     # 装 pytest + ruff
ruff check src tests        # lint
pytest -q                   # 41 个测试，monkeypatch discovery，< 1s 全过
oed --version               # 确认 entry point 工作
```

**改任何 .py 文件后** — 项目级 `PostToolUse` hook 会自动跑
`ruff check src tests && pytest -q`。失败会**阻断**，你必须修完才能继续。

### 加新功能的标准流程

1. 看 `docs/cli-design.md` §4 模块职责，决定改动放在哪个文件。
2. 实现。
3. 加测试（monkeypatch discovery，不联网）。
4. `ruff check src tests && pytest -q` 全过。
5. 更新 `docs/cli-design.md`：
   - §4 模块职责 / §4.1 包结构（如改了模块边界）
   - §8 路线图（如关闭了一个 roadmap 项）
   - §7 决策表（如改了一条设计决策）
6. 同步更新 `README.md` / `.claude/skills/oed-cli/SKILL.md`（如改了用户可见行为）。
7. **不要**自动 commit。报告给用户，等用户说「commit」再走 `git add <files> && git commit`。

### 修 bug 的标准流程

1. 跑 `pytest -q` —— 先确认能复现（或加一个失败的回归测试）。
2. 在合适文件里改。
3. 加回归测试。
4. 全跑一遍。
5. **不要**自动 commit，等用户。

### 检查文档是否过期

1. 读 `docs/cli-design.md` 全文。
2. 对照 `src/oed_cli/` 各文件（行数、签名、行为）。
3. 把 drift 列给用户，**不要**自己改 doc —— 等用户确认。

---

## Maintainer's Guide（长期维护视角）

### 不可破坏的约束（破坏会立刻出大事）

| 约束 | 原因 |
|---|---|
| 运行时 URL = `resolve_runtime_gateway(service) + op.path`；discovery feed 的 `service.base_url` 是 host 的**唯一**来源 | gateway 现行返回的是真实地址（如 `https://apig.osinfra.cn`）；**无 fallback** —— 若 feed 返回空或占位符，HTTP 调用直接失败而不是被静默重写。**不要**让代码从 backend block 推导 host，也不要绕过 resolver 自己拼 |
| 退出码固定为 0/1/2/3/4 | CI / Agent 脚本按这四个码分流，新增错误码会让所有下游失效。需要时**升级**到下一个 5+ 的值 |
| `Operation.display_name` 剥 `API_` 前缀，`operation_id` 保留原文 | 两者都注册到 `operations_table`。Huawei APIG 只在 `software-package-server` 加了前缀 —— **不要**假设其他服务也加了 |
| per-param flag 用 `to_flag()` 转 kebab-case | `cveId` → `--cve-id`，原始名 (`--cveId`) 也接受。**不要**绕过 `param_flag_index` 自己拼 flag 索引 |
| stdout 永远 JSON（`ensure_ascii=False`），stderr 承载错误 | 这是 Agent 脚本能 `\| jq` 的前提 —— 任何写 stdout 的调试 print 都会破管道 |
| 缓存 TTL = 600s | 改这个值要在 README "Keeping in sync with the gateway" 一节同步解释 |

### 错误处理约定

- 复用 `errors.py` 里的四个异常：`UserError` / `NetworkError` / `UpstreamError` / `NotFoundError`，不要再发明新类。
- 每个 `OedError.__init__` 必须传 `kind=...`（字符串标识符），并尽量传 `hint=...`（人类可读修复建议）。
- 在 `main.py` 顶层 `try/except OedError` 已经被全局捕获 —— 在子模块里**不要**自己 catch 后 print，会双重输出。

### 已知坑（commit message 里也要写）

- `$APIG_GROUP_ENTRY_URL` 是网关早期占位符 —— gateway 自某次升级后开始返回真实 URL（所有 openeuler 服务当前都是 `https://apig.osinfra.cn`），但 `resolve_runtime_gateway` 不再做兜底：占位符会原样流到 HTTP 调用层，URL 会因 `$APIG_GROUP_ENTRY_URL/v1/...` 直接失败，错误会清楚地暴露 gateway 还在发占位符的事实。
- Windows 默认缓存位置是 `%LOCALAPPDATA%\oed-cli\cache\`，不是 `~/.cache/`。
- 中文 stdout 在 Windows console 需要 `chcp 65001` 或 `PYTHONIOENCODING=utf-8`，这是终端问题不是 CLI 问题 —— 已经在 README 写明。
- `cve-sa-backend` 的 spec 把 `x-apigateway-backend.httpEndpoints.address` 指向 `cvesa.test.osinfra.cn`（测试域），所有 cve-sa-backend 调用走 discovery feed 的 `service.base_url` 绕开 —— 这就是为什么 host 必须来自 feed、不能信 spec 的 `address`。

### 模块边界（不要轻易动）

| 加什么 | 放哪里 |
|---|---|
| 新保留命令（`oed foo`） | `cli.py` |
| 新顶层 flag（全局适用） | `main.py:_dispatch_dynamic` 解析层 |
| 新 OpenAPI→Operation 转换规则 | `dynamic.py` |
| 新调用 / 输出包装规则 | `invoke.py` |
| 新 HTTP 客户端行为（如重试） | `http.py` |
| 新退出码 / 错误类型 | `errors.py`（先看现有四个能不能复用） |

新增 .py 文件到 `src/oed_cli/` 是**最后手段** —— 优先扩展现有七个模块。

---

## Agent 红线（绝对不要）

- ❌ 直接 `git push` 到 `master`。
- ❌ `git commit --amend` 已有 commit（除非用户明确说「amend」）。
- ❌ `git reset --hard` / `git push --force` / `rm -rf` / 改 `.git/` 里的任何东西。
- ❌ 改 git config（`user.email` / `user.name` / `core.*` 等）。
- ❌ hardcode 真实 endpoint 到代码里（host 必须从 `ServiceMeta.base_url` 经 `resolve_runtime_gateway` 读，**不**写常量、不**写环境变量）。
- ❌ 把新依赖加到 `[project.dependencies]`（除非用户明确说）—— 加到 `[project.optional-dependencies].dev`。
- ❌ 新增 `.py` 文件到 `src/oed_cli/` 除非确实必要 —— 保持七个模块的边界。
- ❌ 改 `docs/cli-design.md` 后不同步 `README.md` 和 SKILL.md。
- ❌ 自动 commit —— 改完报告用户，等用户说「commit」。
- ❌ 跳过 hook（用 `--no-verify` 或绕过 PostToolUse）—— 这是 agent 守规矩的红线测试。

---

## 自检清单（提交前）

- [ ] `ruff check src tests` 通过
- [ ] `pytest -q` 41 个测试全过
- [ ] `docs/cli-design.md` 反映了本次代码改动（如有）
- [ ] `README.md` 命令示例与新行为一致（如有）
- [ ] `.claude/skills/oed-cli/SKILL.md` 工作流反映新能力（如有）
- [ ] 没有未跟踪的临时文件（`git status` 干净或仅有本次改动）
- [ ] commit message 用祈使句、能解释「为什么」而不是「做了什么」

---

## 项目规范（速查）

| 工具 | 配置 |
|---|---|
| ruff | `line-length=100`, `target-version=py310`, `select=E,F,W,I,B,UP,SIM` |
| pytest | `testpaths=tests`, `addopts=-q` |
| Python | `>=3.10`，无本地编译，wheel only |
| entry point | `oed = oed_cli.main:main` |
| 依赖 | 仅 `click>=8.1`, `httpx>=0.27`（runtime） |