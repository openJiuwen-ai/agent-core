# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK loading and option-building helpers."""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any, Callable, Mapping

from openjiuwen.harness_protocol import HarnessError, McpServerConfig, McpTransport, UnsupportedHarnessCapabilityError
from openjiuwen.harness_providers.claudecode.config import ClaudeCodeHarnessConfig, ClaudeModelConfig

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

_CLAUDE_ENV_STRIP_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")
_ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
_ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"


def load_claude_sdk() -> Any:
    """Import the Claude Agent SDK only when a Claude harness is used."""
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise HarnessError("claude-agent-sdk is required for the Claude Code harness; install the optional SDK") from exc
    return claude_agent_sdk


def build_claude_session_id(*, host_session_id: str | None, agent_name: str) -> str | None:
    """Build a stable Claude UUID from the host session and agent identity."""
    if not host_session_id:
        return None
    seed = json.dumps([host_session_id, agent_name], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def delete_claude_session(*, host_session_id: str, agent_name: str, cwd: str | None) -> bool:
    """Delete the Claude SDK session derived for an agent."""
    claude_session_id = build_claude_session_id(host_session_id=host_session_id, agent_name=agent_name)
    if claude_session_id is None:
        return False
    sdk = load_claude_sdk()
    sdk.delete_session(claude_session_id, directory=cwd)
    return True


def strip_parent_claude_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Remove parent Claude session markers before launching a child Claude."""
    return {
        key: value
        for key, value in environ.items()
        if not any(key.startswith(prefix) for prefix in _CLAUDE_ENV_STRIP_PREFIXES)
    }


def build_process_env(config: ClaudeCodeHarnessConfig, context_env: Mapping[str, str]) -> dict[str, str]:
    """Merge the inherited process env, provider env and per-agent context env."""
    env: dict[str, str] = {}
    if config.inherit_process_env:
        env.update(strip_parent_claude_env(dict(os.environ)))
    env.update(config.env)
    env.update(context_env)
    return env


def model_settings(model: ClaudeModelConfig | None) -> str | None:
    """Render the CLI ``--settings`` JSON injecting an external endpoint.

    The CLI applies ``~/.claude/settings.json`` after the process env, which
    would shadow any env-var injection; the ``--settings`` source sits above
    user/project/local settings and wins.
    """
    if model is None:
        return None
    flag_env: dict[str, str] = {}
    if model.api_base:
        flag_env[_ANTHROPIC_BASE_URL_ENV] = model.api_base
    if model.api_key:
        flag_env[_ANTHROPIC_AUTH_TOKEN_ENV] = model.api_key
    if not flag_env:
        return None
    return json.dumps({"env": flag_env})


def mcp_servers_to_sdk(servers: tuple[McpServerConfig, ...]) -> dict[str, Any]:
    """Translate protocol MCP server configs into Claude SDK server configs."""
    result: dict[str, Any] = {}
    for server in servers:
        if server.transport is McpTransport.STDIO:
            binary, *args = server.command
            entry: dict[str, Any] = {"type": "stdio", "command": binary, "args": list(args)}
            if server.env:
                entry["env"] = dict(server.env)
            result[server.name] = entry
        elif server.transport is McpTransport.HTTP:
            entry = {"type": "http", "url": server.url}
            if server.headers:
                entry["headers"] = dict(server.headers)
            result[server.name] = entry
        elif server.transport is McpTransport.IN_PROCESS:
            result[server.name] = server.instance
        else:  # pragma: no cover - enum is closed
            raise UnsupportedHarnessCapabilityError(f"unsupported MCP transport {server.transport}")
    return result


def build_claude_options(
    *,
    sdk: Any,
    config: ClaudeCodeHarnessConfig,
    model: ClaudeModelConfig | None,
    cwd: str | None,
    env: Mapping[str, str],
    system_prompt: str,
    session_id: str | None,
    resume: str | None,
    mcp_servers: dict[str, Any],
    can_use_tool: Callable[..., Any] | None,
    stderr: Callable[[str], None] | None,
) -> "ClaudeAgentOptions":
    """Build ``ClaudeAgentOptions`` for one harness session."""
    if config.system_prompt_mode == "replace" and system_prompt:
        prompt_option: Any = system_prompt
    else:
        prompt_option = {"type": "preset", "append": system_prompt or ""}
    settings = config.settings
    endpoint_settings = model_settings(model)
    if endpoint_settings is not None:
        settings = endpoint_settings
    return sdk.ClaudeAgentOptions(
        skills="all" if config.skills else None,
        setting_sources=["user", "project", "local"] if config.skills else None,
        add_dirs=list(config.add_dirs),
        cli_path=config.cli_path,
        cwd=cwd,
        env=dict(env),
        mcp_servers=mcp_servers,
        model=model.model if model is not None else None,
        permission_mode=config.permission_mode,
        resume=resume,
        session_id=session_id,
        settings=settings,
        system_prompt=prompt_option,
        include_partial_messages=config.include_partial_messages,
        max_turns=config.max_turns,
        can_use_tool=can_use_tool,
        stderr=stderr,
    )


__all__ = [
    "build_claude_options",
    "build_claude_session_id",
    "build_process_env",
    "delete_claude_session",
    "load_claude_sdk",
    "mcp_servers_to_sdk",
    "model_settings",
    "strip_parent_claude_env",
]
