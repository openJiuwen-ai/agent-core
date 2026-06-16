# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""stdio MCP server exposing team collaboration to external agents.

``server.py`` builds a low-level ``mcp.server.lowlevel.Server`` whose tool set
is chosen from the join descriptor's ``scope``: a ``member`` exposes the real
teammate ``TeamTool`` instances (view_task / claim_task / send_message — same
schema + ``map_result()`` text as a native teammate) + the external-only
``read_inbox`` with empty instructions; an ``operator`` exposes the broad
team-control set + workflow instructions. An MCP-capable agent (claudecode /
codex / ...) connects over stdio; the descriptor is read from the
``OPENJIUWEN_TEAM_JOIN`` environment variable. This is the repository's first
MCP *server* (the rest of the codebase is an MCP client).
"""

from openjiuwen.agent_teams.mcp.server import build_server, main

__all__ = ["build_server", "main"]
