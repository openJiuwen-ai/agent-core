# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""File path extraction and tool registry for Native ``file_guard`` (enterprise L1)."""

from openjiuwen.harness.security.files.extract import (
    extract_accesses_native,
    extract_path_aware_command_accesses,
    extract_shell_path_accesses,
)
from openjiuwen.harness.security.files.registry import (
    FileToolSpec,
    lookup_file_tool_specs,
    register_file_tool,
)

__all__ = [
    "FileToolSpec",
    "extract_accesses_native",
    "extract_path_aware_command_accesses",
    "extract_shell_path_accesses",
    "lookup_file_tool_specs",
    "register_file_tool",
]
