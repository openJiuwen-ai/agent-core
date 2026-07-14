# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""stdio MCP server exposing team collaboration to external agents.

``server.py`` builds a FastMCP server whose tools wrap
``ExternalTeamClient`` operations (send / view / claim / ... + inbox). An
MCP-capable agent (claudecode / codex / ...) connects over stdio; the
connection descriptor is read from the ``OPENJIUWEN_TEAM_JOIN`` environment
variable. This is the repository's first MCP *server* (the rest of the
codebase is an MCP client).
"""

from openjiuwen.agent_teams.mcp.server import build_server, main

__all__ = ["build_server", "main"]
