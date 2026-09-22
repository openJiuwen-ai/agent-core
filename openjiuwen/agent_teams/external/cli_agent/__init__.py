# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Auto-launch of external CLI agent backends as team members (P2).

When the team spawns an external CLI-backed member, this package supplies the
backend registry (``backends``), dedicated Claude / Codex SDK
backends, generic per-CLI launch knowledge (``adapters``), the side-channel
input transport (``injector``), and the spawn entry that wires a backend
runtime into the team (``spawn``).

Side-channel injection uses the CLI's **stdin pipe** (Unix-first; the
``Injector`` Protocol leaves room for PTY / Windows backends later). Only
CLIs that read stdin continuously support mid-turn steer; others degrade to
turn-boundary delivery.
"""

from __future__ import annotations

#: Logical name every CLI registers the team's MCP server under. The member's
#: system prompt names it to say which server its bare tool names belong to,
#: so the name the prompt states and the name the server is registered with
#: have to be the same one.
TEAM_MCP_SERVER_NAME = "openjiuwen-team"

__all__ = ["TEAM_MCP_SERVER_NAME"]
