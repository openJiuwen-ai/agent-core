# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Permission check pipeline for the Bash tool.

Five-layer pipeline:
1. bypass mode → allow everything
2. deny_patterns → block matching commands
3. allow_patterns → pass matching commands
4. mode check (read_only / accept_edits)
5. pipeline sub-command validation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from openjiuwen.harness.tools.shell.bash._semantics import (
    _extract_base_command,
    _split_pipeline,
    is_read_only,
)


class PermissionMode(str, Enum):
    """Bash tool permission enforcement mode."""

    AUTO = "auto"
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """Outcome of a permission check."""

    allowed: bool
    reason: str | None = None


# Commands auto-allowed in ACCEPT_EDITS mode (file mutation).
_FILE_OP_COMMANDS: frozenset[str] = frozenset({
    "mkdir", "touch", "rm", "rmdir", "mv", "cp",
    "sed", "chmod", "chown", "chgrp", "ln",
})

# Broad set of known-safe commands (union of read + list + search + silent + common dev tools).
_KNOWN_SAFE_COMMANDS: frozenset[str] = frozenset({
    # search
    "find", "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "locate", "which", "whereis", "type", "command",
    # read
    "cat", "head", "tail", "less", "more", "wc", "stat",
    "file", "strings", "jq", "yq", "awk", "gawk", "cut",
    "sort", "uniq", "tr", "tee", "od", "xxd", "hexdump",
    "sha256sum", "sha1sum", "md5sum", "md5", "shasum",
    # list
    "ls", "tree", "du", "df", "lsof",
    # neutral
    "echo", "printf", "true", "false", ":", "test", "[",
    # silent / file ops
    "mkdir", "touch", "rm", "rmdir", "mv", "cp",
    "sed", "chmod", "chown", "chgrp", "ln",
    "cd", "export", "unset", "source", ".", "wait", "pushd", "popd",
    # common dev tools
    "git", "python", "python3", "pip", "pip3", "uv",
    "node", "npm", "npx", "yarn", "pnpm",
    "make", "cmake", "cargo", "go", "java", "javac", "mvn", "gradle",
    "docker", "docker-compose", "kubectl",
    "curl", "wget", "ssh", "scp", "rsync",
    "tar", "zip", "unzip", "gzip", "gunzip",
    "date", "env", "id", "whoami", "hostname", "uname", "ps", "top",
    "diff", "patch", "xargs", "basename", "dirname", "realpath",
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
        return [re.compile(p, re.IGNORECASE) for p in raw]


def check_permission(command: str, config: PermissionConfig) -> PermissionResult:
    """Run the 5-layer permission pipeline.

    Args:
        command: Shell command string.
        config: Permission configuration.

    Returns:
        PermissionResult indicating allow or deny with reason.
    """
    # Layer 1: bypass
    if config.mode == PermissionMode.BYPASS:
        return PermissionResult(allowed=True)

    # Layer 2: deny patterns (checked against each sub-command)
    if config.deny_patterns:
        for segment in _split_pipeline(command):
            for pattern in config.deny_patterns:
                if pattern.search(segment):
                    return PermissionResult(
                        allowed=False,
                        reason=f"Command denied by pattern: {pattern.pattern}",
                    )

    # Layer 3: allow patterns (any match → allow whole command)
    if config.allow_patterns:
        for pattern in config.allow_patterns:
            if pattern.search(command):
                return PermissionResult(allowed=True)

    # Layer 4: mode-specific check
    if config.mode == PermissionMode.READ_ONLY:
        if is_read_only(command):
            return PermissionResult(allowed=True)
        return PermissionResult(
            allowed=False,
            reason="Read-only mode: only read/search/list commands are allowed",
        )

    if config.mode == PermissionMode.ACCEPT_EDITS:
        segments = _split_pipeline(command)
        for segment in segments:
            base = _extract_base_command(segment)
            if base in _FILE_OP_COMMANDS or base in _KNOWN_SAFE_COMMANDS:
                continue
            return PermissionResult(
                allowed=False,
                reason=f"Accept-edits mode: unknown command '{base}' requires explicit approval",
            )
        return PermissionResult(allowed=True)

    # Layer 5 (AUTO mode): pipeline sub-command validation
    segments = _split_pipeline(command)
    for segment in segments:
        base = _extract_base_command(segment)
        if not base:
            continue
        if base not in _KNOWN_SAFE_COMMANDS:
            # Unknown commands are allowed in AUTO mode — this layer is advisory.
            # The deny_patterns in layer 2 handle actual blocking.
            pass
    return PermissionResult(allowed=True)
