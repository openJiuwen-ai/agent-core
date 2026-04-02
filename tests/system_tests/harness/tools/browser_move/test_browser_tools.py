#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""System test for browser_move browser_tools registration flow."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Dict

import pytest

from openjiuwen.harness.tools.browser_move import REPO_ROOT
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import (
    MISSING_API_KEY_MESSAGE,
    build_runtime_settings,
    load_repo_dotenv,
)


SERVER_MODULE = "openjiuwen.harness.tools.browser_move.playwright_runtime_mcp_server"


def _system_tests_enabled() -> bool:
    return (os.getenv("RUN_BROWSER_MOVE_SYSTEM_TESTS") or "").strip().lower() in {"1", "true", "yes", "on"}


pytestmark = pytest.mark.skipif(
    not _system_tests_enabled(),
    reason="Set RUN_BROWSER_MOVE_SYSTEM_TESTS=1 to run browser_move system tests.",
)


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _wait_for_port(host: str, port: int, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_port_open(host, port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for browser runtime MCP server at {host}:{port}")


def _module_launch_cwd() -> str:
    current = Path(REPO_ROOT).expanduser().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "openjiuwen" / "__init__.py").exists():
            return str(candidate)
    raise RuntimeError(f"Could not resolve module launch cwd from {current}")


def _start_local_streamable_http_server() -> tuple[str, subprocess.Popen[str] | None]:
    configured_url = (os.getenv("PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH") or "").strip()
    if configured_url:
        return configured_url, None

    host = (os.getenv("PLAYWRIGHT_RUNTIME_MCP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_text = (os.getenv("PLAYWRIGHT_RUNTIME_MCP_PORT") or "8940").strip() or "8940"
    path = (os.getenv("PLAYWRIGHT_RUNTIME_MCP_PATH") or "/mcp").strip() or "/mcp"
    if not path.startswith("/"):
        path = f"/{path}"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError(f"Invalid PLAYWRIGHT_RUNTIME_MCP_PORT: {port_text}") from exc

    server_url = f"http://{host}:{port}{path}"
    if _is_port_open(host, port):
        return server_url, None

    cmd = [
        sys.executable,
        "-m",
        SERVER_MODULE,
        "--transport",
        "streamable-http",
        "--host",
        host,
        "--port",
        str(port),
        "--path",
        path,
        "--no-banner",
        "--log-level",
        "ERROR",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=_module_launch_cwd(),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_port(host, port)
    except Exception:
        with suppress(Exception):
            proc.terminate()
        with suppress(Exception):
            proc.wait(timeout=3)
        raise
    return server_url, proc


async def _run_browser_tools_check(query: str, session_id: str) -> Dict[str, object]:
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_tools import (
        build_browser_runtime_mcp_config,
        register_browser_runtime_mcp_server,
    )

    settings = build_runtime_settings()
    if not settings.api_key:
        raise RuntimeError(f"Missing API key for browser_tools example. {MISSING_API_KEY_MESSAGE}")

    browser_tools_cfg = build_browser_runtime_mcp_config()
    if browser_tools_cfg is None:
        raise RuntimeError(
            "browser_tools system test requires PLAYWRIGHT_RUNTIME_MCP_ENABLED=1 and a valid "
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE/PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH setup."
        )

    agent = ReActAgent(
        card=AgentCard(
            id="agent.playwright.browser_tools_system_test",
            name="playwright_browser_tools_system_test",
            description="System test agent using browser runtime MCP tools via browser_tools.",
            input_params={},
        )
    ).configure(
        ReActAgentConfig()
        .configure_model_client(
            provider=settings.provider,
            api_key=settings.api_key,
            api_base=settings.api_base,
            model_name=settings.model_name,
        )
        .configure_max_iterations(8)
        .configure_prompt_template(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a browser task agent.\n"
                        "Use browser_run_task for browser work.\n"
                        "Pass the full task through clearly.\n"
                        "Keep the final answer concise and factual.\n"
                        "If a browser tool fails, report the exact error."
                    ),
                }
            ]
        )
    )

    registered = await register_browser_runtime_mcp_server(agent, tag="agent.main")
    result = await Runner.run_agent(agent, {"query": query}, session=session_id)

    result_type = result.get("result_type") if isinstance(result, dict) else None
    final = result.get("output", "") if isinstance(result, dict) else str(result)
    ok = result_type != "error"
    error = None if ok else str(final)

    return {
        "ok": ok,
        "mode": "browser-tools",
        "registered": registered,
        "client_type": browser_tools_cfg.client_type,
        "server_path": browser_tools_cfg.server_path,
        "session_id": session_id,
        "final": final,
        "error": error,
    }


def test_browser_tools_end_to_end() -> None:
    load_repo_dotenv(override=True)
    started_proc: subprocess.Popen[str] | None = None
    try:
        os.environ["PLAYWRIGHT_RUNTIME_MCP_ENABLED"] = "1"
        os.environ["PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE"] = "streamable-http"
        server_url, started_proc = _start_local_streamable_http_server()
        os.environ["PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH"] = server_url

        result = asyncio.run(
            _run_browser_tools_check(
                query="Go to https://example.com and return the page title.",
                session_id="system-test-browser-tools",
            )
        )

        assert result["ok"] is True
        assert result["mode"] == "browser-tools"
        assert result["registered"] is True
        assert result["client_type"] == "streamable-http"
        assert result["server_path"] == server_url
        assert result["session_id"] == "system-test-browser-tools"
        assert result["error"] is None
        assert str(result["final"]).strip()
    except Exception as exc:
        raise AssertionError(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2)) from exc
    finally:
        if started_proc is not None and started_proc.poll() is None:
            with suppress(Exception):
                started_proc.terminate()
            with suppress(Exception):
                started_proc.wait(timeout=5)
