# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression coverage for team permission imports and narrowing guarantees."""

from copy import deepcopy

import pytest


def test_team_security_import_uses_the_existing_canonical_policy():
    from openjiuwen.agent_teams import security
    from openjiuwen.agent_teams.security import narrowing
    from openjiuwen.harness.security import tiered_policy
    from openjiuwen.harness.security.permission_engine.toolguard import tool_policy

    assert security.narrow_permissions is narrowing.narrow_permissions
    assert narrowing.strictest is tiered_policy.strictest is tool_policy.strictest
    assert narrowing._parse_level is tool_policy._parse_level


@pytest.mark.parametrize("base,override,expected", [
    ("allow", "allow", "allow"),
    ("allow", "ask", "ask"),
    ("allow", "deny", "deny"),
    ("ask", "allow", "ask"),
    ("ask", "ask", "ask"),
    ("ask", "deny", "deny"),
    ("deny", "allow", "deny"),
    ("deny", "ask", "deny"),
    ("deny", "deny", "deny"),
])
@pytest.mark.parametrize("source", ["tool", "default"])
def test_team_permissions_only_tighten(base, override, expected, source):
    from openjiuwen.agent_teams.security import narrow_permissions

    config = {
        "tools": {"target": base, "untouched": "deny"} if source == "tool" else {"untouched": "deny"},
        "defaults": {"*": base},
        "rules": [{"id": "keep-rule", "action": "deny"}],
    }
    original = deepcopy(config)
    result = narrow_permissions(config, {"target": override})
    assert result["tools"]["target"] == expected
    assert result["tools"]["untouched"] == "deny"
    assert result["defaults"] == original["defaults"]
    assert result["rules"] == original["rules"]
    assert config == original


def test_missing_tool_and_default_require_approval_instead_of_widening():
    from openjiuwen.agent_teams.security import narrow_permissions

    assert narrow_permissions({}, {"target": "allow"})["tools"]["target"] == "ask"
