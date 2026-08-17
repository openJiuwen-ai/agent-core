# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pipeline shell：suggestion / approval_overrides / auto_confirm 与 shell_subcommands 分段一致。"""

from __future__ import annotations

import json

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.toolguard.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.approve.suggestions import build_permission_suggestions
from openjiuwen.harness.security.toolguard.tiered_policy import evaluate_tiered_policy


_SEG_NEW_ITEM = 'New-Item -Path "C:\\Users\\hanzhibin\\test2.txt" -ItemType File -Force'
_SEG_SELECT = "Select-Object FullName, Length, LastWriteTime"
_PIPE_CMD = f"{_SEG_NEW_ITEM} | {_SEG_SELECT}"


def _rail() -> PermissionInterruptRail:
    return PermissionInterruptRail(
        config={"enabled": True, "mode": "strict", "defaults": {"*": "ask"}},
        host=ToolPermissionHost(),
    )


def test_powershell_pipeline_suggestions_are_per_subcommand() -> None:
    suggestions = build_permission_suggestions(
        "powershell",
        {"command": _PIPE_CMD},
    )
    patterns = [s.pattern for s in suggestions]
    assert len(patterns) == 2
    assert _SEG_NEW_ITEM in patterns
    assert _SEG_SELECT in patterns
    assert _PIPE_CMD not in patterns


def test_powershell_pipeline_merge_writes_per_subcommand_overrides() -> None:
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
        {"command": _PIPE_CMD},
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    patterns = {o["pattern"] for o in overrides if isinstance(o, dict)}
    assert patterns == {_SEG_NEW_ITEM, _SEG_SELECT}
    assert "powershell" not in (merged.get("allow_tools") or [])


def test_powershell_pipeline_auto_confirm_key_is_segmented() -> None:
    rail = _rail()
    key = rail._get_auto_confirm_key(
        ToolCall(
            id="c1",
            type="function",
            name="powershell",
            arguments=json.dumps({"command": _PIPE_CMD}),
        )
    )
    sep = PermissionInterruptRail._SHELL_AUTO_CONFIRM_SEG_SEP
    assert sep in key
    assert f"powershell:{_SEG_NEW_ITEM}" in key
    assert f"powershell:{_SEG_SELECT}" in key
    assert key == sep.join(
        [f"powershell:{_SEG_NEW_ITEM}", f"powershell:{_SEG_SELECT}"]
    )


def test_powershell_pipeline_segment_overrides_allow() -> None:
    """两段各自写入 approval_overrides 后，分段评估聚合为 ALLOW。"""
    cfg = {
        "enabled": True,
        "mode": "strict",
        "permission_mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {"powershell": "ask"},
        "approval_overrides": [
            {
                "id": "a",
                "tools": ["powershell"],
                "match_type": "command",
                "pattern": _SEG_NEW_ITEM,
                "action": "allow",
            },
            {
                "id": "b",
                "tools": ["powershell"],
                "match_type": "command",
                "pattern": _SEG_SELECT,
                "action": "allow",
            },
        ],
    }
    level, matched = evaluate_tiered_policy(cfg, "powershell", {"command": _PIPE_CMD})
    assert level == PermissionLevel.ALLOW
    assert matched.startswith("tiered_policy:approval_overrides:")


@pytest.mark.asyncio
async def test_powershell_pipeline_segment_overrides_not_escalated_by_findings() -> None:
    """strict 下 pipeline findings 不把分段 approval_overrides 的 ALLOW 抬回 ASK。"""
    from openjiuwen.harness.security.engine import PermissionEngine

    cfg = {
        "enabled": True,
        "mode": "strict",
        "permission_mode": "strict",
        "defaults": {"*": "ask"},
        "tools": {"powershell": "ask"},
        "file_guard": {"enabled": False},
        "approval_overrides": [
            {
                "id": "a",
                "tools": ["powershell"],
                "match_type": "command",
                "pattern": _SEG_NEW_ITEM,
                "action": "allow",
            },
            {
                "id": "b",
                "tools": ["powershell"],
                "match_type": "command",
                "pattern": _SEG_SELECT,
                "action": "allow",
            },
        ],
    }
    engine = PermissionEngine(cfg)
    result = await engine.check_permission("powershell", {"command": _PIPE_CMD})
    assert result.permission == PermissionLevel.ALLOW
    assert result.matched_rule and result.matched_rule.startswith(
        "tiered_policy:approval_overrides:"
    )
    assert result.findings


def test_powershell_builds_command_suggestion() -> None:
    suggestions = build_permission_suggestions(
        "powershell",
        {"command": 'Get-Item "C:\\Users\\hanzhibin\\test1.txt"'},
    )
    assert suggestions
    assert suggestions[0].match_type == "command"
    assert "powershell" in suggestions[0].tools


def test_powershell_simple_command_writes_override_not_allow_tools() -> None:
    cfg = {
        "enabled": True,
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
    assert merged.get("approval_overrides")
    assert "powershell" not in (merged.get("allow_tools") or [])


def test_powershell_compound_does_not_write_allow_tools() -> None:
    cfg = {
        "enabled": True,
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


def test_powershell_pipeline_writes_segment_overrides_when_defaults_allow() -> None:
    cfg = {
        "enabled": True,
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "approval_overrides": [],
        "allow_tools": [],
    }
    merged, applied = merge_permission_allow_rule_into_permissions(
        cfg,
        "powershell",
        {"command": _PIPE_CMD},
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


def _call(name: str, args: dict) -> ToolCall:
    return ToolCall(id="c1", type="function", name=name, arguments=json.dumps(args))


def test_powershell_auto_confirm_key_is_command_scoped() -> None:
    key = _rail()._get_auto_confirm_key(
        _call("powershell", {"command": "Get-ChildItem -Path C:\\tmp"})
    )
    assert key.startswith("powershell:")
    assert "Get-ChildItem" in key


def test_write_file_auto_confirm_key_includes_path() -> None:
    key = _rail()._get_auto_confirm_key(
        _call("write_file", {"file_path": r"C:\Users\hanzhibin\test1.txt", "content": "x"})
    )
    assert key == "write_file:C:/Users/hanzhibin/test1.txt"


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
