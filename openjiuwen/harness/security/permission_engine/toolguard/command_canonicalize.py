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
    r"\b(?:pwsh|powershell(?:\.exe)?)"
    r"(?:\s+-{1,2}[A-Za-z][A-Za-z-]*)*"
    r"\s+(?:-Command|-c)\s+"
    r"(?:"
    r'"(?P<dq>(?:\\.|[^"\\])*)"'
    r"|'(?P<sq>(?:\\.|[^'\\])*)'"
    r"|(?P<bare>\S+)"
    r")",
)

# POSIX shell launchers: bash|sh|zsh|dash|ash, optionally given as a quoted or
# absolute launcher path, optionally preceded by other short/long switches, then
# a switch group containing ``c`` (-c / -lc / -cl / -ic).
# ``(?<![^\s;&|()])`` keeps the launcher in command position (start of text or
# right after a shell separator) without consuming that separator; a variable
# width look-behind would not compile.
_SH_LAUNCHER_RE = re.compile(
    r"(?ix)"
    r"(?<![^\s;&|()])"
    r"(?:"
    r'"(?:[^"]*[\\/])?(?:bash|sh|zsh|dash|ash)(?:\.exe)?"'
    r"|'(?:[^']*[\\/])?(?:bash|sh|zsh|dash|ash)(?:\.exe)?'"
    r"|(?:[^\s;&|()]*[\\/])?(?:bash|sh|zsh|dash|ash)(?:\.exe)?"
    r")"
    r"(?:\s+-{1,2}[A-Za-z][A-Za-z-]*)*"
    r"\s+-[A-Za-z]*c[A-Za-z]*\s+"
    r"(?:"
    r'"(?P<dq>(?:\\.|[^"\\])*)"'
    r"|'(?P<sq>(?:\\.|[^'\\])*)'"
    r"|(?P<bare>\S+)"
    r")",
)

_FD_ALIAS_TOKEN_RE = re.compile(r"(?<!\S)(?:\d+>&\d+|>&\d+|<\d+|&\d+)(?!\S)")

# 拆壳后若命令位之前只剩空白/分隔符（如 PowerShell 的调用符 ``& '…bash.exe' -c '…'``，
# 或 ``; bash -c '…'``），一并去掉：用户规则的通配是全串锚定的，留一个 ``& `` 就匹配不上。
_LEADING_SEPARATORS_RE = re.compile(r"^[\s;&|()]+")


def canonicalize_shell_command_for_permission(command: str) -> str:
    """Return a permission-view copy: unwrap one launcher layer.

    Recognised launchers: ``cmd /c``, ``pwsh``/``powershell -Command|-c`` and
    POSIX shells (``bash``/``sh``/``zsh``/``dash``/``ash``) invoked with a
    ``-c``-style switch. A wrapper left in the string makes anchored wildcard
    rules (``rm *``) and the shell regex prefixes miss the inner command, so the
    permission view has to look through it. Leading separators left in front of
    the unwrapped command are dropped for the same reason; a prefix that holds an
    actual command (``cd d && …``) is preserved.

    Does not rewrite ``tool_args`` used for execution. Does not invent missing
    backslashes in already-corrupted paths.
    """
    text = (command or "").strip()
    if not text:
        return text
    unwrapped, count = _CMD_LAUNCHER_RE.subn(_launcher_inner, text, count=1)
    if count:
        return _strip_leading_separators(unwrapped.strip())
    unwrapped, count = _PS_LAUNCHER_RE.subn(_launcher_inner, text, count=1)
    if count:
        return _strip_leading_separators(unwrapped.strip())
    unwrapped, count = _SH_LAUNCHER_RE.subn(_launcher_inner, text, count=1)
    if count:
        return _strip_leading_separators(unwrapped.strip())
    return text


def _strip_leading_separators(command: str) -> str:
    """Drop leading whitespace/separators left in front of an unwrapped command."""
    stripped = _LEADING_SEPARATORS_RE.sub("", command)
    return stripped if stripped else command


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
