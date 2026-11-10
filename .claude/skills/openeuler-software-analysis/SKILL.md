---
name: openeuler-software-analysis
description: "分析一款软件在 openEuler 社区的整体支持情况（RPM / 应用镜像 / pip / oepkg / conda 等全形态的收录、版本、OS/架构覆盖、仓库来源、维护者与活跃度），产出三个视图：OS 主视图（版本支持矩阵）、版本全量清单（每个可用版本 × 可得形态交叉表）、覆盖结论。**不做与上游版本的差距对比**。当用户要求「分析 XX 软件在 openEuler 的支持情况/收录情况」「XX 在 openEuler 哪些版本可用」「用 easysoftware/search 查软件」时加载本 skill。核心结论：search.searchSoftwareByKeyword（POST）是唯一可靠的搜索入口；easysoftware 的 eulerver 系列是精确匹配，必须先用模糊搜索拿到真实包名（如 python3-vllm、vllm-cpu）再查；easysoftware 的 searchRPMPkg 等搜索接口目前后端损坏（永远 400），不要浪费时间。"
---

# openEuler 软件版本支持分析（easysoftware + search）

用 openEuler 网关的两个服务分析一款软件在 openEuler 社区的整体支持情况——**看它收录了哪些形态、哪些 OS 版本、哪些架构、从哪里获取、维护是否活跃**，而不是与上游版本比差距：

- **`search`**（APIG_OPENEULER_EASYSEARCH）：模糊搜索，找真实包名和全量记录
- **`easysoftware`**（APIG_OPENEULER_EASYSOFTWARE）：精确查询，拿支持版本列表与发布地址

调用方式：MCP 工具 `mcp__oed__oed_describe` / `mcp__oed__oed_call`（与 CLI `oed <service> <operation>` 等价）。**GET 接口参数放 `params`，POST 接口参数放 `body`**（CLI 对应 `--params` / `--json`）。API 不确定时先 `oed_describe(service, operation)` 看参数结构。

## ⚠️ 已知的坑（不要重复踩）

1. **不要使用easysoftware 的搜索接口，这个接口是拿来定位的！**：`searchRPMPkg`（GET `/rpmpkg`）、`searchCondaPkg`、`searchEpkgPkg` **不要在这上面浪费尝试**，改走 `search.searchSoftwareByKeyword`。
2. **HTTP 200 不代表成功**：`search.searchAllSoftwareData`（GET `/search/all`）返回 200，但 body 是 `{"status": 201, "msg": "查询失败"}`。判断成败要看 **body 里的 status/msg**，不能只看 HTTP 码。oed 的 exit code：3 = 上游 API 错误（此时 body 仍会打出来，读它）。
3. **`dataType` 的运行时合法值与 OpenAPI schema 不一致**：schema 写的是 `oepkg/all/rpmpkg/apppkg/epkgpkg/appversion/conda`，但运行时正则是
   `oepkg | all | rpmpkg | apppkg | epkgpkg | appversion | conda | pypi | npm | other`。
   传 `pypipkg` / `npmpkg` / `otherpkg` 会被上游拒绝（body 里 `"obj": "[dataType->...正则...]"` 会给出提示）。**pip 生态传 `pypi`，源码安装传 `other`**。
4. **eulerver 系列是精确匹配**：`queryRpmEulerVersions` / `queryCondaEulerVersions` / `queryEpkgEulerVersions` / `queryAppEulerVersions` 按名字**精确匹配**。搜 `vllm` 全部返回空——真实包名是 `python3-vllm`（RPM）、`vllm-cpu`（镜像）。openEuler 里 RPM 通常带 `python3-` 前缀，应用镜像可能带 `-cpu` 等后缀。**必须先模糊搜索拿到真实名**。
5. **`queryAppEulerVersions` 每个 OS 只返回 1 条**，且返回的是哪一版不确定（不保证最新）。完整的镜像版本列表要用 `searchSoftwareByKeyword` 的 `dataType=apppkg`（每条带 `appVer` 字段）。
6. **响应里的中文描述字段可能是乱码**（编码问题），但名称、版本、URL、pkgId 等关键字段是 ASCII，不影响分析；不要因为乱码重试。
7. **easysoftware GET 搜索曾对 `pageSize=50` 与 `name` 单独传都报 400**——原因未知（接口本身坏），若其他接口报 400，先减参数到最小集再逐个加回，定位是哪个参数被拒。

## ✅ 最优调用流程

### Step 1：模糊搜索，锁定真实包名与形态

```
oed_call(service="search", operation="searchSoftwareByKeyword",
         body={"keyword": "<软件名>", "dataType": "all", "pageNum": 1, "pageSize": 50})
```

从返回的 `data.all[]` 提取：
- **真实包名**（`name` 字段带 `<span>` 高亮标签，取内核即可）
- **形态标签**（`tags` / `pkgIds` 的 key）：`RPM` / `IMAGE`（应用镜像）/ `OEPKG`（oepkgs.net 社区仓）等
- **覆盖的 OS**（`os`）与**架构**（`arch`）

### Step 2：按 dataType 分类型深挖（每类一次调用）

对 `rpmpkg` / `apppkg` / `pypi` / `other` / `oepkg` / `epkgpkg` / `conda` 各发一次（body 同上，换 `dataType`）。产出：

| dataType | 关键字段 | 说明 |
|---|---|---|
| `rpmpkg` | `version` `os` `arch` `subPath`（仓库路径）`rpmUpdateAt` `rpmCategory` `binDownloadUrl` `srcRepo` | subPath 区分 update / EPOL/main 等 |
| `apppkg` | `appVer` `os` `arch` `pkgId` `maintainers` | 应用/容器镜像全版本列表 |
| `pypi` | `appVer` `os` `arch` `pipUsage` `homepage` `updateAt` | pip 安装指引（pipUsage 常含版本配套约束） |
| `other` | `appVer` `os` `installType`（源码安装等） | 源码安装覆盖 |
| `oepkg` | 同 rpmpkg，URL 指向 repo.oepkgs.net | 社区第三方仓，多为生态周边包 |

注意：`dataType=all` 时各分类数组为空、只填 `all[]`；传具体 dataType 才填对应数组（部分请求会把全量聚合回来，以各数组内容为准）。总数看 `data.total`。

### Step 3：easysoftware 精确接口补全（用 Step 1 拿到的真实名）

```
oed_call(service="easysoftware", operation="queryRpmEulerVersions", params={"name": "<RPM名>"})
oed_call(service="easysoftware", operation="queryAppEulerVersions", params={"name": "<应用名>"})
oed_call(service="easysoftware", operation="queryAppVersion",
         params={"name": "<应用名>", "pageNum": 1, "pageSize": 50})
```

- `queryRpmEulerVersions`：RPM 覆盖的 OS/架构清单（与 Step 2 的 rpmpkg 结果交叉核对）。
- **`queryAppVersion` 用来补全镜像维度**：`applicationVersions[]` 里的 `openeulerVersion`（本仓版本）、`eulerOsVersion`、`type`（image 等）、`eulerHomepage`（Docker Hub 等发布地址）、`maintainers`；返回可能是多条同 `name` 分组，每条都要看，合并进镜像版本列表。
- 该接口也会返回 `upstreamVersion` / `status`（如 OUTDATED）字段——那是给上游对比用的，**本 skill 不做与上游的差距对比，忽略这两个字段，不要写进结论**。

### Step 4：综合输出

**版本支持矩阵**（OS × 形态 × 版本 × 仓库 × 架构 × 更新时间）+ **覆盖结论**。模板：

```markdown
# <软件> 在 openEuler 的支持情况

## 版本支持矩阵
| openEuler 版本 | 形态(RPM/镜像/pip) | 版本 | 仓库 | 架构 | 更新时间 |
|---|---|---|---|---|---|

## 版本全量清单（版本 × 形态）
| 版本 | RPM | 镜像 | pip | 源码 | 覆盖 OS | 架构 |
|---|---|---|---|---|---|---|

## 覆盖结论
- OS 覆盖重心：哪些 openEuler 系列（LTS / SP / 创新版）有收录、哪些完全没有
- 形态完整度：RPM / 应用镜像 / pip / oepkg / conda / npm 哪些有、哪些缺失，各形态覆盖的 OS 范围差异
- 版本-形态交叉：哪些版本多形态重合（社区投入度最高）、各形态各自的最新版本与节奏差异、
  版本号断档（说明是按 SP/特性节奏选择性构建，非连续全量发布）、同一版本跨 OS 代的覆盖
- 架构覆盖：x86_64 / aarch64 / loongarch64 / riscv64 等
- 维护活跃度：各形态最近一次更新时间（rpmUpdateAt / updateAt），是否存在长期未更新的形态
- 维护者与获取入口：maintainers、eulerHomepage（Docker Hub）、binDownloadUrl、pipUsage 安装指引
- 硬件配套的版本锁定关系：昇腾（如 vllm-ascend 锁 vllm 版本 + triton-ascend）、NVIDIA/CUDA 等
  组合约束（pipUsage 里常见）
```

版本字段解读（用于读矩阵，**不用于和上游比差距**）：镜像 `0.9.1-oe2403lts` 中 `0.9.1` 是应用版本本身，`-oe2403lts` 是构建标签；RPM 版本 `-1.oe2403sp3` 的 `.oe<ver>` 后缀同样是构建标签。

### Step 5：版本全量清单（版本 × 形态 交叉视图）

除 OS 主视图外，把 Step 2/3 的全部记录**按版本号归并**，产出"每个可用版本在哪些形态可得"的交叉表。

**版本号归一化**（各形态格式不同，先统一再归并）：

| 形态 | 原始格式 | 归一化方法 |
|---|---|---|
| RPM | `0.9.1-1.oe2403sp3` | 取 `-<release>.oe*` 之前的本体（第一个 `-数字.oe` 处切） |
| 镜像 | `0.9.1-oe2403lts` | 取 `-oe` 之前 |
| pip / conda | `0.24.0` | `appVer` 本身就是裸版本 |
| other（源码） | `v0.29.0` | 去掉前导 `v` |
| oepkg | `1.1.11-1` | **多为周边生态包**（名字含本软件但非本体，如 happy-vllm、vllm-nccl-cu12），版本号与本软件无关，**不并入本体版本清单**，结论里单独提一句 |

注意变体与锁定关系：
- `-cpu` / `-ascend` 等后缀是**构建目标变体**（同一软件的不同硬件目标），在清单备注中说明，不当作不同软件；
- 硬件插件（如 `vllm-ascend 0.23.0rc1`）的版本号跟随本体但带 `rc`，其 pipUsage 里的**锁定链**（它锁哪个本体版本）要写进对应本体版本行的备注——即插件会间接给某个本体版本带来 pip 形态。

**分析要点**（写进覆盖结论的"版本-形态交叉"）：
- 哪些版本实现了**多形态重合**（既打 RPM 又发镜像/有 pip）——社区投入度最高的版本；
- 各形态**各自的最新版本**：镜像 / pip / RPM 三条线节奏通常不同步（镜像滚动最快，RPM 最保守，pip 往往单版本押注最新）；
- **版本断档**：可用版本号之间的跳跃（如 0.10.1 → 0.14.0）说明社区按 SP/特性节奏**选择性构建**，不做连续全量发布；
- **跨 OS 代重合**：同一版本同时出现在旧代（22.03）与新代（24.03）线上，说明它是跨代稳定版。

## 快速示例（vLLM 实战验证过）

```
# 1. 模糊搜索 → 发现 python3-vllm(RPM) / vllm-cpu(IMAGE) / vllm-ascend(pypi)
oed_call(search, searchSoftwareByKeyword, body={"keyword":"vllm","dataType":"all","pageNum":1,"pageSize":50})
# 2. 分类型 → rpmpkg(7条) apppkg(34条) pypi(8条) other(2条) oepkg(14条)；conda/npm/epkgpkg=0
# 3. 精确查询
oed_call(easysoftware, queryRpmEulerVersions, params={"name":"python3-vllm"})
oed_call(easysoftware, queryAppVersion, params={"name":"vllm-cpu","pageNum":1,"pageSize":50})
#    → 镜像全量版本清单 + 发布地址 hub.docker.com/r/openeuler/vllm-cpu（忽略其 upstreamVersion/status 字段）
```
