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
  DeepAgent, so SnapshotRail's per-round boundary snapshot fires automatically.
  The fake therefore only implements ``invoke`` behavior; it must NOT fire
  task-iteration events itself.
- ``write_invoke_result_to_stream`` writes the final round answer (called by
  ``DeepAgent._write_round_result_to_stream``).
- ``context_engine.get_context(session_id).get_messages/set_messages`` back the
  snapshot/rollback machinery; ``clear_context_messages`` is the no-snapshot
  rollback fallback.

The fake's ``invoke`` is scriptable: a per-call ``sleep_seconds`` makes it a
cancellation point (for abort/pause), it drains the steering queue (to verify
immediate steer injection), records each call's inputs, and counts how many
times it observed a ``CancelledError`` (the cancel-chain assertion).
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
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
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
        # Set the moment ``invoke`` enters its sleep window; lets tests wait for
        # the real inner work to actually be in-flight before aborting/pausing,
        # instead of racing the phase transition (RUNNING is set when the round
        # task is created, before the executor reaches ``invoke``).
        self.invoke_running: asyncio.Event = asyncio.Event()

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
        """Run one outer round's inner work, streaming to the session.

        Mirrors the real react_agent contract the executor depends on: read the
        query + steering queue from ``effective``, append a user message to the
        context (so a snapshot has something to capture), optionally sleep (a
        cancellation point), emit a chunk, and return an answer result.
        """
        self.invocations.append(inputs if isinstance(inputs, dict) else {"query": inputs})
        query = inputs["query"] if isinstance(inputs, dict) else inputs
        steering_q = inputs.get("_steering_queue") if isinstance(inputs, dict) else None
        if steering_q is not None:
            while not steering_q.empty():
                self.seen_steers.append(steering_q.get_nowait())

        context = self.context_engine.get_context(session_id=session.get_session_id())
        context.add_message(UserMessage(content=str(query)))

        self.invoke_running.set()
        try:
            if self.sleep_seconds > 0:
                await asyncio.sleep(self.sleep_seconds)
        except asyncio.CancelledError:
            self.cancelled_count += 1
            raise
        finally:
            self.invoke_running.clear()

        if self.raise_exc is not None:
            raise self.raise_exc

        await session.write_stream(
            OutputSchema(type="mock_chunk", index=0, payload={"query": str(query)}),
        )
        output = self.answer_output or f"echo:{query}"
        return {"output": output, "result_type": "answer"}

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


async def start_harness(
    harness: Any,
    *,
    sleep_seconds: float = 0.0,
    answer_output: str = "",
) -> FakeReactAgent:
    """Start a harness and inject a freshly-scripted FakeReactAgent.

    Builds the real task-loop kernel via ``start()`` then swaps in the fake so
    the real executor drives it.

    Args:
        harness: The NativeHarness to start.
        sleep_seconds: Initial ``invoke`` sleep (cancellation-point length).
        answer_output: Initial answer output for the fake.

    Returns:
        The injected FakeReactAgent (tests mutate its script between rounds).
    """
    await harness.start()
    fake = FakeReactAgent(harness.card)
    fake.sleep_seconds = sleep_seconds
    fake.answer_output = answer_output
    harness.set_react_agent(fake, initialized=True)
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
    """Block until the fake's ``invoke`` is actually in its sleep window.

    The harness sets RUNNING when it creates the round task, which is before the
    executor reaches ``react_agent.invoke``. Aborting on RUNNING alone races the
    inner work; waiting on this event guarantees the CancelledError lands inside
    ``invoke`` (the cancel-chain contract). Clears the event so the next round
    can wait on it again.
    """
    await asyncio.wait_for(fake.invoke_running.wait(), timeout=timeout)
    fake.invoke_running.clear()
