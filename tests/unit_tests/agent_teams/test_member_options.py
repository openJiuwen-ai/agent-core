# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for member_options helpers."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_teams.tools.member_options import (
    MemberModelRef,
    TeamMemberOptions,
    build_member_options,
    dump_member_options,
    get_member_model_ref,
    get_member_options,
    get_member_permissions_override,
    get_member_worktree,
    load_member_options,
    merge_legacy_member_options,
    set_member_permissions_override,
    set_member_worktree_options,
)


def test_load_member_options_empty_string():
    """Empty string returns default TeamMemberOptions."""
    result = load_member_options("")
    assert result.model_ref is None
    assert result.worktree is None
    assert result.permissions_override is None


def test_load_member_options_none():
    """None input returns default TeamMemberOptions."""
    result = load_member_options(None)
    assert result.model_ref is None
    assert result.worktree is None
    assert result.permissions_override is None


def test_load_member_options_invalid_json():
    """Malformed JSON returns default TeamMemberOptions."""
    result = load_member_options("{invalid}")
    assert result.model_ref is None


def test_load_member_options_valid_json():
    """Valid options JSON is parsed correctly."""
    raw = '{"model_ref": {"model_name": "gpt-4", "model_index": 1}, "worktree": {"isolation": "worktree", "path": "/tmp/ws"}, "permissions_override": {"bash": "deny"}}'
    result = load_member_options(raw)
    assert result.model_ref is not None
    assert result.model_ref.model_name == "gpt-4"
    assert result.model_ref.model_index == 1
    assert result.worktree is not None
    assert result.worktree.isolation == "worktree"
    assert result.worktree.path == "/tmp/ws"
    assert result.permissions_override is not None
    assert result.permissions_override == {"bash": "deny"}


def test_load_member_options_partial_json():
    """Options with only some fields are parsed correctly."""
    raw = '{"model_ref": {"model_name": "gpt-4"}}'
    result = load_member_options(raw)
    assert result.model_ref is not None
    assert result.model_ref.model_name == "gpt-4"
    assert result.model_ref.model_index is None
    assert result.worktree is None
    assert result.permissions_override is None


def test_dump_member_options_empty():
    """Empty options serialize to None."""
    result = dump_member_options(TeamMemberOptions())
    assert result is None


def test_dump_member_options_with_fields():
    """Options with fields serialize to JSON string."""
    opts = TeamMemberOptions(
        model_ref=MemberModelRef(model_name="gpt-4"),
        permissions_override={"bash": "deny"},
    )
    result = dump_member_options(opts)
    assert result is not None
    assert "model_ref" in result
    assert "permissions_override" in result


def test_build_member_options_empty():
    """No inputs produce None (empty config)."""
    result = build_member_options()
    assert result is None


def test_build_member_options_with_model_ref():
    """Model ref alone produces options JSON."""
    result = build_member_options(model_ref={"model_name": "gpt-4", "model_index": 0})
    assert result is not None
    parsed = load_member_options(result)
    assert parsed.model_ref is not None
    assert parsed.model_ref.model_name == "gpt-4"


def test_build_member_options_with_permissions_override():
    """Permissions override alone produces options JSON."""
    result = build_member_options(permissions_override={"bash": "deny", "write_file": "ask"})
    assert result is not None
    parsed = load_member_options(result)
    assert parsed.permissions_override == {"bash": "deny", "write_file": "ask"}
    assert parsed.model_ref is None


def test_build_member_options_with_all_fields():
    """All fields together produce complete options JSON."""
    result = build_member_options(
        model_ref={"model_name": "gpt-4", "model_index": 1},
        worktree_isolation="worktree",
        worktree_path="/tmp/ws",
        permissions_override={"bash": "deny"},
    )
    assert result is not None
    parsed = load_member_options(result)
    assert parsed.model_ref is not None
    assert parsed.model_ref.model_name == "gpt-4"
    assert parsed.worktree is not None
    assert parsed.worktree.isolation == "worktree"
    assert parsed.worktree.path == "/tmp/ws"
    assert parsed.permissions_override == {"bash": "deny"}


def test_merge_legacy_member_options_from_model_ref_json():
    """Merging legacy model_ref_json into options."""
    result = merge_legacy_member_options(
        model_ref_json='{"model_name": "gpt-4", "model_index": 2}',
    )
    assert result is not None
    parsed = load_member_options(result)
    assert parsed.model_ref is not None
    assert parsed.model_ref.model_name == "gpt-4"
    assert parsed.model_ref.model_index == 2


def test_merge_legacy_member_options_preserves_existing_options():
    """Existing options are preserved when merging legacy columns."""
    existing = '{"model_ref": {"model_name": "existing"}}'
    result = merge_legacy_member_options(
        options=existing,
        model_ref_json='{"model_name": "should-not-override"}',
    )
    parsed = load_member_options(result)
    # Existing model_ref should NOT be overridden.
    assert parsed.model_ref.model_name == "existing"


def test_get_member_model_ref_from_namespace():
    """get_member_model_ref reads from a SimpleNamespace with options."""
    member = SimpleNamespace(
        options='{"model_ref": {"model_name": "gpt-4", "model_index": 0}}',
    )
    ref = get_member_model_ref(member)
    assert ref is not None
    assert ref.model_name == "gpt-4"


def test_get_member_model_ref_none_options():
    """get_member_model_ref returns None when options is None."""
    member = SimpleNamespace(options=None)
    assert get_member_model_ref(member) is None


def test_get_member_permissions_override_from_namespace():
    """get_member_permissions_override reads from a SimpleNamespace with options."""
    member = SimpleNamespace(
        options='{"permissions_override": {"bash": "deny", "write_file": "ask"}}',
    )
    override = get_member_permissions_override(member)
    assert override is not None
    assert override == {"bash": "deny", "write_file": "ask"}


def test_get_member_permissions_override_none():
    """get_member_permissions_override returns None when no override."""
    member = SimpleNamespace(options=None)
    assert get_member_permissions_override(member) is None


def test_set_member_permissions_override_adds_new():
    """set_member_permissions_override adds override to empty options."""
    result = set_member_permissions_override(
        None,
        permissions_override={"bash": "deny"},
    )
    parsed = load_member_options(result)
    assert parsed.permissions_override == {"bash": "deny"}


def test_set_member_permissions_override_replaces_existing():
    """set_member_permissions_override replaces existing override."""
    raw = '{"permissions_override": {"bash": "ask"}}'
    result = set_member_permissions_override(
        raw,
        permissions_override={"bash": "deny", "write_file": "ask"},
    )
    parsed = load_member_options(result)
    assert parsed.permissions_override == {"bash": "deny", "write_file": "ask"}


def test_set_member_permissions_override_clears_with_none():
    """set_member_permissions_override clears override when set to None."""
    raw = '{"permissions_override": {"bash": "deny"}}'
    result = set_member_permissions_override(
        raw,
        permissions_override=None,
    )
    parsed = load_member_options(result)
    assert parsed.permissions_override is None


def test_set_member_worktree_options_preserves_permissions_override():
    """Setting worktree options preserves existing permissions_override."""
    raw = '{"permissions_override": {"bash": "deny"}}'
    result = set_member_worktree_options(
        raw,
        isolation="worktree",
        worktree_path="/tmp/ws",
    )
    parsed = load_member_options(result)
    assert parsed.worktree is not None
    assert parsed.worktree.isolation == "worktree"
    assert parsed.permissions_override == {"bash": "deny"}


def test_get_member_options_from_dict():
    """get_member_options reads from a dict-like record."""
    record = {"options": '{"model_ref": {"model_name": "gpt-4"}}'}
    result = get_member_options(record)
    assert result.model_ref is not None
    assert result.model_ref.model_name == "gpt-4"


def test_get_member_worktree_from_namespace():
    """get_member_worktree reads from a SimpleNamespace with options."""
    member = SimpleNamespace(
        options='{"worktree": {"isolation": "worktree", "path": "/tmp/ws"}}',
    )
    worktree = get_member_worktree(member)
    assert worktree is not None
    assert worktree.isolation == "worktree"
    assert worktree.path == "/tmp/ws"
