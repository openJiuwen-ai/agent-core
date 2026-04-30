# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool-layer security: injection detection and destructive-command warnings.

These checks *complement* sys_operation's built-in safety (dangerous-pattern
blocking, allowlist).  Injection checks block execution; destructive warnings
are purely informational and do not prevent execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecurityCheck:
    """Result of a tool-layer security check."""

    blocked: bool
    reason: str | None = None
    warning: str | None = None


# ── injection patterns ────────────────────────────────────────
# These detect shell meta-programming that could bypass per-command
# analysis.  They are deliberately conservative — false positives
# are safer than false negatives.

_BACKTICK_RE = re.compile(r"(?<!')`[^`]+`")
_DOLLAR_PAREN_RE = re.compile(r"\$\(")
_PROC_SUBST_RE = re.compile(r"[<>]\(")

_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_BACKTICK_RE, "backtick command substitution"),
    (_DOLLAR_PAREN_RE, "$() command substitution"),
    (_PROC_SUBST_RE, "process substitution <() or >()"),
]


def check_injection(command: str) -> SecurityCheck:
    """Detect shell injection patterns that could bypass static analysis.

    Returns a SecurityCheck with ``blocked=True`` if any injection
    pattern is found.
    """
    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(command):
            return SecurityCheck(blocked=True, reason=f"Shell injection detected: {label}")
    return SecurityCheck(blocked=False)


# ── destructive-command warnings (informational only) ─────────

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
]


def get_destructive_warning(command: str) -> str | None:
    """Return a human-readable warning if the command looks destructive.

    This is purely informational — it does **not** block execution.
    The warning is included in tool output so the LLM is aware of
    the risk.
    """
    for pattern, warning in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return warning
    return None
