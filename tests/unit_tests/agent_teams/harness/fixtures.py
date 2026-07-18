# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Test fixtures for NativeHarness against the real task-loop kernel.

The NativeHarness now *is* a DeepAgent and drives the full task-loop kernel
(TaskLoopController / TaskScheduler / TaskLoopEventHandler / LoopCoordinator),
all real. The only fake is the inner ``react_agent``: ``start()`` builds the
real kernel and a real react_agent, and tests then call
``set_react_agent(FakeReactAgent(...), initialized=True)`` so the real
TaskLoopEventExecutor drives the fake.

Wiring contract the fake must honor (mirrors ``TaskLoopEventExecutor``):
- The executor calls ``invoke(effective, session, _streaming=True)`` where
  ``effective`` is a dict containing ``query``, ``conversation_id`` and a
  ``_steering_queue`` (the shared LoopQueues.steering ``asyncio.Queue``).
- The executor — not the fake — fires BEFORE/AFTER_TASK_ITERATION on the outer
  DeepAgent, so the outer boundary snapshot fires automatically. The fake must
  NOT fire task-iteration events itself.
- ``write_invoke_result_to_stream`` writes the final round answer (called by
  ``DeepAgent._write_round_result_to_stream``).
- ``context_engine.get_context(session_id).get_messages/set_messages`` back the
  snapshot/rollback machinery; ``clear_context_messages`` is the no-snapshot
  rollback fallback.

``invoke`` runs a **miniature ReAct loop** that mirrors the real inner loop's
hook order and stop points, so phase-aware pause/abort can be exercised:

    iteration top   -> consume_force_finish (boundary stop)
    BEFORE_MODEL_CALL -> consume_force_finish (a before-hook stop skips the LLM)
    <model sleep>   -- the ``model_call_in_flight`` window (hard-cancel-safe)
    AFTER_MODEL_CALL  -> consume_force_finish (stop before the assistant msg
                          and therefore before any tool starts)
    BEFORE_TOOL_CALL / <tool sleep> / AFTER_TOOL_CALL  -- irreversible window
    AFTER_REACT_ITERATION  -- clean boundary; the rail snapshots here

Scripting knobs: ``iterations`` (inner iterations before the final answer),
``sleep_seconds`` (model-call window; also the classic cancellation point),
``emit_tools`` + ``tool_sleep_seconds`` (tool window), ``raise_exc`` /
``raise_exc_once``. The fake drains the steering queue, records each call's
inputs, and counts observed ``CancelledError``\\ s (the cancel-chain assertion).

``set_react_agent`` does not re-wire rails, so tests must call
``rebind_bridge_rails`` (``start_harness`` does) to re-register the harness's
BRIDGE-event hooks onto the injected fake — otherwise phase tracking is dead.
"""
from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.agent_callback_manager import (
    AgentCallbackManager,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


class MockContext:
    """Stand-in for a ContextEngine context with history/current segmentation.

    Mirrors ContextMessageBuffer: messages live in one list split at
    ``_history_size``; ``with_history=False`` operates only on the current
    segment, so snapshot capture (``with_history=False``) and rollback rewind
    only the in-progress round, never the persisted history.
    """

    def __init__(self) -> None:
        self._messages: list[Any] = []
        self._history_size: int = 0

    def seed_history(self, messages: list[Any]) -> None:
        """Install persisted-history messages (the segment rollback must keep)."""
        self._messages = list(messages)
        self._history_size = len(messages)

    def get_messages(self, size: int | None = None, with_history: bool = True) -> list[Any]:
        """Return messages; with_history=False returns only the current segment."""
        _ = size
        if with_history:
            return list(self._messages)
        return list(self._messages[self._history_size:])

    def set_messages(self, messages: list[Any], with_history: bool = True) -> None:
        """Replace messages; with_history=False replaces only the current segment."""
        if with_history:
            self._messages = list(messages)
            self._history_size = 0
            return
        self._messages = self._messages[:self._history_size] + list(messages)

    def add_message(self, msg: Any) -> None:
        """Append one message to the current segment."""
        self._messages.append(msg)


class MockContextEngine:
    """Minimal stand-in for ContextEngine keyed by session id."""

    def __init__(self) -> None:
        self._contexts: dict[str, MockContext] = {}

    def get_context(self, session_id: str, context_id: str = "default_context_id") -> MockContext:
        """Return (creating if needed) the context for a session id."""
        _ = context_id
        if session_id not in self._contexts:
            self._contexts[session_id] = MockContext()
        return self._contexts[session_id]


class FakeReactAgent:
    """Scriptable inner react_agent driven by the real TaskLoopEventExecutor.

    Attributes:
        card: Owning agent card (the harness's card).
        context_engine: Backs snapshot capture / rollback.
        invocations: One entry per ``invoke`` call (the ``effective`` dict).
        cancelled_count: Times ``invoke`` observed a CancelledError mid-run.
        seen_steers: Steering messages drained from ``_steering_queue`` across
            all invocations.
        sleep_seconds: Seconds ``invoke`` sleeps before emitting its chunk; a
            cancellation point for abort/pause tests (set high to model a
            long-running LLM call).
        answer_output: ``output`` field of the result this fake returns; when
            empty it echoes the query.
    """

    def __init__(self, card: AgentCard) -> None:
        self.card = card
        self.context_engine = MockContextEngine()
        self._agent_callback_manager = AgentCallbackManager(card.id)
        self.invocations: list[dict[str, Any]] = []
        self.cancelled_count: int = 0
        self.seen_steers: list[str] = []
        self.sleep_seconds: float = 0.0
        self.answer_output: str = ""
        # When set, ``invoke`` raises this after its sleep window — models an
        # inner-round failure so tests can exercise the executor's error path
        # (e.g. AFTER_TASK_ITERATION firing on failure).
        self.raise_exc: BaseException | None = None
        # One-shot variant: raised by exactly one ``invoke`` then cleared, so
        # tests can model "first round crashes, its retry succeeds" without
        # racing the harness's automatic failure retry.
        self.raise_exc_once: BaseException | None = None
        # Set the moment ``invoke`` enters its model-call sleep window; lets
        # tests wait for the real inner work to actually be in-flight before
        # aborting/pausing, instead of racing the phase transition (RUNNING is
        # set when the round task is created, before the executor reaches
        # ``invoke``). This window is exactly ``model_call_in_flight``.
        self.invoke_running: asyncio.Event = asyncio.Event()
        # Inner iterations to run before returning the final answer. >= 2 lets a
        # test pause/abort with a completed boundary already snapshotted.
        self.iterations: int = 1
        # Apply ``sleep_seconds`` only from this iteration onwards, so a test can
        # let the first iteration complete (snapshotting a boundary) and then
        # park the next one inside its model call.
        self.sleep_from_iteration: int = 0
        # Tool phase: when enabled the loop fires BEFORE/AFTER_TOOL_CALL around
        # a ``tool_sleep_seconds`` window — the irreversible-side-effect window
        # a pause must never interrupt.
        self.emit_tools: bool = False
        self.tool_sleep_seconds: float = 0.0
        self.tool_running: asyncio.Event = asyncio.Event()
        # Observability for assertions.
        self.completed_iterations: int = 0
        self.completed_tools: int = 0

    @property
    def agent_callback_manager(self) -> AgentCallbackManager:
        """Expose the callback manager (SnapshotRail registers onto the outer agent)."""
        return self._agent_callback_manager

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: Any,
        priority: int = 100,
    ) -> "FakeReactAgent":
        """Register a callback, mirroring BaseAgent.register_callback."""
        await self._agent_callback_manager.register_callback(event, callback, priority)
        return self

    async def clear_context_messages(
        self,
        session_id: str,
        context_id: str = "default_context_id",
    ) -> None:
        """Clear the current-round messages, keeping persisted history."""
        _ = context_id
        ctx = self.context_engine.get_context(session_id=session_id)
        ctx.set_messages([], with_history=False)

    async def invoke(self, inputs: Any, session: Session, **kwargs: Any) -> dict:
        """Run one outer round as a miniature ReAct loop (see module docstring).

        Fires the inner-loop hooks in the real order and honours ``force_finish``
        at exactly the three points the real loop does, so pause/abort can be
        exercised per phase. A continuation round appends no new user turn.
        """
        self.invocations.append(inputs if isinstance(inputs, dict) else {"query": inputs})
        query = inputs["query"] if isinstance(inputs, dict) else inputs
        resume_continuation = False
        if isinstance(inputs, dict):
            steering_q = inputs.get("_steering_queue")
            if steering_q is not None:
                while not steering_q.empty():
                    self.seen_steers.append(steering_q.get_nowait())
            resume_continuation = bool(inputs.get("_resume_continuation"))

        context = self.context_engine.get_context(session_id=session.get_session_id())
        # Mirrors react_agent: a continuation resumes the preserved context.
        if not resume_continuation:
            context.add_message(UserMessage(content=str(query)))

        ctx = AgentCallbackContext(agent=self, session=session)
        try:
            finish = await self._run_inner_loop(ctx, context)
        except asyncio.CancelledError:
            self.cancelled_count += 1
            raise
        if finish is not None:
            return finish

        if self.raise_exc_once is not None:
            exc_once = self.raise_exc_once
            self.raise_exc_once = None
            raise exc_once
        if self.raise_exc is not None:
            raise self.raise_exc

        await session.write_stream(
            OutputSchema(type="mock_chunk", index=0, payload={"query": str(query)}),
        )
        output = self.answer_output or f"echo:{query}"
        return {"output": output, "result_type": "answer"}

    async def _run_inner_loop(self, ctx: AgentCallbackContext, context: MockContext) -> dict | None:
        """Drive the scripted inner iterations; return a force-finish result, if any."""
        for _ in range(self.iterations):
            # Iteration top: a boundary force_finish stops before any work.
            finish = ctx.consume_force_finish()
            if finish is not None:
                return finish.result

            await ctx.fire(AgentCallbackEvent.BEFORE_MODEL_CALL)
            if ctx.has_force_finish_request:
                # Mirrors @rail: a before-hook force_finish skips the body, so
                # the LLM never runs and context stays at the boundary.
                finish = ctx.consume_force_finish()
                return finish.result if finish is not None else {}

            model_sleep = (
                self.sleep_seconds
                if self.completed_iterations >= self.sleep_from_iteration
                else 0.0
            )
            self.invoke_running.set()
            try:
                if model_sleep > 0:
                    await asyncio.sleep(model_sleep)
            finally:
                self.invoke_running.clear()

            await ctx.fire(AgentCallbackEvent.AFTER_MODEL_CALL)
            finish = ctx.consume_force_finish()
            if finish is not None:
                # Mirrors react_agent: break before the assistant message lands,
                # so no tool_call is ever committed.
                return finish.result

            context.add_message(UserMessage(content=f"assistant-{self.completed_iterations}"))

            if self.emit_tools:
                await ctx.fire(AgentCallbackEvent.BEFORE_TOOL_CALL)
                self.tool_running.set()
                try:
                    if self.tool_sleep_seconds > 0:
                        await asyncio.sleep(self.tool_sleep_seconds)
                finally:
                    self.tool_running.clear()
                self.completed_tools += 1
                await ctx.fire(AgentCallbackEvent.AFTER_TOOL_CALL)

            # Iteration fully succeeded: the rail snapshots this clean boundary.
            await ctx.fire(AgentCallbackEvent.AFTER_REACT_ITERATION)
            self.completed_iterations += 1
        return None

    async def write_invoke_result_to_stream(self, result: dict, session: Session) -> None:
        """Write the final round result to the session stream (mirrors the real method)."""
        await session.write_stream(
            OutputSchema(
                type="answer",
                index=0,
                payload={
                    "output": result.get("output", ""),
                    "result_type": result.get("result_type", ""),
                },
            ),
        )


def make_card(name: str = "native_harness_test") -> AgentCard:
    """Build a minimal AgentCard for tests."""
    return AgentCard(name=name, description="native-harness-test")


def make_spec(card: AgentCard | None = None, *, completion_timeout: float = 600.0) -> Any:
    """Build a fake DeepAgentSpec whose ``resolve_parts`` yields task-loop parts.

    Forward construction: NativeHarness configures itself from this spec's
    parts (a task-loop-enabled config, no model, no rails), builds the real
    task-loop kernel, then the test injects a FakeReactAgent via
    ``set_react_agent`` — the react_agent ``ensure_initialized`` would build is
    immediately replaced.

    Args:
        card: Optional card for the spec (and thus the harness).
        completion_timeout: Round completion timeout in seconds.

    Returns:
        A fake spec exposing ``resolve_parts(context) -> DeepAgentParts``.
    """
    from openjiuwen.harness.factory import DeepAgentParts

    agent_card = card or make_card()

    class _FakeSpec:
        def resolve_parts(self, context: Any = None) -> DeepAgentParts:
            return DeepAgentParts(
                config=DeepAgentConfig(
                    card=agent_card,
                    enable_task_loop=True,
                    completion_timeout=completion_timeout,
                ),
                rails=[],
                tool_cards=[],
                tool_instances=[],
            )

    return _FakeSpec()


async def rebind_bridge_rails(harness: Any, fake: FakeReactAgent) -> None:
    """Re-register the harness's BRIDGE-event rail hooks onto an injected fake.

    ``DeepAgent._register_rail_selective`` wires BRIDGE events (model / tool /
    react-iteration) onto whichever ``react_agent`` existed when the rails were
    initialised, and ``set_react_agent`` does not re-wire them. Tests inject the
    fake after ``start()``, so without this the fake would never fire
    PhaseSnapshotRail's hooks and phase tracking would be dead.
    """
    from openjiuwen.harness.deep_agent import _BRIDGE_EVENTS

    rails = list(getattr(harness, "_registered_rails", None) or [])
    if not rails:
        rails = [harness._snapshot_rail]
    for rail in rails:
        for event, callback in rail.get_callbacks().items():
            if event in _BRIDGE_EVENTS:
                await fake.register_callback(event, callback, rail.priority)


async def start_harness(
    harness: Any,
    *,
    sleep_seconds: float = 0.0,
    answer_output: str = "",
    iterations: int = 1,
    emit_tools: bool = False,
    tool_sleep_seconds: float = 0.0,
) -> FakeReactAgent:
    """Start a harness and inject a freshly-scripted FakeReactAgent.

    Builds the real task-loop kernel via ``start()``, swaps in the fake so the
    real executor drives it, then re-binds the harness's bridge rails onto the
    fake (``set_react_agent`` alone would leave phase tracking dead).

    Args:
        harness: The NativeHarness to start.
        sleep_seconds: Model-call window length (the classic cancellation point).
        answer_output: Initial answer output for the fake.
        iterations: Inner ReAct iterations before the final answer.
        emit_tools: Whether each iteration runs a tool phase.
        tool_sleep_seconds: Tool-execution window length.

    Returns:
        The injected FakeReactAgent (tests mutate its script between rounds).
    """
    await harness.start()
    fake = FakeReactAgent(harness.card)
    fake.sleep_seconds = sleep_seconds
    fake.answer_output = answer_output
    fake.iterations = iterations
    fake.emit_tools = emit_tools
    fake.tool_sleep_seconds = tool_sleep_seconds
    harness.set_react_agent(fake, initialized=True)
    await rebind_bridge_rails(harness, fake)
    return fake


async def drain_outputs(harness: Any, sink: list) -> None:
    """Background consumer: append every output chunk into ``sink``.

    outputs() is a single long stream for the harness's whole life; it ends
    only after ``stop()`` closes the stream. Tests spawn this as a task, do
    their work, call ``stop()``, then await the task.
    """
    async for chunk in harness.outputs():
        sink.append(chunk)


def mock_chunks(collected: list) -> list:
    """Filter collected chunks to the scripted ``mock_chunk`` payloads."""
    return [c.payload for c in collected if getattr(c, "type", None) == "mock_chunk"]


def answers(collected: list) -> list:
    """Filter collected chunks to ``answer`` payloads."""
    return [c.payload for c in collected if getattr(c, "type", None) == "answer"]


def answer_outputs(collected: list) -> list:
    """Extract the ``output`` of each collected ``answer`` chunk."""
    return [a["output"] for a in answers(collected)]


def aborted_markers(collected: list) -> list:
    """Filter collected chunks to ``round_aborted`` marker payloads."""
    return [c.payload for c in collected if getattr(c, "type", None) == "round_aborted"]


async def wait_for_state(harness: Any, state: Any, timeout: float = 3.0) -> bool:
    """Poll until the harness reaches ``state`` or the timeout elapses.

    Returns True if the state was reached, False on timeout. Avoids fixed
    ``sleep`` guesses so tests stay deterministic under load.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if harness.state is state:
            return True
        await asyncio.sleep(0.01)
    return harness.state is state


async def wait_invoke_running(fake: FakeReactAgent, timeout: float = 3.0) -> None:
    """Block until the fake's ``invoke`` is actually in its model-call window.

    The harness sets RUNNING when it creates the round task, which is before the
    executor reaches ``react_agent.invoke``. Aborting on RUNNING alone races the
    inner work; waiting on this event guarantees the CancelledError lands inside
    the model call (the cancel-chain contract, and the only hard-cancel-safe
    window). Clears the event so the next iteration can wait on it again.
    """
    await asyncio.wait_for(fake.invoke_running.wait(), timeout=timeout)
    fake.invoke_running.clear()


async def wait_tool_running(fake: FakeReactAgent, timeout: float = 3.0) -> None:
    """Block until the fake's tool phase is actually executing.

    The tool window is the irreversible one: a pause landing here must let the
    iteration run to completion rather than hard-cancel it mid-tool.
    """
    await asyncio.wait_for(fake.tool_running.wait(), timeout=timeout)
    fake.tool_running.clear()


async def wait_completed_iterations(
    fake: FakeReactAgent,
    count: int,
    timeout: float = 3.0,
) -> bool:
    """Poll until the fake has completed ``count`` inner iterations.

    Lets a test guarantee a clean iteration boundary was reached (and therefore
    snapshotted) before it pauses/aborts the next one.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if fake.completed_iterations >= count:
            return True
        await asyncio.sleep(0.01)
    return fake.completed_iterations >= count
