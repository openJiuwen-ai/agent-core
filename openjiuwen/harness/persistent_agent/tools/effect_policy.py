# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conservative tool side-effect classification for Persistent Agent."""

# Tools that do not directly change the workspace or any external system. search_session_history may
# repair only its derived archive index; it never changes archived messages or Session state.
# share_file only records a listing in this Execution's own table; the host uploads or sends after
# the ending is recorded.
# read_execution_record only reads saved execution logs of this Request (PA-18).
READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "read_file",
        "list_files",
        "share_file",
        "read_execution_record",
        "search_group_messages",
        "search_session_history",
        # Persistent control request only; it does not write workspace or host state.
        "new_context",
    }
)


def is_read_only_tool_name(tool_name: str | None) -> bool:
    """Return whether the exact model-visible tool name is explicitly read-only."""
    return tool_name in READ_ONLY_TOOL_ALLOWLIST
