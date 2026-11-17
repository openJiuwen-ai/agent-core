# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for permission narrowing: overrides may tighten, never loosen."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.security import (
    format_base_permissions_for_desc,
    narrow_permissions,
)


@pytest.mark.parametrize(
    ("base_level", "override_level", "expected"),
    [
        ("deny", "allow", "deny"),
        ("deny", "ask", "deny"),
        ("ask", "allow", "ask"),
        ("ask", "deny", "deny"),
        ("allow", "ask", "ask"),
        ("allow", "deny", "deny"),
        ("ask", "ask", "ask"),
        ("allow", "allow", "allow"),
    ],
)
def test_override_never_loosens_base(base_level: str, override_level: str, expected: str) -> None:
    """Every base/override pair resolves to the stricter of the two."""
    result = narrow_permissions({"tools": {"bash": base_level}}, {"bash": override_level})
    assert result["tools"]["bash"] == expected


def test_sequential_overrides_stay_monotonic() -> None:
    """Applying allow -> ask -> deny keeps tightening and never bounces back."""
    config = {"tools": {"bash": "allow"}}
    for override_level, expected in (("allow", "allow"), ("ask", "ask"), ("deny", "deny")):
        config = narrow_permissions(config, {"bash": override_level})
        assert config["tools"]["bash"] == expected


def test_defaults_wildcard_applies_when_tool_absent() -> None:
    """An unlisted tool inherits the ``*`` default before narrowing."""
    result = narrow_permissions({"tools": {}, "defaults": {"*": "deny"}}, {"grep": "allow"})
    assert result["tools"]["grep"] == "deny"


def test_defaults_per_tool_wins_over_wildcard() -> None:
    """A per-tool default takes precedence over the ``*`` default."""
    result = narrow_permissions(
        {"tools": {}, "defaults": {"grep": "ask", "*": "allow"}},
        {"grep": "allow"},
    )
    assert result["tools"]["grep"] == "ask"


def test_missing_defaults_fall_back_to_ask() -> None:
    """No per-tool and no ``*`` default means the base level is ASK."""
    result = narrow_permissions({"tools": {}}, {"grep": "allow"})
    assert result["tools"]["grep"] == "ask"


def test_other_fields_preserved_and_input_untouched() -> None:
    """Narrowing only rewrites ``tools`` and never mutates the caller's config."""
    base = {
        "tools": {"bash": "allow"},
        "permission_mode": "strict",
        "rules": [{"tool": "bash", "pattern": "rm -rf /"}],
    }
    result = narrow_permissions(base, {"bash": "deny"})
    assert result["permission_mode"] == "strict"
    assert result["rules"] == [{"tool": "bash", "pattern": "rm -rf /"}]
    assert result["tools"]["bash"] == "deny"
    assert result is not base
    assert base["tools"]["bash"] == "allow"


def test_format_base_permissions_for_desc_lists_rules() -> None:
    """The description surfaces tool rules and the tightening-only contract."""
    text = format_base_permissions_for_desc(
        {"tools": {"bash": "allow"}, "defaults": {"*": "deny"}}
    )
    assert "bash: allow" in text
    assert "Other tools not listed above: deny" in text
    assert "only tightening, never loosening" in text
    assert format_base_permissions_for_desc({}) == ""
