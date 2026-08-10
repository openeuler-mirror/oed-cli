# openEuler Infra API Gateway — 发现服务（Discovery Service）使用文档

> 服务定位：`https://api-gateway.osinfra.cn` 网关下挂的「服务发现」模块，用于对外暴露 openEuler Infra 生态中各社区、各服务的 OpenAPI 元数据/规范。
> 抓取日期：2026-07-25；测试期间均添加浏览器风格请求头（`User-Agent`、`Accept`、`Referer`）以绕过 WAF。

---

## 1. 端点总览

| 方法 + 路径 | 说明 | 实测响应 |
|---|---|---|
| `GET /discovery/apis` | 不带参数，返回**所有社区**及每个社区下的服务清单（payload 形如 `{kind, communities: {...}}`） | 200，JSON |
| `GET /discovery/apis?community={name}` | 仅返回指定社区下的服务列表（数组） | 200，JSON |
| `GET /discovery/apis/{community}/{service_name}` | 返回单个服务的 **OpenAPI 3.0.3 规范对象**（非 YAML，但语义等价） | 200，JSON |
| 其它子路径，如 `/discovery`、`/discovery/communities` | **不存在** | 404 |
| `HEAD` 任意端点 | 网关不支持 | 501 Not Implemented |

> 已知社区：截至本次抓取，仅 `openeuler` 有数据；`openmind`、`opencloud` 等返回空数组 `[]`；拼写错误的社区名也返回 `[]`（不报错）。

---

## 2. 端点 1：`GET /discovery/apis`

获取所有社区的服务全景。

### 请求

```http
GET https://api-gateway.osinfra.cn/discovery/apis
Accept: application/json
```

无 query / body / 鉴权。

### 响应示例

```json
{
  "kind": "discovery#servicesListByCommunity",
  "communities": {
    "openeuler": [
      {
        "name": "openeuler/software-package-server",
        "service_name": "software-package-server",
        "community": "openeuler",
        "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
        "version": "1.0.0",
        "description": "",
        "base_url": "$APIG_GROUP_ENTRY_URL"
      },
      {
        "name": "openeuler/easysearch",
        "service_name": "easysearch",
        "community": "openeuler",
        "title": "APIG_OPENEULER_EASYSEARCH",
        "version": "1.0.0",
        "description": "文档搜索服务接口文档",
        "base_url": "$APIG_GROUP_ENTRY_URL"
      }
      /* 共 8 条 openeuler 服务 */
    ]
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | string | 固定值 `discovery#servicesListByCommunity`，用于客户端区分响应类型（参考 Google API Discovery 命名风格）。 |
| `communities` | object<string, array> | key 为社区标识（如 `openeuler`），value 为该社区下的服务项数组；数据为空时 value 为 `[]`。 |

### 服务项字段（`communities.*[]`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | `"{community}/{service_name}"` 形式的唯一标识。 |
| `service_name` | string | 在社区内的短名称，可作为 `GET /discovery/apis/{community}/{service_name}` 的 `{service_name}` 段。 |
| `community` | string | 所属社区标识。 |
| `title` | string | 在 APIG 网关上的展示名（通常以 `APIG_<COMMUNITY>_<SERVICE>` 命名）。 |
| `version` | string | OpenAPI 规范版本（`1.0.0`、`latest` 等）。 |
| `description` | string | 服务描述，可为空字符串或多行 Markdown。 |
| `base_url` | string | 服务默认 baseURL，**返回值为占位符 `$APIG_GROUP_ENTRY_URL`**，调用方需要从网关/环境获取真实入口并替换。 |

### 调用示例

```bash
curl -sS \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36' \
  -H 'Accept: application/json' \
  -H 'Referer: https://api-gateway.osinfra.cn/' \
  https://api-gateway.osinfra.cn/discovery/apis
```

---

## 3. 端点 2：`GET /discovery/apis?community={name}`

仅获取指定社区下的服务清单，响应是一个顶层 JSON 数组（与上一节中的 `communities[name]` 等价）。

### 请求

```http
GET https://api-gateway.osinfra.cn/discovery/apis?community=openeuler
```

### 响应示例

```json
[
  {
    "name": "openeuler/software-package-server",
    "service_name": "software-package-server",
    "community": "openeuler",
    "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
    "version": "1.0.0",
    "description": "",
    "base_url": "$APIG_GROUP_ENTRY_URL"
  }
  /* 共 8 条 */
]
```

### 调用示例

```bash
curl -sS \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36' \
  -H 'Accept: application/json' \
  -H 'Referer: https://api-gateway.osinfra.cn/' \
  "https://api-gateway.osinfra.cn/discovery/apis?community=openeuler"
```

### 行为说明

- **未匹配到任何服务时返回 `[]`，HTTP 仍为 200**，没有 404 错误。  
  例：`?community=invalid_xyz` / `?community=openmind` / `?community=opencloud` 均得到 `[]`。
- **支持的 community 目前仅 `openeuler` 已实际填充**，其它社区为将来扩展保留位。
- 无分页/排序参数——单社区服务规模较小时一包返回。

---

## 4. 端点 3：`GET /discovery/apis/{community}/{service_name}`

获取单个服务的完整 OpenAPI 3.0.3 规范对象（通常是从后端 OpenAPI 文件解析后缓存的 JSON，结构与 `.yaml` 等价）。

### 请求

```http
GET https://api-gateway.osinfra.cn/discovery/apis/openeuler/software-package-server
```

### 路径参数

| 参数 | 描述 |
|---|---|
| `{community}` | 社区名（如 `openeuler`），大小写敏感，需与 `discovery/apis` 返回的 `community` 字段一致 |
| `{service_name}` | 服务短名（如 `software-package-server`），需与 `discovery/apis` 返回的 `service_name` 字段一致 |

### 响应结构（OpenAPI 3.0.3）

```json
{
  "openapi": "3.0.3",
  "info": {
    "title": "APIG_OPENEULER_SOFTWARE_PACKAGE_SERVER",
    "version": "1.0.0"
  },
  "servers": [
    { "url": "$APIG_GROUP_ENTRY_URL" }
  ],
  "paths": {
    /* 各 endpoint 的 GET/POST/PUT 等定义 */
  },
  "components": {}
}
```

### 调用示例

```bash
# 单服务规范
curl -sS \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36' \
  -H 'Accept: application/json' \
  -H 'Referer: https://api-gateway.osinfra.cn/' \
  https://api-gateway.osinfra.cn/discovery/apis/openeuler/software-package-server
```

### 错误情况

| 路径 | 实际响应 |
|---|---|
| `{community}/{unknown_service}` | 视网关实现可能返回 404 或空对象——取不到的服务请以清单里 `service_name` 为准。 |

---

## 5. 典型使用流程（伪代码 / Bash）

1. 拉全部社区服务列表：

   ```bash
   curl -sS -H 'Accept: application/json' https://api-gateway.osinfra.cn/discovery/apis \
     | jq '.communities | keys'
   ```

2. 按社区裁剪：

   ```bash
   curl -sS -H 'Accept: application/json' \
     'https://api-gateway.osinfra.cn/discovery/apis?community=openeuler' \
     | jq '[.[] | {service_name, title, version}]'
   ```

3. 取某服务的 OpenAPI 并高亮路径：

   ```bash
   curl -sS -H 'Accept: application/json' \
     https://api-gateway.osinfra.cn/discovery/apis/openeuler/software-package-server \
     | jq '.paths | keys'
   ```

---

## 6. 注意事项 & 已知限制

1. **必须带浏览器风格请求头**。否则会被 `HWWAFSESTIME`/`HWWAFSESID` Cookie 驱动的 CloudWAF 拦截，返回中文 HTML「访问被拦截！」。
   - 至少需要：`User-Agent`、`Accept: application/json`、`Referer: https://api-gateway.osinfra.cn/`（手写 curl 时）。
   - **oed-cli 默认不发 `Referer`**：实测 `easysearch` 的 `/sigsearch/docs` 在带 `Referer: https://api-gateway.osinfra.cn/` 时返回 HTTP 401；不带时 `/discovery/apis` 仍能正常过 CloudWAF（2026-07-28 验证）。oed-cli 内部仅带 `User-Agent` / `Accept` / `Accept-Language` 三个头。
2. **`base_url` 仅为占位**。OpenAPI 中 `servers[0].url` / 服务元数据 `base_url` 都是 `$APIG_GROUP_ENTRY_URL`，**不是真实可调用地址**。落地时需用网关下发的真实入口域名替换（详见具体服务的鉴权/环境变量说明）。
3. **范围有限**。实测有效端点只有 3 个（见第 1 节）；`/discovery`、`/discovery/communities` 等常见路径均 404，没有「列出所有社区」专用端点。
4. **HEAD 不支持**：会得到 `501 Unsupported method`。健康检查请直接 `GET`。
5. **CORS 未声明**：浏览器侧直接调用会受同源策略限制，统一走后端/BFF 转发最稳妥。
6. **数据完整性**：当前只有 `openeuler` 一个社区被填充，要观察其它社区何时启用需持续轮询本接口。
7. **鉴权**：本服务**未声明鉴权**（所有端点都返回 200 + 公开数据），但调用方 IP 仍可能被网关侧风控。

---

## 7. 实测验证记录

| 时间 | 请求 | HTTP | 备注 |
|---|---|---|---|
| 2026-07-25 | `GET /discovery/apis` | 200 | kind=`discovery#servicesListByCommunity`，含 `openeuler` 8 项 |
| 2026-07-25 | `GET /discovery/apis?community=openeuler` | 200 | 顶层数组，8 项 |
| 2026-07-25 | `GET /discovery/apis?community=openmind` | 200 | `[]` |
| 2026-07-25 | `GET /discovery/apis?community=opencloud` | 200 | `[]` |
| 2026-07-25 | `GET /discovery/apis?community=invalid_xyz` | 200 | `[]` |
| 2026-07-25 | `GET /discovery/apis/openeuler/software-package-server` | 200 | OpenAPI 3.0.3 规范对象 |
| 2026-07-25 | `GET /discovery` | 404 | `Not Found`（Python 内置错页） |
| 2026-07-25 | `GET /discovery/communities` | 404 | `Not Found` |
| 2026-07-25 | `HEAD /discovery/apis?community=openeuler` | 501 | `Unsupported method ('HEAD')` |
| 2026-07-25 | 任意请求 **不带**浏览器请求头 | (HTML) | WAF 拦截页「访问被拦截！」 |
