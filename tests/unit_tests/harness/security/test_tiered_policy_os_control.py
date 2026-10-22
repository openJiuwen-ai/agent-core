# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the os_control (cua desktop) tier of the permission policy."""

from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.permission_engine import PermissionEngine
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
)
from openjiuwen.harness.security.tiered_policy import (
    _os_control_pattern_matches,
    _tool_category,
    rule_tools_category_consistent,
)


def test_desktop_control_tools_are_os_control_category() -> None:
    assert _tool_category("click") == "os_control"
    assert _tool_category("type_text") == "os_control"
    assert _tool_category("kill_app") == "os_control"
    # Read-only tools are intentionally uncategorized (baseline/default govern them).
    assert _tool_category("list_windows") is None
    assert _tool_category("get_window_state") is None


def test_os_control_tools_do_not_mix_with_other_categories() -> None:
    assert rule_tools_category_consistent(["kill_app", "launch_app"])
    # Mixing an os_control tool with a shell tool must be rejected.
    assert not rule_tools_category_consistent(["kill_app", "bash"])


def test_wildcard_pattern_matches_any_invocation() -> None:
    # kill_app carries only a pid (no string args); "*" must still match.
    assert _os_control_pattern_matches("*", {"pid": 844})
    assert _os_control_pattern_matches("*", {})


def test_arg_pattern_matches_relevant_string_args() -> None:
    assert _os_control_pattern_matches("re:(?i)password", {"text": "my PASSWORD is x"})
    assert not _os_control_pattern_matches("re:(?i)password", {"text": "hello world"})
    # keys arrays (hotkey) are joined before matching.
    assert _os_control_pattern_matches("re:cmd", {"keys": ["cmd", "c"]})


def test_package_builtin_rules_carry_no_desktop_rules() -> None:
    """The desktop tools have bare names that other agents share (mobile_gui
    has its own ``type_text``). Rules that every engine inlines would put a
    phone agent's typing behind the desktop agent's ASK, so the desktop rules
    live with the cua agent instead."""
    rules = inline_package_command_rules({})["rules"]
    assert not [r for r in rules if str(r.get("id", "")).startswith("cua_desktop")]


def test_a_non_cua_agents_type_text_is_unaffected() -> None:
    engine = PermissionEngine({"enabled": True, "defaults": {"*": "allow"}})

    level, matched = engine.check_tool_permission_directly("type_text", {"text": "hello"})

    assert level == PermissionLevel.ALLOW
    assert "cua_desktop" not in str(matched)
