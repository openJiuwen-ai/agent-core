# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared tool-name catalogs for allow-always / path helpers.

These frozensets used to live next to the deleted core ``tiered_compat``
evaluator.  Production decisions go through agent_shield; this module only
keeps the name sets (and a tiny path-arg collector) that other core helpers
still need.
"""

from __future__ import annotations

from typing import Any

_SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal"})
_PATH_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "read_text_file",
        "write_text_file",
        "write",
        "read",
        "glob_file_search",
        "glob",
        "list_dir",
        "list_files",
        "grep",
        "search_replace",
    }
)
_PATH_ARG_KEYS = frozenset(
    {
        "path",
        "file_path",
        "target_file",
        "file",
        "old_path",
        "new_path",
        "source_path",
        "dest_path",
        "directory",
        "dir",
    }
)


def _tool_arg_value_looks_like_path(arg_key: str, value: str) -> bool:
    if arg_key in _PATH_ARG_KEYS:
        return True
    if "/" in value or "\\" in value:
        return True
    return len(value) > 1 and value[1] == ":"


def _iter_path_strings(_tool_name: str, tool_args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, value in tool_args.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if _tool_arg_value_looks_like_path(key, value):
            out.append(value.strip())
    return out
