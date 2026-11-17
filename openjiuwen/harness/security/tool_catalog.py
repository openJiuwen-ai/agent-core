# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared tool-name catalogs for allow-always helpers.

Production decisions go through agent_shield; this module only keeps the shell
tool name set that core persistence helpers still need.
"""

from __future__ import annotations

_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
