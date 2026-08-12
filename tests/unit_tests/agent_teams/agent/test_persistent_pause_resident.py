# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistent pause: abort LLM, kill handles, keep harness PAUSED."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle


@pytest.mark.asyncio
async def test_finalize_round_stops_paused_harness() -> None:
    harness = SimpleNamespace(state=HarnessState.PAUSED, stop=AsyncMock())
    host = SimpleNamespace(
        member_name="office",
        stream_controller=SimpleNamespace(stop=AsyncMock(), stream_queue=object()),
        resources=SimpleNamespace(harness=harness),
    )
    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host

    await kernel.finalize_round()

    host.stream_controller.stop.assert_awaited_once()
    harness.stop.assert_awaited_once()
    assert host.stream_controller.stream_queue is None


@pytest.mark.asyncio
async def test_finalize_round_stops_non_paused_harness() -> None:
    harness = SimpleNamespace(state=HarnessState.RUNNING, stop=AsyncMock())
    host = SimpleNamespace(
        member_name="office",
        stream_controller=SimpleNamespace(stop=AsyncMock(), stream_queue=object()),
        resources=SimpleNamespace(harness=harness),
    )
    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host

    await kernel.finalize_round()

    harness.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_leader_pause_shuts_down_handles_keeps_harness_paused() -> None:
    shutdown_all = AsyncMock()
    cancel_recovery = AsyncMock()
    mark = AsyncMock()
    pause_round = AsyncMock()
    stop = AsyncMock()

    host = SimpleNamespace(
        member_name="office",
        role=TeamRole.LEADER,
        team_name="team-demo",
        spawn_manager=SimpleNamespace(
            cancel_recovery_tasks=cancel_recovery,
            shutdown_all_handles=shutdown_all,
        ),
        persist_allocator_state=MagicMock(),
        resources=SimpleNamespace(
            memory_manager=None,
            harness=SimpleNamespace(
                state=HarnessState.PAUSED,
                stop=stop,
                active_round=SimpleNamespace(
                    pause_requested=False,
                    model_call_ctx=SimpleNamespace(request_abort_stream=MagicMock()),
                ),
            ),
        ),
        infra=SimpleNamespace(messager=None, team_backend=None),
        session_manager=SimpleNamespace(release_session=MagicMock()),
    )

    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host
    kernel._lifecycle_state = "running"
    kernel._scheduler = None
    kernel._event_bus = None
    kernel._persist_team_lifecycle = MagicMock()
    kernel._persist_pending_resume = MagicMock()
    kernel.unsubscribe_transport = AsyncMock()
    kernel.close_stream = MagicMock()
    kernel.pause_agent_round = pause_round
    kernel._await_harness_paused = AsyncMock()
    kernel._mark_live_teammates = mark

    await kernel.pause()

    host.resources.harness.active_round.model_call_ctx.request_abort_stream.assert_called_once()
    assert host.resources.harness.active_round.pause_requested is True
    cancel_recovery.assert_awaited_once()
    shutdown_all.assert_awaited_once()
    mark.assert_awaited_once()
    pause_round.assert_awaited_once()
    stop.assert_not_awaited()
    assert kernel._lifecycle_state == "paused"


@pytest.mark.asyncio
async def test_abort_llm_stream_arms_pause_requested() -> None:
    abort = MagicMock()
    active = SimpleNamespace(pause_requested=False, model_call_ctx=SimpleNamespace(request_abort_stream=abort))
    handle = InProcessSpawnHandle(
        agent_ref=SimpleNamespace(
            resources=SimpleNamespace(harness=SimpleNamespace(active_round=active)),
        ),
    )

    handle.abort_llm_stream()

    assert active.pause_requested is True
    abort.assert_called_once()


@pytest.mark.asyncio
async def test_complete_rejected_while_team_pausing() -> None:
    from openjiuwen.agent_teams.runtime import pause_gate
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

    pause_gate.clear_team_pausing("team-demo")
    pause_gate.mark_team_pausing("team-demo")
    try:
        mgr = TeamTaskManager(
            "team-demo",
            "assistant",
            db=MagicMock(),
            messager=MagicMock(),
        )
        result = await mgr.complete("pos-lg")
        assert not result.ok
        assert "paused" in result.reason
        assert "complete_task" in result.reason
    finally:
        pause_gate.clear_team_pausing("team-demo")


@pytest.mark.asyncio
async def test_start_task_rejected_while_member_pausing() -> None:
    from openjiuwen.agent_teams.runtime import pause_gate
    from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

    pause_gate.clear_team_pausing("team-demo")
    pause_gate.clear_member_pausing("team-demo", "market-insight")
    pause_gate.mark_member_pausing("team-demo", "market-insight")
    try:
        mgr = TeamTaskManager(
            "team-demo",
            "market-insight",
            db=MagicMock(),
            messager=MagicMock(),
        )
        result = await mgr.start_task("debate-mi")
        assert not result.ok
        assert "paused" in result.reason
        assert "start_task" in result.reason
    finally:
        pause_gate.clear_member_pausing("team-demo", "market-insight")


@pytest.mark.asyncio
async def test_on_pause_during_pausing_waits_for_settle() -> None:
    """Second pause() while PAUSING must not ack early."""
    from openjiuwen.agent_teams.harness.native_harness import NativeHarness
    from openjiuwen.agent_teams.harness.state import ActiveRound, HarnessInternalState

    loop = asyncio.get_running_loop()
    first_ack = loop.create_future()
    second_ack = loop.create_future()
    active = ActiveRound(
        round_id=1,
        task_id="t1",
        original_query="q",
        deep_agent=MagicMock(),
        task=MagicMock(done=MagicMock(return_value=False)),
        steering_queue=MagicMock(),
        pause_requested=True,
        pause_acks=[first_ack],
    )
    harness = NativeHarness.__new__(NativeHarness)
    harness._st = HarnessInternalState(phase=HarnessState.PAUSING, active=active)
    harness._ack = MagicMock()  # type: ignore[method-assign]
    harness._transition = AsyncMock()  # type: ignore[method-assign]

    from openjiuwen.agent_teams.harness.control import _CmdPause

    await NativeHarness._on_pause(harness, _CmdPause(ack=second_ack))

    assert not second_ack.done()
    assert second_ack in active.pause_acks
    harness._ack.assert_not_called()


@pytest.mark.asyncio
async def test_team_harness_start_stops_leftover_paused_native() -> None:
    from openjiuwen.agent_teams.harness.team_harness import TeamHarness
    import openjiuwen.agent_teams.harness.team_harness as th_mod

    leftover = SimpleNamespace(
        state=HarnessState.PAUSED,
        stop=AsyncMock(),
        background_task_controller=None,
    )
    fresh_native = SimpleNamespace(
        state=HarnessState.IDLE,
        start=AsyncMock(),
        background_task_controller=None,
    )
    child = SimpleNamespace(pre_run=AsyncMock())
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = leftover
    harness._bg_controller = None
    harness._agent_spec = SimpleNamespace()
    harness._build_context = None
    harness._active_agent_session = None
    harness._native_session_id = "old"
    harness._make_child_session = MagicMock(return_value=child)  # type: ignore[method-assign]
    harness._session_id_of = MagicMock(return_value="team-sess")  # type: ignore[method-assign]
    harness._seed_initial_plan_mode = MagicMock()  # type: ignore[method-assign]

    with patch.object(th_mod, "NativeHarness", return_value=fresh_native):
        with patch.object(th_mod.kv_cache_hooks, "on_harness_session_created", MagicMock()):
            await harness.start(team_session=SimpleNamespace())

    leftover.stop.assert_awaited_once()
    harness._make_child_session.assert_called_once()
    fresh_native.start.assert_awaited_once_with(session=child)
    assert harness._native is fresh_native
    assert harness._native_session_id == "team-sess"


def _make_pause_kernel(harness: object, pending_user_query: str) -> CoordinationKernel:
    """Leader kernel wired for pause(), with a real _persist_pending_resume."""
    host = SimpleNamespace(
        member_name="office",
        role=TeamRole.LEADER,
        team_name="team-demo",
        state=SimpleNamespace(pending_user_query=pending_user_query),
        spawn_manager=SimpleNamespace(
            cancel_recovery_tasks=AsyncMock(),
            shutdown_all_handles=AsyncMock(),
        ),
        persist_allocator_state=MagicMock(),
        resources=SimpleNamespace(memory_manager=None, harness=harness),
        infra=SimpleNamespace(messager=None, team_backend=None),
        session_manager=SimpleNamespace(
            release_session=MagicMock(),
            team_session=SimpleNamespace(),
        ),
    )
    kernel = CoordinationKernel.__new__(CoordinationKernel)
    kernel._host = host
    kernel._lifecycle_state = "running"
    kernel._scheduler = None
    kernel._event_bus = None
    kernel._persist_team_lifecycle = MagicMock()
    kernel.unsubscribe_transport = AsyncMock()
    kernel.close_stream = MagicMock()
    kernel.pause_agent_round = AsyncMock()
    kernel._await_harness_paused = AsyncMock()
    kernel._mark_live_teammates = AsyncMock()
    return kernel


@pytest.mark.asyncio
async def test_pause_writes_empty_pending_resume_when_harness_terminated() -> None:
    """Park lost the round-end race: the marker is still written, empty."""
    harness = SimpleNamespace(state=HarnessState.TERMINATED, active_round=None)
    kernel = _make_pause_kernel(harness, pending_user_query="已完成的问题")

    with patch(
        "openjiuwen.agent_teams.runtime.metadata.merge_pending_resume",
    ) as merge:
        await kernel.pause()

    kernel.pause_agent_round.assert_not_awaited()
    merge.assert_called_once()
    assert merge.call_args.args[2] == {"query": ""}
    assert kernel._lifecycle_state == "paused"


@pytest.mark.asyncio
async def test_pause_persists_paused_query_when_park_succeeds() -> None:
    """In-flight round parked: the marker carries the round's query."""
    harness = SimpleNamespace(
        state=HarnessState.RUNNING,
        paused_query="继续写报告",
        active_round=None,
    )
    kernel = _make_pause_kernel(harness, pending_user_query="别的消息")

    with patch(
        "openjiuwen.agent_teams.runtime.metadata.merge_pending_resume",
    ) as merge:
        await kernel.pause()

    merge.assert_called_once()
    assert merge.call_args.args[2] == {"query": "继续写报告"}


@pytest.mark.asyncio
async def test_pause_falls_back_to_pending_user_query_on_idle_park() -> None:
    """Idle park: the last user message may be unprocessed — carry it."""
    harness = SimpleNamespace(
        state=HarnessState.IDLE,
        paused_query=None,
        active_round=None,
    )
    kernel = _make_pause_kernel(harness, pending_user_query="还没处理的消息")

    with patch(
        "openjiuwen.agent_teams.runtime.metadata.merge_pending_resume",
    ) as merge:
        await kernel.pause()

    merge.assert_called_once()
    assert merge.call_args.args[2] == {"query": "还没处理的消息"}
