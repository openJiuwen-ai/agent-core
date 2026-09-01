# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shell AST pre-processing for tiered tool permissions.

The module prefers a tree-sitter bash backend when the optional runtime
dependencies are installed. If the backend is unavailable, it falls back to a
conservative scanner:

- obviously simple commands keep a single-command representation
- compound / redirection / substitution syntax degrades to parse_unavailable
- callers must fail closed for parse_unavailable + risky structure
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_TREE_SITTER_BASH_READY: bool | None = None
_TREE_SITTER_PARSER: Any | None = None

_COMMAND_SUBSTITUTION_RE = re.compile(r"`|\$\(")
_PROCESS_SUBSTITUTION_RE = re.compile(r"[<>]\(")
_HEREDOC_RE = re.compile(r"<<<?")
_PARAM_EXPANSION_RE = re.compile(r"\$\{")
_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass(frozen=True, slots=True)
class _ShellArgSyntax:
    """Parser-owned facts needed to interpret one normalized shell argv item."""

    static: bool
    expand_user: bool


@dataclass(frozen=True, slots=True)
class _ShellLexeme:
    value: str
    syntax: _ShellArgSyntax
    control: bool = False
    assignment_prefix: bool = False


def _tilde_expansion_eligible(raw: str) -> bool:
    if not raw.startswith("~"):
        return False
    prefix = raw.split("/", 1)[0]
    return not any(char in prefix for char in {'"', "'", "\\"})


def _unsupported_tilde_expansion(raw: str) -> bool:
    if not _tilde_expansion_eligible(raw):
        return False
    prefix = raw.split("/", 1)[0]
    return prefix.startswith("~-") or (prefix.startswith("~+") and prefix != "~+")


def _normalize_posix_shell_word(raw: str) -> tuple[str, _ShellArgSyntax] | None:
    """Normalize one POSIX shell word without evaluating dynamic expansion."""

    output: list[str] = []
    single_quoted = False
    double_quoted = False
    syntax_static = True
    expand_user = _tilde_expansion_eligible(raw)
    if _unsupported_tilde_expansion(raw):
        syntax_static = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if single_quoted:
            if char == "'":
                single_quoted = False
            else:
                output.append(char)
            index += 1
            continue
        if double_quoted:
            if char == '"':
                double_quoted = False
                index += 1
                continue
            if char == "\\" and index + 1 < len(raw):
                escaped = raw[index + 1]
                if escaped in {'$', '`', '"', "\\"}:
                    output.append(escaped)
                    index += 2
                    continue
                if escaped == "\n":
                    index += 2
                    continue
                output.append(char)
                index += 1
                continue
            if char in {"$", "`"}:
                syntax_static = False
            output.append(char)
            index += 1
            continue
        if char == "'":
            single_quoted = True
            index += 1
            continue
        if char == '"':
            double_quoted = True
            index += 1
            continue
        if char == "\\":
            if index + 1 >= len(raw):
                return None
            escaped = raw[index + 1]
            if escaped != "\n":
                output.append(escaped)
            index += 2
            continue
        if char in {"$", "`", "*", "?", "[", "{"}:
            syntax_static = False
        output.append(char)
        index += 1
    if single_quoted or double_quoted:
        return None
    return "".join(output), _ShellArgSyntax(
        static=syntax_static,
        expand_user=expand_user,
    )


def _normalize_windows_shell_word(raw: str) -> tuple[str, _ShellArgSyntax]:
    """Preserve the previous non-POSIX token value behavior conservatively."""

    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}
    value = raw[1:-1] if quoted else raw
    single_quoted = quoted and raw[0] == "'"
    static = single_quoted or not any(char in value for char in "$`*?[{")
    return value, _ShellArgSyntax(
        static=static,
        expand_user=not quoted and value.startswith("~"),
    )


def _normalize_shell_word(raw: str) -> tuple[str, _ShellArgSyntax] | None:
    if os.name == "nt":
        return _normalize_windows_shell_word(raw)
    return _normalize_posix_shell_word(raw)


def _lex_windows_shell_words(text: str) -> tuple[_ShellLexeme, ...] | None:
    try:
        lexer = shlex.shlex(text, posix=False, punctuation_chars="();&|<>")
        lexer.commenters = ""
        raw_tokens = tuple(lexer)
    except ValueError:
        return None
    punctuation = frozenset("();&|<>")
    lexemes: list[_ShellLexeme] = []
    for raw in raw_tokens:
        if not raw:
            continue
        control = all(char in punctuation for char in raw)
        if control:
            lexemes.append(
                _ShellLexeme(
                    value=raw,
                    syntax=_ShellArgSyntax(False, False),
                    control=True,
                )
            )
            continue
        value, syntax = _normalize_windows_shell_word(raw)
        lexemes.append(
            _ShellLexeme(
                value=value,
                syntax=syntax,
                assignment_prefix=bool(_SHELL_ASSIGNMENT_RE.match(raw)),
            )
        )
    return tuple(lexemes)


def _lex_shell_words(text: str) -> tuple[_ShellLexeme, ...] | None:
    """Conservatively lex shell words and operators for parser fallback use."""

    if os.name == "nt":
        return _lex_windows_shell_words(text)

    lexemes: list[_ShellLexeme] = []
    raw_word: list[str] = []
    single_quoted = False
    double_quoted = False
    escaped = False
    word_started = False
    punctuation = frozenset("();&|<>")

    def flush_word() -> bool:
        nonlocal word_started
        if not word_started:
            return True
        raw = "".join(raw_word)
        normalized = _normalize_shell_word(raw)
        if normalized is None:
            return False
        value, syntax = normalized
        lexemes.append(
            _ShellLexeme(
                value=value,
                syntax=syntax,
                assignment_prefix=bool(_SHELL_ASSIGNMENT_RE.match(raw)),
            )
        )
        raw_word.clear()
        word_started = False
        return True

    index = 0
    while index < len(text):
        char = text[index]
        if escaped:
            raw_word.append(char)
            word_started = True
            escaped = False
            index += 1
            continue
        if char == "\\" and not single_quoted:
            raw_word.append(char)
            word_started = True
            escaped = True
            index += 1
            continue
        if char == "'" and not double_quoted:
            raw_word.append(char)
            word_started = True
            single_quoted = not single_quoted
            index += 1
            continue
        if char == '"' and not single_quoted:
            raw_word.append(char)
            word_started = True
            double_quoted = not double_quoted
            index += 1
            continue
        if single_quoted or double_quoted:
            raw_word.append(char)
            word_started = True
            index += 1
            continue
        if char.isspace():
            if not flush_word():
                return None
            if char in {"\n", "\r"}:
                lexemes.append(
                    _ShellLexeme(
                        value=";",
                        syntax=_ShellArgSyntax(static=False, expand_user=False),
                        control=True,
                    )
                )
            index += 1
            continue
        if char in punctuation:
            if not flush_word():
                return None
            end = index + 1
            while end < len(text) and text[end] in punctuation:
                end += 1
            lexemes.append(
                _ShellLexeme(
                    value=text[index:end],
                    syntax=_ShellArgSyntax(static=False, expand_user=False),
                    control=True,
                )
            )
            index = end
            continue
        raw_word.append(char)
        word_started = True
        index += 1
    if escaped or single_quoted or double_quoted or not flush_word():
        return None
    return tuple(lexemes)


def _executable_shell_lexemes(
    lexemes: tuple[_ShellLexeme, ...],
) -> tuple[_ShellLexeme, ...]:
    """Remove consecutive, syntactically certain assignment prefixes."""

    index = 0
    while (
        index < len(lexemes)
        and not lexemes[index].control
        and lexemes[index].assignment_prefix
    ):
        index += 1
    return lexemes[index:]


@dataclass(frozen=True)
class ShellStructureFlags:
    has_compound_operators: bool = False
    has_pipeline: bool = False
    has_subshell: bool = False
    has_command_group: bool = False
    has_command_substitution: bool = False
    has_process_substitution: bool = False
    has_parameter_expansion: bool = False
    has_heredoc: bool = False
    has_input_redirection: bool = False
    has_output_redirection: bool = False
    has_actual_operator_nodes: bool = False
    operators: tuple[str, ...] = field(default_factory=tuple)

    def has_risky_structure(self) -> bool:
        return any((
            self.has_compound_operators,
            self.has_pipeline,
            self.has_subshell,
            self.has_command_group,
            self.has_command_substitution,
            self.has_process_substitution,
            self.has_parameter_expansion,
            self.has_heredoc,
            self.has_input_redirection,
            self.has_output_redirection,
        ))


@dataclass(frozen=True)
class ShellSubcommand:
    text: str
    argv: tuple[str, ...] = field(default_factory=tuple)
    redirects: tuple[str, ...] = field(default_factory=tuple)
    source_span: tuple[int, int] | None = None
    parent_operators: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ShellAstParseResult:
    kind: str
    subcommands: tuple[ShellSubcommand, ...] = field(default_factory=tuple)
    flags: ShellStructureFlags = field(default_factory=ShellStructureFlags)
    reason: str | None = None
    backend: str = "fallback"


def parse_shell_for_permission(command: str) -> ShellAstParseResult:
    """Parse shell command for permission checks.

    Returns:
        ShellAstParseResult with one of:
        - simple: trustworthy subcommands are available
        - too_complex: parser succeeded but command should not be trusted
        - parse_unavailable: parser backend unavailable or command cannot be
          safely analyzed by the conservative fallback
    """
    result, _argv_syntax = _parse_shell_for_permission_details(command)
    return result


def _parse_shell_for_permission_details(
    command: str,
) -> tuple[ShellAstParseResult, tuple[tuple[_ShellArgSyntax, ...], ...]]:
    """Return the public parse result plus private argv syntax provenance."""

    from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
        canonicalize_shell_command_for_permission,
    )

    text = canonicalize_shell_command_for_permission((command or "").strip())
    if not text:
        return ShellAstParseResult(kind="simple", backend="fallback"), ()

    parser = _get_tree_sitter_bash_parser()
    if parser is not None:
        try:
            argv_syntax: list[tuple[_ShellArgSyntax, ...]] = []
            result = _parse_with_tree_sitter(
                text,
                parser,
                argv_syntax_out=argv_syntax,
            )
            return result, tuple(argv_syntax)
        except Exception:  # pragma: no cover - defensive logging path
            logger.warning("[PermissionEngine] permission.shell_ast.parse_failed fallback=true", exc_info=True)

    result = _parse_with_conservative_fallback(text)
    lexemes = _lex_shell_words(text)
    if lexemes is None or any(lexeme.control for lexeme in lexemes):
        return result, tuple(
            tuple(_ShellArgSyntax(False, False) for _ in subcommand.argv)
            for subcommand in result.subcommands
        )
    executable = _executable_shell_lexemes(lexemes)
    syntax = tuple(lexeme.syntax for lexeme in executable)
    return result, (syntax,) if result.subcommands else ()


def _get_tree_sitter_bash_parser() -> Any | None:
    global _TREE_SITTER_BASH_READY, _TREE_SITTER_PARSER
    if _TREE_SITTER_BASH_READY is False:
        return None
    if _TREE_SITTER_PARSER is not None:
        return _TREE_SITTER_PARSER
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_bash

        language = Language(tree_sitter_bash.language())
        try:
            parser = Parser(language)
        except TypeError:
            parser = Parser()
            parser.language = language
        _TREE_SITTER_PARSER = parser
        _TREE_SITTER_BASH_READY = True
        return _TREE_SITTER_PARSER
    except Exception:
        _TREE_SITTER_BASH_READY = False
        logger.info("[PermissionEngine] permission.shell_ast.backend_unavailable fallback=fallback_scanner")
        return None


def _parse_with_conservative_fallback(command: str) -> ShellAstParseResult:
    flags = _scan_shell_structure(command)
    if flags.has_risky_structure():
        return ShellAstParseResult(
            kind="parse_unavailable",
            flags=flags,
            reason="tree-sitter backend unavailable and fallback detected shell structure",
            backend="fallback",
        )
    lexemes = _lex_shell_words(command)
    if lexemes is None or any(lexeme.control for lexeme in lexemes):
        return ShellAstParseResult(
            kind="parse_unavailable",
            flags=flags,
            reason="fallback lexer failed to tokenize command safely",
            backend="fallback",
        )
    executable = _executable_shell_lexemes(lexemes)
    if not executable:
        return ShellAstParseResult(
            kind="simple",
            flags=flags,
            backend="fallback",
        )
    argv = tuple(lexeme.value for lexeme in executable)
    subcommand = ShellSubcommand(
        text=command,
        argv=argv,
        source_span=(0, len(command)),
    )
    return ShellAstParseResult(
        kind="simple",
        subcommands=(subcommand,),
        flags=flags,
        backend="fallback",
    )


def _scan_shell_structure(command: str) -> ShellStructureFlags:
    from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
        strip_fd_alias_tokens,
    )

    scanned = strip_fd_alias_tokens(command)
    has_pipeline = "|" in scanned
    has_compound = any(token in scanned for token in ("&&", "||", ";", "\n", "\r"))
    has_input_redirection = "<" in scanned
    has_output_redirection = ">" in scanned
    has_command_substitution = bool(_COMMAND_SUBSTITUTION_RE.search(command))
    has_process_substitution = bool(_PROCESS_SUBSTITUTION_RE.search(command))
    has_parameter_expansion = bool(_PARAM_EXPANSION_RE.search(command))
    has_heredoc = bool(_HEREDOC_RE.search(command))
    operators = _collect_operator_markers(scanned)
    return ShellStructureFlags(
        has_compound_operators=has_compound,
        has_pipeline=has_pipeline,
        has_command_substitution=has_command_substitution,
        has_process_substitution=has_process_substitution,
        has_parameter_expansion=has_parameter_expansion,
        has_heredoc=has_heredoc,
        has_input_redirection=has_input_redirection,
        has_output_redirection=has_output_redirection,
        operators=operators,
    )


def _collect_operator_markers(command: str) -> tuple[str, ...]:
    markers: list[str] = []
    for token in ("&&", "||", ";", "|", ">>", ">", "<", "$(", "`", "<(", ">(", "<<", "<<<"):
        if token in command and token not in markers:
            markers.append(token)
    return tuple(markers)


def _parse_with_tree_sitter(
    command: str,
    parser: Any,
    *,
    argv_syntax_out: list[tuple[_ShellArgSyntax, ...]] | None = None,
) -> ShellAstParseResult:
    source = command.encode("utf-8")
    tree = parser.parse(source)
    root = getattr(tree, "root_node", None)
    if root is None:
        return ShellAstParseResult(
            kind="parse_unavailable",
            reason="tree-sitter returned no root node",
            backend="tree-sitter",
        )
    if getattr(root, "has_error", False):
        return ShellAstParseResult(
            kind="too_complex",
            reason="tree-sitter reported parse errors",
            backend="tree-sitter",
        )

    flags = _downgrade_fd_only_redirect_flags(_collect_tree_sitter_flags(root), command)
    if any((
        flags.has_command_substitution,
        flags.has_process_substitution,
        flags.has_parameter_expansion,
        flags.has_heredoc,
        flags.has_subshell,
        flags.has_command_group,
    )):
        return ShellAstParseResult(
            kind="too_complex",
            flags=flags,
            reason="tree-sitter detected unsupported complex shell structure",
            backend="tree-sitter",
        )

    command_nodes = _collect_command_nodes(root)
    if not command_nodes:
        return ShellAstParseResult(
            kind="too_complex",
            flags=flags,
            reason="tree-sitter could not extract any executable command node",
            backend="tree-sitter",
        )

    subcommands: list[ShellSubcommand] = []
    for node in command_nodes:
        text = _node_text(node, source).strip()
        if not text:
            continue
        normalized_argv = _normalized_argv_for_command(node, source)
        if normalized_argv is None:
            argv = ()
            argv_syntax = ()
        else:
            argv = tuple(value for value, _syntax in normalized_argv)
            argv_syntax = tuple(syntax for _value, syntax in normalized_argv)
        redirects = _redirects_for_command(node, source)
        if argv_syntax_out is not None:
            argv_syntax_out.append(argv_syntax)
        subcommands.append(
            ShellSubcommand(
                text=text,
                argv=argv,
                redirects=redirects,
                source_span=(int(node.start_byte), int(node.end_byte)),
                parent_operators=_parent_operators_for_command(node),
            )
        )

    if not subcommands:
        return ShellAstParseResult(
            kind="too_complex",
            flags=flags,
            reason="tree-sitter extracted only empty command nodes",
            backend="tree-sitter",
        )

    return ShellAstParseResult(
        kind="simple",
        subcommands=tuple(subcommands),
        flags=flags,
        backend="tree-sitter",
    )


def _normalized_argv_for_command(
    node: Any,
    source: bytes,
) -> tuple[tuple[str, _ShellArgSyntax], ...] | None:
    raw_argv = tuple(
        _node_text(child, source)
        for index, child in enumerate(getattr(node, "children", []))
        if child is not None
        and getattr(node, "field_name_for_child", lambda _index: None)(index)
        in {"name", "argument"}
    )
    normalized: list[tuple[str, _ShellArgSyntax]] = []
    for raw in raw_argv:
        word = _normalize_shell_word(raw)
        if word is None:
            return None
        normalized.append(word)
    return tuple(normalized)


def _redirects_for_command(node: Any, source: bytes) -> tuple[str, ...]:
    """Return redirect nodes structurally attached to one command."""

    redirects: list[str] = []
    cursor = node
    while cursor is not None:
        if str(getattr(cursor, "type", "")) == "redirected_statement":
            for child in getattr(cursor, "children", []):
                child_type = str(getattr(child, "type", ""))
                if child is not None and child_type in {
                    "file_redirect",
                    "heredoc_redirect",
                }:
                    text = _node_text(child, source).strip()
                    if text and text not in redirects:
                        redirects.append(text)
        parent = getattr(cursor, "parent", None)
        if parent is None or str(getattr(parent, "type", "")) not in {
            "list",
            "pipeline",
            "redirected_statement",
        }:
            break
        cursor = parent
    return tuple(redirects)


def _parent_operators_for_command(node: Any) -> tuple[str, ...]:
    """Return only operators in structural ancestors of one command."""

    operators: list[str] = []
    cursor = getattr(node, "parent", None)
    while cursor is not None:
        cursor_type = str(getattr(cursor, "type", ""))
        if cursor_type not in {"list", "pipeline", "redirected_statement"}:
            break
        if cursor_type in {"list", "pipeline"}:
            for child in getattr(cursor, "children", []):
                child_type = str(getattr(child, "type", ""))
                if child_type in {";", "&&", "||", "|", "|&", "&"}:
                    if child_type not in operators:
                        operators.append(child_type)
        cursor = getattr(cursor, "parent", None)
    return tuple(operators)


def _downgrade_fd_only_redirect_flags(
        flags: ShellStructureFlags,
        command: str,
) -> ShellStructureFlags:
    from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
        strip_fd_alias_tokens,
    )

    if not (flags.has_input_redirection or flags.has_output_redirection):
        return flags
    scanned = strip_fd_alias_tokens(command)
    if ">" in scanned or "<" in scanned:
        return flags
    return ShellStructureFlags(
        has_compound_operators=flags.has_compound_operators,
        has_pipeline=flags.has_pipeline,
        has_subshell=flags.has_subshell,
        has_command_group=flags.has_command_group,
        has_command_substitution=flags.has_command_substitution,
        has_process_substitution=flags.has_process_substitution,
        has_parameter_expansion=flags.has_parameter_expansion,
        has_heredoc=flags.has_heredoc,
        has_input_redirection=False,
        has_output_redirection=False,
        has_actual_operator_nodes=flags.has_actual_operator_nodes,
        operators=tuple(op for op in flags.operators if op not in {">", "<", ">>"}),
    )


def _collect_tree_sitter_flags(root: Any) -> ShellStructureFlags:
    operators: list[str] = []
    flags: dict[str, bool] = {
        "has_compound_operators": False,
        "has_pipeline": False,
        "has_subshell": False,
        "has_command_group": False,
        "has_command_substitution": False,
        "has_process_substitution": False,
        "has_parameter_expansion": False,
        "has_heredoc": False,
        "has_input_redirection": False,
        "has_output_redirection": False,
        "has_actual_operator_nodes": False,
    }

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = str(getattr(node, "type", ""))
        if node_type == "pipeline":
            flags["has_pipeline"] = True
        if node_type in {"list", "list_item"}:
            flags["has_compound_operators"] = True
        if node_type in {"subshell", "subshell_expression"}:
            flags["has_subshell"] = True
        if node_type in {"compound_statement", "brace_group"}:
            flags["has_command_group"] = True
        if node_type == "command_substitution":
            flags["has_command_substitution"] = True
        if node_type == "process_substitution":
            flags["has_process_substitution"] = True
        if node_type in {"expansion", "simple_expansion"}:
            flags["has_parameter_expansion"] = True
        if "heredoc" in node_type:
            flags["has_heredoc"] = True
        if node_type in {"redirected_statement", "file_redirect", "heredoc_redirect"}:
            flags["has_input_redirection"] = True
            flags["has_output_redirection"] = True
        if node_type in {"<", ">", ">>"}:
            flags["has_actual_operator_nodes"] = True
            if node_type == "<":
                flags["has_input_redirection"] = True
            else:
                flags["has_output_redirection"] = True
            if node_type not in operators:
                operators.append(node_type)
        if node_type in {";", "&&", "||", "|", "|&", "&"}:
            flags["has_actual_operator_nodes"] = True
            flags["has_compound_operators"] = flags["has_compound_operators"] or node_type != "|"
            flags["has_pipeline"] = flags["has_pipeline"] or node_type in {"|", "|&"}
            if node_type not in operators:
                operators.append(node_type)
        for child in reversed(list(getattr(node, "children", []) or [])):
            if child is not None:
                stack.append(child)

    return ShellStructureFlags(operators=tuple(operators), **flags)


def _collect_command_nodes(root: Any) -> list[Any]:
    command_nodes: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if str(getattr(node, "type", "")) == "command":
            command_nodes.append(node)
            continue
        for child in reversed(list(getattr(node, "children", []) or [])):
            if child is not None:
                stack.append(child)
    return command_nodes


def _node_text(node: Any, source: bytes) -> str:
    start = int(getattr(node, "start_byte", 0))
    end = int(getattr(node, "end_byte", 0))
    return source[start:end].decode("utf-8", errors="replace")
