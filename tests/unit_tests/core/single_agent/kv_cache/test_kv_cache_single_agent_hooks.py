# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-agent KVC hook isolation tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.foundation.kv_cache import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    KVCacheAffinityConfig,
)
from openjiuwen.core.single_agent.kv_cache import kv_cache_hooks


def _agent(*, enabled: bool) -> SimpleNamespace:
    config = SimpleNamespace(
        kv_cache_affinity_config=KVCacheAffinityConfig(
            enable_kv_cache_affinity=enabled
        )
    )
    return SimpleNamespace(config=lambda: config)


def test_child_session_hook_is_strict_noop_when_affinity_disabled() -> None:
    parent_session = MagicMock()

    kwargs = kv_cache_hooks.build_child_session_kwargs(
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

    kwargs = kv_cache_hooks.build_child_session_kwargs(
        _agent(enabled=True),
        parent_session,
    )

    assert kwargs == {
        "envs": {
            "existing": "value",
            KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV: "parent-session",
        }
    }
