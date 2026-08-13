# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the session-scoped DeepAgent interaction entrypoint."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.interaction import (
    ActiveInteractionRound,
    InputDispatchMode,
    RoundWorkItem,
    SendInputRequest,
)
from openjiuwen.harness.tools.worktree import session as worktree_session_state
from openjiuwen.harness.tools.worktree.models import WorktreeSession
from openjiuwen.harness.tools.worktree.session import (
    get_current_session,
    set_current_session,
)
from tests.unit_tests.agent_teams.harness.fixtures import (
    FakeReactAgent,
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


@pytest.mark.asyncio
async def test_interaction_timeout_is_visible_and_cancels_real_executor() -> None:
    """The real task-loop kernel must not leave a timed-out executor running."""
    await Runner.start()
    agent = DeepAgent(AgentCard(name="timeout-integration", description="test"))
    agent.configure(
        DeepAgentConfig(
            enable_task_loop=True,
            completion_timeout=0.01,
        )
    )
    fake = FakeReactAgent(agent.card)
    fake.sleep_seconds = 1.0
    agent.set_react_agent(fake, initialized=True)

    try:
        await agent.start()
        stream = await agent.attach_output()
        assert stream is not None

        await agent.send_input(
            SendInputRequest(
                request_id="timeout-integration",
                inputs={"query": "slow round"},
            )
        )
        chunks = [chunk async for chunk in stream]

        answers = [chunk.payload for chunk in chunks if getattr(chunk, "type", None) == "answer"]
        assert answers == [
            {
                "output": "Task loop round timed out after 0.01 seconds.",
                "result_type": "error",
            }
        ]
        assert fake.cancelled_count == 1
    finally:
        await agent.stop()
        await Runner.stop()
