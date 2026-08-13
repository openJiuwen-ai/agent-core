# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission suggestion builders for allow_always persistence."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.security.shell_ast import (
    ShellAstParseResult,
    ShellSubcommand,
    parse_shell_for_permission,
)

logger = logging.getLogger(__name__)

_SHELL_SUGGESTION_TOOLS = frozenset({
    "bash", "mcp_exec_command", "create_terminal", "powershell",
})
_PATH_SUGGESTION_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
    "send_file_to_user",
})
_PATH_SUGGESTION_KEYS = (
    "path", "file_path", "target_file", "file", "old_path", "new_path",
    "source_path", "dest_path", "directory", "dir",
    "abs_file_path_list",
)


@dataclass(frozen=True)
class PermissionSuggestion:
    tools: tuple[str, ...]
    match_type: str
    pattern: str
    action: str = "allow"
    scope: str = "exact"
    reason: str | None = None


def build_permission_suggestions(
        tool_name: str,
        tool_args: dict[str, Any],
        shell_ast_result: ShellAstParseResult | None = None,
) -> list[PermissionSuggestion]:
    if tool_name in _SHELL_SUGGESTION_TOOLS:
        command = str(tool_args.get("command", "") or tool_args.get("cmd", "") or "").strip()
        if not command:
            return []
        return build_shell_permission_suggestions(
            tool_name,
            command,
            shell_ast_result=shell_ast_result,
        )
    if tool_name in _PATH_SUGGESTION_TOOLS:
        suggestion = _build_path_permission_suggestion(tool_name, tool_args)
        return [suggestion] if suggestion is not None else []
    return []


def build_shell_permission_suggestions(
        tool_name: str,
        command: str,
        *,
        shell_ast_result: ShellAstParseResult | None = None,
) -> list[PermissionSuggestion]:
    shell_ast_result = shell_ast_result or parse_shell_for_permission(command)
    flags = shell_ast_result.flags

    if shell_ast_result.kind == "too_complex":
        return []
    if shell_ast_result.kind == "parse_unavailable" and flags.has_risky_structure():
        return []
    if any((
            flags.has_input_redirection,
            flags.has_output_redirection,
            flags.has_command_substitution,
            flags.has_process_substitution,
            flags.has_heredoc,
            flags.has_subshell,
            flags.has_command_group,
            flags.has_parameter_expansion,
    )):
        return []

    # && / || / ; 等复合结构：不提供可持久化 suggestion（与评估保守策略一致）。
    if flags.has_compound_operators:
        return []

    # 管道 / 多段 simple：按子命令分段 suggestion（与 shell_subcommands 评估、
    # approval_overrides 落盘、auto_confirm key 一致）。
    if shell_ast_result.kind == "simple" and len(shell_ast_result.subcommands) >= 1:
        out: list[PermissionSuggestion] = []
        for subcommand in shell_ast_result.subcommands:
            suggestion = _build_single_shell_suggestion(tool_name, subcommand.text)
            if suggestion is not None:
                out.append(suggestion)
        return _dedupe_suggestions(out)

    suggestion = _build_single_shell_suggestion(tool_name, command)
    return [suggestion] if suggestion is not None else []


def _build_single_shell_suggestion(
        tool_name: str,
        command: str,
) -> PermissionSuggestion | None:
    text = (command or "").strip()
    if not text:
        return None

    heredoc_prefix = _extract_prefix_before_heredoc(text)
    if heredoc_prefix:
        return PermissionSuggestion(
            tools=(tool_name,),
            match_type="command",
            pattern=_build_prefix_pattern(heredoc_prefix),
            scope="prefix",
            reason="heredoc_prefix",
        )

    if "\n" in text:
        first_line = text.splitlines()[0].strip()
        if first_line:
            prefix = _extract_simple_command_prefix(first_line)
            if prefix:
                return PermissionSuggestion(
                    tools=(tool_name,),
                    match_type="command",
                    pattern=_build_prefix_pattern(prefix),
                    scope="prefix",
                    reason="first_line_prefix",
                )
        return None

    return PermissionSuggestion(
        tools=(tool_name,),
        match_type="command",
        pattern=text,
        scope="exact",
        reason="exact_command",
    )


def _extract_prefix_before_heredoc(command: str) -> str | None:
    if "<<" not in command:
        return None
    before = command.split("<<", 1)[0].strip()
    if not before:
        return None
    return _extract_simple_command_prefix(before) or before


def _extract_simple_command_prefix(command: str) -> str | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    return " ".join(argv[:2]).strip() or None


def _build_prefix_pattern(prefix: str) -> str:
    return prefix.strip() + " *"


def _build_path_permission_suggestion(
        tool_name: str,
        tool_args: dict[str, Any],
) -> PermissionSuggestion | None:
    from openjiuwen.harness.security.tiered_policy import expand_path_arg_values

    for key in _PATH_SUGGESTION_KEYS:
        values = expand_path_arg_values(tool_args.get(key))
        if values:
            return PermissionSuggestion(
                tools=(tool_name,),
                match_type="path",
                pattern=values[0],
                scope="exact",
                reason="exact_path",
            )
    for key, value in tool_args.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if _value_looks_like_path(key, text):
            return PermissionSuggestion(
                tools=(tool_name,),
                match_type="path",
                pattern=text,
                scope="exact",
                reason="derived_exact_path",
            )
    return None


def _value_looks_like_path(key: str, text: str) -> bool:
    if key in _PATH_SUGGESTION_KEYS:
        return True
    if "/" in text or "\\" in text:
        return True
    return len(text) > 1 and text[1] == ":"


def _dedupe_suggestions(
        suggestions: list[PermissionSuggestion],
) -> list[PermissionSuggestion]:
    seen: set[tuple[tuple[str, ...], str, str, str]] = set()
    result: list[PermissionSuggestion] = []
    for suggestion in suggestions:
        signature = (
            suggestion.tools,
            suggestion.match_type,
            suggestion.pattern,
            suggestion.action,
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append(suggestion)
    return result
