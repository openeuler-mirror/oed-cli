# 本地快速测试 oed-cli

> 约 5 分钟即可把 `oed` 跑起来，命令全部经过实测可用。

---

## 0. 前置

| 工具 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.10 | 本仓库已用 3.13 验证 |
| pip | 任意最新版 | `python -m pip install -U pip` |
| 网络 | 可达 `api-gateway.osinfra.cn` | 否则只能跑离线单测 |

> 无网络也能跑：见 §5「离线测试」。

---

## 1. 克隆与安装（editable 模式）

```bash
git clone https://atomgit.com/openeuler/oed-cli
cd oed-cli
python -m pip install -e ".[dev]"
```

执行成功后：

```bash
which oed           # Windows: where oed
oed --version       # → oed, version 0.1.0
```

> `pip install -e .` 的好处是改源码即时生效；想取消直接 `pip uninstall oed-cli`。

---

## 2. 跑测试（约 0.2 秒）

```bash
python -m pytest -v
```

应看到：

```
tests/test_cli.py ........                                       [100%]
============================== 8 passed in 0.16s ==============================
```

测试覆盖：
- `test_version` — `--version` 输出
- `test_info_returns_structured_payload` — `info` JSON 形状
- `test_services_lists_only_current_community` — `OED_COMMUNITY` 过滤
- `test_schema_full` — 完整 OpenAPI
- `test_schema_method_lookup` — 单接口 schema
- `test_schema_unknown_method_reports_available` — exit 4 + `available_paths`
- `test_schema_missing_spec_reports_hint` — 空 spec → exit 4 + `spec_missing`
- `test_help_lists_commands` — `--help` 列出子命令

测试完全离线，依靠 monkeypatch 替换 `fetch_discovery` / `fetch_spec`。

---

## 3. 真实网关注入测试

```bash
# 1) 看连通性
oed info
# 期望:
# {
#   "ok": true,
#   "gateway": "https://api-gateway.osinfra.cn",
#   "community": "openeuler",
#   "services_total": 8,
#   "cache": {"ttl_seconds": 600, "stale": false, ...}
# }

# 2) 列服务
oed services | python -c "import json,sys;print('\n'.join(s['service_name'] for s in json.load(sys.stdin)))"
# 期望: software-package-server / easysearch / cve / app-meeting-server
#       discourse / mailman / etherpad / copr

# 3) 取完整 OpenAPI
oed schema software-package-server | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['paths']),'paths')"
# 期望: 11 paths

# 4) 单 method schema
oed schema software-package-server.listSoftwarePackages | python -c "import json,sys; d=json.load(sys.stdin); print(d['matches'][0]['path'], d['matches'][0]['method'])"
# 期望: /v1/softwarepkg GET

# 5) 错误路径验证退出码
oed schema software-package-server.WHATEVER; echo "exit=$?"
# 期望: exit=4, 同时 JSON 里有 available_paths

# 6) 还没发 OpenAPI 的服务（发现 feed 列了但 spec 空）
oed schema easysearch; echo "exit=$?"
# 期望: exit=4, error="spec_missing"
```

---

## 4. 缓存调试

```bash
oed cache show      # 看缓存路径、TTL、是否新鲜
oed cache refresh   # 强制重拉一次
oed cache clear     # 删除缓存文件（下次命令重新拉）

# 自定义缓存目录
OED_CACHE_DIR=/tmp/oed-cache oed info

# 切换社区（当前仅 openeuler 有数据）
OED_COMMUNITY=openeuler oed services
```

Windows 下默认缓存位置：

```
C:\Users\<you>\AppData\Local\oed-cli\cache\discovery.json
```

---

## 5. 离线测试

完全无网络也能验证框架：

```bash
# 1) 编辑器测：进入 python REPL
python -c "from oed_cli.main import main; main()" -- --help
# 或：
python -m oed_cli --help
# 或：
oed --help          # 不用联网的命令

# 2) pytest 跑离线用例
python -m pytest -v

# 3) WAF 检测单元
python -c "
from oed_cli.http import _is_waf_block
print(_is_waf_block('<!DOCTYPE html><title>访问被拦截</title>'))   # True
print(_is_waf_block('{\"ok\":true}'))                              # False
"
```

---

## 6. 常用调试技巧

| 场景 | 命令 |
|---|---|
| 看完整 `--help` 含子命令 | `oed --help` / `oed schema --help` |
| 看退出码定义 | `docs/cli-design.md` §4.2 |
| JSON 不带颜色（已默认） | `NO_COLOR=1 oed info` |
| 取原始 OpenAPI（绕过 schema 包装） | `oed schema software-package-server` 走的是 `httpx`，加 `-v` 看 click 帮助 |
| 重置全部状态 | `oed cache clear` |

---

## 7. 下一步可以测什么

按 `docs/cli-design.md` §8 路线图，**当前 v0.1 仅含只读/查询命令**。

| 想试… | 怎么做 |
|---|---|
| 真实发起 API 调用 | 等 v0.2（动态 `<service> <method>` 分发） |
| 鉴权 | 等 v0.4（`OED_TOKEN` + `oed login`） |
| 自动分页 | 等 v0.3（`--page-all`，流式 NDJSON） |
| 给 Claude Code 用 | 直接打开本仓库，`oed --help` 就能用，`.claude/skills/oed-cli/SKILL.md` 已自动装载 |

---

## 8. 常见报错

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: oed_cli` | 没有 `pip install -e .` | `pip install -e .` |
| `oed info` 卡住 | 网络不通 / 被 WAF 拦截 | 确认能 `curl https://api-gateway.osinfra.cn`；如被拦截看 `context/discoverAPI.md` §6 |
| 中文输出乱码 | Windows 终端 codepage 不是 UTF-8 | 用 PowerShell / WSL / `chcp 65001` 或通过 `\| python` 管道传递 |
| `oed cache show` 报路径不存在 | 第一次跑 `oed info` 后才会写缓存 | 先跑一次 `oed info` |

---

## 9. 一键自检脚本

把以下内容存为 `scripts/smoke.sh`（或 `.bat`），每次改完源码跑一遍：

```bash
#!/usr/bin/env bash
set -euo pipefail
echo "==> pytest"
python -m pytest -q
echo "==> oed --version"
oed --version
echo "==> oed info"
oed info
echo "==> oed services count"
oed services | python -c "import json,sys; print(len(json.load(sys.stdin)),'services')"
echo "==> oed schema software-package-server"
oed schema software-package-server | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['paths']),'paths')"
echo "==> exit codes"
oed schema software-package-server.MISSING 2>/dev/null && true
echo "exit=$?"
```

跑出来 8 passed + services_total=8 + paths=11 就代表工具链通达。

---

## 10. 动态调用 v0.2

从 v0.2 起 `oed` 真正能向云服务发请求，无需手写 `curl`，详见 `docs/cli-design.md`。

### 10.1 列出某服务的全部 method

```bash
oed software-package-server
```

返回 JSON 中 `operations` 数组列出每个 (path, verb) 对应的 operation 名
（自动剥离 APIG 生成的 `API_` 前缀，原始形式在 `operation_aliases` 字典里），
含 `path_params` / `query_params` / `body_required` 等。

### 10.2 单个 operation 的 flag cheatsheet

```bash
oed software-package-server getSoftwarePackage --help
```

```json
{
  "ok": true,
  "help_for": "getSoftwarePackage",
  "operation_id_raw": "API_getSoftwarePackage",
  "method": "GET",
  "path": "/v1/softwarepkg/{id}",
  "url": "https://apig.osinfra.cn/v1/softwarepkg/{id}",
  "parameters": [
    { "name": "id",       "in": "path",  "required": true,  "flag": "--id",       "type": "string" },
    { "name": "language", "in": "query", "required": false, "flag": "--language", "type": "string" }
  ],
  "usage": "oed <service> getSoftwarePackage --id <value> [--language <value>] [--dry-run]"
}
```

每个声明的 query / path 参数都自动转成一个 `--<kebab-case>` flag。
`cveId` → `--cve-id`，`count_per_page` → `--count-per-page`，原始写法
（`--cveId`）也兼容。`API_` 前缀（APIG 自动加的）会被剥离，写
`API_getSoftwarePackage` 和 `getSoftwarePackage` 都行。

### 10.3 单调用：每个参数一个 flag（推荐写法）

```bash
oed software-package-server getSoftwarePackage --id 12345 --language zh_CN
# → GET https://apig.osinfra.cn/v1/softwarepkg/12345?language=zh_CN

oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
# → GET https://apig.osinfra.cn/cve-security-notice-server/securitynotice/getByCveId?cveId=CVE-2019-10082

oed software-package-server listSoftwarePackages --page-num 1 --count-per-page 5
# 整数 / 数字参数自动从字符串强转
```

CLI 自动把 query 拼到 `https://apig.osinfra.cn/...`，连 WAF header 都烤进 `http.py`。`apig.osinfra.cn` 是生产网关，spec 里的 `x-apigateway-backend.httpEndpoints.address`（可能指向测试域）**不会被信任**。

### 10.4 批量参数：`--params` JSON（备用写法）

```bash
oed software-package-server getSoftwarePackages \
  --params '{"phase":"accepted","page_num":1,"count_per_page":5}'
```

适合一次性传一堆参数，或者脚本里拼 JSON。`--params` 与 per-param flag 可同时使用，flag 覆盖 `--params` 中同名字段。

### 10.5 单调用：POST + body

```bash
oed software-package-server applyNewSoftwarePackage \
  --json '{"pkg_name":"demo","version":"1.0.0"}'
```

自动设置 `Content-Type: application/json`。

### 10.6 预览 dry-run

```bash
oed cve getSecurityNoticeByCveId --cve-id 1 --dry-run
# → 输出 ok=true, dry_run=true, request.{url,query,headers,body}，真正请求不发生
```

### 10.7 退出码表（重要，写脚本必看）

| 情形 | 退出码 |
|---|---|
| 200 ≤ status < 400 | 0 |
| 4xx / 5xx 上游错误 | 3 |
| 未知 service / method | 4 |
| 已知 service 但 spec 未发布 | 4 (`error="spec_missing"`) |
| JSON 解析失败 / 未知 flag | 1 |
| 网络失败 / WAF 拦截 | 2 |

### 10.8 错误处理示例

```bash
$ oed does-not-exist getX; echo "exit=$?"
{"ok": false, "code": 4, "error": "service_not_found", "message": "...", "hint": "..."}
exit=4

$ oed software-package-server DOES_NOT_EXIST; echo "exit=$?"
{"ok": false, "code": 4, "error": "method_not_found", ...}
exit=4

$ oed easysearch API_x; echo "exit=$?"
{"ok": false, "code": 4, "error": "spec_missing", "hint": "The discovery feed lists this service but its OpenAPI spec has not been published yet."}
exit=4

$ oed software-package-server API_x --params 'not-json'; echo "exit=$?"
{"ok": false, "code": 1, "error": "invalid_json", "message": "--params is not valid JSON: Expecting value (line 1, col 1)"}
exit=1
```

用 LLM Agent 时直接看 `code` 字段分支，无需解析消息文本。

---

## 11. cve：query CVE 安全公告

`cve` 是 openEuler 的 CVE / 安全公告服务。重要：spec 里
`x-apigateway-backend.httpEndpoints.address` 字段经常指向测试后端
（例如 `cvesa.test.osinfra.cn`），这些测试域会被 CloudWAF 拦截。
oed-cli 不信任该字段，所有运行时调用统一走生产网关
`https://apig.osinfra.cn` —— 路径用 OpenAPI `paths` 里的 key。

```bash
# 1) 列出全部 operationId
oed cve --help
# 找到: getSecurityNoticeByCveId, getCVEDatabaseByCveId, findAllSecurityNotice …

# 2) 看某个 operation 的 flag cheatsheet
oed cve getSecurityNoticeByCveId --help
# → {"parameters":[{"name":"cveId","flag":"--cve-id","required":true,...}], "usage": "..."}

# 3) 推荐写法：每个参数一个 flag
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082
# → HTTP 200，单条命中

# 4) 模糊匹配（query "1" 命中 cveId 包含 "1" 的所有公告，~828 条）
oed cve getSecurityNoticeByCveId --cve-id 1

# 5) 备用：--params JSON（适合脚本 / 复杂入参）
oed cve getSecurityNoticeByCveId --params '{"cveId":"CVE-2024"}'

# 6) 预览（不打网络）
oed cve getSecurityNoticeByCveId --cve-id 1 --dry-run

# 7) 解析输出
oed cve getSecurityNoticeByCveId --cve-id CVE-2019-10082 \
  | jq '.response.result[0] | {cveId, affectedProduct, announcementTime}'
```

### 常见 cve 操作

| operationId | flag 形式 | 用途 |
| --- | --- | --- |
| `getSecurityNoticeByCveId` | `--cve-id <value>` | 按 CVE 编号查安全公告 |
| `getCVEDatabaseByCveId` | `--cve-id <value>` | 按 CVE 编号查漏洞库详情 |
| `getCVEDatabaseByCveIdAndPackageName` | `--cve-id` + `--package-name` | 按 CVE + 包名定位修复版本 |
| `getPackageListByCveId` | `--cve-id <value>` | 查某个 CVE 影响的所有包 |
| `findAllSecurityNotice` | （分页参数） | 列全部安全公告 |
| `getKernelCVEList` | （分页参数） | 内核相关 CVE 列表 |

### URL 构造规则（为什么不是 spec 里的 `cvesa.test.osinfra.cn`）

`oed` 不读 `x-apigateway-backend.httpEndpoints.address`，只取其中的
HTTP method / scheme 信息。运行时 URL 是 discovery feed 里的
`ServiceMeta.base_url` + OpenAPI path（没有 fallback 常量）：

```
resolve_runtime_gateway(service) + spec.paths.<key>
= https://apig.osinfra.cn + /cve-security-notice-server/securitynotice/getByCveId
```

这样保证：spec 写错后端、spec 指向测试域、spec 临时下线都不会影响
`oed` 的可用性 —— 网关是单一可信入口。
