# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the session-scoped DeepAgent interaction entrypoint."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.task_loop.loop_queues import LoopQueues, SteeringInput
from openjiuwen.harness.task_loop.task_loop_controller import TaskLoopController
from openjiuwen.harness.schema.interaction import (
    ActiveInteractionRound,
    InputDispatchMode,
    InputDisposition,
    InteractionEventType,
    RoundWorkItem,
    SendInputRequest,
)
from openjiuwen.harness.tools.worktree import session as worktree_session_state
from openjiuwen.harness.tools.worktree.models import WorktreeSession
from openjiuwen.harness.tools.worktree.session import (
    get_current_session,
    set_current_session,
)


@pytest.mark.asyncio
async def test_start_initializes_context_before_scheduler_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduler created during start shares the pre-created mutable holder."""
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._task_completion_rail = object()  # type: ignore[assignment]

    scheduler_release = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    active = WorktreeSession(
        original_cwd="/repo",
        worktree_path="/repo/.worktrees/shared",
        worktree_name="shared",
    )

    async def scheduler() -> None:
        await scheduler_release.wait()
        set_current_session(active)

    async def prepare_interaction_task_loop(_session):
        nonlocal scheduler_task
        scheduler_task = asyncio.create_task(scheduler())
        return MagicMock(), MagicMock()

    async def forward_session_stream() -> None:
        return

    monkeypatch.setattr(
        agent,
        "prepare_interaction_task_loop",
        prepare_interaction_task_loop,
    )
    monkeypatch.setattr(agent, "_forward_session_stream", forward_session_stream)
    monkeypatch.setattr(agent, "_ensure_supervisor_running", MagicMock())

    token = worktree_session_state._state.set(None)
    try:
        session = MagicMock()
        session.get_session_id.return_value = "shared-holder-session"
        await agent.start(session=session)

        assert scheduler_task is not None
        scheduler_release.set()
        await scheduler_task
        assert get_current_session() is active
    finally:
        worktree_session_state._state.reset(token)


@pytest.mark.asyncio
async def test_send_input_queues_interactive_input_as_interrupt_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})

    await agent.send_input(
        SendInputRequest(
            request_id="resume-1",
            inputs={"query": interactive_input},
        )
    )

    work = agent._event_manager.next_work()
    assert work is not None
    assert isinstance(work.query, InteractiveInput)
    assert work.query.user_inputs == {
        "call_123": {"action": "allow_once"},
    }
    assert work.reset_loop is False


@pytest.mark.asyncio
async def test_send_input_preserves_text_validation_and_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    with pytest.raises(ValueError, match="non-empty string or InteractiveInput"):
        await agent.send_input(SendInputRequest(request_id="empty-1", inputs={"query": ""}))

    await agent.send_input(SendInputRequest(request_id="text-1", inputs={"query": "continue"}))

    work = agent._event_manager.next_work()
    assert work is not None
    assert work.query == "continue"
    assert work.reset_loop is True


@pytest.mark.parametrize("mode", list(InputDispatchMode))
@pytest.mark.asyncio
async def test_send_input_ignores_dispatch_mode_for_interrupt_resume(
    mode: InputDispatchMode,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})

    await agent.send_input(
        SendInputRequest(
            request_id="resume-1",
            inputs={"query": interactive_input},
            mode=mode,
        )
    )

    work = agent._event_manager.next_work()
    assert work is not None
    assert isinstance(work.query, InteractiveInput)
    assert work.query.user_inputs == interactive_input.user_inputs
    assert work.reset_loop is False


@pytest.mark.asyncio
async def test_run_one_round_uses_single_round_path_for_interrupt_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    interactive_input = InteractiveInput()
    interactive_input.update("call_123", {"action": "allow_once"})
    work = RoundWorkItem.user(
        request_id="resume-1",
        inputs={"query": interactive_input},
        reset_loop=False,
    )
    session = MagicMock()
    coordinator = MagicMock()
    controller = MagicMock()
    controller.submit_round = AsyncMock()
    result = {"result_type": "answer", "output": "resumed"}
    react_agent = MagicMock()
    react_agent.invoke = AsyncMock(return_value=result)
    agent._react_agent = react_agent
    write_result = AsyncMock()
    build_next_work = MagicMock(return_value=None)

    monkeypatch.setattr(
        agent,
        "prepare_interaction_task_loop",
        AsyncMock(return_value=(coordinator, controller)),
    )
    monkeypatch.setattr(agent, "_write_round_result_to_stream", write_result)
    monkeypatch.setattr(agent, "_build_interaction_next_work", build_next_work)
    monkeypatch.setattr(agent, "save_state", MagicMock())
    monkeypatch.setattr(agent, "clear_state", MagicMock())

    outcome = await agent.run_one_round(work, "task-1", session)

    effective_inputs = react_agent.invoke.await_args.args[0]
    assert isinstance(effective_inputs["query"], InteractiveInput)
    assert effective_inputs["query"].user_inputs == interactive_input.user_inputs
    coordinator.reset.assert_not_called()
    controller.submit_round.assert_not_awaited()
    build_next_work.assert_not_called()
    write_result.assert_awaited_once_with(result, session)
    assert outcome.error_code is None


@pytest.mark.asyncio
async def test_cancel_active_round_does_not_block_on_slow_cancel_task() -> None:
    """abort first; cancel_task wait is bounded so overwrite is not stuck."""
    import asyncio

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    agent._interaction_session = MagicMock()
    agent.abort = AsyncMock()

    async def _slow_cancel_task(_task_id: str):
        await asyncio.sleep(10)

    scheduler = MagicMock()
    scheduler.cancel_task = _slow_cancel_task
    controller = MagicMock()
    controller.task_scheduler = scheduler
    agent._loop_controller = controller

    work = RoundWorkItem(
        kind="goal",
        request_id="req-cancel",
        inputs={"query": "overwrite me"},
        context={"goal_id": "g1", "revision": 1},
    )
    agent._active_interaction_round = ActiveInteractionRound(
        work=work,
        task_id="task-slow",
    )

    async def _noop_round():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    agent._interaction_round_task = asyncio.create_task(_noop_round())

    started = asyncio.get_running_loop().time()
    await agent._cancel_active_round(
        reason="goal_overwrite",
        wait_timeout=0.05,
    )
    elapsed = asyncio.get_running_loop().time() - started

    agent.abort.assert_awaited_once()
    assert elapsed < 1.0
    assert agent._interaction_round_task.cancelled() or agent._interaction_round_task.done()


# ------------------------------------------------- explicit steer dispatch


def _steer(
    request_id: str = "steer-1",
    *,
    expected_round_id: str | None = None,
) -> SendInputRequest:
    return SendInputRequest(
        request_id=request_id,
        inputs={"query": "prefer the async client"},
        mode=InputDispatchMode.STEER,
        expected_round_id=expected_round_id,
    )


@pytest.mark.asyncio
async def test_steer_with_active_round_is_queued_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    loop = MagicMock()
    agent._loop_controller = loop
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="round-1", inputs={"query": "start"}),
        task_id="task-1",
    )

    result = await agent.send_input(_steer())

    assert result.accepted is True
    assert result.disposition is InputDisposition.STEER_QUEUED
    assert result.reason is None
    # The id must ride along, not just the text. STEER_APPLIED builds ``dropped``
    # from these ids, so queuing a bare string makes every dropped steer
    # invisible -- reported as applied when a rail actually removed it.
    loop.enqueue_steer.assert_called_once_with(
        SteeringInput(text="prefer the async client", id="steer-1")
    )


@pytest.mark.asyncio
async def test_the_request_id_survives_the_whole_queue_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through a real controller and queue, not a mock.

    The mock above proves ``send_input`` passes an id to ``enqueue_steer``. It
    cannot prove the id is still there when the ReAct loop drains it, because
    every hop between them could drop it: ``enqueue_steer`` narrowed its
    parameter to ``str`` for a while, and coercion happens in two different
    drains. That gap is exactly how ``dropped`` came to be permanently empty
    while ten unit tests passed -- they all built SteeringInput by hand and so
    never crossed a single one of these hops.
    """
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    queues = LoopQueues()
    controller = TaskLoopController.__new__(TaskLoopController)
    monkeypatch.setattr(
        controller, "_get_interaction_queues", lambda: queues, raising=False
    )
    agent._loop_controller = controller
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="round-1", inputs={"query": "start"}),
        task_id="task-1",
    )

    await agent.send_input(_steer("steer-real-path"))

    drained = queues.drain_steering()
    assert [item.text for item in drained] == ["prefer the async client"]
    assert [item.id for item in drained] == ["steer-real-path"]


@pytest.mark.asyncio
async def test_steer_with_matching_expected_round_id_is_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    loop = MagicMock()
    agent._loop_controller = loop
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="round-a", inputs={"query": "start"}),
        task_id="task-1",
    )

    result = await agent.send_input(_steer(expected_round_id="round-a"))

    assert result.accepted is True
    assert result.disposition is InputDisposition.STEER_QUEUED
    loop.enqueue_steer.assert_called_once()


@pytest.mark.asyncio
async def test_steer_with_wrong_expected_round_id_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    loop = MagicMock()
    agent._loop_controller = loop
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="round-a", inputs={"query": "start"}),
        task_id="task-1",
    )

    result = await agent.send_input(_steer("steer-stale-round", expected_round_id="round-b"))

    assert result.accepted is False
    assert result.reason == "round_mismatch"
    assert result.disposition is InputDisposition.REJECTED
    loop.enqueue_steer.assert_not_called()


@pytest.mark.asyncio
async def test_idle_steer_is_rejected_instead_of_starting_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance criterion: no silent downgrade to a fresh turn.

    Rejection requires *both* no active round and nothing keeping the
    interaction open -- genuinely nothing to steer.  Before the dispatch result
    existed this case fell into the fresh-turn path, so a stale steer started a
    whole turn the user never asked for, indistinguishable from success at the
    wire.
    """
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    monkeypatch.setattr(
        agent, "_should_keep_interaction_open_locked", MagicMock(return_value=False)
    )

    loop = MagicMock()
    agent._loop_controller = loop
    assert agent._active_interaction_round is None

    result = await agent.send_input(_steer("steer-stale"))

    assert result.accepted is False
    assert result.reason == "no_active_round"
    assert result.disposition is InputDisposition.REJECTED

    # The load-bearing assertion: nothing was queued at all. A rejected steer
    # must not become a follow-up, a fresh turn, or a steer on a later round.
    assert agent._event_manager.next_work() is None
    loop.enqueue_steer.assert_not_called()


@pytest.mark.asyncio
async def test_steer_with_open_interaction_still_queues_as_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the Goal-supplement path.

    While a Goal is ACTIVE the Web sends ordinary input as
    ``input_mode="steer"`` (``useWebSocket.ts:1474``) so it lands as a
    supplementary constraint on the running Goal rather than overwriting it.
    Between attempts there is legitimately no active round, so an
    unconditional rejection would silently drop the user's message.

    It would also stall the Goal: ``_should_keep_interaction_open_locked`` is
    what queues the next attempt, and returning before it skips that.
    """
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    keep_open = MagicMock(return_value=True)
    monkeypatch.setattr(agent, "_should_keep_interaction_open_locked", keep_open)

    agent._loop_controller = MagicMock()
    assert agent._active_interaction_round is None

    result = await agent.send_input(_steer("steer-during-goal"))

    assert result.accepted is True
    assert result.disposition is InputDisposition.FOLLOW_UP_QUEUED

    work = agent._event_manager.next_work()
    assert work is not None
    assert work.query == "prefer the async client"

    # The side effect matters as much as the queued work: this is the call that
    # schedules the Goal's next attempt.
    keep_open.assert_called_once()


@pytest.mark.asyncio
async def test_accepted_paths_report_their_own_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every accepted path names what it did, not merely that it succeeded."""
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    fresh = await agent.send_input(
        SendInputRequest(request_id="turn-1", inputs={"query": "hello"})
    )
    assert fresh.accepted is True
    assert fresh.disposition is InputDisposition.TURN_QUEUED

    interactive_input = InteractiveInput()
    interactive_input.update("call_1", {"action": "allow_once"})
    resumed = await agent.send_input(
        SendInputRequest(request_id="resume-1", inputs={"query": interactive_input})
    )
    assert resumed.accepted is True
    assert resumed.disposition is InputDisposition.RESUME_QUEUED


@pytest.mark.asyncio
async def test_undrained_steers_are_dropped_when_the_round_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A steer ACKed after the last model call must not reach the next round.

    The session steering queue outlives one interaction round. Without a
    teardown flush, ``steer_queued`` text sits until the following round's
    first drain and is admitted under the wrong turn.
    """
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    queues = LoopQueues()
    controller = TaskLoopController.__new__(TaskLoopController)
    monkeypatch.setattr(
        controller, "_get_interaction_queues", lambda: queues, raising=False
    )
    agent._loop_controller = controller
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="round-1", inputs={"query": "start"}),
        task_id="task-1",
    )

    await agent.send_input(_steer("steer-late"))
    assert queues.steering.qsize() == 1

    events: list = []
    monkeypatch.setattr(
        agent, "_emit_interaction_event", lambda event: events.append(event)
    )

    agent._drop_undrained_steering()

    assert queues.steering.qsize() == 0
    assert len(events) == 1
    assert events[0].type is InteractionEventType.STEER_APPLIED
    assert events[0].payload["applied"] == []
    assert events[0].payload["dropped"] == ["steer-late"]


@pytest.mark.asyncio
async def test_steer_with_expected_round_id_and_no_active_round_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client-bound round id must not fall through to follow_up_queued.

    ``chat.steer`` stamps ``expected_round_id``. When that round is already
    gone, promoting the text into a Goal follow-up is a silent race win.
    """
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    monkeypatch.setattr(agent, "_notify_work", MagicMock())
    keep_open = MagicMock(return_value=True)
    monkeypatch.setattr(agent, "_should_keep_interaction_open_locked", keep_open)
    loop = MagicMock()
    agent._loop_controller = loop
    assert agent._active_interaction_round is None

    result = await agent.send_input(
        _steer("steer-stale-bound", expected_round_id="round-gone")
    )

    assert result.accepted is False
    assert result.reason == "round_mismatch"
    assert result.disposition is InputDisposition.REJECTED
    loop.enqueue_steer.assert_not_called()
    assert agent._event_manager.next_work() is None
    keep_open.assert_not_called()
