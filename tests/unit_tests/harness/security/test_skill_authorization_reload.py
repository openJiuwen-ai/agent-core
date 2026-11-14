# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""权限配置热更新时 Grant 与委托审批状态的联动清理。"""

from __future__ import annotations

import pytest

from openjiuwen.harness.security.skill_authorization.grant_store import (
    SkillGrantStore,
    peek_skill_grant_store,
    set_skill_grant_store,
)
from openjiuwen.harness.security.skill_authorization.models import (
    GrantDecision,
    SkillManifest,
    SkillTrustLevel,
)
from openjiuwen.harness.security.skill_authorization.reload import (
    sync_grants_on_permissions_reload,
)


def _manifest() -> SkillManifest:
    return SkillManifest(
        skill_name="demo",
        source="hub",
        version="1.0.0",
        trust=SkillTrustLevel.BUILTIN,
        permissions_hash="permissions-v1",
        skill_md_hash="skill-v1",
        overlay={"tools": {"write_file": "ask"}},
    )


def _activate(store: SkillGrantStore, *, manifest: SkillManifest | None = None) -> None:
    manifest = manifest or _manifest()
    store.create_pending_grant(
        "session-1",
        "main",
        manifest,
        decision=GrantDecision.SESSION,
        approval_tool_call_id="load-1",
    )
    assert (
        store.activate_pending(
            "session-1",
            "main",
            manifest.skill_name,
            manifest,
            approval_tool_call_id="load-1",
        )
        is not None
    )


@pytest.fixture
def isolated_global_store():
    previous = peek_skill_grant_store()
    store = SkillGrantStore()
    set_skill_grant_store(store)
    try:
        yield store
    finally:
        set_skill_grant_store(previous)


def test_sync_on_reload_clears_grants_when_feature_turns_off(
    isolated_global_store: SkillGrantStore,
) -> None:
    _activate(isolated_global_store)

    sync_grants_on_permissions_reload(
        {"skill_authorization": {"enabled": True}},
        {"skill_authorization": {"enabled": False}},
    )

    assert isolated_global_store.list_grants("session-1") == []


def test_sync_on_reload_keeps_grants_on_enable_or_ordinary_change(
    isolated_global_store: SkillGrantStore,
) -> None:
    _activate(isolated_global_store)

    sync_grants_on_permissions_reload(
        {"skill_authorization": {"enabled": False}},
        {"skill_authorization": {"enabled": True}},
    )
    sync_grants_on_permissions_reload(
        {"skill_authorization": {"enabled": True}},
        {"skill_authorization": {"enabled": True}},
    )

    assert isolated_global_store.list_grants("session-1")
