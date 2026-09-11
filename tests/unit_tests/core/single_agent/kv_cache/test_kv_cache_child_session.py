# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-agent child Session KVC isolation tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.kv_cache import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    KVCacheAffinityConfig,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.kv_cache import kv_cache_child_session


def _agent(*, enabled: bool) -> SimpleNamespace:
    config = SimpleNamespace(
        kv_cache_affinity_config=KVCacheAffinityConfig(
            enable_kv_cache_affinity=enabled
        )
    )
    return SimpleNamespace(config=lambda: config)


def test_child_session_hook_is_strict_noop_when_affinity_disabled() -> None:
    parent_session = MagicMock()

    kwargs = kv_cache_child_session.build_child_session_kwargs(
        _agent(enabled=False),
        parent_session,
    )

    assert kwargs == {}
    parent_session.get_envs.assert_not_called()
    parent_session.get_session_id.assert_not_called()


def test_child_session_hook_injects_parent_lineage_when_enabled() -> None:
    parent_session = MagicMock()
    parent_session.get_envs.return_value = {"existing": "value"}
    parent_session.get_session_id.return_value = "parent-session"
    runtime = parent_session.get_kv_cache_runtime.return_value

    kwargs = kv_cache_child_session.build_child_session_kwargs(
        _agent(enabled=True),
        parent_session,
    )

    assert kwargs == {
        "envs": {
            "existing": "value",
            KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV: "parent-session",
        },
        "parent_session_id": "parent-session",
        "kv_cache_runtime": runtime,
    }


def test_child_session_hook_uses_team_member_cache_identity() -> None:
    parent_session = Session(session_id="product-session", envs={"existing": "value"})
    parent_session.set_team_cache_scope(team_id="team-a", agent_id="member-a")

    kwargs = kv_cache_child_session.build_child_session_kwargs(
        _agent(enabled=True),
        parent_session,
    )

    assert kwargs["envs"]["existing"] == "value"
    assert kwargs["envs"][KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV] == (
        "team:product-session:team:team-a:member:member-a"
    )
