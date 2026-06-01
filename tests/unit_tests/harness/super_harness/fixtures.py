# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mock DeepAgent / ReActAgent for SuperHarness unit tests.

The mock mirrors the real ReActAgent contract SuperHarness depends on:
- ``invoke(inputs, session, _streaming=True)`` drives a scripted ReAct loop,
  writing chunks to ``session.write_stream`` (so a real Session's stream
  emitter / iterator behavior — including close_stream semantics — is
  exercised), honoring ``ctx.consume_force_finish`` at each iteration top,
  draining the steering queue passed via ``inputs["_steering_queue"]``, and
  firing AFTER_REACT_ITERATION only on tool-steps (never on the answer break).
- ``write_invoke_result_to_stream`` writes the final result.

MockContext reproduces the real ContextMessageBuffer's history/current
segmentation so ``with_history`` (de)symmetry is testable.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
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
from openjiuwen.harness.schema.state import DeepAgentState


@dataclass
class IterationStep:
    """One scripted ReAct iteration of the mock.

    Attributes:
        chunks: Stream chunks to write to the session this iteration.
        is_answer: When True, this is the terminating answer iteration — no
            AFTER_REACT_ITERATION fires and the loop stops (mirrors the real
            no-tool-call break). When False, it is a tool-step: a tool
            AssistantMessage is added and AFTER_REACT_ITERATION fires.
        answer_output: Result ``output`` for an answer step.
        sleep_before: Seconds to sleep before emitting chunks (a cancellation
            point for abort/pause tests).
        sleep_after: Seconds to sleep after emitting chunks.
    """

    chunks: list = field(default_factory=list)
    is_answer: bool = False
    answer_output: str = ""
    sleep_before: float = 0.0
    sleep_after: float = 0.0


class MockContext:
    """Stand-in for a ContextEngine context with history/current segmentation.

    Mirrors ContextMessageBuffer: messages live in one list split at
    ``_history_size``; ``with_history=False`` operates only on the current
    segment.
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


class MockReActAgent:
    """invoke-based scripted mock mirroring the real ReActAgent contract."""

    def __init__(self, card: AgentCard) -> None:
        self.card = card
        self.context_engine = MockContextEngine()
        self._agent_callback_manager = AgentCallbackManager(card.id)
        self.iteration_script: list[IterationStep] = []
        self.invocations: list[dict] = []
        self.steps_executed: int = 0  # counts tool/answer steps actually run

    @property
    def agent_callback_manager(self) -> AgentCallbackManager:
        """Expose the callback manager (SuperHarness registers SnapshotRail here)."""
        return self._agent_callback_manager

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: Callable,
        priority: int = 100,
    ) -> "MockReActAgent":
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
        """Drive the scripted ReAct loop, streaming to the session."""
        self.invocations.append({"inputs": inputs, "session": session, "kwargs": kwargs})
        query = inputs["query"] if isinstance(inputs, dict) else inputs
        steering_q = inputs.get("_steering_queue") if isinstance(inputs, dict) else None

        ctx = AgentCallbackContext(agent=self, inputs={"query": query}, session=session)
        if steering_q is not None:
            ctx.bind_steering_queue(steering_q)

        context = self.context_engine.get_context(session_id=session.get_session_id())
        await ctx.fire(AgentCallbackEvent.BEFORE_INVOKE)
        context.add_message(UserMessage(content=str(query)))

        result: dict = {"output": "max_steps", "result_type": "answer"}
        for step in self.iteration_script:
            # Iteration-top force_finish check (mirrors react_agent.py).
            boundary = ctx.consume_force_finish()
            if boundary is not None:
                result = boundary.result
                break

            steered = ctx.drain_steering()
            if steered:
                context.add_message(UserMessage(content="[STEERING] " + "\n".join(steered)))

            if step.sleep_before > 0:
                await asyncio.sleep(step.sleep_before)
            for chunk in step.chunks:
                await session.write_stream(
                    OutputSchema(type="mock_chunk", index=0, payload=dict(chunk)),
                )
            if step.sleep_after > 0:
                await asyncio.sleep(step.sleep_after)

            self.steps_executed += 1
            context.add_message(AssistantMessage(content=str(step.chunks)))

            if step.is_answer:
                result = {"output": step.answer_output, "result_type": "answer"}
                break
            # Tool-step: snapshot boundary.
            await ctx.fire(AgentCallbackEvent.AFTER_REACT_ITERATION)

        await ctx.fire(AgentCallbackEvent.AFTER_INVOKE)
        return result

    async def write_invoke_result_to_stream(self, result: dict, session: Session) -> None:
        """Write the final result to the session stream (mirrors the real method)."""
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


class MockDeepAgent:
    """Minimal DeepAgent stand-in for SuperHarness unit tests."""

    def __init__(self, card: AgentCard | None = None) -> None:
        self.card = card or AgentCard(name="mock_deep_agent", description="test")
        self.react_agent = MockReActAgent(self.card)
        self._initialized = False
        self._state_by_session: dict[str, DeepAgentState] = {}

    async def ensure_initialized(self) -> None:
        """Mark initialized (no MCP/workspace setup in the mock)."""
        self._initialized = True

    def load_state(self, session: Session) -> DeepAgentState:
        """Return (creating if needed) the per-session DeepAgentState."""
        sid = session.get_session_id()
        if sid not in self._state_by_session:
            self._state_by_session[sid] = DeepAgentState()
        return self._state_by_session[sid]

    def save_state(self, session: Session, state: DeepAgentState | None = None) -> None:
        """Persist the per-session DeepAgentState."""
        sid = session.get_session_id()
        if state is not None:
            self._state_by_session[sid] = state


def make_card(name: str = "test_agent") -> AgentCard:
    """Build a minimal AgentCard for tests."""
    return AgentCard(name=name, description="super-harness-test")


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


def aborted_markers(collected: list) -> list:
    """Filter collected chunks to ``round_aborted`` marker payloads."""
    return [c.payload for c in collected if getattr(c, "type", None) == "round_aborted"]
