# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""File-guard pipeline B: path policy, extraction, registry, and sensitive paths."""

from openjiuwen.harness.security.permission_engine.fileguard.file_tool_specs import (
    FileToolSpec,
    lookup_file_tool_specs,
    register_file_tool,
)
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import (
    extract_accesses_native,
)

__all__ = [
    "FileToolSpec",
    "extract_accesses_native",
    "lookup_file_tool_specs",
    "register_file_tool",
]
