# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PowerShell command classification and exit-code semantic interpretation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


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
    "select-string", "sls", "findstr", "get-command", "where-object", "where",
})

_READ_COMMANDS: frozenset[str] = frozenset({
    "get-content", "gc", "type", "get-item", "gi", "test-path", "resolve-path", "get-filehash",
    "select-object", "select", "sort-object", "sort", "format-table", "ft", "format-list", "fl",
    "format-wide", "fw", "foreach-object", "foreach", "measure-object",
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

_GET_CHILD_ITEM_COMMANDS: frozenset[str] = frozenset({
    "get-childitem", "gci", "dir", "ls",
})

_SEARCH_EXIT_ONE_COMMANDS: frozenset[str] = frozenset({
    "select-string", "sls", "findstr",
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


def _split_pipeline(command: str) -> list[str]:
    """Split a command on top-level shell operators.

    This is intentionally a small scanner instead of a regex because PowerShell
    operators can appear inside strings, script blocks, arrays, and calculated
    properties such as ``@{Name='x';Expression={...}}``. Those nested operators
    must not split the command for exit-code semantics.
    """
    parts: list[str] = []
    start = 0
    depths = {"{": 0, "(": 0, "[": 0}
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "`":
            escaped = True
        elif quote is not None:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char == "}":
            depths["{"] = max(0, depths["{"] - 1)
        elif char == ")":
            depths["("] = max(0, depths["("] - 1)
        elif char == "]":
            depths["["] = max(0, depths["["] - 1)
        elif all(depth == 0 for depth in depths.values()):
            op_len = _operator_length_at(command, index)
            if op_len > 0:
                part = command[start:index].strip()
                if part:
                    parts.append(part)
                index += op_len
                start = index
                continue
        index += 1

    tail = command[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _operator_length_at(command: str, index: int) -> int:
    """Return the top-level operator length at the given index."""
    char = command[index]
    next_char = command[index + 1] if index + 1 < len(command) else ""
    if char in {"|", "&"} and next_char == char:
        return 2
    if char in {"|", ";"}:
        return 1
    return 0


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
    if exit_code == 0:
        return ExitCodeMeaning(is_error=False)

    parts = _split_pipeline(command)
    if not parts:
        return ExitCodeMeaning(is_error=True)

    base = _extract_base_command(parts[-1])
    handler = _SEMANTICS_TABLE.get(base)
    if handler:
        return handler(exit_code, stdout, stderr)

    is_partial_success = exit_code == 1 and stdout and not stderr
    if is_partial_success and is_read_only(command):
        return ExitCodeMeaning(
            is_error=False,
            message="PowerShell returned exit code 1 after producing output; treating output as partial result",
        )

    return ExitCodeMeaning(is_error=True)


def _get_child_item_semantics(code: int, stdout: str, stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False)
    if code == 1 and stdout and not stderr:
        return ExitCodeMeaning(is_error=False, message="Partial results produced; some items may be inaccessible")
    return ExitCodeMeaning(is_error=True, message=f"Get-ChildItem error (exit {code})")


def _search_semantics(code: int, stdout: str, stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False)
    if code == 1 and not stdout and not stderr:
        return ExitCodeMeaning(is_error=False, message="No matches found")
    return ExitCodeMeaning(is_error=True, message=f"Search command error (exit {code})")


_SEMANTICS_TABLE: dict[str, Callable[[int, str, str], ExitCodeMeaning]] = {}
for _cmd in _GET_CHILD_ITEM_COMMANDS:
    _SEMANTICS_TABLE[_cmd] = _get_child_item_semantics
for _cmd in _SEARCH_EXIT_ONE_COMMANDS:
    _SEMANTICS_TABLE[_cmd] = _search_semantics
