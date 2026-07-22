# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""KVC identity binding tests for TeamHarness and team child sessions."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.kv_cache import kv_cache_hooks
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.foundation.kv_cache import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    KV_CACHE_AFFINITY_SESSION_ID_ENV,
    KVCacheIdentity,
    KVCacheAffinityConfig,
    resolve_session_lineage,
)
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _stub_native(*, model: Any = None) -> MagicMock:
    native = MagicMock(name="NativeHarness")
    native.deep_config = SimpleNamespace(model=model)
    native.loop_session = None
    return native


def _make_harness(native: MagicMock) -> TeamHarness:
    return TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        native,
        role=TeamRole.LEADER,
        member_name="leader",
    )


def test_team_child_session_sets_member_level_kv_cache_identity() -> None:
    team_session = create_agent_team_session(session_id="team-sid", team_id="team-a")

    child = team_session.create_agent_session(
        card=AgentCard(id="coder", name="Coder"),
    )

    assert child.get_session_id() == "team-sid"
    assert child.get_cache_identity() == KVCacheIdentity(
        cache_id="team:team-sid:team:team-a:member:coder",
        parent_cache_id="team-sid",
    )


def test_standalone_worker_session_identity_is_self_parented() -> None:
    session = Session(session_id="child-session")

    assert session.get_cache_identity() == KVCacheIdentity(
        cache_id="child-session",
        parent_cache_id="child-session",
    )


def test_session_parent_binding_is_set_once() -> None:
    session = Session(session_id="child-session")

    session.bind_parent_session_id(" product-session ")
    session.bind_parent_session_id("product-session")

    assert session.get_cache_identity() == KVCacheIdentity(
        cache_id="child-session",
        parent_cache_id="product-session",
    )
    with pytest.raises(ValueError, match="cannot rebind"):
        session.bind_parent_session_id("another-session")


def test_team_child_identity_does_not_depend_on_source_metadata() -> None:
    team_session = create_agent_team_session(
        session_id="team-sid",
        team_id="team-a",
        source_metadata_enabled=False,
    )

    child = team_session.create_agent_session(
        card=AgentCard(id="coder", name="Coder"),
    )

    assert child.get_cache_identity() == KVCacheIdentity(
        cache_id="team:team-sid:team:team-a:member:coder",
        parent_cache_id="team-sid",
    )


def test_resolve_session_lineage_prefers_kv_cache_identity_env() -> None:
    team_session = create_agent_team_session(session_id="team-sid", team_id="team-a")
    child = team_session.create_agent_session(
        card=AgentCard(id="reviewer", name="Reviewer"),
    )

    session_id, parent_session_id = resolve_session_lineage(child)

    assert session_id == "team:team-sid:team:team-a:member:reviewer"
    assert parent_session_id == "team-sid"


def test_team_harness_child_session_owns_identity_without_kvc_env() -> None:
    team_session = create_agent_team_session(session_id="team-sid", team_id="team-a")
    native = _stub_native()
    native.card = AgentCard(id="coder", name="Coder")
    spec = MagicMock(name="DeepAgentSpec")
    spec.kv_cache_affinity_config = KVCacheAffinityConfig(
        enable_kv_cache_affinity=True
    )
    harness = TeamHarness(
        spec,
        None,
        native,
        role=TeamRole.LEADER,
        member_name="leader",
    )

    child = harness._make_child_session(team_session)

    assert child.get_env(KV_CACHE_AFFINITY_SESSION_ID_ENV) is None
    assert child.get_env(KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV) is None
    assert child.get_cache_identity() == KVCacheIdentity(
        cache_id="team:team-sid:team:team-a:member:coder",
        parent_cache_id="team-sid",
    )


def test_team_harness_does_not_inject_identity_when_affinity_disabled() -> None:
    team_session = create_agent_team_session(session_id="team-sid", team_id="team-a")
    native = _stub_native()
    native.card = AgentCard(id="coder", name="Coder")
    spec = MagicMock(name="DeepAgentSpec")
    spec.kv_cache_affinity_config = KVCacheAffinityConfig()
    harness = TeamHarness(
        spec,
        None,
        native,
        role=TeamRole.LEADER,
        member_name="leader",
    )

    child = harness._make_child_session(team_session)

    assert child.get_env(KV_CACHE_AFFINITY_SESSION_ID_ENV) is None
    assert child.get_env(KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV) is None


@pytest.mark.asyncio
async def test_start_keeps_baseline_standalone_session_creation() -> None:
    native = _stub_native()
    native.state = HarnessState.IDLE
    native.card = AgentCard(id="stateful-worker", name="Stateful Worker")
    native.start = AsyncMock()
    harness = TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        native,
        role=TeamRole.WORKER,
        member_name="stateful-worker",
    )
    await harness.start()

    worker_session = native.start.await_args.kwargs["session"]
    identity = worker_session.get_cache_identity()
    assert identity.cache_id == worker_session.get_session_id()
    assert identity.parent_cache_id == worker_session.get_session_id()


@pytest.mark.asyncio
@pytest.mark.parametrize(("enabled", "expected_calls"), [(False, 0), (True, 1)])
async def test_run_once_uses_registered_kvc_session_hooks(
    enabled: bool,
    expected_calls: int,
) -> None:
    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        evict_kvc=AsyncMock(return_value=True),
    )
    native = _stub_native(model=model)
    native.state = HarnessState.IDLE
    native.card = AgentCard(id="worker", name="Worker")
    native.run_once = AsyncMock(return_value={"output": "ok"})
    native.deep_config = SimpleNamespace(
        model=model,
        kv_cache_affinity_config=KVCacheAffinityConfig(
            enable_kv_cache_affinity=enabled,
        ),
    )
    harness = TeamHarness(
        MagicMock(name="DeepAgentSpec"),
        None,
        native,
        role=TeamRole.WORKER,
        member_name="worker",
    )

    configured = kv_cache_hooks.configure_harness_session_hooks(
        harness,
        product_session_id="product-session",
        evict_on_finish=True,
        reason="test-worker-finish",
        owner_id="worker",
    )

    assert configured is enabled
    assert await harness.run_once("hello") == {"output": "ok"}

    session = native.run_once.await_args.kwargs["session"]
    assert session.get_cache_identity() == KVCacheIdentity(
        cache_id=session.get_session_id(),
        parent_cache_id=(
            "product-session" if enabled else session.get_session_id()
        ),
    )
    assert model.evict_kvc.await_count == expected_calls
    if enabled:
        assert model.evict_kvc.await_args.kwargs["session_id"] == session.get_session_id()
        assert model.evict_kvc.await_args.kwargs["parent_session_id"] == "product-session"
