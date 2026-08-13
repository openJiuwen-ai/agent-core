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
from openjiuwen.harness.security.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.suggestions import build_permission_suggestions
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy


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
    from openjiuwen.harness.security.core import PermissionEngine

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
