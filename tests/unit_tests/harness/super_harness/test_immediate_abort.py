# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness immediate abort: task cancel + rollback to snapshot."""
from __future__ import annotations

import asyncio

import pytest

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.harness.super_harness import HarnessState, SuperHarness
from tests.unit_tests.harness.super_harness.fixtures import (
    IterationStep,
    MockDeepAgent,
)


@pytest.mark.asyncio
async def test_immediate_abort_cancels_and_returns_to_idle() -> None:
    """abort(immediate=True) cancels the round task and returns to IDLE."""
    await Runner.start()
    agent = MockDeepAgent()
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[], sleep_before=1.0),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        await harness.send("longjob")
        await asyncio.sleep(0.02)
        await harness.abort(immediate=True)

        # Allow supervisor to process the round_finished event.
        await asyncio.sleep(0.05)
        assert harness.state is HarnessState.IDLE
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_immediate_abort_rolls_back_to_last_safe_snapshot() -> None:
    """Snapshot captured after iter 1 is restored when iter 2 is cancelled."""
    await Runner.start()
    agent = MockDeepAgent()

    # Pre-seed the session context so we can observe rollback.
    sess_marker = UserMessage(content="<original>")
    # Iter 1 succeeds (fires AFTER_REACT_ITERATION → snapshot captured);
    # iter 2 hangs (gets cancelled by abort).
    agent.react_agent.iteration_script = [
        IterationStep(chunks=[{"type": "step", "value": "i1"}]),
        IterationStep(chunks=[], sleep_before=1.0),
    ]

    harness = SuperHarness(lambda: agent)
    await harness.start()
    try:
        # Seed the context with one message that should survive after rollback.
        ctx = agent.react_agent.context_engine.get_context(
            session_id=harness.session_id,
        )
        ctx.add_message(sess_marker)

        await harness.send("go")

        # Wait for iter 1 to complete (chunk drained + snapshot taken).
        async for _ in harness.outputs():
            break
        await asyncio.sleep(0.05)

        # Mutate context as if iter 2 added stuff.
        ctx.add_message(UserMessage(content="<dirty>"))

        # Immediate abort during iter 2 hang.
        await harness.abort(immediate=True)
        await asyncio.sleep(0.05)

        # Context should be restored to the snapshot captured after iter 1
        # (which contained only the original marker).
        restored = agent.react_agent.context_engine.get_context(
            session_id=harness.session_id,
        ).get_messages()
        assert any(getattr(m, "content", None) == "<original>" for m in restored)
        assert not any(getattr(m, "content", None) == "<dirty>" for m in restored)
    finally:
        await harness.stop()
