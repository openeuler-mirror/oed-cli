# 本地开发

> 返回 [README](../README_cn.md)

## 克隆并以可编辑模式安装

```
git clone https://gitcode.com/openeuler/oed-cli && cd oed-cli
pip install -e ".[dev]"
```

`pip install -e .` 使得对源码的修改在下次 `oed` 调用时生效。完成后用 `pip uninstall oed-cli` 卸载即可。

## 运行测试（约 0.3 秒，完全离线）

```
python -m pytest -q
```

58 个测试覆盖了 v0.1 + v0.2 分发、操作帮助速查表、各参数标志的强制转换、`API_` 前缀别名、`resolve_runtime_gateway` 的无回退语义、每条退出码路径，以及 v0.4 的 `ag` 令牌存储（DPAPI/base64）+ 自动注入。它们对发现层进行了 monkeypatch，因此不需要访问网关。

## 对接真实网关进行冒烟测试

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

## 缓存调试

```
oed cache show      # 路径、大小、年龄、TTL
oed cache refresh   # 强制重新拉取发现数据
oed cache clear     # 删除缓存文件
```

缓存位于 `~/.cache/oed-cli/`（XDG）或 Windows 上的 `C:\Users\<你>\AppData\Local\oed-cli\cache\`。可用 `OED_CACHE_DIR=...` 覆盖。各服务的 OpenAPI 规范缓存于其旁的 `specs/<community>/<service>.json`，具有相同的 10 分钟 TTL。认证文件（`auth.json`）以及交换端点所内置的 OAuth 客户端凭据也根植于同一个 `OED_CACHE_DIR` 下。

## 与网关保持同步

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

## 常见错误

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

## 离线模式

`oed --help`、`oed info`（使用缓存数据）、`pytest`，以及任何针对其规范已在本地缓存中的服务的命令，都可以在无网络的情况下工作。要在不安装的情况下从源码运行 `oed`：

```
python -m oed_cli --help
# 或
python -c "from oed_cli.main import main; sys.argv = ['oed','--help']; main()"
```
