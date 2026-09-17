# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-mode permission narrowing must import after the permission_engine split."""

from __future__ import annotations

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
