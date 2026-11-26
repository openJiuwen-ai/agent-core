# coding: utf-8
"""Tests for EventBus lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    EventBus,
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.runtime.metadata import (
    merge_pending_resume,
    read_pending_resume,
)
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.schema.status import MemberStatus


class _StubSession:
    """Minimal session exposing the state API the kernel's persistence uses."""

    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def _make_kernel_host(
        memory_manager: object | None = None,
        messager: object | None = None,
) -> SimpleNamespace:
    stream_controller = SimpleNamespace(
        stream_queue=object(),
        drain_agent_task=AsyncMock(),
        pause_agent=AsyncMock(),
        resume_agent=AsyncMock(),
        close_stream=MagicMock(),
        stop=AsyncMock(),
    )
    # 真实 StreamController.close_stream 会清空 stream_queue——stub 对齐该语义
    stream_controller.close_stream.side_effect = lambda: setattr(
        stream_controller, "stream_queue", None
    )
    return SimpleNamespace(
        member_name="leader-1",
        role=TeamRole.LEADER,
        team_name="test-team",
        resources=SimpleNamespace(
            memory_manager=memory_manager,
            harness=SimpleNamespace(
                stop=AsyncMock(),
                dispose=AsyncMock(),
                # Not PAUSED, so kernel.start's warm-resume hook stays inert.
                state=HarnessState.IDLE,
            ),
        ),
        infra=SimpleNamespace(
            messager=messager,
            team_backend=None,
        ),
        spawn_manager=SimpleNamespace(
            spawned_handles={},
            cancel_recovery_tasks=AsyncMock(),
            shutdown_all_handles=AsyncMock(),
        ),
        session_manager=SimpleNamespace(
            team_session=None,
            release_session=MagicMock(),
        ),
        stream_controller=stream_controller,
        persist_allocator_state=MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_stop_sets_running_flag():
    """start() sets is_running, stop() clears it."""
    loop = EventBus(role=TeamRole.LEADER)
    assert loop.is_running is False

    await loop.start()
    assert loop.is_running is True

    await loop.stop()
    assert loop.is_running is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_is_idempotent():
    """Calling stop() twice does not raise."""
    loop = EventBus(role=TeamRole.LEADER)
    await loop.start()
    await loop.stop()
    await loop.stop()
    assert loop.is_running is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_wake_callback_invoked_on_event():
    """When an event is enqueued, the wake callback fires."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = EventBus(role=TeamRole.LEADER)
    await loop.start(wake_callback=on_wake)

    event = EventMessage(
        event_type=TeamEvent.MESSAGE,
        payload={"msg": "hello"},
    )
    await loop.enqueue(event)
    await asyncio.sleep(0.05)
    await loop.stop()

    assert len(woke) == 1
    assert woke[0].event_type == TeamEvent.MESSAGE


@pytest.mark.asyncio
@pytest.mark.level1
async def test_poll_timer_fires_periodically():
    """Poll timer enqueues synthetic poll events."""
    woke: list[CoordinationEvent] = []

    async def on_wake(event: CoordinationEvent) -> None:
        woke.append(event)

    loop = EventBus(
        role=TeamRole.LEADER,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await loop.start(wake_callback=on_wake)

    await asyncio.sleep(0.15)
    await loop.stop()

    mailbox_polls = [
        e for e in woke if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_MAILBOX
    ]
    task_polls = [e for e in woke if isinstance(e, InnerEventMessage) and e.event_type == InnerEventType.POLL_TASK]
    assert len(mailbox_polls) >= 2
    assert len(task_polls) >= 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_extracts_memory_once_and_stop_after_pause_does_not_repeat():
    """Pause owns persistent-team extraction; a later stop does not repeat it."""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()
    memory_manager.extract_after_round.assert_awaited_once()

    await kernel.stop()
    memory_manager.extract_after_round.assert_awaited_once()
    memory_manager.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_stops_at_boundary_and_never_hard_cancels():
    """kernel.pause pauses the round; it must never hard-cancel it.

    Regression guard for the root cause: pause used to route through
    ``drain_agent_task`` → ``abort(immediate=True)``, throwing away everything
    the member had done in the round it interrupted mid-way.
    """
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    host.stream_controller.pause_agent.assert_awaited_once()
    host.stream_controller.drain_agent_task.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_hard_cancels_the_round():
    """kernel.stop tears the round down: it is discarded outright."""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    host.stream_controller.drain_agent_task.assert_awaited_once()
    host.stream_controller.pause_agent.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_persists_pending_resume_for_a_later_cold_start():
    """A paused leader records what a cold start needs to continue its round."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    host.resources.harness.paused_query = "the original task"
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    assert read_pending_resume(session, "test-team") == {
        "query": "the original task",
        "paused_at_idle": False,
    }


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_records_nothing_when_no_round_was_suspended():
    """An idle harness had nothing to suspend, so no marker is written."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.pause()

    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_cold_path_consumes_the_marker():
    """A rebuilt (IDLE) harness continues the round the marker recorded.

    This is what makes ``pause -> stop -> start`` behave like ``pause -> resume``.
    """
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    merge_pending_resume(session, "test-team", {"query": "the original task"})
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_awaited_once_with(query="the original task")
    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_cold_path_is_leader_only():
    """team 级 pending_resume 标记由 leader 的 pause 路径写入，
    recover 冷启动的 teammate 不得读它用 leader 的 query 复起假轮
    标记须保留给 leader 自己恢复时消费。
    """
    session = _StubSession()
    host = _make_kernel_host()
    host.role = TeamRole.TEAMMATE
    host.member_name = "teammate-1"
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    merge_pending_resume(session, "test-team", {"query": "the leader's task"})
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_not_awaited()
    assert read_pending_resume(session, "test-team") == {"query": "the leader's task"}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_warm_path_ignores_the_marker():
    """A still-PAUSED harness resumes from memory, then drops the marker."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    merge_pending_resume(session, "test-team", {"query": "unused"})
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_awaited_once_with()
    assert read_pending_resume(session, "test-team") is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_resume_paused_round_is_noop_without_a_marker():
    """Nothing was paused: the idle harness is left alone."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = CoordinationKernel(host)

    await kernel.resume_paused_round()

    host.stream_controller.resume_agent.assert_not_awaited()


def _arm_kernel_for_start(host: SimpleNamespace) -> CoordinationKernel:
    """Wire the minimum a ``start`` call needs, short of a real setup().

    ``start`` returns early without an event bus, so the bus and dispatcher
    are stubbed as already-running: this test targets the tail of ``start``,
    not bus construction.
    """
    host.session_manager.bind_session = AsyncMock()
    host.resources.harness.start = AsyncMock()
    host.stream_controller.start = AsyncMock()
    host.update_status = AsyncMock()
    host.refresh_idle_baseline = MagicMock()
    host.infra.workspace_manager = None
    host.infra.workspace_initialized = True
    host.blueprint = SimpleNamespace(spec=SimpleNamespace(workspace=None))

    kernel = CoordinationKernel(host)
    kernel._event_bus = SimpleNamespace(is_running=True, start=AsyncMock(), enqueue=AsyncMock())
    kernel._dispatcher = SimpleNamespace(team_completion=SimpleNamespace(rearm=MagicMock()))
    return kernel


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_resumes_a_round_left_paused():
    """``start`` must continue the round a lifecycle pause suspended.

    Regression guard. The resume call used to live in ``notify_team_built``,
    which returns early when there is no scheduler — so an **autonomous** team
    (no scheduler, ever) resumed into silence and silently dropped the work it
    was suspended mid-way through, despite four docs and the method's own
    docstring promising ``start`` resumes it. The other ``resume_paused_round``
    tests call the method directly and so could never catch that; this one
    drives ``start`` itself.
    """
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    kernel = _arm_kernel_for_start(host)

    await kernel.start(session)

    kernel._event_bus.enqueue.assert_awaited_once()
    event = kernel._event_bus.enqueue.await_args.args[0]
    assert isinstance(event, InnerEventMessage)
    assert event.event_type == InnerEventType.REFRESH_TEAM_CONTEXT
    host.stream_controller.resume_agent.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_leaves_an_unpaused_round_alone():
    """A member with nothing suspended is not driven by ``start``."""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.IDLE
    kernel = _arm_kernel_for_start(host)

    await kernel.start(session)

    host.stream_controller.resume_agent.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_extracts_memory_when_stopping_from_running():
    """Direct stop from running performs final memory extraction before teardown."""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    memory_manager.extract_after_round.assert_awaited_once()
    memory_manager.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_closes_messager_transport():
    """Direct stop closes the current host's messager transport."""
    messager = SimpleNamespace(stop=AsyncMock())
    host = _make_kernel_host(messager=messager)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    await kernel.stop()

    messager.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_finalize_round_does_not_extract_memory():
    """Round cleanup no longer performs team memory extraction."""
    memory_manager = SimpleNamespace(extract_after_round=AsyncMock())
    host = _make_kernel_host(memory_manager)
    kernel = CoordinationKernel(host)

    await kernel.finalize_round()

    memory_manager.extract_after_round.assert_not_awaited()
    host.stream_controller.stop.assert_awaited_once()
    host.resources.harness.stop.assert_awaited_once()


def _kernel_with_live_bus(host) -> tuple:
    """kernel + 已启动的 EventBus（默认长间隔，测试内不会真的触发轮询）。"""
    kernel = CoordinationKernel(host)
    bus = EventBus(role=TeamRole.LEADER, mailbox_poll_interval=60.0, task_poll_interval=60.0)
    kernel._event_bus = bus
    return kernel, bus


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pause_stops_polls_and_bus_on_normal_path():
    host = _make_kernel_host()
    kernel, bus = _kernel_with_live_bus(host)
    kernel._lifecycle_state = "running"
    await bus.start()

    await kernel.pause()

    assert kernel._lifecycle_state == "paused"
    assert bus.is_running is False
    assert bus._task_poll_task is None and bus._mailbox_poll_task is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pause_stops_event_bus_even_when_midstep_fails():
    """pause 中段失败（memory extract 抛异常）也必须停掉 EventBus，异常原样上抛。"""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(side_effect=RuntimeError("db gone")),
        close=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager)
    kernel, bus = _kernel_with_live_bus(host)
    kernel._lifecycle_state = "running"
    await bus.start()

    with pytest.raises(RuntimeError, match="db gone"):
        await kernel.pause()

    assert bus.is_running is False
    assert bus._task_poll_task is None and bus._mailbox_poll_task is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stop_stops_event_bus_even_when_drain_fails():
    """stop 中段失败（drain_agent_task 抛异常）也必须停掉 EventBus。"""
    host = _make_kernel_host()
    host.stream_controller.drain_agent_task = AsyncMock(side_effect=RuntimeError("drain boom"))
    kernel, bus = _kernel_with_live_bus(host)
    kernel._lifecycle_state = "running"
    await bus.start()

    with pytest.raises(RuntimeError, match="drain boom"):
        await kernel.stop()

    assert bus.is_running is False
    assert bus._task_poll_task is None and bus._mailbox_poll_task is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pause_early_return_still_stops_bus():
    """state 非 running 的早退路径：幂等收掉历史半途中断残留的活 bus。"""
    host = _make_kernel_host()
    kernel, bus = _kernel_with_live_bus(host)
    kernel._lifecycle_state = "paused"  # 残留态：state 已非 running 但 bus 还活着
    await bus.start()

    await kernel.pause()  # 早退

    assert bus.is_running is False


@pytest.mark.asyncio
@pytest.mark.level1
async def test_pause_pauses_polls_before_midsteps():
    """轮询在 teardown 中段之前即被停（泄漏载体先行关闭）。"""
    host = _make_kernel_host()
    kernel, bus = _kernel_with_live_bus(host)
    kernel._lifecycle_state = "running"
    await bus.start()
    assert bus._task_poll_task is not None

    # 在中段（pause_agent）观察：此刻轮询应已被 pause_polls 取消
    seen = {}

    async def _observe():
        seen["polls_paused"] = bus.polls_paused

    host.stream_controller.pause_agent = AsyncMock(side_effect=_observe)
    await kernel.pause()

    assert seen.get("polls_paused") is True
    assert host.stream_controller.stream_queue is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_mark_live_teammates_skips_starting_member():
    """pause/stop 命中 spawn 中（STARTING）的成员 → 跳过不标。

    STARTING 在 MEMBER_TRANSITIONS 中无 PAUSED/STOPPED 边（成员现场还没建起来，
    谈不上暂停它），标记只会被 DAO CAS 守卫拒绝并刷误导性 error 日志；
    spawn 完成自会落 READY。READY 成员不受影响照标。
    """
    host = _make_kernel_host()
    host.spawn_manager.spawned_handles = {
        "m-ready": object(),
        "m-starting": object(),
        "m-unstarted": object(),
    }
    roster = [
        SimpleNamespace(member_name="leader-1", status="busy"),  # leader 跳过
        SimpleNamespace(member_name="m-ready", status="ready"),
        SimpleNamespace(member_name="m-starting", status="starting"),
        SimpleNamespace(member_name="m-unstarted", status="unstarted"),
        SimpleNamespace(member_name="m-not-spawned", status="ready"),  # 不在 spawned_handles 跳过
    ]
    team_backend = SimpleNamespace(
        list_member_roster=AsyncMock(return_value=roster),
        set_member_status=AsyncMock(return_value=True),
    )
    host.infra.team_backend = team_backend
    kernel = CoordinationKernel(host)

    await kernel._mark_live_teammates(MemberStatus.PAUSED)

    marked = [c.args[0] for c in team_backend.set_member_status.call_args_list]
    assert marked == ["m-ready"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_stops_event_bus_and_is_idempotent():
    """成员任务结束后的 dispose 必须停掉 EventBus 与轮询任务，且可重复调用。"""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    bus = EventBus(
        role=TeamRole.TEAMMATE,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await bus.start()
    assert bus.is_running is True
    kernel._event_bus = bus

    await kernel.dispose()

    assert bus.is_running is False
    assert bus._mailbox_poll_task is None
    assert bus._task_poll_task is None
    host.session_manager.release_session.assert_called_once_with()
    host.stream_controller.close_stream.assert_called_once_with()
    host.resources.harness.dispose.assert_awaited_once_with()

    # 幂等：再次调用不再触碰已拆资源。
    await kernel.dispose()
    host.session_manager.release_session.assert_called_once_with()
    host.resources.harness.dispose.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_failure_still_tears_down_member_handles():
    """pause 中段失败也必须取消成员句柄，不能留下成员轮询。"""
    host = _make_kernel_host()
    host.stream_controller.pause_agent = AsyncMock(side_effect=RuntimeError("pause boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    bus = EventBus(
        role=TeamRole.LEADER,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await bus.start()
    kernel._event_bus = bus

    with pytest.raises(RuntimeError):
        await kernel.pause()

    assert bus.is_running is False
    host.spawn_manager.cancel_recovery_tasks.assert_awaited()
    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_failure_still_tears_down_handles():
    """stop 中段失败也必须取消成员句柄。"""
    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    bus = EventBus(
        role=TeamRole.LEADER,
        mailbox_poll_interval=0.05,
        task_poll_interval=0.05,
    )
    await bus.start()
    kernel._event_bus = bus

    with pytest.raises(RuntimeError):
        await kernel.stop()

    assert bus.is_running is False
    host.spawn_manager.shutdown_all_handles.assert_awaited()


def _ordered_member_backend(host: SimpleNamespace, calls: list[str]) -> SimpleNamespace:
    """成员兜底链路的有序桩：mark → cancel → shutdown 依次记入 ``calls``。"""
    team_backend = SimpleNamespace(
        list_member_roster=AsyncMock(
            return_value=[SimpleNamespace(member_name="m", status="busy")]
        ),
        set_member_status=AsyncMock(
            side_effect=lambda *args, **kwargs: calls.append("mark")
        ),
    )
    host.infra.team_backend = team_backend
    host.spawn_manager.spawned_handles = {"m": object()}
    host.spawn_manager.cancel_recovery_tasks = AsyncMock(
        side_effect=lambda: calls.append("cancel")
    )
    host.spawn_manager.shutdown_all_handles = AsyncMock(
        side_effect=lambda: calls.append("shutdown")
    )
    return team_backend


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_marks_paused_before_tearing_down_members():
    """pause 中段失败停在标记之前：兜底必须先写 PAUSED 再拆句柄。"""
    calls: list[str] = []
    host = _make_kernel_host()
    host.stream_controller.pause_agent = AsyncMock(side_effect=RuntimeError("pause boom"))
    team_backend = _ordered_member_backend(host, calls)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    assert calls == ["mark", "cancel", "shutdown"]
    team_backend.set_member_status.assert_awaited_once_with("m", MemberStatus.PAUSED)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_fallback_marks_stopped_before_tearing_down_members():
    """stop 中段失败停在标记之前：兜底必须先写 STOPPED 再拆句柄。"""
    calls: list[str] = []
    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    team_backend = _ordered_member_backend(host, calls)
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    assert calls == ["mark", "cancel", "shutdown"]
    team_backend.set_member_status.assert_awaited_once_with("m", MemberStatus.STOPPED)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_fallback_latches_stopped_lifecycle():
    """stop 中段失败：兜底拆完句柄后内存态必须落 stopped（与 pause 兜底对称）。

    否则后续 pause/stop 会把已拆的资源当运行中再跑一遍。
    """
    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    assert kernel._lifecycle_state == "stopped"
    host.spawn_manager.shutdown_all_handles.assert_awaited_once()

    # 再 pause 属幂等早退：不得对已拆成员再跑一轮暂停链。
    await kernel.pause()
    host.stream_controller.pause_agent.assert_not_awaited()
    host.spawn_manager.shutdown_all_handles.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_fallback_releases_permanent_resources_when_chain_aborts():
    """stop 中段失败并 latch stopped 后，主链没走到的永久资源必须兜底释放。

    latch 之后内核不再重试，未释放的 stream/memory/harness/messager/session
    会一直泄漏到进程退出——兜底链必须把它们全部收掉。
    """
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    messager = SimpleNamespace(
        stop=AsyncMock(),
        unregister_direct_message_handler=AsyncMock(),
        unsubscribe=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager=memory_manager, messager=messager)
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    assert kernel._lifecycle_state == "stopped"
    host.stream_controller.close_stream.assert_called_once()
    memory_manager.close.assert_awaited_once()
    host.resources.harness.dispose.assert_awaited_once()
    messager.stop.assert_awaited_once()
    host.session_manager.release_session.assert_called_once_with()
    # 主链没到记忆抽取：兜底不得补跑（提取只在正常 stop 语义里发生）。
    memory_manager.extract_after_round.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_fallback_step_failure_does_not_block_remaining_release():
    """兜底释放链单步失败（如 harness dispose 抛错）不得阻断后续释放。"""
    memory_manager = SimpleNamespace(
        extract_after_round=AsyncMock(),
        close=AsyncMock(),
    )
    messager = SimpleNamespace(
        stop=AsyncMock(),
        unregister_direct_message_handler=AsyncMock(),
        unsubscribe=AsyncMock(),
    )
    host = _make_kernel_host(memory_manager=memory_manager, messager=messager)
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    host.resources.harness.dispose = AsyncMock(
        side_effect=RuntimeError("harness boom")
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    memory_manager.close.assert_awaited_once()
    messager.stop.assert_awaited_once()
    host.session_manager.release_session.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_handle_teardown_survives_mark_failure():
    """标记失败（DB 不可用）不得阻断成员句柄拆除，原异常照常上抛。"""
    calls: list[str] = []
    host = _make_kernel_host()
    host.stream_controller.pause_agent = AsyncMock(side_effect=RuntimeError("pause boom"))
    team_backend = _ordered_member_backend(host, calls)
    team_backend.list_member_roster = AsyncMock(side_effect=RuntimeError("db gone"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    assert calls == ["cancel", "shutdown"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_persists_pending_resume_and_lifecycle():
    """pause 主链中断：兜底拆成员后必须补写 lifecycle + pending-resume。"""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    host.resources.harness.paused_query = "the original task"
    host.stream_controller.pause_agent = AsyncMock(
        side_effect=RuntimeError("pause boom")
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    assert read_pending_resume(session, "test-team") == {
        "query": "the original task",
        "paused_at_idle": False,
    }
    assert "paused" in str(session.state)
    assert kernel._lifecycle_state == "paused"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_latches_paused_after_members_torn_down():
    """成员已拆、收尾段失败：内存态仍必须落 paused（不能停在 running）。

    主链的 ``_lifecycle_state = "paused"`` 在末尾；成员拆除成功（
    ``members_still_live=False``）之后、落 paused 之前失败时，旧兜底条件
    不生效，内核会停在 running，后续 pause/stop 把已释放资源再跑一遍。
    """
    host = _make_kernel_host()
    host.session_manager.release_session = MagicMock(
        side_effect=RuntimeError("release boom")
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="release boom"):
        await kernel.pause()

    assert kernel._lifecycle_state == "paused"
    host.spawn_manager.shutdown_all_handles.assert_awaited_once()

    # 已落 paused：再 pause 属幂等早退，不得重跑暂停链。
    await kernel.pause()
    host.stream_controller.pause_agent.assert_awaited_once()
    host.spawn_manager.shutdown_all_handles.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_releases_pause_scope_resources_when_chain_aborts():
    """pause 主链在关流/释放 session 之前中断：兜底必须补做，latch 后无法再补。"""
    host = _make_kernel_host()
    host.stream_controller.pause_agent = AsyncMock(
        side_effect=RuntimeError("pause boom")
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    assert kernel._lifecycle_state == "paused"
    host.stream_controller.close_stream.assert_called_once()
    host.session_manager.release_session.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_persistence_survives_members_already_torn_down():
    """成员已拆后主链中断：lifecycle/pending-resume 也必须补写。"""
    session = _StubSession()
    host = _make_kernel_host()
    host.session_manager.team_session = session
    host.resources.harness.state = HarnessState.PAUSED
    host.resources.harness.paused_query = "the original task"
    host.session_manager.release_session = MagicMock(
        side_effect=RuntimeError("release boom")
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="release boom"):
        await kernel.pause()

    assert kernel._lifecycle_state == "paused"
    assert read_pending_resume(session, "test-team") == {
        "query": "the original task",
        "paused_at_idle": False,
    }
    assert "paused" in str(session.state)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_cancels_leader_timers():
    """dispose 必须回收 leader-only 的接管提示/预算收尾定时器。"""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    nudge = asyncio.create_task(asyncio.sleep(60))
    wake = asyncio.create_task(asyncio.sleep(60))
    kernel._takeover_nudge_task = nudge
    kernel._budget_wake_task = wake

    await kernel.dispose()

    assert kernel._takeover_nudge_task is None
    assert kernel._budget_wake_task is None
    for task in (nudge, wake):
        with pytest.raises(asyncio.CancelledError):
            await task


def _failing_bus_stub(exc: BaseException) -> SimpleNamespace:
    """EventBus stub whose ``stop`` raises — isolation tests only."""
    return SimpleNamespace(
        pause_polls=AsyncMock(),
        stop=AsyncMock(side_effect=exc),
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_fallback_survives_event_bus_stop_failure():
    """stop 兜底中「停 EventBus」失败不得连带跳过成员句柄拆除。"""
    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    kernel._event_bus = _failing_bus_stub(RuntimeError("bus boom"))

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    kernel._event_bus.stop.assert_awaited()
    host.spawn_manager.cancel_recovery_tasks.assert_awaited()
    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_pause_fallback_survives_event_bus_stop_failure():
    """pause 兜底中「停 EventBus」失败不得连带跳过成员句柄拆除。"""
    host = _make_kernel_host()
    host.stream_controller.pause_agent = AsyncMock(side_effect=RuntimeError("pause boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    kernel._event_bus = _failing_bus_stub(RuntimeError("bus boom"))

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    host.spawn_manager.cancel_recovery_tasks.assert_awaited()
    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_teardown_failure_does_not_skip_handle_shutdown():
    """兜底内 cancel_recovery_tasks 失败也不得跳过 shutdown_all_handles。"""
    host = _make_kernel_host()
    host.spawn_manager.cancel_recovery_tasks = AsyncMock(side_effect=RuntimeError("cancel boom"))
    host.stream_controller.pause_agent = AsyncMock(side_effect=RuntimeError("pause boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    with pytest.raises(RuntimeError, match="pause boom"):
        await kernel.pause()

    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_tail_inner_cancellation_does_not_hijack_original_error():
    """兜底步骤冒出的内层取消不得冒充调用方取消，也不得覆盖原始异常。"""
    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    kernel._event_bus = _failing_bus_stub(asyncio.CancelledError())

    with pytest.raises(RuntimeError, match="persist boom"):
        await kernel.stop()

    host.spawn_manager.cancel_recovery_tasks.assert_awaited()
    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_tail_re_raises_only_when_caller_cancelled():
    """调用方任务被取消：拆完句柄后把 CancelledError 回抛给调用方。"""
    entered = asyncio.Event()

    async def _blocked_drain() -> None:
        entered.set()
        await asyncio.Event().wait()

    host = _make_kernel_host()
    host.stream_controller.drain_agent_task = AsyncMock(
        side_effect=_blocked_drain
    )
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"

    task = asyncio.create_task(kernel.stop())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    host.spawn_manager.shutdown_all_handles.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_stop_tail_cancellation_does_not_mask_original_error():
    """主链异常在途时 tail 收到调用方取消：原异常必须继续上抛。

    ``finally`` 里新抛的 CancelledError 会顶掉正在传播的异常，调用方将
    永远看不到真正的失败原因；取消只允许记日志，不得覆盖主链异常。
    """
    entered = asyncio.Event()

    async def _blocked_bus_stop() -> None:
        entered.set()
        await asyncio.Event().wait()

    host = _make_kernel_host()
    host.persist_allocator_state = MagicMock(side_effect=RuntimeError("persist boom"))
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "running"
    kernel._event_bus = SimpleNamespace(
        pause_polls=AsyncMock(),
        stop=AsyncMock(side_effect=_blocked_bus_stop),
    )

    task = asyncio.create_task(kernel.stop())
    await entered.wait()
    task.cancel()
    with pytest.raises(RuntimeError, match="persist boom"):
        await task

    host.spawn_manager.shutdown_all_handles.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_early_return_swallows_event_bus_stop_failure():
    """幂等早退路径：EventBus 停失败只记日志，不把调用方拖崩。"""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    kernel._lifecycle_state = "stopped"
    kernel._event_bus = _failing_bus_stub(RuntimeError("bus boom"))

    await kernel.stop()  # 不抛

    kernel._event_bus.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_stops_bus_when_an_early_step_fails():
    """dispose 早段（pause_polls）失败也必须停 EventBus 并走完后续释放。"""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    bus = SimpleNamespace(
        pause_polls=AsyncMock(side_effect=RuntimeError("polls boom")),
        stop=AsyncMock(),
    )
    kernel._event_bus = bus

    await kernel.dispose()

    bus.stop.assert_awaited()
    host.session_manager.release_session.assert_called_once_with()
    host.resources.harness.dispose.assert_awaited_once_with()
    assert kernel._disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_continues_after_a_step_fails():
    """dispose 后段失败（harness）不跳过 session 释放；异常不外抛。"""
    host = _make_kernel_host()
    host.resources.harness.dispose = AsyncMock(side_effect=RuntimeError("harness boom"))
    kernel = CoordinationKernel(host)
    kernel._event_bus = SimpleNamespace(
        pause_polls=AsyncMock(),
        stop=AsyncMock(),
    )

    await kernel.dispose()

    host.session_manager.release_session.assert_called_once_with()
    assert kernel._disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_start_after_dispose_is_rejected():
    """dispose 是终态：再次 start 必须拒绝，不能新起无人回收的 EventBus。"""
    host = _make_kernel_host()
    kernel, bus = _kernel_with_live_bus(host)
    await bus.start()
    await kernel.dispose()
    assert bus.is_running is False

    with pytest.raises(RuntimeError, match="disposed"):
        await kernel.start(None)

    assert bus.is_running is False


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_swallows_inner_cancellation_and_completes():
    """步骤内部任务取消（非调用方取消）不得让 dispose 误报被取消。"""
    host = _make_kernel_host()
    kernel = CoordinationKernel(host)
    bus = SimpleNamespace(
        pause_polls=AsyncMock(side_effect=asyncio.CancelledError()),
        stop=AsyncMock(),
    )
    kernel._event_bus = bus

    await kernel.dispose()  # 不抛

    bus.stop.assert_awaited()
    host.session_manager.release_session.assert_called_once_with()
    assert kernel._disposed is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_dispose_interrupted_step_stays_retryable():
    """调用方取消打断某步：不得 latch，二次 dispose 必须重跑并补齐。

    旧契约「取消后一律 latch、不重试」把被中断步骤的半成品资源当成已释放；
    现按步骤结局判定：有 interrupted_step 时不 latch，保留唯一的重试点。
    """
    entered = asyncio.Event()
    calls = {"harness": 0}

    async def _blocked_harness_dispose() -> None:
        calls["harness"] += 1
        if calls["harness"] == 1:
            entered.set()
            await asyncio.Event().wait()

    host = _make_kernel_host()
    host.resources.harness.dispose = AsyncMock(
        side_effect=_blocked_harness_dispose
    )
    kernel = CoordinationKernel(host)
    kernel._event_bus = SimpleNamespace(
        pause_polls=AsyncMock(),
        stop=AsyncMock(),
    )

    task = asyncio.create_task(kernel.dispose())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # harness 步被调用方取消打断：未 latch，可重试；后续步骤仍按隔离链跑过。
    assert kernel._disposed is False
    host.session_manager.release_session.assert_called_once_with()

    await kernel.dispose()

    assert calls["harness"] == 2
    assert kernel._disposed is True
    assert kernel._lifecycle_state == "stopped"
    assert host.session_manager.release_session.call_count == 2
