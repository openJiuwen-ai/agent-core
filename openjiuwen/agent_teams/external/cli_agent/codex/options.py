# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build Codex Python SDK options for external team members."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

_MCP_STARTUP_TIMEOUT_S = 120


def load_codex_sdk() -> Any:
    """Import the Codex SDK only when a Codex member needs it."""
    try:
        import openai_codex
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="codex external CLI members require the openai-codex dependency",
            cause=exc,
        )
        raise AssertionError("raise_error should have raised") from exc
    return openai_codex


def build_codex_config(
    *,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    member_name: str,
    command_override: tuple[str, ...] | None,
    sdk: Any | None = None,
) -> Any:
    """Build ``CodexConfig`` without importing the optional SDK eagerly."""
    sdk = sdk or load_codex_sdk()
    config_overrides: tuple[str, ...] = ()
    if inject_mcp:
        if not mcp_server_command:
            raise_error(
                StatusCode.AGENT_TEAM_CONFIG_INVALID,
                reason="Codex SDK MCP injection requires a non-empty mcp_server_command",
            )
            raise AssertionError  # pragma: no cover - raise_error always raises
        config_overrides = codex_mcp_config_overrides(
            server_name=mcp_server_name,
            server_command=mcp_server_command,
        )

    launch_args_override = None
    if command_override is not None:
        launch_args_override = _inject_config_into_command(command_override, config_overrides)
        config_overrides = ()

    return sdk.CodexConfig(
        launch_args_override=launch_args_override,
        config_overrides=config_overrides,
        cwd=cwd,
        env=env,
        client_name="openjiuwen_agent_team",
        client_title=f"OpenJiuwen Team Member {member_name}",
        client_version="1",
    )


def build_codex_thread_options(
    *,
    cwd: str | None,
    system_prompt: str | None,
) -> dict[str, Any]:
    """Build thread start/resume options, leaving sandbox policy unset."""
    options: dict[str, Any] = {"ephemeral": False}
    if cwd:
        options["cwd"] = cwd
    if system_prompt:
        options["developer_instructions"] = system_prompt
    return options


def codex_mcp_config_overrides(
    *,
    server_name: str,
    server_command: tuple[str, ...],
) -> tuple[str, ...]:
    """Render ``mcp_servers.*`` entries for ``CodexConfig.config_overrides``."""
    if not server_command:
        return ()
    key = server_name.replace("-", "_")
    binary, *args = server_command
    overrides = [f"mcp_servers.{key}.command={json.dumps(binary)}"]
    if args:
        overrides.append(f"mcp_servers.{key}.args={json.dumps(args)}")
    overrides.extend(
        [
            f"mcp_servers.{key}.env_vars={json.dumps([TEAM_JOIN_ENV])}",
            f"mcp_servers.{key}.startup_timeout_sec={_MCP_STARTUP_TIMEOUT_S}",
            f"mcp_servers.{key}.required=true",
        ]
    )
    return tuple(overrides)


def _inject_config_into_command(
    command: tuple[str, ...],
    config_overrides: tuple[str, ...],
) -> tuple[str, ...]:
    """Insert SDK config flags before an overridden app-server subcommand."""
    if not config_overrides:
        return command
    argv = list(command)
    insert_at = argv.index("app-server") if "app-server" in argv else len(argv)
    flags = [part for value in config_overrides for part in ("--config", value)]
    argv[insert_at:insert_at] = flags
    return tuple(argv)


__all__ = [
    "build_codex_config",
    "build_codex_thread_options",
    "codex_mcp_config_overrides",
    "load_codex_sdk",
]
