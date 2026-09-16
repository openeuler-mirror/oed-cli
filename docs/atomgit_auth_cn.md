# AtomGit（`ag`）认证

> 返回 [README](../README_cn.md)

AtomGit 操作通过其规范上声明的 `access_token` 查询参数进行认证。一次性存储个人访问令牌，之后每次 `oed ag ...` 调用都会自动使用它：

```
# 交互式（提示输入令牌，绝不回显）
oed ag login

# 非交互式——适合 CI / 脚本
oed ag login --token <pat>

# 存储前跳过向 AtomGit 验证令牌
oed ag login --token <pat> --no-verify

# 仅报告是否已配置令牌（不访问网络，不提示）
oed ag login --status

# 忘记已存储的令牌
oed ag logout
```

令牌存储：

- 与 `oed login` 使用相同的 keyring 后端存储：当可访问操作系统凭据存储时使用 macOS 钥匙串 / Windows DPAPI / Linux SecretService，否则使用 0600 明文回退文件。
- 位于缓存目录下的 `tokens/` 子目录中（keyring 不可用时为 `tokens/ag.json`）；`oed cache clear` 绝不会触碰凭据。
- 存储在独立的 keyring 条目（`ag:ag`）下，与 `oed login` 的 oneid 令牌分开，因此两套凭据绝不会冲突。

实际调用时的自动注入：

- 如果操作声明了 `access_token` 而你未传入，已存储的令牌会被自动填充——`oed ag listAuthenticatedUserIssues` 直接可用。
- 显式的 `--access-token <pat>` 始终优先于已存储的令牌。
- 如果操作需要令牌而任何地方都没有可用的令牌，你会得到一个清晰的 `ag_token_missing` 错误及提示，而不是一个不透明的网关 401。
- `--dry-run` 和请求回显视图会将令牌掩码为 `<stored>`——真实值仅在网络上传输。
