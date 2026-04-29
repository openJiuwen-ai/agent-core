# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""模拟用户选择「记住 / 总是允许」(auto_confirm) 后的权限合并。

护栏持久化路径会调用 :func:`merge_permission_allow_rule_into_permissions`；
本模块用**旧版 YAML 常见写法**（``tools.<name>.*`` 字典）与标量写法验证合并结果与
``evaluate_tiered_policy`` 二次判定一致。
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
def test_read_file_merge_after_auto_confirm_adds_path_override_and_allows(
    tools_fragment: dict,
) -> None:
    """旧/新 tools 写法在 ASK 下合并后应追加 path 类 approval_override，且同参再次评估为 ALLOW。"""
    cfg = {**_base_tiered(), "tools": {**tools_fragment, "write_file": "deny"}}
    tool_args = {"file_path": "notes.txt"}

    before, _rule = evaluate_tiered_policy(cfg, "read_file", tool_args)
    assert before == PermissionLevel.ASK

    merged, applied = merge_permission_allow_rule_into_permissions(
        deepcopy(cfg), "read_file", tool_args
    )
    assert applied is True

    overrides = merged.get("approval_overrides") or []
    assert isinstance(overrides, list) and overrides
    found = next(
        (
            o
            for o in overrides
            if isinstance(o, dict)
            and o.get("match_type") == "path"
            and o.get("pattern") == "notes.txt"
            and o.get("action") == "allow"
            and "read_file" in (o.get("tools") or [])
        ),
        None,
    )
    assert found is not None

    after, matched = evaluate_tiered_policy(merged, "read_file", tool_args)
    assert after == PermissionLevel.ALLOW
    assert "approval_overrides" in matched

    again, applied_again = merge_permission_allow_rule_into_permissions(
        deepcopy(merged), "read_file", tool_args
    )
    assert applied_again is False
    assert again.get("approval_overrides") == merged.get("approval_overrides")


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
