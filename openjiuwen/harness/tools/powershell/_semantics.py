# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PowerShell command classification and exit-code semantic interpretation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CommandKind(Enum):
    """Classification of the primary command."""

    SEARCH = "search"
    READ = "read"
    LIST = "list"
    NEUTRAL = "neutral"
    SILENT = "silent"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ExitCodeMeaning:
    """Semantic meaning of a process exit code."""

    is_error: bool
    message: str | None = None


_SEARCH_COMMANDS: frozenset[str] = frozenset({
    "select-string", "findstr", "get-command", "where-object",
})

_READ_COMMANDS: frozenset[str] = frozenset({
    "get-content", "gc", "type", "get-item", "gi", "test-path", "resolve-path", "get-filehash",
})

_LIST_COMMANDS: frozenset[str] = frozenset({
    "get-childitem", "gci", "dir", "ls",
})

_NEUTRAL_COMMANDS: frozenset[str] = frozenset({
    "write-output", "echo", "write-host", "out-host",
})

_SILENT_COMMANDS: frozenset[str] = frozenset({
    "set-location", "cd", "sl", "push-location", "pop-location",
    "new-item", "ni", "remove-item", "ri", "rm",
    "move-item", "mi", "mv", "copy-item", "cp", "cpi",
    "rename-item", "rni", "set-content", "sc", "add-content", "ac",
    "clear-content", "clc",
})

_KIND_LOOKUP: dict[str, CommandKind] = {}
for _cmds, _kind in (
    (_SEARCH_COMMANDS, CommandKind.SEARCH),
    (_READ_COMMANDS, CommandKind.READ),
    (_LIST_COMMANDS, CommandKind.LIST),
    (_NEUTRAL_COMMANDS, CommandKind.NEUTRAL),
    (_SILENT_COMMANDS, CommandKind.SILENT),
):
    for _cmd in _cmds:
        _KIND_LOOKUP[_cmd] = _kind

_OPERATOR_RE = re.compile(r"\s*(?:\|\||&&|[;|])\s*")


def _split_pipeline(command: str) -> list[str]:
    """Split a command on shell operators."""
    parts = _OPERATOR_RE.split(command)
    return [part.strip() for part in parts if part.strip()]


def _extract_base_command(segment: str) -> str:
    """Extract the executable or cmdlet name from a command segment."""
    tokens = segment.split()
    for token in tokens:
        if token in {"&", "."}:
            continue
        if token.startswith("$") and "=" in token:
            continue
        if token.startswith("-"):
            continue
        base = token.rsplit("\\", maxsplit=1)[-1].rsplit("/", maxsplit=1)[-1]
        if base.lower().endswith(".exe"):
            base = base[:-4]
        return base.lower()
    return ""


_READ_KINDS = frozenset({CommandKind.SEARCH, CommandKind.READ, CommandKind.LIST})


def is_read_only(command: str) -> bool:
    """Return True when every non-neutral segment is a read-like command."""
    parts = _split_pipeline(command)
    if not parts:
        return False
    for part in parts:
        base = _extract_base_command(part)
        kind = _KIND_LOOKUP.get(base, CommandKind.OTHER)
        if kind == CommandKind.NEUTRAL:
            continue
        if kind not in _READ_KINDS:
            return False
    return True


def is_silent(command: str) -> bool:
    """Return True when the command is expected to produce no stdout."""
    parts = _split_pipeline(command)
    if not parts:
        return False
    for part in parts:
        base = _extract_base_command(part)
        kind = _KIND_LOOKUP.get(base, CommandKind.OTHER)
        if kind == CommandKind.NEUTRAL:
            continue
        if kind != CommandKind.SILENT:
            return False
    return True


def interpret_exit_code(
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> ExitCodeMeaning:
    """Interpret an exit code with PowerShell-aware semantics."""
    del command, stdout, stderr
    if exit_code == 0:
        return ExitCodeMeaning(is_error=False)
    return ExitCodeMeaning(is_error=True)
