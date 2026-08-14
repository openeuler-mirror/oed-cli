"""Unit tests for `context/functionGraph.py` — gateway front-auth.

These tests load `functionGraph.py` directly via `importlib.util` because it
lives outside the `oed_cli` package and is deployed as an APIG custom auth
function. We test the `build_backend_headers` pure function in isolation; the
`handler` integrates with HMAC + role lookup and is exercised end-to-end in
the gateway environment, not here.

All tests run offline — no HTTP calls to auth-center / gateway.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = ROOT / "context"
MODULE_PATH = CONTEXT_DIR / "functionGraph.py"


@pytest.fixture
def functionGraph():
    """Load `context/functionGraph.py` fresh for each test."""
    spec = importlib.util.spec_from_file_location("_functionGraph_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------- discourse 分支 ----------


def test_discourse_overrides_api_headers_with_bot_creds(functionGraph, monkeypatch):
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot-key-xyz")
    monkeypatch.setattr(functionGraph, "FORUM_BOT_USERNAME", "oed-bot")
    headers = {
        "authorization": "Bearer user-oneid",
        "api-key": "user-key",
        "api-username": "user-name",
        "user-agent": "oed/0.1",
        "x-forwarded-for": "10.0.0.1",
    }
    out = functionGraph.build_backend_headers("discourse", headers)
    assert out["Api-Key"] == "bot-key-xyz"
    assert out["Api-Username"] == "oed-bot"
    # 其它 header 原样透传
    assert out["Authorization"] == "Bearer user-oneid"
    assert out["User-Agent"] == "oed/0.1"
    assert out["X-Forwarded-For"] == "10.0.0.1"


def test_discourse_passes_through_when_bot_creds_empty(functionGraph):
    """默认环境（未配 FORUM_BOT_*）→ 不覆盖、原样透传。"""
    headers = {
        "authorization": "Bearer user-oneid",
        "api-key": "user-key",
        "api-username": "user-name",
        "user-agent": "oed/0.1",
    }
    out = functionGraph.build_backend_headers("discourse", headers)
    assert out["Api-Key"] == "user-key"
    assert out["Api-Username"] == "user-name"
    assert out["Authorization"] == "Bearer user-oneid"
    assert out["User-Agent"] == "oed/0.1"


def test_discourse_strips_secret_token(functionGraph, monkeypatch):
    """x-secret-token 是 CLI↔网关的鉴权信封，绝不带给 Discourse 后端。"""
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot")
    monkeypatch.setattr(functionGraph, "FORUM_BOT_USERNAME", "oed")
    headers = {
        "authorization": "Bearer x",
        "x-secret-token": "encrypted-envelope",
        "api-key": "user",
    }
    out = functionGraph.build_backend_headers("discourse", headers)
    assert "x-secret-token" not in out
    assert "x-secret-token" not in {k.lower() for k in out}
    # 机器人凭据仍然覆盖
    assert out["Api-Key"] == "bot"
    assert out["Api-Username"] == "oed"


def test_discourse_partial_bot_creds_only_overrides_provided(functionGraph, monkeypatch):
    """只配了 API key 时，username 头不写空串，保持透传。"""
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot-key")
    monkeypatch.setattr(functionGraph, "FORUM_BOT_USERNAME", "")
    headers = {
        "api-key": "user-key",
        "api-username": "user-name",
    }
    out = functionGraph.build_backend_headers("discourse", headers)
    assert out["Api-Key"] == "bot-key"
    # username 头没配机器人 → 不写入空串；用户原值原样保留
    assert out["Api-Username"] == "user-name"


def test_discourse_output_headers_are_canonical_case(functionGraph, monkeypatch):
    """出站头用 Discourse 官方约定大小写（Api-Key / Api-Username）。"""
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot")
    monkeypatch.setattr(functionGraph, "FORUM_BOT_USERNAME", "oed")
    out = functionGraph.build_backend_headers("discourse", {"authorization": ""})
    assert "Api-Key" in out
    assert "Api-Username" in out
    # 确认没有产生奇怪的纯小写 / 全大写别名
    assert "api-key" not in out
    assert "API-KEY" not in out


# ---------- 现有分支回归 ----------


def test_software_package_server_branch_unchanged(functionGraph, monkeypatch):
    """forum 凭据即使配了，也不能影响 software-package-server 分支。"""
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot-key")
    headers = {
        "authorization": "Bearer xyz",
        "api-key": "should-not-touch",
    }
    out = functionGraph.build_backend_headers("software-package-server", headers)
    assert out["TOKEN"] == "xyz"
    assert "Api-Key" not in out
    assert "Api-Key" not in {k.lower() for k in out}


def test_fallback_branch_only_passes_auth_headers(functionGraph, monkeypatch):
    """其它服务的兜底分支：只透传 Authorization / Cookie，不应被 forum 凭据污染。"""
    monkeypatch.setattr(functionGraph, "FORUM_BOT_API_KEY", "bot-key")
    monkeypatch.setattr(functionGraph, "FORUM_BOT_USERNAME", "oed")
    headers = {
        "authorization": "Bearer xyz",
        "cookie": "_Y_G_=abc",
        "api-key": "user",
        "user-agent": "oed",
    }
    out = functionGraph.build_backend_headers("easysearch", headers)
    assert out == {"Authorization": "Bearer xyz", "Cookie": "_Y_G_=abc"}
