# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build Claude Agent SDK options for team-member runtimes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions


_CLAUDE_ENV_STRIP_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")


def load_claude_sdk() -> Any:
    """Import the Claude Agent SDK only when a Claude member is used."""
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="claude external CLI members require the claude-agent-sdk dependency",
            cause=exc,
        )
        raise AssertionError  # pragma: no cover - raise_error always raises
    return claude_agent_sdk


def build_claude_options(
    *,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    system_prompt: str | None,
) -> "ClaudeAgentOptions":
    """Build SDK options matching the previous Claude CLI member behavior."""
    sdk = load_claude_sdk()
    mcp_servers = None
    if inject_mcp:
        if not mcp_server_command:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Claude SDK MCP injection requires a non-empty mcp_server_command",
            )
            raise AssertionError  # pragma: no cover - raise_error always raises
        mcp_servers = {
            mcp_server_name: {
                "type": "stdio",
                "command": mcp_server_command[0],
                "args": list(mcp_server_command[1:]),
                "env": {TEAM_JOIN_ENV: env[TEAM_JOIN_ENV]},
            }
        }
    return sdk.ClaudeAgentOptions(
        cwd=cwd,
        env=env,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        system_prompt={"type": "preset", "append": system_prompt or ""},
    )


def strip_parent_claude_env(environ: dict[str, str]) -> dict[str, str]:
    """Remove parent Claude session markers before launching a child Claude."""
    return {
        key: value
        for key, value in environ.items()
        if not any(key.startswith(prefix) for prefix in _CLAUDE_ENV_STRIP_PREFIXES)
    }


__all__ = ["build_claude_options", "load_claude_sdk", "strip_parent_claude_env"]
