# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026.
# All rights reserved.
"""System test: steering injection in inner ReAct loop.

Validates that steering messages injected via agent.steer()
during tool execution appear as UserMessage before the NEXT
model call within the SAME inner ReAct invoke — not delayed
to the next outer-loop iteration.

Uses MockLLM + a blocking tool to control timing precisely.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from typing import Any, List, cast

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.deepagents import create_deep_agent
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
)
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
class _MockRuntimeModel:
    """Wrap MockLLMModel for DeepAgent's model contract."""

    def __init__(self, client: MockLLMModel) -> None:
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_mock_model(
    mock_llm: MockLLMModel,
) -> Model:
    """Build a Model-compatible wrapper."""
    return cast(Model, _MockRuntimeModel(mock_llm))


class _BlockingTool(Tool):
    """Tool that blocks until released by the test.

    Allows the test to inject steer while the tool is
    executing, ensuring precise timing control.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                name="blocking_tool",
                description="A tool that blocks until released",
            )
        )
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.call_count = 0

    async def invoke(self, inputs: Any, **kwargs: Any) -> str:
        """Block until gate is set, signal entry."""
        self.call_count += 1
        self.entered.set()
        await self.gate.wait()
        return "tool done"

    async def stream(self, inputs: Any, **kwargs: Any) -> Any:
        """Not used."""
        pass


class _ModelCallObserver(AgentRail):
    """Records each model call's messages for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.model_call_messages: List[List[Any]] = []

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Snapshot messages for each model call."""
        messages = getattr(ctx.inputs, "messages", None)
        if isinstance(messages, list):
            self.model_call_messages.append(list(messages))


def _extract_content(msg: Any) -> str:
    """Extract text content from a message."""
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    return str(getattr(msg, "content", ""))


def _seed_plan(session: Session) -> TaskPlan:
    """Seed a 2-step TaskPlan into session state."""
    plan = TaskPlan(
        goal="test steering injection",
        tasks=[
            TaskItem(
                id="t1",
                title="step-1",
                description="执行第一步操作",
            ),
            TaskItem(
                id="t2",
                title="step-2",
                description="执行第二步操作",
                depends_on=["t1"],
            ),
        ],
    )
    session.update_state(
        {
            "deepagent": {
                "iteration": 0,
                "task_plan": plan.to_dict(),
            }
        }
    )
    return plan


# ----------------------------------------------------------------
# Test
# ----------------------------------------------------------------
class TestSteerInnerLoopInjection(
    unittest.IsolatedAsyncioTestCase,
):
    """Verify steering injected via agent.steer() during
    tool execution appears as [STEERING] UserMessage in
    the same inner ReAct loop's next model call.
    """

    async def asyncSetUp(self) -> None:
        await Runner.start()

    async def asyncTearDown(self) -> None:
        await Runner.stop()

    @pytest.mark.asyncio
    async def test_steer_visible_in_same_invoke(
        self,
    ) -> None:
        """External agent.steer() during tool execution
        is visible as [STEERING] UserMessage on the next
        model call within the same inner invoke.

        Timeline:
        1. Outer loop round 1 starts
        2. MockLLM call #1 -> returns tool_call
        3. blocking_tool.invoke() starts, signals entered
        4. Test calls agent.steer(steer_text)
           -> pushes to LoopQueues.steering (shared queue)
        5. Test releases blocking_tool
        6. Inner loop iteration 2: drain_steering()
           -> injects UserMessage("[STEERING] ...")
        7. MockLLM call #2 -> observer sees [STEERING]
        8. MockLLM call #2 -> returns text -> round ends
        """
        steer_text = "请用中文输出简洁要点"

        blocking_tool = _BlockingTool()
        observer = _ModelCallObserver()

        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                # Round 1, model call #1: tool call
                create_tool_call_response(
                    "blocking_tool",
                    "{}",
                    tool_call_id="tc_1",
                ),
                # Round 1, model call #2: final answer
                create_text_response("第一步已完成。"),
                # Round 2, model call #1: final answer
                create_text_response("第二步已完成。"),
            ]
        )

        model = _build_mock_model(mock_llm)
        agent = create_deep_agent(
            model=model,
            system_prompt="你是一个测试助手。",
            tools=[blocking_tool],
            rails=[observer],
            enable_task_loop=True,
            max_iterations=6,
        )

        session = Session(
            session_id=(f"steer_inner_{uuid.uuid4().hex}"),
        )
        _seed_plan(session)

        # Start agent.invoke in background.
        invoke_task = asyncio.create_task(
            agent.invoke(
                {"query": "执行两步计划"},
                session=session,
            )
        )

        # Wait for blocking_tool to be entered.
        await asyncio.wait_for(
            blocking_tool.entered.wait(),
            timeout=10.0,
        )

        # Inject steer while tool is blocking.
        await agent.steer(steer_text, session=session)

        # Release the tool.
        blocking_tool.gate.set()

        # Wait for completion.
        result = await asyncio.wait_for(invoke_task, timeout=15.0)

        # --- Assertions ---
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")

        # At least 2 model calls happened in round 1.
        self.assertGreaterEqual(len(observer.model_call_messages), 2)

        # Model call #1 should NOT have [STEERING].
        first_msgs = observer.model_call_messages[0]
        steer_in_first = any("[STEERING]" in _extract_content(m) for m in first_msgs)
        self.assertFalse(
            steer_in_first,
            "[STEERING] should NOT appear in model call #1 (steer not yet injected).",
        )

        # Model call #2 SHOULD have [STEERING] with
        # the steer text — injected in the same invoke.
        second_msgs = observer.model_call_messages[1]
        steering_found = any(
            "[STEERING]" in _extract_content(m) and steer_text in _extract_content(m) for m in second_msgs
        )
        self.assertTrue(
            steering_found,
            f"[STEERING] with '{steer_text}' not found in model call #2 (same invoke). Messages: {second_msgs}",
        )
