# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared tool-category catalogs for Pipeline A (policy, suggestions, persist, network)."""

from __future__ import annotations

SHELL_TOOLS = frozenset({"bash", "mcp_exec_command", "create_terminal", "powershell"})

PATH_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
    "send_file_to_user",
})

NETWORK_TOOLS = frozenset({"mcp_fetch_webpage", "mcp_free_search", "mcp_paid_search"})

PATH_ARG_KEYS = (
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
    "abs_file_path_list",
)
