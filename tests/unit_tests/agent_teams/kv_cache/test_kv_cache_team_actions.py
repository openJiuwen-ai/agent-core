# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""KVC-only tests for the thin Team terminal-action hooks."""

from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_team_actions
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import TeamKVCActionPlan
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam


@pytest.mark.asyncio
async def test_delete_without_kvc_state_calls_only_baseline_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    baseline_delete = AsyncMock(return_value=True)
    execute_plan = AsyncMock()
    monkeypatch.setattr(manager, "delete_team", baseline_delete)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.execute_action_plan",
        execute_plan,
    )

    assert await kv_cache_team_actions.delete_team(
        manager,
        team_name="team-a",
        session_ids=["session-a"],
        force=True,
    )

    baseline_delete.assert_awaited_once_with(
        team_name="team-a",
        session_ids=["session-a"],
        force=True,
    )
    execute_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_executes_captured_evict_after_baseline_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    kv_cache_team_actions._manifests(manager)[("team-a", "session-a")] = (
        TeamKVCActionPlan(action="offload", root_cache_id="session-a", steps=())
    )
    events: list[str] = []

    async def baseline_delete(**_kwargs) -> bool:
        events.append("baseline-delete")
        await manager.pool.remove("team-a")
        return True

    async def execute_plan(plan, *, reason: str):
        events.append(f"{plan.action}:{reason}")
        return []

    monkeypatch.setattr(manager, "delete_team", baseline_delete)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.execute_action_plan",
        execute_plan,
    )

    assert await kv_cache_team_actions.delete_team(
        manager,
        team_name="team-a",
        session_ids=["session-a"],
        force=True,
    )

    assert events == ["baseline-delete", "evict:team_delete"]
    assert not kv_cache_team_actions._existing_manifests(manager)


@pytest.mark.asyncio
async def test_delete_snapshots_live_registry_before_baseline_force_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    await manager.pool.add(
        ActiveTeam(
            team_name="team-a",
            agent=object(),
            current_session_id="session-a",
        )
    )
    plan = TeamKVCActionPlan(action="evict", root_cache_id="session-a", steps=())
    events: list[str] = []

    async def build_plan(_agent, action):
        events.append(f"snapshot:{action}")
        return plan

    async def baseline_delete(**_kwargs) -> bool:
        events.append("baseline-delete")
        await manager.pool.remove("team-a")
        return True

    async def execute_plan(_plan, *, reason: str):
        events.append(f"execute:{reason}")
        return []

    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.build_action_plan",
        build_plan,
    )
    monkeypatch.setattr(manager, "delete_team", baseline_delete)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.execute_action_plan",
        execute_plan,
    )

    assert await kv_cache_team_actions.delete_team(
        manager,
        team_name="team-a",
        session_ids=["session-a"],
        force=True,
    )

    assert events == ["snapshot:evict", "baseline-delete", "execute:team_delete"]


@pytest.mark.asyncio
async def test_delete_failure_does_not_evict_or_discard_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamRuntimeManager()
    plan = TeamKVCActionPlan(action="offload", root_cache_id="session-a", steps=())
    kv_cache_team_actions._manifests(manager)[("team-a", "session-a")] = plan
    execute_plan = AsyncMock()
    monkeypatch.setattr(manager, "delete_team", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "openjiuwen.agent_teams.kv_cache.kv_cache_hooks.execute_action_plan",
        execute_plan,
    )

    assert not await kv_cache_team_actions.delete_team(
        manager,
        team_name="team-a",
        session_ids=["session-a"],
        force=True,
    )

    execute_plan.assert_not_awaited()
    assert kv_cache_team_actions._existing_manifests(manager)[
        ("team-a", "session-a")
    ] is plan
