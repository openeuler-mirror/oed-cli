# 发布到 PyPI

最短路径:**改两处版本号 → build → twine upload**。

发版是少数几个会**触发跨文件改动**的操作;其它元数据(名称、依赖、license、Python 版本约束)在 `pyproject.toml` 都已固化,基本不需要动。

---

## 0. 前置

- 一个 PyPI 账户(`https://pypi.org/account/register/`)
- 一个 TestPyPI 账户(推荐先 dry-run,`https://test.pypi.org/`)
- 一个 **API token**,在 `https://pypi.org/manage/account/token/` 创建
- 通过 token 认证 —— **不要用密码**,PyPI 已关闭密码登录

把 token 放进环境变量(避免落进 shell 历史或 commit):

```bash
# bash / zsh
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-..."        # 整段粘贴,包括前缀 pypi-

# PowerShell
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-..."
```

> `TWINE_USERNAME` 永远是字面量 `__token__`,**不是**你的 PyPI 用户名。

---

## 1. 改两处版本号

本项目有两个版本源 —— setuptools 不读 `__init__.py`,所以它们**必须同时改**:

| 位置 | 字段 | 谁用它 |
|---|---|---|
| `pyproject.toml:7` | `version = "0.1.5"` | wheel 文件名 + PyPI 项目页版本 |
| `src/oed_cli/__init__.py:5` | `__version__ = "0.1.5"` | `oed --version` 的输出、`http.py` 的 `User-Agent` 头(`oed/0.1.5 (+...)`)|

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@
 [project]
 name = "oed-cli"
-version = "0.1.5"
+version = "0.1.5"

--- a/src/oed_cli/__init__.py
+++ b/src/oed_cli/__init__.py
@@
-from __future__ import annotations
-
-__version__ = "0.1.5"
-__all__ = ["__version__"]
+from __future__ import annotations
+
+__version__ = "0.1.5"
+__all__ = ["__version__"]
```

或者一行命令搞定:

```bash
sed -i 's/version = "0.1.5"/version = "0.1.5"/' pyproject.toml
sed -i 's/__version__ = "0.1.5"/__version__ = "0.1.5"/' src/oed_cli/__init__.py
```

> ⚠️ 改完第二处后,User-Agent 会变成 `oed/0.1.5 ...`。CloudWAF 跟 UA 没有强耦合(就是 `http.py:30` 那条),但**如果你之前因为 WAF 拦了默认 UA 而改过 `OED_USER_AGENT` 环境变量**,那是另一回事,跟这里无关。

---

## 2. 跑校验

项目里改了 `.py` 会触发 `PostToolUse` hook 自动跑 ruff + pytest。发版前手动再过一遍:

```bash
ruff check src tests
pytest -q
```

任何红都不能发。

---

## 3. 装构建/上传工具

`build` 和 `twine` 已经在 `[project.optional-dependencies].dev` 里:

```bash
pip install -e ".[dev]"
```

不需要再单独装。

---

## 4. 清理旧产物,重新构建

```bash
rm -rf dist/ build/ src/oed_cli.egg-info/
python -m build
```

成功的话 `dist/` 下出现两个文件:

```
dist/oed_cli-0.1.5-py3-none-any.whl
dist/oed_cli-0.1.5.tar.gz
```

> 🚨 **文件名中间的 `0.1.5` 改成新版本号了么?** 没改就是没生效 —— 见第 7 节"常见问题 2"。

---

## 5. 先发到 TestPyPI(强烈推荐)

```bash
twine upload --repository testpypi dist/*
```

完事后随手验证:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            oed-cli==0.1.5
oed --version    # 应该输出: oed, version 0.1.5
```

`--extra-index-url` 兜底拉普通 PyPI 上的依赖(`click`、`httpx`),因为 TestPyPI 没有这些。

---

## 6. 发到正式 PyPI

```bash
twine upload --repository pypi dist/*
```

`--repository pypi` 是 twine 的默认源,理论上可以省略;但显式写出可避免手滑。

---

## 7. 验证发布

- PyPI 项目页:`https://pypi.org/project/oed-cli/#history` 看到新版本在 latest
- 重装确认:

  ```bash
  pip install --upgrade oed-cli
  oed --version
  ```

- 随便挑一个 operation 跑通(discovery 跟 PyPI 版本无关,但确认 entry point 没坏):

  ```bash
  oed services | head -20
  ```

---

## 8. 常见问题

### (1) "File already exists"

PyPI 不允许覆盖已发版本。解决:

- bump 版本号重发(推荐)
- 或者在 PyPI 项目页 → "Manage" → "Releases" → 选已发布版本 → "Delete"(只允许删 latest)

### (2) "Invalid distribution filename" 或文件名没带新版本号

`python -m build` 没正确读版本号,通常是:

- 没清 build cache → `rm -rf dist/ build/ src/oed_cli.egg-info/` 再重试
- `pyproject.toml` 改错行(版本号可能同时出现在 `[project]` 之外的别处,本项目目前只在第 7 行)

### (3) "Invalid or non-existent authentication information"

- 确认 `TWINE_PASSWORD` 环境变量已经设置,token 没截断
- token 应该是 `pypi-AgENd...` 这种长串,不是 PyPI 登录密码

### (4) 上传成功但 `pip install` 拉不到

- 检查 `pip` 用的是哪个 index:`pip config list`,可能被 `.pip/pip.conf` 锁住
- 直接拉: `pip install --index-url https://pypi.org/simple/ oed-cli==0.1.5`

### (5) wheel 装好但 `oed` 命令找不到

entry point 没装进 PATH。检查:

```bash
pip show -f oed-cli | grep -E '(oed |oed_cli.*main)'
# 应该看到: oed_cli/__init__.py   oed_cli/main.py
#            .../oed              ← 这是 entry point
```

如果 entry point script 没生成,重新 `pip install --force-reinstall oed-cli`。

### (6) 改了 `__version__` 但 `oed --version` 还是老版本

- `__init__.py` 在 editable install 模式下会被即时读取,普通 install 会被打进 metadata
- `pip install --upgrade --force-reinstall oed-cli` 重装即可

---

## 9. 自动化(可选,本项目目前未启用)

> ⚠️ **API token 严禁写到仓库**。用 PyPI Trusted Publishing(OIDC),token 完全不需要存在。

如果以后想加 GitHub Actions 自动化发版,大致骨架:

```yaml
# .github/workflows/release.yml
name: release
on:
  push:
    tags: ['v*']
jobs:
  publish:
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/oed-cli
    permissions:
      id-token: write       # OIDC / Trusted Publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: ruff check src tests
      - run: pytest -q
      - run: rm -rf dist/ build/ src/oed_cli.egg-info/
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

PyPI 那边要在项目设置里配 Trusted Publisher,把仓库 + workflow 文件名 + environment 配齐。

> 按 `CLAUDE.md` 约束:自动化发版是**横跨多个文件**(workflow 文件 + PyPI 后台配置 + `pyproject.toml` 元数据),所以走 Push 前先让 reviewer 确认。

---

## 10. 收尾 checklist

- [ ] `pyproject.toml` 和 `src/oed_cli/__init__.py` 版本号一致
- [ ] `ruff check src tests` 通过
- [ ] `pytest -q` 全过(41 个)
- [ ] `dist/oed_cli-<ver>-py3-none-any.whl` 文件名版本号正确
- [ ] TestPyPI 试发成功 + `pip install` + `oed --version` 都 OK
- [ ] PyPI 发布成功
- [ ] `git tag v<ver>` 并 push tag(配合 git 的 tag-driven release 工作流)
- [ ] 仓库 README / docs 反映新版本号(如果写了版本号)
- [ ] **不自动 commit** —— 报告给 reviewer,等他们说"commit"再走 `git add <files> && git commit`
