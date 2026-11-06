# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool-layer destructive-command warnings for PowerShell (informational only).

Injection / permission adjudication lives in PermissionInterruptRail /
``agent_shield``; this module only surfaces warnings in tool output.
"""
from __future__ import annotations

import re


_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bremove-item\b[^\n]*-(?:recurse|force)\b"), "May permanently remove files or directories"),
    (re.compile(r"(?i)\bclear-content\b"), "May remove file contents"),
    (re.compile(r"(?i)\bset-content\b"), "May overwrite file contents"),
    (re.compile(r"(?i)\brename-item\b"), "May rename or replace files"),
    (re.compile(r"(?i)\bmove-item\b"), "May move or overwrite files"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "May discard uncommitted changes"),
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force|-f)\b"), "May overwrite remote history"),
    (re.compile(r"\bgit\s+commit\s+--amend\b"), "May rewrite the last commit"),
]


def get_destructive_warning(command: str) -> str | None:
    """Return a human-readable warning if the command looks destructive."""
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return warning
    return None
