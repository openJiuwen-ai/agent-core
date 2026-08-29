# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permission ASK presentation: categorized title/summary for HITL UI."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
    build_permission_ask_presentation,
    render_ask_presentation_message,
)
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult


def test_path_ask_uses_path_title_and_write_summary() -> None:
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="file_guard:defaults",
        external_paths=[r"C:\Users\hanzhibin\test1.txt"],
    )
    pres = build_permission_ask_presentation(
        "write_file",
        {"file_path": r"C:\Users\hanzhibin\test1.txt", "content": "x"},
        result,
    )
    assert pres.category == "path"
    assert pres.title == "检测到受保护的文件路径访问"
    assert pres.summary == r"write C:\Users\hanzhibin\test1.txt"
    assert "file_guard" not in pres.summary
    assert "write_file" not in pres.summary
    assert not pres.details


def test_shell_ask_summary_includes_full_command() -> None:
    cmd = (
        'New-Item -Path "C:\\Users\\hanzhibin\\test2.txt" -ItemType File -Force'
        " | Select-Object FullName, Length, LastWriteTime"
    )
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="tiered_policy:defaults.*",
    )
    pres = build_permission_ask_presentation("powershell", {"command": cmd}, result)
    assert pres.category == "shell"
    assert pres.title == "检测到需确认的命令执行"
    assert pres.summary == f"powershell: {cmd}"
    assert "Select-Object FullName, Length, LastWriteTime" in pres.summary


def test_finding_ask_preferred_over_defaults_when_medium_finding() -> None:
    cmd = "echo hi > out.txt"
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="tiered_policy:defaults.*",
    )
    result.findings = [  # type: ignore[attr-defined]
        SimpleNamespace(severity="MEDIUM", reason="shell_risky_structure"),
    ]
    pres = build_permission_ask_presentation("bash", {"command": cmd}, result)
    assert pres.category == "finding"
    assert pres.title == "检测到风险命令结构"
    assert "含重定向或命令替换等结构" in pres.summary
    assert cmd in pres.summary


def test_network_ask_shows_url() -> None:
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="tools.mcp_fetch_webpage",
    )
    pres = build_permission_ask_presentation(
        "mcp_fetch_webpage",
        {"url": "https://evil.test/a"},
        result,
    )
    assert pres.category == "network"
    assert pres.title == "检测到需确认的网络访问"
    assert "evil.test" in pres.summary


def test_tool_ask_for_non_shell_defaults() -> None:
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="tiered_policy:defaults.*",
    )
    pres = build_permission_ask_presentation("todo_list", {}, result)
    assert pres.category == "tool"
    assert pres.title == "工具需要授权后才能使用"
    assert "todo_list" in pres.summary


def test_render_message_puts_summary_first() -> None:
    result = PermissionResult(
        permission=PermissionLevel.ASK,
        matched_rule="file_guard:defaults",
        external_paths=[r"C:\tmp\a.txt"],
    )
    pres = build_permission_ask_presentation(
        "write_file", {"file_path": r"C:\tmp\a.txt"}, result
    )
    msg = render_ask_presentation_message(pres, always_allow_hint="> 记住提示")
    first = next(line for line in msg.splitlines() if line.strip())
    assert first.strip() == pres.summary
    assert "记住提示" in msg
    assert "file_guard" not in msg
    assert "匹配规则" not in msg
