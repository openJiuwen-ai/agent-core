# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cancellation-cause marking rules for ``DeepAgent.abort()``.

``abort()`` may only tag the bound session with a CANCEL cause when it has
something it can actually cancel. A no-op abort would leave a stale mark that
relabels a later internal failure as ``user_cancel`` within the registry TTL.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.context.cancellation import (
    CancellationCause,
    reset_cancellation_causes,
    resolve_cancellation_cause,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.interaction import (
    ActiveInteractionRound,
    RoundWorkItem,
)


@pytest.fixture(autouse=True)
def _clean_cancellation_causes():
    reset_cancellation_causes()
    yield
    reset_cancellation_causes()


def _agent() -> DeepAgent:
    agent = DeepAgent(AgentCard(name="deep", description="test"))
    agent._bound_session_id = "session-abort"
    return agent


@pytest.mark.asyncio
async def test_abort_without_in_flight_target_does_not_mark_cancel():
    """A no-op abort must not leave a cause mark behind."""
    agent = _agent()

    await agent.abort()

    assert resolve_cancellation_cause("session-abort") is None


@pytest.mark.asyncio
async def test_abort_with_active_round_marks_cancel():
    agent = _agent()
    agent._active_interaction_round = ActiveInteractionRound(
        work=RoundWorkItem(kind="user", request_id="r1", inputs={}),
    )

    await agent.abort()

    assert resolve_cancellation_cause("session-abort") is CancellationCause.CANCEL


@pytest.mark.asyncio
async def test_abort_with_live_stream_task_marks_cancel():
    agent = _agent()
    agent._stream_process_task = asyncio.ensure_future(asyncio.sleep(60))

    try:
        await agent.abort()
    finally:
        agent._stream_process_task.cancel()

    assert resolve_cancellation_cause("session-abort") is CancellationCause.CANCEL


@pytest.mark.asyncio
async def test_abort_with_done_stream_task_does_not_mark_cancel():
    agent = _agent()
    agent._stream_process_task = asyncio.ensure_future(asyncio.sleep(0))
    await agent._stream_process_task

    await agent.abort()

    assert resolve_cancellation_cause("session-abort") is None


@pytest.mark.asyncio
async def test_abort_with_subagent_controls_marks_cancel():
    agent = _agent()
    session = MagicMock()
    session.get_session_id.return_value = "session-abort"
    control = MagicMock()
    control.cancel_all = AsyncMock()
    agent._subagent_controls = {"session-abort": control}

    await agent.abort(session=session)

    assert resolve_cancellation_cause("session-abort") is CancellationCause.CANCEL


@pytest.mark.asyncio
async def test_abort_marks_target_session_not_bound_session():
    """``abort(session=...)`` must tag the target session, not the bound one.

    The in-flight guard checks the passed ``session``; the mark must land on
    the same session or the foreign-session abort path is ineffective.
    """
    agent = _agent()
    session = MagicMock()
    session.get_session_id.return_value = "session-target"
    control = MagicMock()
    control.cancel_all = AsyncMock()
    agent._subagent_controls = {"session-target": control}

    await agent.abort(session=session)

    assert resolve_cancellation_cause("session-target") is CancellationCause.CANCEL
    assert resolve_cancellation_cause("session-abort") is None
