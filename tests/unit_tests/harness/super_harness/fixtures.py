# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mock DeepAgent / ReActAgent for SuperHarness unit tests.

Provides a script-driven mock that lets each test specify per-iteration
behavior (chunks to yield, whether to fire AFTER_REACT_ITERATION, when to
sleep / hang, etc.) without spinning up real LLM or context engines.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from openjiuwen.core.session.agent import Session
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
    """One scripted iteration of the mock ReActAgent."""

    chunks: list[Any] = field(default_factory=list)
    fire_after_react_iteration: bool = True
    sleep_before: float = 0.0
    # If set, after firing AFTER_REACT_ITERATION, sleep then check force_finish.
    sleep_after: float = 0.0


class MockContext:
    """Minimal stand-in for ContextEngine.get_context() result."""

    def __init__(self) -> None:
        self._messages: list[Any] = []

    def get_messages(self) -> list[Any]:
        return list(self._messages)

    def set_messages(self, messages: list[Any], with_history: bool = True) -> None:
        # Match real Context.set_messages signature; with_history unused here.
        _ = with_history
        self._messages = list(messages)

    def add_message(self, msg: Any) -> None:
        self._messages.append(msg)


class MockContextEngine:
    """Minimal stand-in for ContextEngine."""

    def __init__(self) -> None:
        self._contexts: dict[str, MockContext] = {}

    def get_context(self, session_id: str, context_id: str = "default") -> MockContext:
        _ = context_id
        if session_id not in self._contexts:
            self._contexts[session_id] = MockContext()
        return self._contexts[session_id]


class MockReActAgent:
    """Scripted mock that mimics ReActAgent.stream + register_callback contract.

    Tests assign ``iteration_script`` before driving the harness; the mock
    walks it iteration by iteration, fires the same callback events a real
    ReActAgent would fire, and honors ``ctx.consume_force_finish`` so the
    SuperHarness graceful-abort path can be exercised.
    """

    def __init__(self, card: AgentCard) -> None:
        self.card = card
        self.context_engine = MockContextEngine()
        # AgentCallbackManager needs an agent_id; route through card.id like
        # the real ReActAgent does.
        self._agent_callback_manager = AgentCallbackManager(card.id)
        self.iteration_script: list[IterationStep] = []
        self.invocations: list[dict] = []  # records each stream() call
        self._force_finish_called = False

    @property
    def agent_callback_manager(self) -> AgentCallbackManager:
        return self._agent_callback_manager

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: Callable,
        priority: int = 100,
    ) -> "MockReActAgent":
        await self._agent_callback_manager.register_callback(event, callback, priority)
        return self

    async def clear_context_messages(self, session_id: str) -> None:
        """Mirror real ReActAgent.clear_context_messages."""
        ctx = self.context_engine.get_context(session_id=session_id)
        ctx.set_messages([], with_history=False)

    async def stream(
        self,
        inputs: Any,
        session: Session | None = None,
        stream_modes: Any = None,
    ) -> AsyncIterator[Any]:
        """Drive the iteration script, firing callbacks like a real ReActAgent."""
        _ = stream_modes
        self.invocations.append({"inputs": inputs, "session": session})

        ctx = AgentCallbackContext(agent=self, inputs=inputs, session=session)
        await ctx.fire(AgentCallbackEvent.BEFORE_INVOKE)

        for step in self.iteration_script:
            # Top-of-iteration force_finish check (mirrors react_agent.py change).
            boundary_finish = ctx.consume_force_finish()
            if boundary_finish is not None:
                break

            if step.sleep_before > 0:
                await asyncio.sleep(step.sleep_before)

            for chunk in step.chunks:
                yield chunk

            if step.sleep_after > 0:
                await asyncio.sleep(step.sleep_after)

            if step.fire_after_react_iteration:
                await ctx.fire(AgentCallbackEvent.AFTER_REACT_ITERATION)

        await ctx.fire(AgentCallbackEvent.AFTER_INVOKE)


class MockDeepAgent:
    """Minimal DeepAgent stand-in for SuperHarness unit tests."""

    def __init__(self, card: AgentCard | None = None) -> None:
        self.card = card or AgentCard(name="mock_deep_agent", description="test")
        self.react_agent = MockReActAgent(self.card)
        self._initialized = False
        self._state_by_session: dict[str, DeepAgentState] = {}

    async def ensure_initialized(self) -> None:
        self._initialized = True

    def load_state(self, session: Session) -> DeepAgentState:
        sid = session.get_session_id()
        if sid not in self._state_by_session:
            self._state_by_session[sid] = DeepAgentState()
        return self._state_by_session[sid]

    def save_state(self, session: Session, state: DeepAgentState | None = None) -> None:
        sid = session.get_session_id()
        if state is not None:
            self._state_by_session[sid] = state


def make_card(name: str = "test_agent") -> AgentCard:
    """Helper to build a minimal AgentCard for tests."""
    return AgentCard(name=name, description="super-harness-test")
