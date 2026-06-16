# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness.run_once: one non-streaming execution returning the invoke dict.

run_once bypasses the supervisor (no steer / no outputs stream): it runs a plain
``DeepAgent.invoke`` and returns its dict, the same shape ``Runner.run_agent``
returns. The spec's ``enable_task_loop`` decides single-round vs self-driven
task loop. Used by single-shot callers (swarmflow workers).
"""
from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.agent_teams.harness import NativeHarness
from tests.unit_tests.agent_teams.harness.fixtures import FakeReactAgent, make_card

from openjiuwen.harness.factory import DeepAgentParts
from openjiuwen.harness.schema.config import DeepAgentConfig


def _spec(*, enable_task_loop: bool):
    """A fake spec whose resolve_parts yields a config with the given loop flag."""
    card = make_card()

    class _FakeSpec:
        def resolve_parts(self, context: Any = None) -> DeepAgentParts:
            return DeepAgentParts(
                config=DeepAgentConfig(card=card, enable_task_loop=enable_task_loop),
                rails=[],
                tool_cards=[],
                tool_instances=[],
            )

    return _FakeSpec()


async def _inject_fake(harness: NativeHarness, *, answer: str = "") -> FakeReactAgent:
    """Prepare the harness (build real kernel/react_agent) then swap in the fake."""
    await harness._prepare()
    fake = FakeReactAgent(harness.card)
    fake.answer_output = answer
    harness.set_react_agent(fake, initialized=True)
    return fake


@pytest.mark.asyncio
async def test_run_once_single_round_returns_invoke_dict() -> None:
    """A single-round run_once returns the inner invoke result dict verbatim."""
    await Runner.start()
    try:
        harness = NativeHarness(_spec(enable_task_loop=False))
        fake = await _inject_fake(harness, answer="done")

        result = await harness.run_once("hello")

        assert isinstance(result, dict)
        assert result["output"] == "done"
        assert result["result_type"] == "answer"
        assert len(fake.invocations) == 1
        assert fake.invocations[0]["query"] == "hello"
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_run_once_owns_and_closes_its_session() -> None:
    """With no session passed, run_once creates one and binds a session_id during the run."""
    await Runner.start()
    try:
        harness = NativeHarness(_spec(enable_task_loop=False))
        await _inject_fake(harness, answer="x")

        assert harness.session_id is None
        result = await harness.run_once("hi")
        assert result["output"] == "x"
        # A session was bound while running (owned + created internally).
        assert harness.session_id is not None
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_run_once_rejects_when_supervisor_active() -> None:
    """run_once must not run while the streaming supervisor is active."""
    from openjiuwen.agent_teams.harness import HarnessState
    from tests.unit_tests.agent_teams.harness.fixtures import start_harness, wait_for_state

    await Runner.start()
    try:
        harness = NativeHarness(_spec(enable_task_loop=False))
        await start_harness(harness, answer_output="hi")  # starts the supervisor
        with pytest.raises(Exception):
            await harness.run_once("nope")
        await harness.stop()
        assert await wait_for_state(harness, HarnessState.TERMINATED)
    finally:
        await Runner.stop()
