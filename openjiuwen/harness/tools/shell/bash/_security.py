# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool-layer destructive-command warnings (informational only).

Injection / permission adjudication lives in PermissionInterruptRail /
``agent_shield``; this module only surfaces warnings in tool output.
"""
from __future__ import annotations

import re


_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "May discard uncommitted changes"),
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force|-f)\b"), "May overwrite remote history"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "May permanently delete untracked files"),
    (re.compile(r"\bgit\s+checkout\s+--\s+\."), "May discard all unstaged changes"),
    (re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b"), "May permanently discard stashed changes"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "May force-delete a branch"),
    (re.compile(r"\bgit\s+commit\s+--amend\b"), "May rewrite the last commit"),
    (re.compile(r"\bgit\s+(?:push|commit|merge)\b[^\n]*--no-verify\b"), "May skip safety hooks"),
    (re.compile(r"\bDROP\s+(?:TABLE|DATABASE)\b", re.IGNORECASE), "May drop database objects"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE), "May truncate database table"),
    (re.compile(r"\bkubectl\s+delete\b"), "May delete Kubernetes resources"),
    (re.compile(r"\bterraform\s+destroy\b"), "May destroy Terraform infrastructure"),
    (re.compile(r"\bsudo\b"), "sudo may require a password in non-interactive mode; configure NOPASSWD or run as root"),
]


def get_destructive_warning(command: str) -> str | None:
    """Return a human-readable warning if the command looks destructive.

    This is purely informational — it does **not** block execution.
    """
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return warning
    return None
