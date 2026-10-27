# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Recognize a deliberately small set of literal Bash invocations.

This is evidence for rule matching, not a replacement shell interpreter.
Unsupported syntax keeps the existing conservative permission path.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from openjiuwen.harness.security.shell_ast import _get_tree_sitter_bash_parser


@dataclass(frozen=True)
class LiteralCommand:
    text: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class LiteralShell:
    commands: tuple[LiteralCommand, ...]
    heredoc: bool = False


_INTERPRETERS = frozenset({
    "python", "python3", "pythonw", "py", "node", "nodejs", "bash", "sh", "dash", "zsh", "fish",
    "perl", "ruby", "pwsh", "powershell",
})
_INDIRECT_COMMANDS = frozenset({"eval", "exec", "source", ".", "env", "command", "builtin"})


def is_script_interpreter(executable: str) -> bool:
    return executable in _INTERPRETERS or re.fullmatch(r"python\d+(?:\.\d+)*", executable) is not None


def _heredoc_commands(
    node: Any, invocation: Callable[[Any], LiteralCommand | None],
) -> tuple[LiteralCommand, ...] | None:
    """Accept a literal call, optionally preceded by exactly one static cd."""
    if node.type != "list":
        call = invocation(node)
        return (call,) if call is not None else None
    parts = node.children
    if len(parts) != 3 or parts[1].type != "&&":
        return None
    leading_cd, call = invocation(parts[0]), invocation(parts[2])
    if leading_cd is None or leading_cd.argv[0] != "cd" or call is None:
        return None
    return leading_cd, call


def parse_literal_shell(command: str) -> LiteralShell | None:
    """Return complete, literal commands, or no evidence (fail closed)."""
    if os.name == "nt":
        return None
    parser = _get_tree_sitter_bash_parser()
    if parser is None:
        return None
    try:
        source = command.strip().encode("utf-8")
        root = parser.parse(source).root_node
    except (ValueError, RuntimeError, UnicodeError):
        return None
    if root.has_error or len(root.children) != 1 or len(root.named_children) != 1:
        return None

    def text(node: Any) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8")

    def literal_argument(node: Any) -> bool:
        if node.type in {"word", "number"}:
            return not any(c in text(node) for c in "*?[]{}~$`\\\n\r")
        if node.type == "raw_string":
            return True
        if node.type == "string":
            return all(child.type == "string_content" for child in node.named_children)
        return False

    def invocation(node: Any) -> LiteralCommand | None:
        children = node.named_children
        if node.type != "command" or not children or children[0].type != "command_name":
            return None
        name = children[0]
        if len(name.named_children) != 1 or name.named_children[0].type != "word":
            return None
        if not literal_argument(name.named_children[0]):
            return None
        if not all(literal_argument(child) for child in children[1:]):
            return None
        raw = text(node)
        try:
            argv = tuple(shlex.split(raw, posix=True))
        except ValueError:
            return None
        if not argv or PurePosixPath(argv[0]).name in _INDIRECT_COMMANDS:
            return None
        executable = PurePosixPath(argv[0]).name
        # The new automatic path does not authorize interpreter inline/stdin code.
        if is_script_interpreter(executable) and (len(argv) < 2 or argv[1].startswith("-")):
            return None
        if executable == "cd":
            if argv[0] != "cd" or not (len(argv) == 2 or (len(argv) == 3 and argv[1] == "--")):
                return None
            if argv[-1].startswith("-"):
                return None
            if not (argv[-1].startswith(("/", "./", "../")) or argv[-1] in {".", ".."}):
                # A bare relative directory can be redirected through CDPATH.
                return None
        return LiteralCommand(raw, argv)

    top = root.named_children[0]
    if top.type == "redirected_statement":
        children = top.named_children
        if len(children) != 2 or children[1].type != "heredoc_redirect":
            return None
        # The parser wraps a leading cd and Python call in the same redirect.
        calls = _heredoc_commands(children[0], invocation)
        if not calls or PurePosixPath(calls[-1].argv[0]).name not in {"python", "python3"}:
            return None
        redirect = children[1]
        if any(child.type not in {"heredoc_start", "heredoc_body", "heredoc_end"}
               for child in redirect.named_children):
            return None
        start = next((c for c in redirect.named_children if c.type == "heredoc_start"), None)
        end = next((c for c in redirect.named_children if c.type == "heredoc_end"), None)
        if start is None or end is None or not re.fullmatch(r"(['\"])[A-Za-z_][A-Za-z0-9_]*\1", text(start)):
            return None
        if text(end) != text(start)[1:-1] or redirect.children[0].type != "<<":
            return None
        return LiteralShell(calls, heredoc=True)

    def collect(node: Any) -> list[LiteralCommand] | None:
        if node.type == "command":
            call = invocation(node)
            return [call] if call is not None else None
        if node.type != "list":
            return None
        children = node.children
        if len(children) != 3 or children[1].type != "&&":
            return None
        left, right = collect(children[0]), collect(children[2])
        return left + right if left is not None and right is not None else None

    commands = collect(top)
    # Keep directory changes to one leading, static cd; subsequent cd can depend
    # on Bash's logical PWD/OLDPWD rather than the filesystem's resolved cwd.
    if commands and any(call.argv[0] == "cd" for call in commands[1:]):
        return None
    return LiteralShell(tuple(commands)) if commands else None


def match_literal_pattern(value: str, pattern: str) -> bool:
    """Wildcard matching only after the complete invocation was verified literal."""
    optional_arguments = pattern.endswith(" *")
    prefix = pattern[:-2] if optional_arguments else pattern
    escaped = re.escape(prefix).replace(r"\*", ".*").replace(r"\?", ".")
    if optional_arguments:
        escaped += "(?: .*)?"
    return re.fullmatch(escaped, value, flags=re.DOTALL) is not None


def pattern_binds_command(pattern: str, call: LiteralCommand) -> bool:
    """A new automatic allowance must identify the executable, not argument text."""
    pattern = pattern.strip()
    if pattern.lower().startswith("re:"):
        expression = pattern[3:].strip()
        prefix = "^" + re.escape(call.argv[0])
        if not any(expression.startswith(prefix + boundary) for boundary in (r"\s", r"\b", " ")):
            return False
        try:
            match = re.search(expression, call.text)
        except re.error:
            return False
        return match is not None and match.start() == 0 and match.end() > len(call.argv[0])
    head = pattern.split(maxsplit=1)[0] if pattern else ""
    return head == call.argv[0] and not any(c in head for c in "*?[")
