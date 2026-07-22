# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ordering tests for Team KVC lifecycle hooks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.kv_cache.kv_cache_lifecycle import (
    TeamKVCacheRegistry,
    TeamKVCState,
    register_harness_binding,
)
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.runtime.dispatch import RunAction, RunActionKind
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig, KVCacheIdentity
from openjiuwen.core.runner.team_runner import _TeamRunnerMixin


class _Gate:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    async def close_and_drain(self) -> None:
        self.closed = True
        self.events.append("gate close")

    async def reset(self) -> None:
        self.closed = False
        self.events.append("gate reset")


class _ActionableRegistry:
    def __init__(self, events: list[str], *, actionable: bool = True) -> None:
        self.events = events
        self.actionable = actionable
        self.registration_frozen = False

    async def has_actionable_records(self, action: str) -> bool:
        return self.actionable

    async def has_actionable_member(self, member_id: str, action: str) -> bool:
        return self.actionable

    async def has_records(self) -> bool:
        return self.actionable

    async def freeze_registration(self) -> None:
        self.registration_frozen = True

    async def unfreeze_registration(self) -> None:
        self.registration_frozen = False
        self.events.append("unfreeze")

    async def offload_all(self, *, reason: str) -> list[bool]:
        self.events.append("offload")
        return [True]

    async def prefetch_offloaded(self, *, reason: str) -> list[bool]:
        self.events.extend(["prefetch", "ACTIVE"])
        return [True]

    async def set_closing(self) -> None:
        return None

    async def evict_all(self, *, reason: str) -> list[bool]:
        self.events.append("evict_all")
        return [True]

    async def clear(self) -> None:
        self.actionable = False
        self.events.append("clear")


class _ReadyMember:
    def __init__(self, events: list[str]) -> None:
        self._status = MemberStatus.BUSY
        self.events = events

    async def status(self) -> MemberStatus:
        return self._status

    async def update_status(self, status: MemberStatus) -> bool:
        self._status = status
        self.events.append(f"status:{status.name}")
        return True


class _ReadyHarness:
    def __init__(self, events: list[str], model: object, *, enabled: bool) -> None:
        self.events = events
        self.model = model
        self.stop_calls = 0
        self.deep_config = SimpleNamespace(
            kv_cache_affinity_config=KVCacheAffinityConfig(enable_kv_cache_affinity=enabled)
        )

    @staticmethod
    def current_kv_cache_identity() -> KVCacheIdentity:
        return KVCacheIdentity(
            cache_id="team:team-sid:team:team-a:member:coder-card",
            parent_cache_id="team-sid",
        )

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("harness stop")


class _ReadyStreamController:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stop_calls = 0
        self.stream_queue = object()

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("stream stop")

    async def drain_agent_task(self) -> None:
        self.events.append("drain")

    async def pause_agent(self) -> None:
        self.events.append("pause agent")

    def close_stream(self) -> None:
        self.events.append("close stream")


class _ReadyAgent:
    def __init__(self, events: list[str], registry: TeamKVCacheRegistry, harness: _ReadyHarness) -> None:
        self.member_name = "coder"
        self.card = SimpleNamespace(id="coder-card")
        self.role = TeamRole.TEAMMATE
        self.team_member = _ReadyMember(events)
        self.resources = SimpleNamespace(
            team_kv_cache_registry=registry, harness=harness, memory_manager=None
        )
        self.infra = SimpleNamespace(messager=None)
        self.stream_controller = _ReadyStreamController(events)
        self.session_manager = SimpleNamespace(
            release_session=lambda: events.append("release"), team_session=None
        )
        self.coordination = CoordinationKernel(self)
        self.coordination._lifecycle_state = "running"

    def persist_allocator_state(self) -> None:
        self.stream_controller.events.append("persist")

    async def pause_coordination(self) -> None:
        await self.coordination.pause()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled-supported"])
async def test_ready_real_finalize_round_then_member_teardown_once(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    events: list[str] = []
    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        offload_kvc=AsyncMock(return_value=True),
        prefetch_kvc=AsyncMock(return_value=True),
        evict_kvc=AsyncMock(return_value=True),
    )
    registry = TeamKVCacheRegistry()
    harness = _ReadyHarness(events, model, enabled=enabled)
    record = await register_harness_binding(
        registry, member_id="coder-card", member_name="coder", harness=harness
    )
    original_mark = registry.mark_ready_resident

    async def _mark(member_id: str) -> None:
        events.append("mark ready")
        await original_mark(member_id)

    monkeypatch.setattr(registry, "mark_ready_resident", _mark)
    agent = _ReadyAgent(events, registry, harness)

    await agent.coordination.finalize_round()
    await TeamRuntimeManager.finalize_member(agent)

    assert events == [
        "stream stop", "harness stop", "pause agent", "persist", "close stream",
        "release", "mark ready", "status:READY",
    ]
    assert agent.stream_controller.stop_calls == 1
    assert harness.stop_calls == 1
    assert events.count("release") == 1
    model.offload_kvc.assert_not_awaited()
    model.prefetch_kvc.assert_not_awaited()
    model.evict_kvc.assert_not_awaited()
    if enabled:
        assert record is not None and record.state is TeamKVCState.READY_RESIDENT
    else:
        assert record is None
        assert await registry.snapshot() == []


class _StreamController:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def drain_agent_task(self) -> None:
        self.events.append("Leader drain")

    async def pause_agent(self) -> None:
        self.events.append("Leader pause")

    def close_stream(self) -> None:
        return None


class _SpawnManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.spawned_handles = {"coder": object()}

    async def cancel_recovery_tasks(self) -> None:
        return None

    async def shutdown_all_handles(self) -> None:
        self.events.append("Teammates done")


class _Harness:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def dispose(self) -> None:
        self.events.append("dispose")


class _SessionManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.team_session = None

    def release_session(self) -> None:
        self.events.append("release")


class _LeaderAgent:
    def __init__(
        self, events: list[str], *, actionable: bool = False, shutdown_requested: bool = False
    ) -> None:
        self.events = events
        self.team_name = "team-a"
        self.member_name = "leader"
        self.role = TeamRole.LEADER
        self.lifecycle = "persistent"
        self._shutdown_requested = shutdown_requested
        self.resources = SimpleNamespace(
            memory_manager=None,
            harness=_Harness(events),
            team_kv_cache_registry=_ActionableRegistry(events, actionable=actionable),
        )
        self.infra = SimpleNamespace(messager=None, team_backend=None)
        self.spawn_manager = _SpawnManager(events)
        self.stream_controller = _StreamController(events)
        self.session_manager = _SessionManager(events)
        self.coordination = CoordinationKernel(self)
        self.coordination._lifecycle_state = "running"

    def persist_allocator_state(self) -> None:
        return None

    async def pause_coordination(self) -> None:
        await self.coordination.pause()

    async def stop_coordination(self) -> None:
        await self.coordination.stop()

    async def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested


@pytest.mark.asyncio
async def test_explicit_pause_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events, actionable=True)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, _Gate(events)))

    assert await manager.pause(team_name="team-a", session_id="sess-a") is True
    assert events == ["Leader pause", "Teammates done", "release"]


@pytest.mark.asyncio
async def test_explicit_pause_without_actionable_record_keeps_baseline_gate_order() -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, _Gate(events)))

    assert await manager.pause(team_name="team-a", session_id="sess-a") is True
    assert events == ["Leader pause", "Teammates done", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown_requested", [False, True], ids=["pause", "stop"])
async def test_leader_finalize_without_actionable_record_does_not_predrain_gate(
    shutdown_requested: bool,
) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events, shutdown_requested=shutdown_requested)
    gate = _Gate(events)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, gate))

    await manager.finalize(team_name="team-a", session_id="sess-a")

    assert "gate close" not in events
    expected_round_action = "Leader drain" if shutdown_requested else "Leader pause"
    assert events[:2] == [expected_round_action, "Teammates done"]


@pytest.mark.asyncio
async def test_enabled_finalize_pause_and_runner_post_finalize_drain_gate_once() -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events, actionable=True)
    gate = _Gate(events)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, gate))

    await manager.finalize(team_name="team-a", session_id="sess-a")
    runner = SimpleNamespace(_get_team_runtime_manager=lambda: manager)
    await _TeamRunnerMixin._close_team_interact_gate(
        runner, team_name="team-a", session_id="sess-a"
    )

    assert events.count("gate close") == 1
    assert events == ["Leader pause", "Teammates done", "release", "gate close"]


@pytest.mark.asyncio
async def test_resume_from_pause_keeps_baseline_gate_reset_without_kvc_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = SimpleNamespace()
    entry = ActiveTeam("team-a", agent, "sess-a", RuntimeState.PAUSED, _Gate(events))

    async def _pre_run(session: Any, inputs: object) -> None:
        return None

    monkeypatch.setattr(TeamRuntimeManager, "_pre_run_with_inputs", staticmethod(_pre_run))

    activation = await manager._apply_action(
        RunAction(kind=RunActionKind.RESUME_FROM_PAUSE, require_spec=False),
        spec=SimpleNamespace(team_name="team-a"),
        team_session=SimpleNamespace(get_session_id=lambda: "sess-a"),
        pool_entry=entry,
        inputs={"query": "hello"},
    )
    events.append("first inference")

    assert activation.agent is agent
    assert entry.state is RuntimeState.RUNNING
    assert events == ["gate reset", "first inference"]


@pytest.mark.asyncio
async def test_resume_without_actionable_record_skips_prefetch_and_resets_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = SimpleNamespace(
        resources=SimpleNamespace(team_kv_cache_registry=_ActionableRegistry(events, actionable=False))
    )
    entry = ActiveTeam("team-a", agent, "sess-a", RuntimeState.PAUSED, _Gate(events))
    monkeypatch.setattr(
        TeamRuntimeManager, "_pre_run_with_inputs", staticmethod(AsyncMock(return_value=None))
    )

    await manager._apply_action(
        RunAction(kind=RunActionKind.RESUME_FROM_PAUSE, require_spec=False),
        spec=SimpleNamespace(team_name="team-a"),
        team_session=SimpleNamespace(get_session_id=lambda: "sess-a"),
        pool_entry=entry,
        inputs={"query": "hello"},
    )

    assert entry.state is RuntimeState.RUNNING
    assert events == ["gate reset"]


class _Member:
    def __init__(self, status: MemberStatus, events: list[str]) -> None:
        self._status = status
        self.events = events

    async def status(self) -> MemberStatus:
        return self._status

    async def update_status(self, status: MemberStatus) -> bool:
        self.events.append(status.name)
        self._status = status
        return True


class _MemberAgent:
    def __init__(self, status: MemberStatus, events: list[str], *, actionable: bool = False) -> None:
        self.member_name = "coder"
        self.card = SimpleNamespace(id="coder")
        self.team_member = _Member(status, events)
        self.events = events
        self.stop_calls = 0
        self.on_quiesced_passed = False
        self.resources = SimpleNamespace(
            team_kv_cache_registry=_ActionableRegistry(events, actionable=actionable)
        )

    async def stop_coordination(self, *, on_quiesced=None) -> None:
        self.stop_calls += 1
        self.on_quiesced_passed = on_quiesced is not None
        self.events.append("drain")
        if on_quiesced is not None:
            await on_quiesced()
        self.events.append("dispose")

    async def pause_coordination(self) -> None:
        self.events.append("pause")

@pytest.mark.asyncio
async def test_member_shutdown_requested_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    agent = _MemberAgent(MemberStatus.SHUTDOWN_REQUESTED, events, actionable=True)

    async def _evict_member(_agent: Any, *, reason: str) -> None:
        events.append("evict_member")

    monkeypatch.setattr("openjiuwen.agent_teams.kv_cache.kv_cache_hooks.evict_member", _evict_member)

    await TeamRuntimeManager.finalize_member(agent)
    assert events == ["drain", "evict_member", "dispose", "SHUTDOWN"]


@pytest.mark.asyncio
async def test_shutdown_self_status_still_evicts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    agent = _MemberAgent(MemberStatus.SHUTDOWN, events, actionable=True)
    evict_calls = 0

    async def _evict_member(_agent: Any, *, reason: str) -> None:
        nonlocal evict_calls
        evict_calls += 1
        events.append("evict_member")

    monkeypatch.setattr("openjiuwen.agent_teams.kv_cache.kv_cache_hooks.evict_member", _evict_member)

    await TeamRuntimeManager.finalize_member(agent)
    assert evict_calls == 1
    assert events == ["drain", "evict_member", "dispose"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MemberStatus.SHUTDOWN_REQUESTED, MemberStatus.SHUTDOWN])
async def test_member_shutdown_without_actionable_binding_uses_plain_baseline_stop(
    status: MemberStatus,
) -> None:
    events: list[str] = []
    agent = _MemberAgent(status, events)

    await TeamRuntimeManager.finalize_member(agent)

    expected = ["drain", "dispose"]
    if status is MemberStatus.SHUTDOWN_REQUESTED:
        expected.append("SHUTDOWN")
    assert events == expected
    assert agent.stop_calls == 1
    assert agent.on_quiesced_passed is False


@pytest.mark.asyncio
async def test_member_shutdown_evict_failure_does_not_block_dispose_or_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    agent = _MemberAgent(MemberStatus.SHUTDOWN_REQUESTED, events, actionable=True)

    async def _failing_evict(_agent: Any, *, reason: str) -> None:
        events.append("evict_member")
        raise RuntimeError("evict failed")

    async def _stop_coordination(*, on_quiesced=None) -> None:
        agent.stop_calls += 1
        agent.on_quiesced_passed = on_quiesced is not None
        events.append("drain")
        if on_quiesced is not None:
            try:
                await on_quiesced()
            except RuntimeError:
                pass
        events.append("dispose")

    agent.stop_coordination = _stop_coordination
    monkeypatch.setattr("openjiuwen.agent_teams.kv_cache.kv_cache_hooks.evict_member", _failing_evict)

    await TeamRuntimeManager.finalize_member(agent)

    assert events == ["drain", "evict_member", "dispose", "SHUTDOWN"]
    assert agent.stop_calls == 1


@pytest.mark.asyncio
async def test_whole_team_stop_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events, actionable=True)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, _Gate(events)))

    original_remove = manager.pool.remove

    async def _remove(team_name: str) -> None:
        await original_remove(team_name)
        events.append("pool remove")

    monkeypatch.setattr(manager.pool, "remove", _remove)

    assert await manager.stop_team(team_name="team-a", session_id="sess-a") is True
    assert events == [
        "Leader drain",
        "Teammates done",
        "dispose",
        "release",
        "pool remove",
    ]


@pytest.mark.asyncio
async def test_whole_team_stop_without_actionable_record_keeps_baseline_gate_order() -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events)
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, _Gate(events)))

    assert await manager.stop_team(team_name="team-a", session_id="sess-a") is True
    assert events == ["Leader drain", "Teammates done", "dispose", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MemberStatus.PAUSED, MemberStatus.STOPPED])
async def test_paused_or_stopped_teammate_local_teardown_does_not_evict(
    monkeypatch: pytest.MonkeyPatch,
    status: MemberStatus,
) -> None:
    events: list[str] = []
    agent = _MemberAgent(status, events)

    async def _evict_member(_agent: Any, *, reason: str) -> None:
        raise AssertionError("PAUSED/STOPPED local teardown must not evict")

    monkeypatch.setattr("openjiuwen.agent_teams.kv_cache.kv_cache_hooks.evict_member", _evict_member)

    await TeamRuntimeManager.finalize_member(agent)
    assert events == ["drain", "dispose"]


@pytest.mark.asyncio
async def test_kvc_hook_failure_does_not_change_pause_result() -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    agent = _LeaderAgent(events)
    agent.resources.team_kv_cache_registry = SimpleNamespace(
        has_actionable_records=AsyncMock(side_effect=RuntimeError("predicate failed")),
        freeze_registration=AsyncMock(side_effect=RuntimeError("freeze failed")),
        offload_all=AsyncMock(side_effect=RuntimeError("offload failed")),
    )
    await manager.pool.add(ActiveTeam("team-a", agent, "sess-a", RuntimeState.RUNNING, _Gate(events)))

    assert await manager.pause(team_name="team-a", session_id="sess-a") is True
    assert events == ["Leader pause", "Teammates done", "release"]


@pytest.mark.asyncio
async def test_stop_entry_paths_reuse_stop_team(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    manager = TeamRuntimeManager()
    await manager.pool.add(
        ActiveTeam("team-a", SimpleNamespace(), "sess-a", RuntimeState.RUNNING, _Gate(events))
    )

    async def _stop_team(*, team_name: str, session_id: str) -> bool:
        events.extend(["Team quiesce", f"stop_team:{team_name}:{session_id}"])
        await manager.pool.remove(team_name)
        return True

    monkeypatch.setattr(manager, "stop_team", _stop_team)

    fake_db = SimpleNamespace(
        initialize=AsyncMock(),
        drop_session_tables_by_id=AsyncMock(return_value=[]),
        team=SimpleNamespace(delete_team=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
        lambda *_args, **_kwargs: fake_db,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
        AsyncMock(return_value=SimpleNamespace(team_names=["team-a"], db_config=None)),
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager._resolve_any_team_session_release_info",
        AsyncMock(return_value=SimpleNamespace(team_names=["team-a"], db_config=None)),
    )
    monkeypatch.setattr("openjiuwen.core.session.checkpointer.CheckpointerFactory.get_checkpointer", lambda: SimpleNamespace(session_exists=AsyncMock(return_value=True), release=AsyncMock()))
    monkeypatch.setattr("openjiuwen.agent_teams.runtime.manager.remove_session_worktrees", AsyncMock(return_value=True))

    assert await manager.delete_team("team-a", ["sess-a"], force=True) is True
    assert events[:2] == ["Team quiesce", "stop_team:team-a:sess-a"]

    events.clear()
    await manager.pool.add(
        ActiveTeam("team-b", SimpleNamespace(), "sess-b", RuntimeState.RUNNING, _Gate(events))
    )
    await manager.release_session("sess-b", force=True)
    assert events[:2] == ["Team quiesce", "stop_team:team-b:sess-b"]

    events.clear()
    await manager.pool.add(
        ActiveTeam("team-c", SimpleNamespace(), "old-sess", RuntimeState.RUNNING, _Gate(events))
    )
    monkeypatch.setattr(manager, "_inspect_session", AsyncMock(return_value=(False, False, None)))
    monkeypatch.setattr(
        manager,
        "_apply_action",
        AsyncMock(return_value=SimpleNamespace(agent=SimpleNamespace(), session=SimpleNamespace(get_session_id=lambda: "new-sess"), action=None)),
    )
    await manager.activate(SimpleNamespace(team_name="team-c"), "new-sess")
    assert events[:2] == ["Team quiesce", "stop_team:team-c:old-sess"]
