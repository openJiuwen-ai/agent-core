# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared glob search-root boundary validation for permissions and execution."""

import re
from pathlib import PurePosixPath, PureWindowsPath


def expand_brace_pattern(pattern: str) -> list[str]:
    """Expand shell-style brace alternatives, preserving recursive globs."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    results = []
    for option in match.group(1).split(","):
        results.extend(expand_brace_pattern(prefix + option.strip() + suffix))
    return results


def validated_glob_patterns(pattern: str) -> list[str]:
    """Reject patterns that select another root, including brace alternatives."""
    patterns = expand_brace_pattern(pattern)
    for item in patterns:
        windows_path = PureWindowsPath(item)
        if (
            PurePosixPath(item).is_absolute()
            or windows_path.drive
            or windows_path.root
            or ".." in item.replace("\\", "/").split("/")
        ):
            raise ValueError("pattern must stay within the search root")
    return patterns
