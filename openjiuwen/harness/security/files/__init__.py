# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility exports for file-guard integrations.

The implementation and registry remain owned by ``permission_engine.fileguard``.
"""

from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
    register_file_tool,
)
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_accesses_native,
    extract_path_aware_command_accesses,
    extract_shell_path_accesses,
)

__all__ = [
    "FileToolSpec",
    "extract_accesses_native",
    "extract_path_aware_command_accesses",
    "extract_shell_path_accesses",
    "lookup_file_tool_specs",
    "register_file_tool",
]
