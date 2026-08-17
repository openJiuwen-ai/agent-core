# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pipeline B: path guards (file_guard, sensitive_paths, extract, registry)."""

from openjiuwen.harness.security.fileguard.extract import extract_accesses_native
from openjiuwen.harness.security.fileguard.file_guard import (
    FileGuardChecker,
    build_file_guard_checker,
)
from openjiuwen.harness.security.fileguard.registry import (
    FileToolSpec,
    register_file_tool,
)
from openjiuwen.harness.security.fileguard.sensitive_paths import (
    get_builtin_sensitive_path_entries,
)

__all__ = [
    "FileGuardChecker",
    "FileToolSpec",
    "build_file_guard_checker",
    "extract_accesses_native",
    "get_builtin_sensitive_path_entries",
    "register_file_tool",
]
