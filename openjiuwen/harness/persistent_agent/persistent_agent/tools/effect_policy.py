# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conservative tool side-effect classification for Persistent Agent."""

READ_ONLY_TOOL_ALLOWLIST = frozenset({"read_file", "list_files"})


def is_read_only_tool_name(tool_name: str | None) -> bool:
    """Return whether the exact model-visible tool name is explicitly read-only."""
    return tool_name in READ_ONLY_TOOL_ALLOWLIST
