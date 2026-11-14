# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-mode permission narrowing must import after the permission_engine split."""

from __future__ import annotations

import copy

import pytest

from openjiuwen.agent_teams.security.narrowing import narrow_permissions
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.security.permission_engine.models import PermissionLevel


def test_tiered_policy_shim_exports_parse_level() -> None:
    from openjiuwen.harness.security.tiered_policy import _parse_level, strictest

    assert _parse_level(" ASK ") is PermissionLevel.ASK
    assert strictest(PermissionLevel.ALLOW, PermissionLevel.ASK) is PermissionLevel.ASK


def test_narrow_permissions_import_and_tighten() -> None:
    from openjiuwen.agent_teams.security.narrowing import narrow_permissions

    narrowed = narrow_permissions(
        {"tools": {"bash": "allow"}, "defaults": {"*": "allow"}},
        {"bash": "ask"},
    )
    assert narrowed["tools"]["bash"] == "ask"

@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("base_level", ["allow", "ask", "deny"])
@pytest.mark.parametrize("override", ["allow", "ask", "deny"])
def test_scalar_and_legacy_base_have_identical_strictest_results(legacy, base_level, override):
    base = {
        "enabled": True,
        "tools": {"write_file": {"*": base_level, "/ignored": "allow"} if legacy else base_level,
                  "read_file": "allow"},
        "defaults": {"*": "allow"},
        "rules": [{"tool": "write_file", "decision": "deny"}],
        "file_guard": {"allow": ["workspace/**"]},
    }
    before = copy.deepcopy(base)
    result = narrow_permissions(base, {"write_file": override})
    order = ["allow", "ask", "deny"]
    assert result["tools"]["write_file"] == max([base_level, override], key=order.index)
    assert base == before
    assert result["rules"] == before["rules"] and result["file_guard"] == before["file_guard"]
    assert result["tools"]["read_file"] == "allow"


@pytest.mark.parametrize("tools", [{}, {"write_file": None}])
@pytest.mark.parametrize("defaults, expected", [
    ({"write_file": "deny", "*": "allow"}, "deny"),
    ({"*": "deny"}, "deny"),
    ({}, "ask"),
])
def test_missing_and_none_base_retain_original_default_precedence(tools, defaults, expected):
    result = narrow_permissions({"tools": tools, "defaults": defaults}, {"write_file": "allow"})
    assert result["tools"]["write_file"] == expected


@pytest.mark.parametrize("value", ["invalid", {}, {"path": "deny"}, {"*": "invalid"}, {"*": False}, 0, False])
def test_invalid_explicit_base_cannot_fall_back_to_allow(value):
    with pytest.raises(BaseError) as raised:
        narrow_permissions({"tools": {"write_file": value}, "defaults": {"*": "allow"}},
                           {"write_file": "allow"})
    assert raised.value.status == StatusCode.AGENT_TEAM_CONFIG_INVALID


def test_no_override_preserves_legacy_base_unchanged():
    base = {"tools": {"write_file": {"*": "ask"}}, "defaults": {"*": "allow"}}
    result = narrow_permissions(base, {})
    assert result == base and result is not base
