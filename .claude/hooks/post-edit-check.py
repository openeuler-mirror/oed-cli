"""PostToolUse hook: run ruff + pytest after any .py file is edited.

Triggered by .claude/settings.json after Edit / Write / MultiEdit. Claude Code
passes the tool invocation as JSON on stdin:

    {"tool_name": "Edit", "tool_input": {"file_path": "...", ...}, ...}

Behaviour:
    * If the edited file is not a project .py under repo root, exit 0 silently.
    * Otherwise run `ruff check src tests` then `pytest -q` from the repo root.
    * Both pass → exit 0 with a short success line.
    * Either fails → exit 1; stdout/stderr are surfaced to the agent as a
      blocking error so the agent is forced to fix it before continuing.

This is the agent's "stop sign" — there is no `--no-verify` flag in
Claude Code, so to keep CI green you must keep this hook green.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Force UTF-8 on stdout/stderr so the hook works on Windows consoles that
# default to GBK (which can't encode the success marker). Subprocess output
# is captured as text and re-printed — errors:='replace' so any non-UTF8
# bytes from a tool call don't bring the hook down.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Paths under these directory names are not project source — skip them.
SKIP_DIRS = (".venv", "venv", "env", ".pytest_cache", ".ruff_cache", "site-packages")


def _repo_root() -> Path:
    """Walk up from CWD to find the directory containing pyproject.toml."""

    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return cwd


def _project_path(file_path: str, repo_root: Path) -> Path | None:
    """Return the absolute path if file_path lives inside the repo, else None."""

    p = Path(file_path).resolve()
    try:
        p.relative_to(repo_root)
    except ValueError:
        return None
    return p


def _run(label: str, args: list[str], cwd: Path) -> int:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print(f"[post-edit] FAILED: {label}", file=sys.stderr)
    return result.returncode


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # not a tool event we recognise — let it through.

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path or not file_path.endswith(".py"):
        return 0

    repo_root = _repo_root()
    abs_path = _project_path(file_path, repo_root)
    if abs_path is None:
        return 0
    if any(part in SKIP_DIRS for part in abs_path.parts):
        return 0

    print(f"[post-edit] checking {abs_path.relative_to(repo_root)} ...")

    if _run("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests"], repo_root):
        return 1
    if _run("pytest", [sys.executable, "-m", "pytest", "-q"], repo_root):
        return 1

    print("[post-edit] OK: ruff + pytest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())