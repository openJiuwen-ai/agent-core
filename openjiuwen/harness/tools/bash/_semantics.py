# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Command classification and exit-code semantic interpretation."""
from __future__ import annotations

import re
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


# ── command sets ──────────────────────────────────────────────

_SEARCH_COMMANDS: frozenset[str] = frozenset({
    "find", "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "locate", "which", "whereis", "type", "command",
})

_READ_COMMANDS: frozenset[str] = frozenset({
    "cat", "head", "tail", "less", "more", "wc", "stat",
    "file", "strings", "jq", "yq", "awk", "gawk", "cut",
    "sort", "uniq", "tr", "tee", "od", "xxd", "hexdump",
    "sha256sum", "sha1sum", "md5sum", "md5", "shasum",
})

_LIST_COMMANDS: frozenset[str] = frozenset({
    "ls", "tree", "du", "df", "lsof", "lsblk",
})

_NEUTRAL_COMMANDS: frozenset[str] = frozenset({
    "echo", "printf", "true", "false", ":", "test", "[",
})

_SILENT_COMMANDS: frozenset[str] = frozenset({
    "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown",
    "chgrp", "touch", "ln", "cd", "export", "unset",
    "source", ".", "wait", "pushd", "popd",
})

_KIND_LOOKUP: dict[str, CommandKind] = {}
for _cmds, _kind in (
    (_SEARCH_COMMANDS, CommandKind.SEARCH),
    (_READ_COMMANDS, CommandKind.READ),
    (_LIST_COMMANDS, CommandKind.LIST),
    (_NEUTRAL_COMMANDS, CommandKind.NEUTRAL),
    (_SILENT_COMMANDS, CommandKind.SILENT),
):
    for _c in _cmds:
        _KIND_LOOKUP[_c] = _kind

# ── shell operator splitter ──────────────────────────────────

_OPERATOR_RE = re.compile(r"\s*(?:\|\||&&|[;|])\s*")


def _split_pipeline(command: str) -> list[str]:
    """Split a command on shell operators (heuristic, not a full parser)."""
    parts = _OPERATOR_RE.split(command)
    return [p.strip() for p in parts if p.strip()]


def _extract_base_command(segment: str) -> str:
    """Extract the executable name from a command segment.

    Strips leading variable assignments (FOO=bar) and env prefixes,
    then returns the basename of the first real token.
    """
    tokens = segment.split()
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            continue
        # strip path: /usr/bin/grep -> grep
        return token.rsplit("/", maxsplit=1)[-1]
    return ""


# ── public API ────────────────────────────────────────────────

def classify_command(command: str) -> CommandKind:
    """Classify the overall command by its *last* pipeline segment.

    The last segment determines the final exit code, so that is the
    one whose semantics matter most.
    """
    parts = _split_pipeline(command)
    if not parts:
        return CommandKind.OTHER
    base = _extract_base_command(parts[-1])
    return _KIND_LOOKUP.get(base, CommandKind.OTHER)


def is_read_only(command: str) -> bool:
    """Return True when every non-neutral segment is a read-like command."""
    _READ_KINDS = frozenset({CommandKind.SEARCH, CommandKind.READ, CommandKind.LIST})
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


# ── exit-code interpretation ─────────────────────────────────

def _grep_semantics(code: int, _stdout: str, _stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False)
    if code == 1:
        return ExitCodeMeaning(is_error=False, message="No matches found")
    return ExitCodeMeaning(is_error=True, message=f"grep error (exit {code})")


def _find_semantics(code: int, _stdout: str, _stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False)
    if code == 1:
        return ExitCodeMeaning(is_error=False, message="Some directories inaccessible")
    return ExitCodeMeaning(is_error=True, message=f"find error (exit {code})")


def _diff_semantics(code: int, _stdout: str, _stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False, message="Files are identical")
    if code == 1:
        return ExitCodeMeaning(is_error=False, message="Files differ")
    return ExitCodeMeaning(is_error=True, message=f"diff error (exit {code})")


def _test_semantics(code: int, _stdout: str, _stderr: str) -> ExitCodeMeaning:
    if code == 0:
        return ExitCodeMeaning(is_error=False, message="Condition is true")
    if code == 1:
        return ExitCodeMeaning(is_error=False, message="Condition is false")
    return ExitCodeMeaning(is_error=True, message=f"test error (exit {code})")


_SEMANTICS_TABLE: dict[str, Callable[[int, str, str], ExitCodeMeaning]] = {
    "grep": _grep_semantics,
    "egrep": _grep_semantics,
    "fgrep": _grep_semantics,
    "rg": _grep_semantics,
    "ag": _grep_semantics,
    "ack": _grep_semantics,
    "find": _find_semantics,
    "diff": _diff_semantics,
    "test": _test_semantics,
    "[": _test_semantics,
}


def interpret_exit_code(
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> ExitCodeMeaning:
    """Interpret an exit code with command-specific semantics.

    Falls back to the simple rule: exit_code != 0 means error.
    """
    if exit_code == 0:
        return ExitCodeMeaning(is_error=False)

    parts = _split_pipeline(command)
    if not parts:
        return ExitCodeMeaning(is_error=True)
    base = _extract_base_command(parts[-1])
    handler = _SEMANTICS_TABLE.get(base)
    if handler:
        return handler(exit_code, stdout, stderr)
    return ExitCodeMeaning(is_error=True)
