# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission-view shell command canonicalize (does not change execution)."""

from __future__ import annotations

import re

_CMD_LAUNCHER_RE = re.compile(
    r"(?ix)"
    r"\b(?:cmd(?:\.exe)?)\s+"
    r"(?://c|/c)\s+"
    r"(?:"
    r'"(?P<dq>(?:\\.|[^"\\])*)"'
    r"|'(?P<sq>(?:\\.|[^'\\])*)'"
    r"|(?P<bare>\S+)"
    r")",
)

_PS_LAUNCHER_RE = re.compile(
    r"(?ix)"
    r"\b(?:pwsh|powershell(?:\.exe)?)\s+"
    r"(?:-Command|-c)\s+"
    r"(?:"
    r'"(?P<dq>(?:\\.|[^"\\])*)"'
    r"|'(?P<sq>(?:\\.|[^'\\])*)'"
    r"|(?P<bare>\S+)"
    r")",
)

_FD_ALIAS_TOKEN_RE = re.compile(r"(?<!\S)(?:\d+>&\d+|>&\d+|<\d+|&\d+)(?!\S)")


def canonicalize_shell_command_for_permission(command: str) -> str:
    """Return a permission-view copy: unwrap one cmd/powershell launcher layer.

    Does not rewrite ``tool_args`` used for execution. Does not invent missing
    backslashes in already-corrupted paths.
    """
    text = (command or "").strip()
    if not text:
        return text
    unwrapped, count = _CMD_LAUNCHER_RE.subn(_launcher_inner, text, count=1)
    if count:
        return unwrapped.strip()
    unwrapped, count = _PS_LAUNCHER_RE.subn(_launcher_inner, text, count=1)
    if count:
        return unwrapped.strip()
    return text


def strip_fd_alias_tokens(command: str) -> str:
    """Remove stderr/stdout fd duplication tokens such as ``2>&1``."""
    return _FD_ALIAS_TOKEN_RE.sub("", command or "")


def is_fd_alias_token(token: str) -> bool:
    text = (token or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"(?:\d+>&\d+|>&\d+|<\d+|&\d+)", text))


def _launcher_inner(match: re.Match[str]) -> str:
    inner = match.group("dq") or match.group("sq") or match.group("bare") or ""
    return inner.strip()


__all__ = [
    "canonicalize_shell_command_for_permission",
    "is_fd_alias_token",
    "strip_fd_alias_tokens",
]
