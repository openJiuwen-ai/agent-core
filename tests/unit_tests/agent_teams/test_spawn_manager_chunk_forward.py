# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Chunk fan-out wiring inside SpawnManager.

Exercises ``_wire_inprocess_chunk_forward`` and the matching cleanup
path, ensuring that an in-process teammate's stream chunks reach the
leader's stream_queue and that the forwarder is detached on teardown.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.spawn_manager import SpawnManager
from openjiuwen.agent_teams.agent.stream_controller import StreamController
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.spawn.inprocess_handle import InProcessSpawnHandle
from openjiuwen.core.session.stream.base import OutputSchema


def _make_stream_controller(member_name: str, *, role: TeamRole = TeamRole.TEAMMATE) -> StreamController:
    """Build a StreamController with the minimum wiring for observer tests."""
    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    state = TeamAgentState()
    blueprint = SimpleNamespace(member_name=member_name, role=role)

    async def _noop(_: Any) -> None:
        return None

    return StreamController(
        blueprint_getter=lambda: blueprint,
        state=state,
        resources=PrivateAgentResources(harness=None),
        status_updater=_noop,
        execution_updater=_noop,
    )


def _make_spawn_manager_with(leader_sc: StreamController) -> SpawnManager:
    """Construct SpawnManager with a fake leader exposing stream_controller."""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    leader_agent = SimpleNamespace(stream_controller=leader_sc)
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: leader_agent,
    )
    return sm


def _make_handle_with(teammate_sc: StreamController) -> InProcessSpawnHandle:
    teammate_agent = SimpleNamespace(stream_controller=teammate_sc)
    return InProcessSpawnHandle(
        process_id="inproc-test",
        agent_ref=teammate_agent,
    )


def _make_alive_handle(
        *,
        dispose_coordination: AsyncMock,
        force_kill: AsyncMock | None = None,
        stop_health_check: AsyncMock | None = None,
) -> SimpleNamespace:
    """Duck-typed "alive" handle for cleanup tests.

    ``cleanup_teammate`` only reads ``chunk_forward`` / ``agent_ref`` /
    ``is_alive`` and awaits ``stop_health_check`` / ``force_kill``. A plain
    namespace keeps the test on that surface instead of monkeypatching a
    class property (which would break if the production access form ever
    changes to a method call).
    """
    return SimpleNamespace(
        process_id="inproc-alive",
        chunk_forward=None,
        agent_ref=SimpleNamespace(dispose_coordination=dispose_coordination),
        is_alive=True,
        stop_health_check=stop_health_check or AsyncMock(),
        force_kill=force_kill or AsyncMock(),
    )


@pytest.mark.asyncio
async def test_wire_forward_routes_teammate_chunk_to_leader_queue() -> None:
    """After wiring, a chunk on the teammate stream_controller observer
    chain must land in the leader's stream_queue.
    """
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_stream_controller("teammate_m")

    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_handle_with(teammate_sc)

    sm._wire_inprocess_chunk_forward(handle)

    assert handle.chunk_forward is not None
    assert teammate_sc._chunk_observers == [handle.chunk_forward]

    raw = OutputSchema(type="message", index=0, payload={"x": 1})
    tagged = teammate_sc._tag_chunk(raw)
    await handle.chunk_forward(tagged)

    received = await asyncio.wait_for(leader_sc.stream_queue.get(), timeout=1.0)
    assert received is tagged
    assert received.source_member == "teammate_m"
    assert received.role == TeamRole.TEAMMATE


@pytest.mark.asyncio
async def test_cleanup_detaches_forward_observer() -> None:
    """cleanup_teammate must remove the forwarder so post-cleanup chunks
    cannot leak into the leader queue.
    """
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    leader_sc.stream_queue = asyncio.Queue()

    teammate_sc = _make_stream_controller("teammate_m")

    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_handle_with(teammate_sc)
    sm._wire_inprocess_chunk_forward(handle)
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    assert handle.chunk_forward is None
    assert teammate_sc._chunk_observers == []
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_disposes_member_coordination() -> None:
    """成员清理必须显式停掉其协调内核的事件平面（EventBus 轮询）。"""
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    disposed = AsyncMock()
    handle = InProcessSpawnHandle(
        process_id="inproc-dispose",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    disposed.assert_awaited_once_with()
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_disposes_member_coordination_when_force_kill_raises() -> None:
    """force_kill 以异常收尾（成员任务带错死亡）也不能跳过协调内核拆除。"""
    order: list[str] = []

    async def _force_kill() -> None:
        order.append("force_kill")
        raise RuntimeError("task died")

    async def _dispose() -> None:
        order.append("dispose")

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_alive_handle(
        dispose_coordination=AsyncMock(side_effect=_dispose),
        force_kill=AsyncMock(side_effect=_force_kill),
    )
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    # 断言分支确实执行过：否则「force_kill 抛异常仍要拆除内核」的回归点
    # 会在无人察觉的情况下失效。顺序也被钉住：先杀后拆。
    handle.force_kill.assert_awaited_once_with()
    assert order == ["force_kill", "dispose"]
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_swallows_cancelled_health_check() -> None:
    """心跳停等被取消时清理不能外抛，仍要走到成员协调内核拆除。"""
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    disposed = AsyncMock()
    handle = InProcessSpawnHandle(
        process_id="inproc-hc-cancel",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )
    handle.stop_health_check = AsyncMock(side_effect=asyncio.CancelledError())
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    disposed.assert_awaited_once_with()
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_cancelled_health_check_still_force_kills() -> None:
    """心跳停等被内层取消不得跳过 force_kill：否则成员任务/EventBus 留存。"""
    order: list[str] = []

    async def _force_kill() -> None:
        order.append("force_kill")

    async def _dispose() -> None:
        order.append("dispose")

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    handle = _make_alive_handle(
        dispose_coordination=AsyncMock(side_effect=_dispose),
        force_kill=AsyncMock(side_effect=_force_kill),
        stop_health_check=AsyncMock(side_effect=asyncio.CancelledError()),
    )
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    handle.stop_health_check.assert_awaited_once_with()
    handle.force_kill.assert_awaited_once_with()
    assert order == ["force_kill", "dispose"]
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_swallows_cancelled_dispose() -> None:
    """内核 dispose 延迟取消路径重抛 CancelledError 时 cleanup 必须吞掉。"""
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    disposed = AsyncMock(side_effect=asyncio.CancelledError())
    handle = InProcessSpawnHandle(
        process_id="inproc-dispose-cancel",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )
    sm.spawned_handles["m"] = handle

    await sm.cleanup_teammate("m")

    disposed.assert_awaited_once_with()
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_re_raises_cancellation_after_teardown() -> None:
    """调用方任务自身被取消：拆完成员后必须补回抛，取消不能被静默消费。"""
    entered = asyncio.Event()

    async def _blocked_health_check() -> None:
        entered.set()
        await asyncio.Event().wait()

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    disposed = AsyncMock()
    handle = InProcessSpawnHandle(
        process_id="inproc-own-cancel",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )
    handle.stop_health_check = AsyncMock(side_effect=_blocked_health_check)
    sm.spawned_handles["m"] = handle

    task = asyncio.create_task(sm.cleanup_teammate("m"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    disposed.assert_awaited_once_with()
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_cleanup_after_handled_cancellation_is_not_misjudged() -> None:
    """任务此前被取消过并已消化：本次清理成功时不得再抛 CancelledError。

    取消计数（``Task.cancelling()``）是累计值、不会因上层消化而清零；
    判据必须取「本次清理期间的增量」，否则成功清理会被误标为被取消。
    """
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    disposed = AsyncMock()
    handle = InProcessSpawnHandle(
        process_id="inproc-stale-cancel",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )
    sm.spawned_handles["m"] = handle

    async def _run() -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass  # 上层有意消化：后续清理照常执行
        await sm.cleanup_teammate("m")
        return "ok"

    task = asyncio.create_task(_run())
    await asyncio.sleep(0)
    task.cancel()
    assert await task == "ok"

    disposed.assert_awaited_once_with()
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_restart_aborts_to_error_when_cancelled() -> None:
    """restart 中途被取消不得把成员留在 RESTARTING。"""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    entered = asyncio.Event()

    async def _blocked_health_check() -> None:
        entered.set()
        await asyncio.Event().wait()

    backend = SimpleNamespace(
        set_member_status=AsyncMock(),
        db=SimpleNamespace(
            member=SimpleNamespace(
                get_member_status=AsyncMock(
                    return_value=MemberStatus.RESTARTING.value
                ),
            ),
        ),
    )
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(
            member_name="leader",
            team_backend=backend,
            team_name="t",
        ),
        team_agent_getter=lambda: SimpleNamespace(),
    )
    handle = InProcessSpawnHandle(
        process_id="inproc-restart-cancel",
        agent_ref=SimpleNamespace(dispose_coordination=AsyncMock()),
    )
    handle.stop_health_check = AsyncMock(side_effect=_blocked_health_check)
    sm.spawned_handles["m"] = handle

    task = asyncio.create_task(sm.restart_teammate("m"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    backend.set_member_status.assert_awaited_once_with(
        "m", MemberStatus.ERROR,
        allowed_from=SpawnManager._RESTART_ABORT_OVERWRITABLE,
    )
    assert "m" not in sm.spawned_handles


@pytest.mark.asyncio
async def test_restart_cancelled_during_context_build_normalizes_status() -> None:
    """取消落在 build_context_from_db（DB await）：仍归一到 ERROR，不卡 RESTARTING。"""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    entered = asyncio.Event()

    async def _blocked_build(member_name: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    backend = SimpleNamespace(
        set_member_status=AsyncMock(),
        db=SimpleNamespace(
            member=SimpleNamespace(
                get_member_status=AsyncMock(
                    return_value=MemberStatus.RESTARTING.value
                ),
            ),
        ),
    )
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(
            member_name="leader",
            team_backend=backend,
            team_name="t",
        ),
        team_agent_getter=lambda: SimpleNamespace(),
    )
    sm.cleanup_teammate = AsyncMock()
    sm.build_context_from_db = AsyncMock(side_effect=_blocked_build)

    task = asyncio.create_task(sm.restart_teammate("m"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    backend.set_member_status.assert_awaited_once_with(
        "m", MemberStatus.ERROR,
        allowed_from=SpawnManager._RESTART_ABORT_OVERWRITABLE,
    )


@pytest.mark.asyncio
async def test_restart_abort_does_not_overwrite_leader_marked_status() -> None:
    """取消展开不得把 leader 刚写的 PAUSED/STOPPED 覆盖成 ERROR。"""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    backend = SimpleNamespace(
        set_member_status=AsyncMock(),
        db=SimpleNamespace(
            member=SimpleNamespace(
                get_member_status=AsyncMock(
                    return_value=MemberStatus.PAUSED.value
                ),
            ),
        ),
    )
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(
            member_name="leader",
            team_backend=backend,
            team_name="t",
        ),
        team_agent_getter=lambda: SimpleNamespace(),
    )

    await sm.mark_restart_aborted("m")

    backend.set_member_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_abort_uses_conditional_status_write() -> None:
    """归一必须走带前驱状态校验的条件写：读后被 leader 改态不得覆盖。"""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    backend = SimpleNamespace(
        # 条件写落空（模拟读后 leader 已写入 PAUSED）。
        set_member_status=AsyncMock(return_value=False),
        db=SimpleNamespace(
            member=SimpleNamespace(
                get_member_status=AsyncMock(
                    return_value=MemberStatus.READY.value
                ),
            ),
        ),
    )
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(
            member_name="leader",
            team_backend=backend,
            team_name="t",
        ),
        team_agent_getter=lambda: SimpleNamespace(),
    )

    await sm.mark_restart_aborted("m")

    backend.set_member_status.assert_awaited_once_with(
        "m",
        MemberStatus.ERROR,
        allowed_from=SpawnManager._RESTART_ABORT_OVERWRITABLE,
    )


@pytest.mark.asyncio
async def test_restart_cancelled_after_spawn_keeps_member_status() -> None:
    """重启已成功产出活句柄后再被取消：不得把健康成员写成 ERROR。"""
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    entered = asyncio.Event()

    async def _spawn_ok(ctx, **kwargs) -> None:
        sm.spawned_handles["m"] = InProcessSpawnHandle(
            process_id="new-member",
            agent_ref=SimpleNamespace(dispose_coordination=AsyncMock()),
        )

    async def _blocked_publish(member_name: str, attempt: int) -> None:
        entered.set()
        await asyncio.Event().wait()

    backend = SimpleNamespace(
        set_member_status=AsyncMock(),
        db=SimpleNamespace(
            member=SimpleNamespace(
                get_member_status=AsyncMock(
                    return_value=MemberStatus.READY.value
                ),
            ),
        ),
    )
    sm = SpawnManager(
        state=TeamAgentState(),
        configurator=SimpleNamespace(
            member_name="leader",
            team_backend=backend,
            team_name="t",
        ),
        team_agent_getter=lambda: SimpleNamespace(),
    )
    sm.build_context_from_db = AsyncMock(
        return_value=SimpleNamespace(member_name="m")
    )
    sm.spawn_teammate = AsyncMock(side_effect=_spawn_ok)
    sm.publish_restart_event = AsyncMock(side_effect=_blocked_publish)

    task = asyncio.create_task(sm.restart_teammate("m"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    backend.set_member_status.assert_not_awaited()
    assert sm.spawned_handles.get("m") is not None


@pytest.mark.asyncio
async def test_shutdown_all_handles_re_raises_after_tearing_down_all_members() -> None:
    """调用方取消：剩余成员仍要全部拆完、句柄清空，最后再补回抛。"""
    entered = asyncio.Event()

    async def _blocked_health_check() -> None:
        entered.set()
        await asyncio.Event().wait()

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    first_disposed = AsyncMock()
    second_disposed = AsyncMock()
    first = InProcessSpawnHandle(
        process_id="inproc-own-cancel-a",
        agent_ref=SimpleNamespace(dispose_coordination=first_disposed),
    )
    first.stop_health_check = AsyncMock(side_effect=_blocked_health_check)
    sm.spawned_handles["a"] = first
    sm.spawned_handles["b"] = InProcessSpawnHandle(
        process_id="inproc-own-cancel-b",
        agent_ref=SimpleNamespace(dispose_coordination=second_disposed),
    )

    task = asyncio.create_task(sm.shutdown_all_handles())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    first_disposed.assert_awaited_once_with()
    second_disposed.assert_awaited_once_with()
    assert sm.spawned_handles == {}


@pytest.mark.asyncio
async def test_shutdown_all_handles_continues_after_cancelled_dispose() -> None:
    """单个成员 dispose 被取消不得中断整轮拆除，spawned_handles 必须清空。"""
    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    cancelled = AsyncMock(side_effect=asyncio.CancelledError())
    disposed = AsyncMock()
    sm.spawned_handles["a"] = InProcessSpawnHandle(
        process_id="inproc-a",
        agent_ref=SimpleNamespace(dispose_coordination=cancelled),
    )
    sm.spawned_handles["b"] = InProcessSpawnHandle(
        process_id="inproc-b",
        agent_ref=SimpleNamespace(dispose_coordination=disposed),
    )

    await sm.shutdown_all_handles()

    cancelled.assert_awaited_once_with()
    disposed.assert_awaited_once_with()
    assert sm.spawned_handles == {}


@pytest.mark.asyncio
async def test_wire_skips_when_leader_or_agent_ref_missing() -> None:
    """Wire must no-op when leader or teammate agent ref is unavailable."""
    sm_no_leader = SpawnManager(
        state=SimpleNamespace(session_id="sess"),
        configurator=SimpleNamespace(member_name="leader"),
        team_agent_getter=lambda: None,
    )
    handle = InProcessSpawnHandle(process_id="inproc-test", agent_ref=SimpleNamespace())
    sm_no_leader._wire_inprocess_chunk_forward(handle)
    assert handle.chunk_forward is None

    leader_sc = _make_stream_controller("leader_m", role=TeamRole.LEADER)
    sm = _make_spawn_manager_with(leader_sc)
    handle_no_ref = InProcessSpawnHandle(process_id="inproc-test", agent_ref=None)
    sm._wire_inprocess_chunk_forward(handle_no_ref)
    assert handle_no_ref.chunk_forward is None
