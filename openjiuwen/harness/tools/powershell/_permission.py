# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Permission check pipeline for the PowerShell tool."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from openjiuwen.harness.tools.powershell._semantics import (
    _extract_base_command,
    _split_pipeline,
    is_read_only,
)


class PermissionMode(str, Enum):
    """PowerShell tool permission enforcement mode."""

    AUTO = "auto"
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """Outcome of a permission check."""

    allowed: bool
    reason: str | None = None


_FILE_OP_COMMANDS: frozenset[str] = frozenset({
    "new-item", "ni", "remove-item", "ri", "rm",
    "move-item", "mi", "mv", "copy-item", "cp", "cpi",
    "rename-item", "rni", "set-content", "sc", "add-content", "ac",
    "clear-content", "clc",
})

_KNOWN_SAFE_COMMANDS: frozenset[str] = frozenset({
    "get-childitem", "gci", "dir", "ls",
    "get-content", "gc", "type", "get-item", "gi", "test-path", "resolve-path", "get-filehash",
    "select-string", "findstr", "get-command", "where-object",
    "write-output", "echo", "write-host", "out-host",
    "set-location", "cd", "sl", "push-location", "pop-location",
    "new-item", "ni", "remove-item", "ri", "rm",
    "move-item", "mi", "mv", "copy-item", "cp", "cpi",
    "rename-item", "rni", "set-content", "sc", "add-content", "ac",
    "clear-content", "clc",
    "git", "python", "python3", "pip", "pip3", "uv",
    "node", "npm", "npx", "yarn", "pnpm",
    "make", "cmake", "cargo", "go", "java", "javac", "mvn", "gradle",
    "docker", "kubectl", "curl", "wget",
    "date", "get-date", "hostname", "whoami",
})


@dataclass
class PermissionConfig:
    """Configuration for the permission pipeline."""

    mode: PermissionMode = PermissionMode.AUTO
    deny_patterns: list[re.Pattern[str]] = field(default_factory=list)
    allow_patterns: list[re.Pattern[str]] = field(default_factory=list)

    @staticmethod
    def compile_patterns(raw: list[str] | None) -> list[re.Pattern[str]]:
        """Compile a list of regex strings into patterns."""
        if not raw:
            return []
        return [re.compile(pattern, re.IGNORECASE) for pattern in raw]


def check_permission(command: str, config: PermissionConfig) -> PermissionResult:
    """Run the PowerShell permission pipeline."""
    if config.mode == PermissionMode.BYPASS:
        return PermissionResult(allowed=True)

    if config.deny_patterns:
        for segment in _split_pipeline(command):
            for pattern in config.deny_patterns:
                if pattern.search(segment):
                    return PermissionResult(
                        allowed=False,
                        reason=f"Command denied by pattern: {pattern.pattern}",
                    )

    if config.allow_patterns:
        for pattern in config.allow_patterns:
            if pattern.search(command):
                return PermissionResult(allowed=True)

    if config.mode == PermissionMode.READ_ONLY:
        if is_read_only(command):
            return PermissionResult(allowed=True)
        return PermissionResult(
            allowed=False,
            reason="Read-only mode: only read/search/list commands are allowed",
        )

    if config.mode == PermissionMode.ACCEPT_EDITS:
        for segment in _split_pipeline(command):
            base = _extract_base_command(segment)
            if base in _FILE_OP_COMMANDS or base in _KNOWN_SAFE_COMMANDS:
                continue
            return PermissionResult(
                allowed=False,
                reason=f"Accept-edits mode: unknown command '{base}' requires explicit approval",
            )
        return PermissionResult(allowed=True)

    return PermissionResult(allowed=True)
