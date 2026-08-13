# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""powershell 纳入 shell 白名单；路径工具 auto_confirm key 带文件路径。"""

from __future__ import annotations

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.suggestions import build_permission_suggestions


def _rail() -> PermissionInterruptRail:
    return PermissionInterruptRail(
        config={"enabled": True, "mode": "strict", "defaults": {"*": "ask"}},
        host=ToolPermissionHost(),
    )


def _call(name: str, args: dict) -> ToolCall:
    import json

    return ToolCall(id="c1", type="function", name=name, arguments=json.dumps(args))


def test_powershell_builds_command_suggestion() -> None:
    suggestions = build_permission_suggestions(
        "powershell",
        {"command": 'Get-Item "C:\\Users\\hanzhibin\\test1.txt"'},
    )
    assert suggestions
    assert suggestions[0].match_type == "command"
    assert "powershell" in suggestions[0].tools


def test_powershell_session_merge_writes_approval_override_not_whole_tool() -> None:
    cfg = {
        "enabled": True,
        "mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {"powershell": "ask"},
        "ask_tools": ["powershell"],
        "approval_overrides": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "powershell",
        {"command": "Get-ChildItem"},
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert overrides, "powershell 应落命令级 approval_overrides"
    assert "powershell" not in (merged.get("allow_tools") or [])


def test_powershell_never_falls_back_to_allow_tools_without_suggestion() -> None:
    """复杂/复合命令无安全 suggestion 时，shell 不得整工具写入 allow_tools。"""
    cfg = {
        "enabled": True,
        "mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {"powershell": "ask"},
        "ask_tools": ["powershell"],
        "approval_overrides": [],
        "allow_tools": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "powershell",
        {"command": "Get-ChildItem; Remove-Item -Recurse tmp"},
    )
    assert applied is False
    assert "powershell" not in (merged.get("allow_tools") or [])
    assert not (merged.get("_allow_tools_added") or [])


def test_powershell_pipeline_findings_allow_still_writes_segment_overrides() -> None:
    """tiered 因 defaults 为 ALLOW（引擎靠 findings 才 ASK）时，记住仍应写分段 overrides。"""
    cfg = {
        "enabled": True,
        "mode": "strict",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "approval_overrides": [],
        "allow_tools": [],
    }
    cmd = (
        'New-Item -Path "C:\\Users\\hanzhibin\\test2.txt" -ItemType File -Force'
        " | Select-Object FullName, Length, LastWriteTime"
    )
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "powershell",
        {"command": cmd},
    )
    assert applied is True
    patterns = {
        o.get("pattern")
        for o in (merged.get("approval_overrides") or [])
        if isinstance(o, dict)
    }
    assert any("New-Item" in str(p) for p in patterns)
    assert any("Select-Object" in str(p) for p in patterns)
    assert "powershell" not in (merged.get("allow_tools") or [])


def test_powershell_auto_confirm_key_is_command_scoped() -> None:
    rail = _rail()
    key = rail._get_auto_confirm_key(
        _call("powershell", {"command": "Get-ChildItem -Path C:\\tmp"})
    )
    assert key.startswith("powershell:")
    assert "Get-ChildItem" in key


def test_write_file_auto_confirm_key_includes_path() -> None:
    rail = _rail()
    key = rail._get_auto_confirm_key(
        _call(
            "write_file",
            {"file_path": r"C:\Users\hanzhibin\test1.txt", "content": "x"},
        )
    )
    assert key == "write_file:C:/Users/hanzhibin/test1.txt"


def test_read_file_auto_confirm_key_includes_path() -> None:
    rail = _rail()
    key = rail._get_auto_confirm_key(
        _call("read_file", {"file_path": r"C:\Users\hanzhibin\test2.txt"})
    )
    assert key == "read_file:C:/Users/hanzhibin/test2.txt"


def test_path_auto_confirm_keys_differ_by_path() -> None:
    rail = _rail()
    a = rail._get_auto_confirm_key(
        _call("write_file", {"file_path": "C:/tmp/a.txt", "content": ""})
    )
    b = rail._get_auto_confirm_key(
        _call("write_file", {"file_path": "C:/tmp/b.txt", "content": ""})
    )
    assert a != b
    assert a == "write_file:C:/tmp/a.txt"
    assert b == "write_file:C:/tmp/b.txt"
