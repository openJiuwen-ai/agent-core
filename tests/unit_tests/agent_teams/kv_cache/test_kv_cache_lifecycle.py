# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_team_actions
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    KVCacheRuntimeBinding,
    TeamKVCacheRegistry,
    TeamKVCActionPlan,
    TeamKVCState,
    cancel_pending_signal_tasks,
    dispatch_action_plan,
    execute_action_plan,
    is_binding_manageable,
)
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.core.foundation.kv_cache import KVCacheIdentity


def _binding(model: object | None = None) -> KVCacheRuntimeBinding:
    if model is None:
        model = SimpleNamespace(
            supports_kv_cache_affinity=lambda: True,
            offload_kvc=AsyncMock(return_value=True),
            prefetch_kvc=AsyncMock(return_value=True),
            evict_kvc=AsyncMock(return_value=True),
        )
    return KVCacheRuntimeBinding(
        identity=KVCacheIdentity(cache_id="team:sid:member:coder", parent_cache_id="sid"),
        model=model,
        enabled=True,
    )


@pytest.mark.asyncio
async def test_registration_frozen_allows_offload_and_prefetch_but_blocks_register() -> None:
    registry = TeamKVCacheRegistry()
    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        offload_kvc=AsyncMock(return_value=True),
        prefetch_kvc=AsyncMock(return_value=True),
        evict_kvc=AsyncMock(return_value=True),
    )
    record = await registry.register_or_update(
        member_id="coder",
        member_name="Coder",
        binding=_binding(model),
    )
    assert record is not None

    await registry.freeze_registration()
    skipped = await registry.register_or_update(
        member_id="reviewer",
        member_name="Reviewer",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity(cache_id="team:sid:member:reviewer", parent_cache_id="sid"),
            model=model,
            enabled=True,
        ),
    )
    assert skipped is None

    assert await registry.offload_member("coder", reason="pause") is True
    assert record.state is TeamKVCState.OFFLOADED
    assert await registry.prefetch_member("coder", reason="resume") is True
    assert record.state is TeamKVCState.ACTIVE


@pytest.mark.asyncio
async def test_closing_allows_only_evict() -> None:
    registry = TeamKVCacheRegistry()
    record = await registry.register_or_update(
        member_id="coder",
        member_name="Coder",
        binding=_binding(),
    )
    assert record is not None

    await registry.set_closing()

    assert await registry.offload_member("coder", reason="pause") is False
    assert record.state is TeamKVCState.ACTIVE
    assert await registry.evict_member("coder", reason="stop") is True
    assert record.state is TeamKVCState.EVICTED


@pytest.mark.asyncio
async def test_prefetch_failure_marks_record_active() -> None:
    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        offload_kvc=AsyncMock(return_value=True),
        prefetch_kvc=AsyncMock(return_value=False),
        evict_kvc=AsyncMock(return_value=True),
    )
    registry = TeamKVCacheRegistry()
    record = await registry.register_or_update(
        member_id="coder",
        member_name="Coder",
        binding=_binding(model),
    )
    assert record is not None

    assert await registry.offload_member("coder", reason="pause") is True
    assert record.state is TeamKVCState.OFFLOADED
    assert await registry.prefetch_member("coder", reason="resume") is False
    assert record.state is TeamKVCState.ACTIVE


@pytest.mark.parametrize(
    "model",
    [
        SimpleNamespace(evict_kvc=AsyncMock(return_value=True)),
        SimpleNamespace(supports_kv_cache_affinity=True, evict_kvc=AsyncMock(return_value=True)),
        SimpleNamespace(
            supports_kv_cache_affinity=lambda: (_ for _ in ()).throw(RuntimeError("capability broken")),
            evict_kvc=AsyncMock(return_value=True),
        ),
    ],
)
def test_binding_capability_fail_closed(model: object) -> None:
    assert is_binding_manageable(_binding(model)) is False


def _routed_model(api_base: str, model_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        model_client_config=SimpleNamespace(
            client_provider="AscendAffinity",
            api_base=api_base,
        ),
        model_config=SimpleNamespace(model_name=model_name),
        supports_kv_cache_affinity=lambda: True,
        offload_kvc=AsyncMock(return_value=True),
        prefetch_kvc=AsyncMock(return_value=True),
        evict_kvc=AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_historical_team_prefetch_reuses_snapshotted_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    kv_cache_team_actions._manifests(manager)[("team-a", "session-a")] = TeamKVCActionPlan(
        action="offload",
        root_cache_id="session-a",
        steps=(),
    )
    dispatched: list[TeamKVCActionPlan] = []

    def _dispatch(plan: TeamKVCActionPlan, *, reason: str) -> bool:
        dispatched.append(plan)
        return True

    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.dispatch_action_plan",
        _dispatch,
    )

    assert await kv_cache_team_actions.dispatch_action(
        manager,
        action="prefetch",
        team_name="team-a",
        session_id="session-a",
        reason="history-resume",
    )
    assert [plan.action for plan in dispatched] == ["prefetch"]


@pytest.mark.asyncio
async def test_team_evict_plan_is_handled_without_duplicate_root_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    kv_cache_team_actions._manifests(manager)[("team-a", "session-a")] = TeamKVCActionPlan(
        action="evict",
        root_cache_id="session-a",
        steps=(),
    )
    execute = AsyncMock(return_value=[True, False])
    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.execute_action_plan",
        execute,
    )

    assert await kv_cache_team_actions.execute_action(
        manager,
        action="evict",
        team_name="team-a",
        session_id="session-a",
        reason="session-delete",
    )
    execute.assert_awaited_once()
    assert (
        "team-a",
        "session-a",
    ) not in kv_cache_team_actions._existing_manifests(manager)


@pytest.mark.asyncio
async def test_team_plan_uses_root_for_same_domain_and_child_for_outlier() -> None:
    registry = TeamKVCacheRegistry()
    leader_model = _routed_model("http://engine-a/v1", "model-a")
    same_domain_model = _routed_model("http://engine-a/v1/", "model-a")
    outlier_model = _routed_model("http://engine-b/v1", "model-b")

    await registry.register_or_update(
        member_id="leader",
        member_name="Leader",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity("team:sid:member:leader", "sid"),
            model=leader_model,
            enabled=True,
        ),
        is_leader=True,
    )
    await registry.register_or_update(
        member_id="same",
        member_name="Same",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity("team:sid:member:same", "sid"),
            model=same_domain_model,
            enabled=True,
        ),
    )
    await registry.register_or_update(
        member_id="outlier",
        member_name="Outlier",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity("team:sid:member:outlier", "sid"),
            model=outlier_model,
            enabled=True,
        ),
    )

    plan = await registry.build_action_plan("offload")
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].uses_root_identity is True
    assert plan.steps[0].binding.identity == KVCacheIdentity("sid", "sid")
    assert set(plan.steps[0].member_ids) == {"leader", "same"}
    assert plan.steps[1].binding.identity.cache_id == "team:sid:member:outlier"

    # The immutable plan remains executable after the live registry is gone.
    await registry.clear()
    assert await execute_action_plan(plan, reason="switch") == [True, True]
    leader_model.offload_kvc.assert_awaited_once()
    assert leader_model.offload_kvc.await_args.kwargs["session_id"] == "sid"
    same_domain_model.offload_kvc.assert_not_awaited()
    outlier_model.offload_kvc.assert_awaited_once()
    assert (
        outlier_model.offload_kvc.await_args.kwargs["session_id"]
        == "team:sid:member:outlier"
    )


@pytest.mark.asyncio
async def test_team_offload_plan_dispatch_does_not_wait_for_provider() -> None:
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def _offload(**_: object) -> bool:
        started.set()
        await blocker.wait()
        return True

    model = _routed_model("http://engine-a/v1", "model-a")
    model.offload_kvc = AsyncMock(side_effect=_offload)
    registry = TeamKVCacheRegistry()
    await registry.register_or_update(
        member_id="leader",
        member_name="Leader",
        binding=KVCacheRuntimeBinding(
            identity=KVCacheIdentity("team:sid:member:leader", "sid"),
            model=model,
            enabled=True,
        ),
        is_leader=True,
    )
    plan = await registry.build_action_plan("offload")
    assert plan is not None

    try:
        assert dispatch_action_plan(plan, reason="session-switch") is True
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert blocker.is_set() is False
    finally:
        await cancel_pending_signal_tasks()


@pytest.mark.asyncio
async def test_actionable_predicates_follow_record_state() -> None:
    registry = TeamKVCacheRegistry()
    record = await registry.register_or_update(
        member_id="coder", member_name="Coder", binding=_binding()
    )
    assert record is not None
    assert await registry.has_actionable_records("offload") is True
    assert await registry.has_actionable_member("coder", "evict") is True

    await registry.offload_member("coder", reason="pause")
    assert await registry.has_actionable_records("offload") is False
    assert await registry.has_actionable_records("prefetch") is True

    await registry.evict_member("coder", reason="stop")
    assert await registry.has_actionable_records("prefetch") is False
    assert await registry.has_actionable_member("coder", "evict") is False
    assert await registry.has_records() is True
