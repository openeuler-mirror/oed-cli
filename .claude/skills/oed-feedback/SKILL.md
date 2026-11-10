---
name: oed-feedback
description: oed-cli 用户反馈通道。对 oed CLI 本身提意见——报 bug、请求新功能、查询已有 issue、跟进维护者回复——直接在 gitcode.com/openeuler/oed-cli 建 issue。当用户说"给 oed 提个 issue / 反馈个 bug / oed 这里有问题要报 / oed 支持一下 XX"时加载本 skill。注意：本 skill 反馈的是 oed-cli 这个工具，不是用它查 openEuler 服务（那是 oed-cli skill 的事）。
---

# oed-feedback — oed-cli 反馈

反馈目标仓库固定为 **gitcode.com/openeuler/oed-cli**（owner=openeuler, repo=oed-cli）。
全部走 `oed ag <operation>`，严禁手写 curl。

## 工作流

### 1. 收集反馈内容

向用户确认三件事（对话里已有就不重复问）：**问题现象**（或功能诉求）、**复现命令**、**期望 vs 实际**。
涉及报错时补齐环境信息，不要凭空编造：

```bash
oed --version          # CLI 版本
python3 --version      # 运行时
uname -s               # 平台
```

原始 stderr 和退出码原样保留（退出码语义：0 成功 / 1 用户错误 / 2 网络错误 / 3 上游错误 / 4 未找到）。
**严禁**把 token、`~/.cache/oed-cli/tokens/` 下任何内容写进 issue。

### 2. 查重（必做，避免重复 issue）

```bash
oed ag listRepoIssues --owner openeuler --repo oed-cli --state all --search '<关键词>' --per-page 20 \
  | jq -r '.response[] | "\(.number)\t\(.state)\t\(.title)\t\(.html_url)"'
```

- 命中已有 issue → 列给用户选择：去该 issue 下评论补充（`createIssueComment --owner openeuler --repo oed-cli --number <n> --json "$(jq -n --rawfile body /tmp/fb.txt '{body: $body}')"`)，不再新开。
- 未命中 → 进入起草。

### 3. 起草并确认

- 标题：祈使句、一句话现象（如 `cache refresh 后 CVE 查询 403`；功能类如 `支持 XX 服务的 YY 端点`）。
- 正文分节：环境（版本/平台/Python）/ 复现步骤（命令原样）/ 期望 / 实际（stderr + 退出码）/ 补充上下文。
- **展示草稿给用户确认后再提交**，用户改了哪句就按哪句。

### 4. 提交 issue

```bash
cat > /tmp/fb_issue.txt << 'EOF'
<issue 正文>
EOF
oed ag createIssue --owner openeuler \
  --json "$(jq -n --arg title '<标题>' --arg repo oed-cli --rawfile body /tmp/fb_issue.txt \
    '{repo: $repo, title: $title, body: $body}')"
```

⚠️ AtomGit API 怪癖：**`repo` 在 body 里**（path 只有 owner），漏了会报错。
默认不打 labels（仓库 label 集不确定）；用户明确要求才加。

### 5. 未登录兜底

报 `error="ag_token_missing"` 时引导用户先执行 `oed ag login`（浏览器授权，存本地 token），
登录后重试；查询（listRepoIssues）匿名可用，提交必须登录。

### 6. 收尾与跟进

- 输出 issue URL，告知用户维护者回复后可用同命令查询：
  `oed ag getRepoIssue --owner openeuler --repo oed-cli --number <n>` 和
  `oed ag listIssueComments --owner openeuler --repo oed-cli --number <n>`。
- 若反馈源于环境问题（缓存、Windows 编码、pipx 隔离等），顺手给用户一行本地规避建议（缓存 TTL 600s
  可 `oed cache refresh`；Windows 中文输出见 README `PYTHONIOENCODING=utf-8`），issue 照提不误。
