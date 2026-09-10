# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os

from openjiuwen.harness.rails.skills.skill_authorization_rail import SkillAuthorizationRail
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.skill_authorization import (
    GrantDecision,
    GrantStatus,
    SkillGrantStore,
    SkillManifest,
    SkillTrustLevel,
    is_skill_authorization_enabled,
)


def _manifest(*, permissions_hash: str = "permissions-v1") -> SkillManifest:
    return SkillManifest(
        skill_name="enterprise-probe",
        source="enterprise-hub",
        version="1.0.0",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash=permissions_hash,
        skill_md_hash="skill-body-v1",
        overlay={"tools": {"write_file": "ask"}},
    )


def test_active_session_grant_is_reusable_for_same_skill_identity() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.SESSION,
        approval_tool_call_id="load-1",
    )
    activated = store.activate_pending(
        "session-1",
        "main",
        manifest.skill_name,
        manifest,
        approval_tool_call_id="load-1",
    )
    assert activated is not None
    assert activated.status == GrantStatus.ACTIVE

    rail = SkillAuthorizationRail(grant_store=store)

    reused = rail._find_reusable_approval("session-1", "main", manifest)

    assert reused is not None
    assert reused.status == GrantStatus.ACTIVE


def test_active_local_grant_is_not_reused_as_session_approval() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.LOCAL,
        approval_tool_call_id="load-1",
    )
    store.activate_pending(
        "session-1",
        "main",
        manifest.skill_name,
        manifest,
        approval_tool_call_id="load-1",
    )
    rail = SkillAuthorizationRail(grant_store=store)

    assert rail._find_reusable_approval("session-1", "main", manifest) is None


def test_session_grant_is_not_reused_after_manifest_identity_changes() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.SESSION,
        approval_tool_call_id="load-1",
    )
    store.activate_pending(
        "session-1",
        "main",
        manifest.skill_name,
        manifest,
        approval_tool_call_id="load-1",
    )
    rail = SkillAuthorizationRail(grant_store=store)

    assert rail._find_reusable_approval(
        "session-1",
        "main",
        _manifest(permissions_hash="permissions-v2"),
    ) is None


def test_permission_rail_exposes_request_scoped_snapshot_hook() -> None:
    expected = {"enabled": True, "tools": {"write_file": "ask"}}
    rail = PermissionInterruptRail(
        config={"enabled": True},
        host=ToolPermissionHost(get_permissions_snapshot=lambda: expected),
    )

    assert rail._get_permissions_snapshot(object()) == expected


def test_explicit_permission_template_overrides_legacy_environment_flag() -> None:
    previous = os.environ.get("SKILL_AUTHORIZATION_ENABLED")
    os.environ["SKILL_AUTHORIZATION_ENABLED"] = "false"
    try:
        assert is_skill_authorization_enabled(
            {"skill_authorization": {"enabled": True}}
        )
    finally:
        if previous is None:
            os.environ.pop("SKILL_AUTHORIZATION_ENABLED", None)
        else:
            os.environ["SKILL_AUTHORIZATION_ENABLED"] = previous
