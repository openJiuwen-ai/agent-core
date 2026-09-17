# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-side Claude Code wiring (SDK MCP tool set, SSH transport, session helpers).

The Claude harness itself lives in ``openjiuwen.harness_providers.claudecode``;
this package only keeps what depends on the team (collaboration tools bound
to a ``TeamBackend`` and the ssh transport configured by the team spec).
"""

from openjiuwen.agent_teams.external.cli_agent.claude.sdk_mcp import ClaudeSdkMcpToolSet, build_claude_sdk_mcp_tool_set
from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport

__all__ = ["ClaudeSdkMcpToolSet", "build_claude_sdk_mcp_tool_set", "build_claude_sdk_ssh_transport"]
