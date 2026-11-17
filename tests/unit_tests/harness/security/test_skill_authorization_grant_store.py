# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process SkillGrantStore lifecycle and global store wiring."""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.skill_authorization.grant_store import (
    SkillGrantStore,
    get_skill_authorization_generation,
    get_skill_grant_store,
    peek_skill_grant_store,
    set_skill_grant_store,
)
from openjiuwen.harness.security.skill_authorization.models import (
    GrantDecision,
    GrantStatus,
    SkillManifest,
    SkillTrustLevel,
)


def _manifest(*, permissions_hash: str = "permissions-v1", skill_md_hash: str = "skill-v1") -> SkillManifest:
    return SkillManifest(
        skill_name="demo",
        source="hub",
        version="1.0.0",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash=permissions_hash,
        skill_md_hash=skill_md_hash,
        overlay={"tools": {"write_file": "ask"}},
    )


def _activate(
    store: SkillGrantStore,
    *,
    session: str = "session-1",
    scope: str = "main",
    decision: GrantDecision = GrantDecision.SESSION,
    manifest: SkillManifest | None = None,
) -> SkillManifest:
    manifest = manifest or _manifest()
    store.create_pending_grant(
        session,
        scope,
        manifest,
        decision=decision,
        approval_tool_call_id="load-1",
    )
    assert (
        store.activate_pending(
            session,
            scope,
            manifest.skill_name,
            manifest,
            approval_tool_call_id="load-1",
        )
        is not None
    )
    return manifest


@pytest.fixture
def isolated_global_store():
    previous = peek_skill_grant_store()
    store = SkillGrantStore()
    set_skill_grant_store(store)
    try:
        yield store
    finally:
        set_skill_grant_store(previous)


# ---------- lifecycle ----------


def test_pending_grant_becomes_active_after_identity_match() -> None:
    store = SkillGrantStore()
    manifest = _manifest()

    pending = store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.SESSION,
        approval_tool_call_id="load-1",
    )
    assert pending.status == GrantStatus.PENDING_ACTIVATION
    assert pending.overlay_snapshot == {"tools": {"write_file": "ask"}}

    active = store.activate_pending(
        "session-1",
        "main",
        manifest.skill_name,
        manifest,
        approval_tool_call_id="load-1",
    )
    assert active is not None
    assert active.status == GrantStatus.ACTIVE
    assert store.get_active("session-1", "main") is not None


def test_create_pending_rejects_non_authorizable_or_bodyless_manifest() -> None:
    store = SkillGrantStore()
    with pytest.raises(ValueError, match="不可授权"):
        store.create_pending_grant(
            "session-1",
            "main",
            SkillManifest(
                skill_name="demo",
                source="hub",
                version=None,
                trust=SkillTrustLevel.OTHER,
                permissions_hash="",
                authorizable=False,
                errors=("bad",),
            ),
            decision=GrantDecision.LOCAL,
        )
    with pytest.raises(ValueError, match="SKILL.md 摘要缺失"):
        store.create_pending_grant(
            "session-1",
            "main",
            _manifest(skill_md_hash=""),
            decision=GrantDecision.LOCAL,
        )


def test_identity_mismatch_discards_candidate() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    store.create_pending_grant("session-1", "main", manifest, decision=GrantDecision.LOCAL)

    changed = _manifest(permissions_hash="permissions-v2")
    assert (
        store.activate_pending(
            "session-1",
            "main",
            manifest.skill_name,
            changed,
            approval_tool_call_id="load-1",
        )
        is None
    )
    assert store.get_grant("session-1", "main", manifest.skill_name) is None


def test_approval_tool_call_id_must_match_when_recorded() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.LOCAL,
        approval_tool_call_id="load-1",
    )

    assert (
        store.activate_pending(
            "session-1",
            "main",
            manifest.skill_name,
            manifest,
            approval_tool_call_id="load-2",
        )
        is None
    )


def test_new_active_grant_supersedes_previous_one() -> None:
    store = SkillGrantStore()
    first = _manifest(permissions_hash="p1")
    second = SkillManifest(
        skill_name="second",
        source="hub",
        version="1.0.0",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash="p2",
        skill_md_hash="skill-v1",
    )

    _activate(store, decision=GrantDecision.SESSION, manifest=first)
    _activate(store, decision=GrantDecision.LOCAL, manifest=second)

    superseded = store.get_grant("session-1", "main", first.skill_name)
    assert superseded is not None
    assert superseded.status == GrantStatus.APPROVED_INACTIVE
    assert store.get_active("session-1", "main").skill_name == "second"


def test_revoke_local_deletes_and_session_keeps_inactive_record() -> None:
    local_store = SkillGrantStore()
    local_manifest = _manifest()
    _activate(local_store, decision=GrantDecision.LOCAL, manifest=local_manifest)
    local_store.revoke_grants("session-1", "main")
    assert local_store.list_grants("session-1") == []

    session_store = SkillGrantStore()
    session_manifest = _manifest()
    _activate(session_store, decision=GrantDecision.SESSION, manifest=session_manifest)
    session_store.revoke_grants("session-1", "main")
    grant = session_store.get_grant("session-1", "main", session_manifest.skill_name)
    assert grant is not None
    assert grant.status == GrantStatus.APPROVED_INACTIVE


def test_invalidate_scope_hides_active_overlay_without_dropping_grant() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    _activate(store, manifest=manifest)

    store.invalidate_scope("session-1", "main", reason="test")

    assert store.get_active("session-1", "main") is None
    assert store.get_grant("session-1", "main", manifest.skill_name) is not None


def test_retired_session_cannot_regain_grants() -> None:
    store = SkillGrantStore()
    store.clear_session("session-1")

    with pytest.raises(RuntimeError, match="session 已永久删除"):
        store.create_pending_grant("session-1", "main", _manifest(), decision=GrantDecision.LOCAL)
    with pytest.raises(RuntimeError, match="session 已永久删除"):
        store.enter_skill_execution("session-1", "main", "demo")


def test_skill_execution_window_round_trip() -> None:
    store = SkillGrantStore()
    assert store.get_active_skill_execution("session-1", "main") is None
    store.enter_skill_execution("session-1", "main", "demo")
    assert store.get_active_skill_execution("session-1", "main") == "demo"
    store.exit_skill_execution("session-1", "main")
    assert store.get_active_skill_execution("session-1", "main") is None


def test_clear_scope_and_clear_all_remove_everything() -> None:
    store = SkillGrantStore()
    _activate(store, scope="main")
    _activate(store, scope="sub")
    store.enter_skill_execution("session-1", "sub", "demo")

    store.clear_scope("session-1", "sub")
    assert store.list_grants("session-1", "sub") == []
    assert store.get_active_skill_execution("session-1", "sub") is None
    assert store.list_grants("session-1", "main")

    store.clear_all()
    assert store.list_grants("session-1") == []


def test_scope_validation_and_non_empty_requirements() -> None:
    store = SkillGrantStore()
    with pytest.raises(ValueError):
        store.get_grant("", "main", "demo")
    with pytest.raises(ValueError):
        store.get_grant("session-1", "", "demo")
    with pytest.raises(ValueError):
        store.list_grants("")
    with pytest.raises(ValueError):
        store.enter_skill_execution("session-1", "main", "")


def test_grants_are_returned_as_defensive_copies() -> None:
    store = SkillGrantStore()
    manifest = _manifest()
    _activate(store, manifest=manifest)

    grant = store.get_grant("session-1", "main", manifest.skill_name)
    assert grant is not None
    grant.overlay_snapshot["tools"]["write_file"] = "allow"

    assert store.get_grant("session-1", "main", manifest.skill_name).overlay_snapshot == {
        "tools": {"write_file": "ask"},
    }


def test_permission_listener_is_notified_after_mutation() -> None:
    store = SkillGrantStore()

    class Listener:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def skill_permissions_changed(self, session_id, agent_scope_id, operation) -> None:
            self.calls.append((session_id, agent_scope_id, operation))

    listener = Listener()
    store.add_permission_listener(listener)
    _activate(store)

    assert ("session-1", "main", "activate_pending") in listener.calls


# ---------- global store ----------


def test_global_store_can_be_replaced(isolated_global_store: SkillGrantStore) -> None:
    assert get_skill_grant_store() is isolated_global_store
    assert peek_skill_grant_store() is isolated_global_store


def test_authorization_generation_uses_shared_store() -> None:
    class SharedStore(SkillGrantStore):
        @property
        def authorization_generation(self) -> int:
            return 42

    previous = peek_skill_grant_store()
    set_skill_grant_store(SharedStore())
    try:
        assert get_skill_authorization_generation() == 42
        with pytest.raises(RuntimeError, match="changed while approval was pending"):
            get_skill_grant_store().create_pending_grant(
                "session-1",
                "main",
                _manifest(),
                decision=GrantDecision.LOCAL,
                authorization_generation=41,
            )
    finally:
        set_skill_grant_store(previous)
