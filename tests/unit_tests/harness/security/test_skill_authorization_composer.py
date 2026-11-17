# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill overlay permission composition and request context."""

from __future__ import annotations

import copy

from openjiuwen.harness.security.skill_authorization.composer import (
    SkillAuthorizationContext,
    command_rule_fingerprint,
    compose_skill_permissions,
    effective_file_guard_axis_level,
    get_skill_authorization_context,
    reset_skill_authorization_context,
    setup_skill_authorization_context,
)

# ---------- tools ----------


def test_tools_only_raise_from_ask_or_guard_and_never_break_deny() -> None:
    base = {
        "tools": {"asked": "ask", "denied": "deny", "allowed": "allow"},
        "defaults": {"*": "ask"},
    }
    overlay = {
        "tools": {
            "asked": "allow",
            "denied": "allow",
            "allowed": "allow",
            "inherited": "allow",
            "tightened": "deny",
            "still_ask": "ask",
        },
    }

    merged = compose_skill_permissions(base, overlay)

    assert merged["tools"]["asked"] == "allow"
    assert merged["tools"]["denied"] == "deny"
    assert merged["tools"]["allowed"] == "allow"
    assert merged["tools"]["inherited"] == "allow"
    assert merged["tools"]["tightened"] == "deny"
    assert merged["tools"]["still_ask"] == "ask"


def test_tool_allow_without_known_baseline_is_not_applied() -> None:
    base = {"tools": {}}
    merged = compose_skill_permissions(base, {"tools": {"new_tool": "allow"}})
    assert "new_tool" not in merged["tools"]


def test_invalid_tool_entries_are_skipped() -> None:
    merged = compose_skill_permissions(
        {"tools": {"kept": "ask"}},
        {"tools": {"": "allow", "bad-level": "sometimes", "kept": "ALLOW"}},
    )
    assert merged["tools"] == {"kept": "allow"}


# ---------- rules ----------


def test_rule_fingerprint_normalizes_scope() -> None:
    assert command_rule_fingerprint({"pattern": "re:^cd .*", "action": "deny"}) == (
        "re:^cd .*",
        "deny",
        "regex",
    )
    assert command_rule_fingerprint({"pattern": "echo *", "action": "allow"}) == (
        "echo *",
        "allow",
        "head",
    )
    assert command_rule_fingerprint(
        {"pattern": "echo *", "action": "allow", "scope": "wildcard"},
    ) == ("echo *", "allow", "head")
    assert command_rule_fingerprint({"pattern": "sudo echo *", "action": "allow"}) == (
        "sudo echo *",
        "allow",
        "wildcard",
    )
    assert command_rule_fingerprint({"pattern": "a b", "action": "allow", "scope": "exact"}) == (
        "a b",
        "allow",
        "exact",
    )


def test_rule_fingerprint_ignores_display_metadata_and_rejects_malformed() -> None:
    first = command_rule_fingerprint(
        {"id": "one", "description": "x", "pattern": "date *", "action": "allow"},
    )
    second = command_rule_fingerprint({"id": "two", "pattern": "date *", "action": "allow"})
    assert first == second
    assert command_rule_fingerprint({"pattern": "", "action": "allow"}) is None
    assert command_rule_fingerprint({"pattern": "x", "action": "maybe"}) is None
    assert command_rule_fingerprint("not-a-dict") is None


def test_rules_are_appended_with_native_shell_tool_binding() -> None:
    base = {
        "rules": [{"id": "existing", "tools": ["bash"], "pattern": "date *", "action": "allow"}],
    }
    merged = compose_skill_permissions(
        base,
        {
            "rules": [
                {"id": "duplicate", "pattern": "date *", "action": "allow"},
                {"id": "deny_rm", "pattern": "rm *", "action": "deny"},
                {"pattern": "broken", "action": "maybe"},
            ],
        },
    )

    assert len(merged["rules"]) == 2
    appended = merged["rules"][1]
    assert appended["id"] == "deny_rm"
    assert appended["pattern"] == "rm *"
    assert appended["action"] == "deny"
    assert appended["tools"] == ["bash", "mcp_exec_command", "create_terminal"]


def test_rules_are_deduplicated_against_approval_overrides() -> None:
    base = {"approval_overrides": [{"pattern": "date *", "action": "allow"}]}
    merged = compose_skill_permissions(
        base,
        {"rules": [{"pattern": "date *", "action": "allow"}]},
    )
    assert merged["rules"] == []


# ---------- file_guard ----------


def test_file_guard_global_is_projected_to_native_prefix_entries() -> None:
    merged = compose_skill_permissions(
        {"file_guard": {"enabled": False}},
        {"file_guard": {"global": {"/data": {"read": "allow", "write": "deny"}}}},
    )

    assert merged["file_guard"]["enabled"] is False
    assert merged["file_guard"]["paths"] == [
        {"path": "/data", "match": "prefix", "read": "allow", "write": "deny"},
    ]


def test_file_guard_projection_enables_guard_when_base_is_silent() -> None:
    merged = compose_skill_permissions(
        {},
        {"file_guard": {"global": {"/data": {"read": "allow"}}}},
    )

    assert merged["file_guard"]["enabled"] is True
    assert merged["file_guard"]["paths"] == [
        {"path": "/data", "match": "prefix", "read": "allow"},
    ]


def test_file_guard_allow_cannot_pierce_base_ancestor_deny() -> None:
    base = {
        "file_guard": {
            "paths": [{"path": "/srv", "match": "prefix", "read": "deny"}],
        },
    }
    merged = compose_skill_permissions(
        base,
        {"file_guard": {"global": {"/srv/child": {"read": "allow"}}}},
    )

    child = next(item for item in merged["file_guard"]["paths"] if item["path"] == "/srv/child")
    assert child["read"] == "deny"


def test_file_guard_allow_cannot_pierce_overlay_ancestor_deny() -> None:
    merged = compose_skill_permissions(
        {},
        {
            "file_guard": {
                "global": {
                    "/x": {"read": "deny"},
                    "/x/y": {"read": "allow"},
                },
            },
        },
    )

    child = next(item for item in merged["file_guard"]["paths"] if item["path"] == "/x/y")
    assert child["read"] == "deny"


def test_file_guard_merges_existing_prefix_entry() -> None:
    base = {
        "file_guard": {
            "paths": [{"path": "/data", "match": "prefix", "read": "ask"}],
        },
    }
    merged = compose_skill_permissions(
        base,
        {"file_guard": {"global": {"/data": {"write": "deny"}}}},
    )

    assert merged["file_guard"]["paths"] == [
        {"path": "/data", "match": "prefix", "read": "ask", "write": "deny"},
    ]


def test_file_guard_paths_are_normalized() -> None:
    merged = compose_skill_permissions(
        {},
        {"file_guard": {"global": {"/data///": {"read": "allow"}}}},
    )
    assert merged["file_guard"]["paths"][0]["path"] == "/data"


def test_effective_file_guard_axis_level_uses_longest_prefix_and_glob() -> None:
    paths = [
        {"path": "/a", "match": "prefix", "read": "deny"},
        {"path": "/a/b", "match": "prefix", "read": "allow"},
        {"path": "**/*.log", "match": "glob", "write": "deny"},
    ]

    assert effective_file_guard_axis_level(paths, "/a/b/c", "read") == "allow"
    assert effective_file_guard_axis_level(paths, "/a/z", "read") == "deny"
    assert effective_file_guard_axis_level(paths, "/tmp/agent.log", "write") == "deny"


def test_effective_file_guard_axis_level_falls_back_to_defaults() -> None:
    paths = [{"path": "/a", "match": "prefix", "read": "deny"}]

    assert effective_file_guard_axis_level(paths, "/other", "exec", defaults={"exec": "deny"}) == "deny"
    assert effective_file_guard_axis_level(paths, "/other", "exec") == "ask"
    assert effective_file_guard_axis_level([{"path": "/a", "match": "prefix"}], "/a/x", "read") == "ask"


def test_effective_file_guard_axis_level_rejects_bad_inputs() -> None:
    assert effective_file_guard_axis_level(None, "/a", "read") is None
    assert effective_file_guard_axis_level([], "/a", "unknown") is None


# ---------- entry point ----------


def test_compose_returns_deep_copy_and_does_not_mutate_inputs() -> None:
    base = {"tools": {"bash": "ask"}, "rules": []}
    overlay = {"tools": {"bash": "ask"}, "rules": [{"pattern": "date *", "action": "allow"}]}
    original_base = copy.deepcopy(base)
    original_overlay = copy.deepcopy(overlay)

    merged = compose_skill_permissions(base, overlay)

    assert base == original_base
    assert overlay == original_overlay
    merged["tools"]["bash"] = "deny"
    assert base["tools"]["bash"] == "ask"


def test_compose_empty_overlay_returns_base_copy() -> None:
    base = {"tools": {"bash": "ask"}}
    for overlay in (None, {}, "invalid"):
        merged = compose_skill_permissions(base, overlay)
        assert merged == base
        assert merged is not base


# ---------- request context ----------


def test_request_context_round_trip() -> None:
    assert get_skill_authorization_context() is None
    token = setup_skill_authorization_context(" session ", " main ", " req ")
    try:
        context = get_skill_authorization_context()
        assert context == SkillAuthorizationContext(
            session_id="session",
            agent_scope_id="main",
            request_id="req",
        )
    finally:
        reset_skill_authorization_context(token)
    assert get_skill_authorization_context() is None
