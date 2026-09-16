# oneid 登录（RFC 8628 设备码流程）

> 返回 [README](../README_cn.md)

openEuler 网关后的大多数服务（eulermaker、pkgcontrib、meeting……）需要一个**用户身份**——不是 AtomGit PAT，而是你的 openEuler oneid 账号。`oed login` 通过 [RFC 8628 设备授权许可（Device Authorization Grant）](https://datatracker.ietf.org/doc/html/rfc8628) 来获取它：无需本地 HTTP 服务器，无需 `redirect_uri`，无需 `client_secret`，无需在防火墙上开放端口。它在笔记本电脑、SSH 远程连接和容器中的工作方式完全一致。

## 登录

```
oed login
```

`oed` 向 openEuler oneid 请求一个设备码，并将**用户码**和**验证 URL**一起打印到 stderr：

```
Open https://omapi.osinfra.cn/oneid/oidc/device in a browser.
Enter the user code: 98R4-GGKW
Waiting for approval...
```
<img width="846" height="98" alt="oed login 触发后终端输出 user code + verification URL" src="images/oed-login-terminal-output.png" />
<img width="601" height="626" alt="oneid 设备码输入页" src="images/oed-login-device-input.png" />
<img width="527" height="712" alt="openEuler 服务授权页（搜索 / CVE / 论坛 / EulerMaker）" src="images/oed-login-approve.png" />
<img width="500" height="205" alt="授权成功，oed 开始轮询后台" src="images/oed-login-success.png" />

在**任意**浏览器中打开该 URL——本机浏览器、手机，或通过 SSH 连接到远程主机的笔记本——输入用户码，并用你的 openEuler 账号批准该请求。`oed` 在后台轮询 oneid，一旦你批准，就自动存储所获得的 `access_token` + `refresh_token`。你无需将令牌粘贴到终端中。

`oed login` 是 `oed auth login` 的顶层快捷方式；两个名称都有效，并运行相同的设备码流程。

## 令牌存储在哪里

成功登录后，当存在可用的操作系统原生凭据存储时，令牌会被写入其中——macOS 钥匙串、Windows DPAPI / 凭据管理器、Linux SecretService（libsecret）。当无法访问任何凭据存储时（无界面的 Linux、CI、锁定的 GNOME 钥匙串），`oed` 会静默回退到 `<OED_CACHE_DIR>/auth.json`，在 POSIX 上权限为 `0600`；无需用户操作。

| 平台               | 后端                                            |
| ---------------------- | ----------------------------------------------- |
| macOS                  | Keychain（加密）                                |
| Windows                | DPAPI / 凭据管理器（加密）                       |
| Linux 桌面             | SecretService / libsecret（加密）               |
| 无界面的 Linux / CI    | `auth.json` 0600 明文回退                        |

> **为什么 `oed auth status` 在 Windows / Linux 上开箱显示 `plaintext`** —— Python 的 `keyring` 库将各平台后端作为*可选*的额外依赖发布（以便在最小化系统上也能安装）。`oed-cli` 仅依赖 `keyring>=24`；要使用 Windows DPAPI / Linux SecretService 后端，你还需要安装以下之一：
>
> ```
> pip install "oed-cli[os-keyring-windows]"   # Windows DPAPI / 凭据管理器
> pip install "oed-cli[os-keyring-linux]"     # Linux SecretService（libsecret）
> pip install "oed-cli[os-keyring]"           # 两者都装
> ```
>
> 如果没有匹配的后端，`oed` 会静默使用 0600 明文回退——没有任何东西会损坏，`oed auth status` 中的 `backend` 字段会显示为 `"plaintext"`。

随时可用 `oed auth status` 查看当前生效的后端——JSON 中包含一个 `"backend"` 字段（`"keyring"` 或 `"plaintext"`）。当使用 keyring 时，不会向磁盘写入任何 `auth.json`。

登录后，后续每次 `oed <service> <operation>` 调用都会自动将令牌作为 `Authorization: Bearer <token>` 发送——无需每次调用的标志。上游返回的 `401` 会以 `UpstreamError(kind="unauthorized")` 的形式呈现，并附带指向 `oed auth status` 的提示。

## 无界面 / SSH / CI 登录

在无界面主机上（无 `DISPLAY` / `WAYLAND_DISPLAY`）`oed login` 依然可用——它将 URL + 用户码打印到 stderr 并持续轮询。你从**任意**其他浏览器批准；当批准到达时，CLI 会获取令牌。服务器上无需运行浏览器。

要完全跳过自动打开浏览器的尝试（CI / 容器），设置：

```
BROWSER=none oed login
```

> **提示** —— 不要在某个码处于待审批状态时再次运行 `oed login`。新的运行会请求一个**新**的设备码，并覆盖你可能已在浏览器中正在审批的那个码。如果第一个码已过期，只需等待——oneid 会使其失效，`oed login` 会提示你重新开始。

## 其他认证命令

```
oed auth status                  # 是否已加载令牌？（令牌以 first3...last2 形式脱敏；显示后端 + 允许列表）
oed login --manual               # 从 TTY 粘贴令牌（在非 TTY 环境中被拒绝）
oed auth token <bearer> [--cookie "k=v"]   # 直接写入令牌——推荐用于智能体 / CI / 管道脚本
oed auth logout                  # 清除已存储的令牌
```

运行时优先级：`OED_TOKEN` 环境变量优先于已存储的令牌；`OED_COOKIE` 对可选的 `Cookie` 请求头同理。

## 按用户的服务允许列表

当你通过 `oed login`（设备码流程）登录时，oneid 会返回你已为此 CLI 授权的服务列表。`oed` 将其本地存储在 `auth.json.allowlist` 中，并用它来管控后续的 `oed <service> ...` 调用——不在列表上的服务会被以 `kind="service_blocked_by_allowlist"`（退出码 1）拒绝。在 oneid 界面中更新你的允许列表，然后重新运行 `oed login` 刷新本地副本。

当 `auth.json` 缺失、没有 `allowlist` 字段（旧版兼容）或列表为 `[]`（你已在 oneid 中显式清空）时，该检查为**失败放行（fail-open）**。保留命令（`auth`、`services`、`info`、`schema`、`cache`、`--help`、`--version`）完全绕过此管控。`oed auth status` 显示当前列表。
