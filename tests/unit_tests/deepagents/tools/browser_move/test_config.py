#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for browser_move config and MCP compatibility plumbing."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from openjiuwen.deepagents.tools.browser_move import REPO_ROOT
from openjiuwen.deepagents.tools.browser_move.clients.streamable_http_client import (
    BrowserMoveStreamableHttpClient,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.agents import (
    ensure_execute_signature_compat,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools import (
    build_browser_runtime_mcp_config,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_MODEL_NAME,
    build_runtime_settings,
    parse_command_args,
)
from openjiuwen.deepagents.tools.browser_move.utils.parsing import extract_json_object

from openjiuwen.core.foundation.tool import McpServerConfig


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _module_launch_cwd() -> str:
    current = Path(REPO_ROOT).expanduser().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "openjiuwen" / "__init__.py").exists():
            return str(candidate)
    raise RuntimeError(f"Could not resolve module launch cwd from {current}")


class _ToolCall:
    def __init__(self, name: str):
        self.name = name


class _Agent:
    def __init__(self, ability_manager: Any):
        self.ability_manager = ability_manager


class _HangingAbilityManager:
    async def execute(self, ctx: Any, tool_call: Any, session: Any, tag: Any = None):
        await asyncio.sleep(1.0)
        return [({"ok": True, "shape": "ctx"}, None)]


def test_build_runtime_settings_uses_shared_defaults() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = build_runtime_settings()

        assert settings.provider == "openai"
        assert settings.api_key == ""
        assert settings.api_base == "https://api.openai.com/v1"
        assert settings.model_name == DEFAULT_MODEL_NAME
        assert settings.guardrails.timeout_s == DEFAULT_BROWSER_TIMEOUT_S
        assert settings.mcp_cfg.params["timeout_s"] == DEFAULT_BROWSER_TIMEOUT_S


def test_build_runtime_settings_respects_env_overrides() -> None:
    with patch.dict(
        os.environ,
        {
            "MODEL_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "test-key",
            "MODEL_NAME": "google/gemini-3.1-pro-preview",
            "BROWSER_TIMEOUT_S": "45",
            "PLAYWRIGHT_MCP_ARGS": '["-y", "@playwright/mcp@latest", "--headless"]',
        },
        clear=True,
    ):
        settings = build_runtime_settings()

        assert settings.provider == "openrouter"
        assert settings.api_key == "test-key"
        assert settings.model_name == "google/gemini-3.1-pro-preview"
        assert settings.guardrails.timeout_s == 45
        assert settings.mcp_cfg.params["args"] == ["-y", "@playwright/mcp@latest", "--headless"]
        assert settings.mcp_cfg.params["timeout_s"] == 45


def test_parse_command_args_accepts_json_list() -> None:
    assert parse_command_args('["-y", "@playwright/mcp@latest"]') == ["-y", "@playwright/mcp@latest"]


def test_extract_json_object_handles_fenced_json() -> None:
    assert extract_json_object("```json\n{\"ok\": true, \"value\": 1}\n```") == {"ok": True, "value": 1}


def test_build_browser_runtime_mcp_config_disabled_by_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert build_browser_runtime_mcp_config() is None


def test_build_browser_runtime_mcp_config_streamable_http_defaults() -> None:
    with patch.dict(
        os.environ,
        {
            "PLAYWRIGHT_RUNTIME_MCP_ENABLED": "1",
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE": "http",
            "PLAYWRIGHT_RUNTIME_MCP_HOST": "127.0.0.1",
            "PLAYWRIGHT_RUNTIME_MCP_PORT": "8940",
        },
        clear=True,
    ):
        cfg = build_browser_runtime_mcp_config()

    assert cfg is not None
    assert cfg.client_type == "streamable-http"
    assert cfg.server_path == "http://127.0.0.1:8940/mcp"


def test_build_browser_runtime_mcp_config_stdio_defaults() -> None:
    with patch.dict(
        os.environ,
        {
            "PLAYWRIGHT_RUNTIME_MCP_ENABLED": "1",
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE": "stdio",
        },
        clear=True,
    ):
        cfg = build_browser_runtime_mcp_config()

    assert cfg is not None
    assert cfg.client_type == "stdio"
    assert cfg.server_path == "stdio://playwright-runtime-wrapper"
    assert cfg.params["cwd"] == _module_launch_cwd()
    args = cfg.params["args"]
    assert args[:2] == ["-m", "openjiuwen.deepagents.tools.browser_move.playwright_runtime_mcp_server"]
    assert "--transport" in args
    assert "stdio" in args
    assert "--no-banner" in args
    assert "--log-level" in args
    assert "ERROR" in args


def test_build_browser_runtime_mcp_config_supports_legacy_env_names() -> None:
    with patch.dict(
        os.environ,
        {
            "BROWSER_RUNTIME_MCP_ENABLED": "1",
            "BROWSER_RUNTIME_MCP_CLIENT_TYPE": "streamable_http",
            "BROWSER_RUNTIME_MCP_HOST": "localhost",
            "BROWSER_RUNTIME_MCP_PORT": "9999",
            "BROWSER_RUNTIME_MCP_PATH": "custom",
        },
        clear=True,
    ):
        cfg = build_browser_runtime_mcp_config()

    assert cfg is not None
    assert cfg.client_type == "streamable-http"
    assert cfg.server_path == "http://localhost:9999/custom"


def test_build_browser_runtime_mcp_config_stdio_passes_child_env_and_maps_openai() -> None:
    with patch.dict(
        os.environ,
        {
            "PLAYWRIGHT_RUNTIME_MCP_ENABLED": "1",
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE": "stdio",
            "API_KEY": "openai-key",
            "API_BASE": "https://api.openai.com/v1",
            "MODEL_PROVIDER": "OpenAI",
            "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://127.0.0.1:9333",
            "HTTP_PROXY": "http://127.0.0.1:9",
        },
        clear=True,
    ):
        cfg = build_browser_runtime_mcp_config()

    assert cfg is not None
    assert cfg.client_type == "stdio"
    child_env = cfg.params["env"]
    assert child_env["API_KEY"] == "openai-key"
    assert child_env["OPENAI_API_KEY"] == "openai-key"
    assert child_env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert child_env["MODEL_PROVIDER"] == "openai"
    assert child_env["PLAYWRIGHT_MCP_CDP_ENDPOINT"] == "http://127.0.0.1:9333"
    assert "HTTP_PROXY" not in child_env


def test_build_browser_runtime_mcp_config_stdio_maps_openrouter_env() -> None:
    with patch.dict(
        os.environ,
        {
            "PLAYWRIGHT_RUNTIME_MCP_ENABLED": "1",
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE": "stdio",
            "API_KEY": "openrouter-key",
            "API_BASE": "https://openrouter.ai/api/v1",
        },
        clear=True,
    ):
        cfg = build_browser_runtime_mcp_config()

    assert cfg is not None
    child_env = cfg.params["env"]
    assert child_env["OPENROUTER_API_KEY"] == "openrouter-key"
    assert child_env["OPENROUTER_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_browser_move_streamable_http_client_accepts_config_constructor() -> None:
    cfg = McpServerConfig(
        server_id="playwright-runtime-wrapper",
        server_name="playwright-runtime-wrapper",
        server_path="http://127.0.0.1:8940/mcp",
        client_type="streamable-http",
    )
    client = BrowserMoveStreamableHttpClient(cfg)

    assert client.server_path == "http://127.0.0.1:8940/mcp"
    assert client.name == "playwright-runtime-wrapper"


def test_execute_wrapper_raises_timeout_error() -> None:
    with patch.dict(os.environ, {"PLAYWRIGHT_TOOL_TIMEOUT_S": "0.05"}):
        ability_manager = _HangingAbilityManager()
        agent = _Agent(ability_manager)

        ensure_execute_signature_compat(agent)

        tool_call = _ToolCall("browser_run_task")

        try:
            _run(agent.ability_manager.execute(ctx=object(), tool_call=tool_call, session=object()))
        except RuntimeError as exc:
            message = str(exc)
            assert "tool_execution_timeout:" in message
            assert "browser_run_task" in message
            assert "timeout_s=" in message
        else:
            raise AssertionError("expected RuntimeError")
