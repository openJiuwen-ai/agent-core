# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.harness.rails.skills.skill_authorization_rail import SkillAuthorizationRail
from openjiuwen.harness.security.skill_authorization import (
    GrantDecision,
    SkillGrantStore,
    SkillManifest,
    SkillTrustLevel,
)


class _RecordingStore(SkillGrantStore):
    """记录 invalidate_scope 调用，用于断言回收是否发生。"""

    def __init__(self) -> None:
        super().__init__()
        self.invalidate_reasons: list[str] = []

    def invalidate_scope(
        self,
        session_id: str,
        agent_scope_id: str,
        *,
        reason: str,
    ) -> None:
        self.invalidate_reasons.append(reason)
        super().invalidate_scope(session_id, agent_scope_id, reason=reason)


def _manifest(*, skill_name: str = "enterprise-probe") -> SkillManifest:
    return SkillManifest(
        skill_name=skill_name,
        source="enterprise-hub",
        version="1.0.0",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash="permissions-v1",
        skill_md_hash="skill-body-v1",
        overlay={"tools": {"write_file": "ask"}},
    )


def _activate_session_grant(store: SkillGrantStore, skill_name: str) -> None:
    manifest = _manifest(skill_name=skill_name)
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
        skill_name,
        manifest,
        approval_tool_call_id="load-1",
    )
    assert activated is not None


def test_retire_skipped_when_no_active_grant_to_retire() -> None:
    # 无 overlay 技能会话首次加载：scope 无任何 ACTIVE Grant，空回收应跳过，
    # 不挂熔断（否则同轮稍后的 active_skill 写入会被收尾守卫丢弃）。
    store = _RecordingStore()
    rail = SkillAuthorizationRail(grant_store=store)

    rail._retire_scope_after_ungranted_load(
        "session-1", "main", reason="skill_load_without_grant",
    )

    assert store.invalidate_reasons == []
    assert store.get_active("session-1", "main") is None


def test_retire_proceeds_when_scope_has_active_grant() -> None:
    # 上一技能仍持有 ACTIVE Grant：切换到无 overlay 技能时须正常回收旧授权。
    store = _RecordingStore()
    _activate_session_grant(store, "previous-skill")
    rail = SkillAuthorizationRail(grant_store=store)

    rail._retire_scope_after_ungranted_load(
        "session-1", "main", reason="skill_load_without_grant",
    )

    assert store.invalidate_reasons == ["skill_load_without_grant"]
    assert store.get_active("session-1", "main") is None


def test_retire_keeps_breaker_when_skill_body_load_fails() -> None:
    # 失败加载不跳过：熔断继续拦截未成功加载技能的伪激活写入。
    store = _RecordingStore()
    rail = SkillAuthorizationRail(grant_store=store)

    rail._retire_scope_after_ungranted_load(
        "session-1", "main", reason="skill_body_load_failed",
    )

    assert store.invalidate_reasons == ["skill_body_load_failed"]
