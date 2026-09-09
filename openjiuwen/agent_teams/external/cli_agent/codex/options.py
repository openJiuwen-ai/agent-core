# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-facing Codex option helpers over ``openjiuwen.harness_providers.codex``."""

from __future__ import annotations

from openjiuwen.agent_teams.external.descriptor import MCP_SERVER_ENV_VARS
from openjiuwen.harness_protocol import McpServerConfig, McpTransport
from openjiuwen.harness_providers.codex.options import (
    codex_mcp_config_overrides as _codex_mcp_config_overrides,
    codex_model_config_overrides,
    load_codex_sdk,
)

_MCP_STARTUP_TIMEOUT_S = 120


def codex_mcp_config_overrides(
    *,
    server_name: str,
    server_command: tuple[str, ...],
    default_tools_approval_mode: str | None = None,
) -> tuple[str, ...]:
    """Render the team MCP server as ``mcp_servers.*`` config overrides."""
    if not server_command:
        return ()
    return _codex_mcp_config_overrides(
        McpServerConfig(name=server_name, transport=McpTransport.STDIO, command=server_command),
        env_passthrough=tuple(MCP_SERVER_ENV_VARS),
        startup_timeout_s=_MCP_STARTUP_TIMEOUT_S,
        required=True,
        default_tools_approval_mode=default_tools_approval_mode,
    )


__all__ = ["codex_mcp_config_overrides", "codex_model_config_overrides", "load_codex_sdk"]
