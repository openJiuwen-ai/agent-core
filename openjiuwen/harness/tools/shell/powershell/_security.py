# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool-layer security for PowerShell commands."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecurityCheck:
    """Result of a tool-layer security check."""

    blocked: bool
    reason: str | None = None
    warning: str | None = None


_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(?:invoke-expression|iex)\b"), "Invoke-Expression"),
    (re.compile(r"(?i)\b(?:powershell|powershell\.exe|pwsh|pwsh\.exe)\b[^\n]*-encodedcommand\b"),
     "nested encoded command"),
    (re.compile(r"(^|[\s;(])&\s*(?:\(|\$)"), "dynamic call operator"),
    (re.compile(r"(?i)\[scriptblock\]::create\s*\("), "dynamic ScriptBlock creation"),
]


def check_injection(command: str) -> SecurityCheck:
    """Detect PowerShell patterns that make static review harder."""
    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(command):
            return SecurityCheck(blocked=True, reason=f"PowerShell injection detected: {label}")
    return SecurityCheck(blocked=False)


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
