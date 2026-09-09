# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the session-scoped DeepAgent interaction entrypoint."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from contextlib import asynccontextmanager
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
    InteractionPhase,
    RoundOutcome,
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

    await agent.send_input(SendInputRequest(request_id="text-2", inputs={"query": "next turn"}))
    next_work = agent._event_manager.next_work()
    assert next_work is not None
    assert next_work.request_id == "text-2"


@pytest.mark.asyncio
async def test_fresh_input_context_spans_enqueue_and_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    events: list[str] = []

    @asynccontextmanager
    async def fresh_context():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    original_push = agent._event_manager.push_user

    def push_user(work: RoundWorkItem) -> None:
        events.append("enqueue")
        original_push(work)

    agent.set_fresh_input_context_factory(fresh_context)
    monkeypatch.setattr(agent._event_manager, "push_user", push_user)
    monkeypatch.setattr(agent, "_notify_work", lambda: events.append("notify"))

    await agent.send_input(SendInputRequest(request_id="fresh-1", inputs={"query": "start"}))

    assert events == ["enter", "enqueue", "notify", "exit"]
    assert agent._event_manager.next_work().request_id == "fresh-1"


@pytest.mark.asyncio
async def test_continuation_inputs_bypass_fresh_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True

    @asynccontextmanager
    async def unexpected_context():
        raise AssertionError("continuation entered fresh context")
        yield

    agent.set_fresh_input_context_factory(unexpected_context)
    monkeypatch.setattr(agent, "_notify_work", MagicMock())

    resume = InteractiveInput()
    resume.update("call-1", {"action": "allow_once"})
    await agent.send_input(SendInputRequest(request_id="resume-1", inputs={"query": resume}))

    monkeypatch.setattr(agent, "_should_keep_interaction_open_locked", lambda: True)
    await agent.send_input(SendInputRequest(request_id="follow-1", inputs={"query": "continue"}))

    loop = MagicMock()
    agent._loop_controller = loop
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem.user(request_id="active", inputs={"query": "active"})
    )
    await agent.send_input(
        SendInputRequest(
            request_id="steer-1",
            inputs={"query": "adjust"},
            mode=InputDispatchMode.STEER,
        )
    )

    assert agent._event_manager.next_work().request_id == "resume-1"
    assert agent._event_manager.next_work().request_id == "follow-1"
    loop.enqueue_steer.assert_called_once_with("adjust")


@pytest.mark.asyncio
async def test_fresh_context_entry_failure_enqueues_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True

    @asynccontextmanager
    async def failing_context():
        raise RuntimeError("fresh_context_failed")
        yield

    agent.set_fresh_input_context_factory(failing_context)
    notify = MagicMock()
    monkeypatch.setattr(agent, "_notify_work", notify)

    with pytest.raises(RuntimeError, match="fresh_context_failed"):
        await agent.send_input(SendInputRequest(request_id="fresh-1", inputs={"query": "start"}))

    assert agent._event_manager.next_work() is None
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_context_cancellation_enqueues_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    entered = asyncio.Event()
    release = asyncio.Event()

    @asynccontextmanager
    async def blocking_context():
        entered.set()
        await release.wait()
        yield

    agent.set_fresh_input_context_factory(blocking_context)
    notify = MagicMock()
    monkeypatch.setattr(agent, "_notify_work", notify)
    task = asyncio.create_task(agent.send_input(SendInputRequest(request_id="fresh-1", inputs={"query": "start"})))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert agent._event_manager.next_work() is None
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_failure_exits_fresh_context_and_preserves_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    events: list[str] = []

    @asynccontextmanager
    async def fresh_context():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fail_enqueue(_work: RoundWorkItem) -> None:
        events.append("enqueue")
        raise RuntimeError("enqueue_failed")

    agent.set_fresh_input_context_factory(fresh_context)
    monkeypatch.setattr(agent._event_manager, "push_user", fail_enqueue)

    with pytest.raises(RuntimeError, match="enqueue_failed"):
        await agent.send_input(SendInputRequest(request_id="fresh-1", inputs={"query": "start"}))

    assert events == ["enter", "enqueue", "exit"]


@pytest.mark.asyncio
async def test_fresh_context_factory_replacement_clear_and_stop_cleanup() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    entered: list[str] = []

    def factory(name: str):
        @asynccontextmanager
        async def context():
            entered.append(name)
            yield

        return context

    agent.set_fresh_input_context_factory(factory("old"))
    agent.set_fresh_input_context_factory(factory("new"))
    await agent.send_input(SendInputRequest(request_id="fresh-1", inputs={"query": "start"}))
    agent._event_manager.next_work()
    agent.set_fresh_input_context_factory(None)
    await agent.send_input(SendInputRequest(request_id="fresh-2", inputs={"query": "continue"}))
    await agent.stop()

    assert entered == ["new"]
    assert agent._fresh_input_context_factory is None


@pytest.mark.asyncio
async def test_stop_drains_work_without_replacing_event_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    event_manager = agent._event_manager
    user_queue = event_manager._user_queue
    goal_queue = event_manager._goal_queue
    event_manager.push_user(RoundWorkItem.user(request_id="pending", inputs={"query": "pending"}))
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()

    async def shutdown():
        shutdown_entered.set()
        await shutdown_release.wait()

    cancel_active = AsyncMock()
    monkeypatch.setattr(agent._interaction_output, "shutdown", shutdown)
    monkeypatch.setattr(agent, "_cancel_active_round", cancel_active)

    stop_task = asyncio.create_task(agent.stop())
    await shutdown_entered.wait()
    event_manager.push_user(RoundWorkItem.user(request_id="during-stop", inputs={"query": "late"}))
    shutdown_release.set()
    await stop_task

    assert agent._event_manager is event_manager
    assert event_manager._user_queue is user_queue
    assert event_manager._goal_queue is goal_queue
    assert event_manager.next_work() is None
    cancel_active.assert_awaited_once_with(reason="stop")


def test_interaction_phase_transition_keeps_terminal_absorbing() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))

    assert agent._try_transition_interaction_phase(InteractionPhase.RUNNING) is True
    assert agent.phase is InteractionPhase.RUNNING
    assert agent._try_transition_interaction_phase(InteractionPhase.IDLE) is True
    assert agent.phase is InteractionPhase.IDLE
    assert agent._try_transition_interaction_phase(InteractionPhase.TERMINATED) is True
    assert agent._try_transition_interaction_phase(InteractionPhase.TERMINATED) is True
    assert agent._try_transition_interaction_phase(InteractionPhase.IDLE) is False
    assert agent._try_transition_interaction_phase(InteractionPhase.RUNNING) is False
    assert agent.phase is InteractionPhase.TERMINATED
    with pytest.raises(TypeError, match="must be InteractionPhase"):
        agent._try_transition_interaction_phase("idle")  # type: ignore[arg-type]


def test_interaction_phase_has_only_initial_and_transition_writers() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(DeepAgent)))
    writers: set[str] = set()
    for member in tree.body[0].body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(member):
            targets: list[ast.expr] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            if any(
                isinstance(target, ast.Attribute)
                and target.attr == "_interaction_phase"
                for target in targets
            ):
                writers.add(member.name)

    assert writers == {"__init__", "_try_transition_interaction_phase"}


@pytest.mark.asyncio
async def test_stop_retires_unstarted_agent_before_default_session_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.core.session import agent as agent_session_module

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    create_session = MagicMock()
    monkeypatch.setattr(agent_session_module, "create_agent_session", create_session)

    await agent.stop()

    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await agent.start()
    create_session.assert_not_called()


@pytest.mark.parametrize("blocked_stage", ["pre_run", "prepare", "register"])
@pytest.mark.asyncio
async def test_stop_waits_for_in_progress_start_setup(
    monkeypatch: pytest.MonkeyPatch,
    blocked_stage: str,
) -> None:
    from openjiuwen.core.session import agent as agent_session_module

    agent = DeepAgent(AgentCard(name="deep", description="test"))
    session = MagicMock()
    session.get_session_id.return_value = "serialized-start"
    session.close_stream = AsyncMock()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block(*_args, **_kwargs):
        entered.set()
        await release.wait()

    session.pre_run = block if blocked_stage == "pre_run" else AsyncMock()

    async def prepare(_session):
        if blocked_stage == "prepare":
            await block()

    async def register(_rail):
        if blocked_stage == "register":
            await block()

    monkeypatch.setattr(agent_session_module, "create_agent_session", MagicMock(return_value=session))
    monkeypatch.setattr(agent, "prepare_interaction_task_loop", prepare)
    monkeypatch.setattr(agent, "register_rail", register)
    monkeypatch.setattr(agent, "_forward_session_stream", AsyncMock())
    monkeypatch.setattr(agent, "_ensure_supervisor_running", MagicMock())
    agent._task_completion_rail = None if blocked_stage == "register" else object()  # type: ignore[assignment]
    start_task = asyncio.create_task(
        agent.start(session=None if blocked_stage == "pre_run" else session)
    )
    await entered.wait()

    stop_task = asyncio.create_task(agent.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    release.set()
    await start_task
    await stop_task

    assert agent.interaction_started is False
    assert agent.phase is InteractionPhase.TERMINATED


@pytest.mark.asyncio
async def test_same_session_start_waits_for_stop_then_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    agent._bound_session_id = "same-session"
    session = MagicMock()
    session.get_session_id.return_value = "same-session"
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()

    async def shutdown():
        shutdown_entered.set()
        await shutdown_release.wait()

    monkeypatch.setattr(agent._interaction_output, "shutdown", shutdown)
    stop_task = asyncio.create_task(agent.stop())
    await shutdown_entered.wait()
    assert agent.interaction_started is True
    assert agent.phase is InteractionPhase.TERMINATED
    start_task = asyncio.create_task(agent.start(session=session))
    await asyncio.sleep(0)
    assert not start_task.done()

    shutdown_release.set()
    await stop_task
    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await start_task


@pytest.mark.asyncio
async def test_unstarted_execute_round_has_no_side_effects() -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    work = RoundWorkItem.user(request_id="unstarted", inputs={"query": "ignored"})

    await agent._execute_round(work)

    assert agent.phase is InteractionPhase.IDLE
    assert agent.active_round is None
    assert agent._event_manager.active_work is None


@pytest.mark.asyncio
async def test_stop_while_waiting_for_send_lock_prevents_late_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    notify = MagicMock()
    send_user = AsyncMock()
    monkeypatch.setattr(agent, "_notify_work", notify)
    monkeypatch.setattr(agent, "_send_user", send_user)
    await agent._interaction_send_lock.acquire()
    task = asyncio.create_task(agent.send_input(SendInputRequest(request_id="late", inputs={"query": "late"})))
    await asyncio.sleep(0)

    await agent.stop()
    agent._interaction_send_lock.release()

    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await task
    send_user.assert_not_awaited()
    assert agent._event_manager.next_work() is None
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_stop_while_waiting_for_control_lock_prevents_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    notify = MagicMock()
    monkeypatch.setattr(agent, "_notify_work", notify)
    resume = InteractiveInput()
    resume.update("call-1", {"action": "allow_once"})
    await agent._interaction_control_lock.acquire()
    task = asyncio.create_task(agent.send_input(SendInputRequest(request_id="late-resume", inputs={"query": resume})))
    for _ in range(10):
        if agent._interaction_send_lock.locked():
            break
        await asyncio.sleep(0)
    assert agent._interaction_send_lock.locked()

    await agent.stop()
    agent._interaction_control_lock.release()

    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await task
    assert agent._event_manager.next_work() is None
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_stop_during_fresh_context_entry_prevents_late_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    entered = asyncio.Event()
    release = asyncio.Event()
    exited = asyncio.Event()

    @asynccontextmanager
    async def blocking_context():
        entered.set()
        await release.wait()
        try:
            yield
        finally:
            exited.set()

    agent.set_fresh_input_context_factory(blocking_context)
    notify = MagicMock()
    monkeypatch.setattr(agent, "_notify_work", notify)
    task = asyncio.create_task(agent.send_input(SendInputRequest(request_id="late-fresh", inputs={"query": "late"})))
    await entered.wait()

    await agent.stop()
    release.set()

    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await task
    assert exited.is_set()
    assert agent._event_manager.next_work() is None
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_stop_prevents_active_round_from_requeueing_next_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    agent._interaction_session = MagicMock()
    round_entered = asyncio.Event()
    round_release = asyncio.Event()
    shutdown_entered = asyncio.Event()
    shutdown_release = asyncio.Event()
    next_work = RoundWorkItem.user(request_id="next", inputs={"query": "next"})

    async def run_one_round(*_args):
        round_entered.set()
        await round_release.wait()
        return RoundOutcome(next_work=next_work)

    async def shutdown():
        shutdown_entered.set()
        await shutdown_release.wait()

    monkeypatch.setattr(agent, "run_one_round", run_one_round)
    monkeypatch.setattr(agent._interaction_output, "has_consumer", lambda: True)
    monkeypatch.setattr(agent._interaction_output, "shutdown", shutdown)
    monkeypatch.setattr(agent, "_emit_round_boundary", AsyncMock(return_value=True))
    work = RoundWorkItem.user(request_id="active", inputs={"query": "active"})
    round_task = asyncio.create_task(agent._execute_round(work))
    await round_entered.wait()

    stop_task = asyncio.create_task(agent.stop())
    await shutdown_entered.wait()
    round_release.set()
    await round_task
    shutdown_release.set()
    await stop_task

    assert agent.phase is InteractionPhase.TERMINATED
    assert agent._event_manager.next_work() is None


@pytest.mark.asyncio
async def test_stop_during_attach_await_prevents_goal_requeue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    attach_entered = asyncio.Event()
    attach_release = asyncio.Event()
    load_goal = MagicMock()
    agent.goal_manager = MagicMock()

    async def blocking_attach():
        attach_entered.set()
        await attach_release.wait()
        return MagicMock()

    monkeypatch.setattr(agent, "_attach_output_locked", blocking_attach)
    monkeypatch.setattr(agent, "_load_goal_record_locked", load_goal)
    attach_task = asyncio.create_task(agent.attach_output())
    await attach_entered.wait()

    await agent.stop()
    attach_release.set()

    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await attach_task
    load_goal.assert_not_called()
    assert agent._event_manager.next_work() is None


@pytest.mark.asyncio
async def test_supervisor_cannot_restore_idle_after_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    close_entered = asyncio.Event()
    close_release = asyncio.Event()

    async def blocking_close():
        close_entered.set()
        await close_release.wait()

    monkeypatch.setattr(agent._interaction_output, "has_consumer", lambda: True)
    monkeypatch.setattr(agent, "_close_idle_output_if_finished", blocking_close)
    supervisor_task = asyncio.create_task(agent._supervisor_loop())
    await close_entered.wait()

    await agent.stop()
    close_release.set()
    await supervisor_task

    assert agent.phase is InteractionPhase.TERMINATED
    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await agent.send_input(SendInputRequest(request_id="late", inputs={"query": "late"}))
    with pytest.raises(RuntimeError, match="interaction_terminated"):
        await agent.attach_output()


@pytest.mark.asyncio
async def test_supervisor_exception_cannot_restore_idle_after_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    promote_entered = asyncio.Event()
    promote_release = asyncio.Event()

    async def failing_promote():
        promote_entered.set()
        await promote_release.wait()
        raise RuntimeError("promote_failed")

    monkeypatch.setattr(agent._interaction_output, "has_consumer", lambda: True)
    monkeypatch.setattr(agent, "_promote_loop_follow_ups", failing_promote)
    supervisor_task = asyncio.create_task(agent._supervisor_loop())
    await promote_entered.wait()

    await agent.stop()
    promote_release.set()
    await supervisor_task

    assert agent.phase is InteractionPhase.TERMINATED


@pytest.mark.asyncio
async def test_fresh_context_cannot_suppress_enqueue_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    primary_error = RuntimeError("enqueue_failed")

    class SuppressingContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return True

    agent.set_fresh_input_context_factory(SuppressingContext)
    monkeypatch.setattr(
        agent._event_manager,
        "push_user",
        MagicMock(side_effect=primary_error),
    )

    with pytest.raises(RuntimeError) as raised:
        await agent.send_input(SendInputRequest(request_id="fresh", inputs={"query": "start"}))
    assert raised.value is primary_error


@pytest.mark.parametrize("exit_error_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_fresh_context_exit_error_cannot_replace_enqueue_error(
    monkeypatch: pytest.MonkeyPatch,
    exit_error_type: type[BaseException],
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    primary_error = RuntimeError("enqueue_failed")
    exit_error = exit_error_type("exit_failed")

    class FailingExitContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            raise exit_error

    agent.set_fresh_input_context_factory(FailingExitContext)
    monkeypatch.setattr(
        agent._event_manager,
        "push_user",
        MagicMock(side_effect=primary_error),
    )

    with pytest.raises(RuntimeError) as raised:
        await agent.send_input(SendInputRequest(request_id="fresh", inputs={"query": "start"}))
    assert raised.value is primary_error
    assert raised.value.__cause__ is exit_error


@pytest.mark.asyncio
async def test_task_cancellation_during_context_exit_cannot_replace_enqueue_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._interaction_started = True
    primary_error = RuntimeError("enqueue_failed")
    exit_entered = asyncio.Event()

    class BlockingExitContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            exit_entered.set()
            await asyncio.Event().wait()

    agent.set_fresh_input_context_factory(BlockingExitContext)
    monkeypatch.setattr(
        agent._event_manager,
        "push_user",
        MagicMock(side_effect=primary_error),
    )
    send_task = asyncio.create_task(
        agent.send_input(SendInputRequest(request_id="fresh", inputs={"query": "start"}))
    )
    await exit_entered.wait()

    send_task.cancel()
    with pytest.raises(RuntimeError) as raised:
        await send_task
    assert raised.value is primary_error
    assert isinstance(raised.value.__cause__, asyncio.CancelledError)


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
    assert react_agent.invoke.await_args.kwargs == {"_streaming": True}
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
