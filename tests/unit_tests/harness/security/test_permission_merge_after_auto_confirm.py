# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""模拟用户选择「记住 / 总是允许」(auto_confirm) 后的权限合并。

护栏持久化路径会调用 :func:`merge_permission_allow_rule_into_permissions`；
本模块用**旧版 YAML 常见写法**（``tools.<name>.*`` 字典）与标量写法验证合并结果与
``evaluate_tiered_policy`` 二次判定一致。路径类 override 已迁出，路径工具 HITL 抬升整工具
``tools.*: allow``，路径细则由 ``file_guard`` 落盘（见 rail 侧，不在此函数内）。
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import merge_permission_allow_rule_into_permissions
from openjiuwen.harness.security.tiered_policy import evaluate_tiered_policy


def _base_tiered() -> dict:
    return {
        "enabled": True,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [],
    }


@pytest.mark.parametrize(
    "tools_fragment",
    [
        pytest.param({"read_file": {"*": "ask"}}, id="legacy_dict_star_ask"),
        pytest.param({"read_file": "ask"}, id="scalar_ask"),
    ],
)
def test_read_file_merge_after_auto_confirm_elevates_whole_tool_allow(
    tools_fragment: dict,
) -> None:
    """路径工具在 ASK 下「总是允许」应抬升 ``tools.read_file: allow``，不写 path 类 override。"""
    cfg = {**_base_tiered(), "tools": {**tools_fragment, "write_file": "deny"}}
    tool_args = {"file_path": "notes.txt"}

    before, _rule = evaluate_tiered_policy(cfg, "read_file", tool_args)
    assert before == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "read_file", tool_args
    )
    assert applied is True

    overrides = merged.get("approval_overrides") or []
    assert overrides == []
    assert merged["tools"]["read_file"] == "allow"

    after, matched = evaluate_tiered_policy(merged, "read_file", tool_args)
    assert after == PermissionLevel.ALLOW
    assert "tools.read_file" in matched

    again, applied_again = merge_permission_allow_rule_into_permissions(
        deepcopy(merged), "read_file", tool_args
    )
    assert applied_again is False
    assert again.get("approval_overrides") == merged.get("approval_overrides")
    assert again["tools"]["read_file"] == "allow"


def test_legacy_bash_star_ask_merge_adds_command_override() -> None:
    """``bash: {\"*\": \"ask\"}`` + 简单 ``git status`` 应对应 command 类 override 并变为 ALLOW。"""
    cfg = {
        **_base_tiered(),
        "tools": {"bash": {"*": "ask"}},
    }
    tool_args = {"command": "git status"}

    assert evaluate_tiered_policy(cfg, "bash", tool_args)[0] == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "bash", tool_args
    )
    assert applied is True
    overrides = merged.get("approval_overrides") or []
    assert any(
        isinstance(o, dict)
        and o.get("match_type") == "command"
        and "git" in str(o.get("pattern", "")).lower()
        and o.get("action") == "allow"
        for o in overrides
    )

    assert evaluate_tiered_policy(merged, "bash", tool_args)[0] == PermissionLevel.ALLOW


def test_plain_tool_auto_confirm_sets_whole_tool_allow() -> None:
    """非 shell / path 工具在 ASK 下选择总是允许后，应持久化为整工具 allow。"""
    cfg = {
        **_base_tiered(),
        "tools": {"cron_create_job": "ask"},
    }
    tool_args = {"cron": "0 * * * *", "name": "sync"}

    before, _rule = evaluate_tiered_policy(cfg, "cron_create_job", tool_args)
    assert before == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "cron_create_job", tool_args
    )

    assert applied is True
    assert merged["tools"]["cron_create_job"] == "allow"
    assert merged.get("approval_overrides") == []

    after, matched = evaluate_tiered_policy(merged, "cron_create_job", tool_args)
    assert after == PermissionLevel.ALLOW
    assert "tools.cron_create_job" in matched
