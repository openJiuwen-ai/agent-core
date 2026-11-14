# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission suggestion builders (``openjiuwen.harness.security.suggestions``)."""

from __future__ import annotations

from openjiuwen.harness.security.shell_ast import (
    ShellAstParseResult,
    ShellSubcommand,
)
from openjiuwen.harness.security.suggestions import (
    PermissionSuggestion,
    build_permission_suggestions,
    build_shell_permission_suggestions,
)


def _ok(*commands: str) -> ShellAstParseResult:
    return ShellAstParseResult(
        kind="ok",
        subcommands=tuple(ShellSubcommand(text=command) for command in commands),
    )


def test_simple_command_yields_exact_suggestion() -> None:
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status",
        shell_ast_result=_ok("git status"),
    )
    assert suggestions == [
        PermissionSuggestion(
            tools=("bash",),
            match_type="command",
            pattern="git status",
            scope="exact",
            reason="exact_command",
        )
    ]


def test_multiple_subcommands_are_deduped() -> None:
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status && git status && git pull",
        shell_ast_result=_ok("git status", "git status", "git pull"),
    )
    assert [item.pattern for item in suggestions] == ["git status", "git pull"]


def test_failed_parse_still_yields_exact_suggestion() -> None:
    result = ShellAstParseResult(kind="failed", reason="unsupported")
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status",
        shell_ast_result=result,
    )
    assert suggestions == [
        PermissionSuggestion(
            tools=("bash",),
            match_type="command",
            pattern="git status",
            scope="exact",
            reason="exact_command",
        )
    ]


def test_redirect_command_still_yields_suggestion() -> None:
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status > out",
        shell_ast_result=_ok("git status > out"),
    )
    assert suggestions == [
        PermissionSuggestion(
            tools=("bash",),
            match_type="command",
            pattern="git status > out",
            scope="exact",
            reason="exact_command",
        )
    ]


def test_heredoc_prefix_suggestion() -> None:
    command = "python3 probe.py --stdin <<'EOF'\n{}\nEOF"
    suggestions = build_shell_permission_suggestions(
        "bash",
        command,
        shell_ast_result=ShellAstParseResult(kind="ok"),
    )
    assert suggestions == [
        PermissionSuggestion(
            tools=("bash",),
            match_type="command",
            pattern="python3 probe.py *",
            scope="prefix",
            reason="heredoc_prefix",
        )
    ]


def test_multiline_command_uses_first_line_prefix() -> None:
    suggestions = build_shell_permission_suggestions(
        "bash",
        "git status\nrm -rf /",
        shell_ast_result=ShellAstParseResult(kind="ok"),
    )
    assert suggestions[0].pattern == "git status *"
    assert suggestions[0].scope == "prefix"
    assert suggestions[0].reason == "first_line_prefix"


def test_path_tool_uses_exact_path_parameter() -> None:
    suggestions = build_permission_suggestions("read_file", {"file_path": "notes.txt"})
    assert suggestions == [
        PermissionSuggestion(
            tools=("read_file",),
            match_type="path",
            pattern="notes.txt",
            scope="exact",
            reason="exact_path",
        )
    ]


def test_path_tool_derives_path_from_unlisted_key() -> None:
    suggestions = build_permission_suggestions("read_file", {"target": "/tmp/data.txt"})
    assert suggestions == [
        PermissionSuggestion(
            tools=("read_file",),
            match_type="path",
            pattern="/tmp/data.txt",
            scope="exact",
            reason="derived_exact_path",
        )
    ]


def test_unknown_tool_yields_no_suggestion() -> None:
    assert build_permission_suggestions("custom_tool", {"file_path": "notes.txt"}) == []


def test_shell_tool_without_command_yields_no_suggestion() -> None:
    assert build_permission_suggestions("bash", {}) == []


def test_build_permission_suggestions_forwards_shell_ast_result() -> None:
    suggestions = build_permission_suggestions(
        "bash",
        {"command": "git status"},
        shell_ast_result=_ok("git status"),
    )
    assert [item.pattern for item in suggestions] == ["git status"]
