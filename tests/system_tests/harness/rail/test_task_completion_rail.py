# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TaskCompletionRail 系统测试。

覆盖用例：
  UC-1  MaxRounds — loop stops after N rounds (mock LLM + pre-seeded plan)
  UC-2  CompletionPromise — loop stops when model outputs tag (mock LLM)
  UC-3  task_instruction template wraps first-round query (mock LLM)
  UC-4  legacy stop_condition migration via factory (mock LLM)
  UC-5  Timeout — loop stops when elapsed > timeout_seconds (mock LLM)
"""
from __future__ import annotations

import asyncio
import logging
import os
import unittest
import uuid
from typing import Any, List, cast

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
)
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
from openjiuwen.harness.schema.task import TaskItem, TaskPlan
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
)

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockRuntimeModel:
    """Wrap MockLLMModel into the Model contract expected by DeepAgent."""

    def __init__(self, client: MockLLMModel) -> None:
        self.client = client
        self.model_client_config = client.model_client_config
        self.model_config = client.model_config

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self.client.invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        async for chunk in self.client.stream(*args, **kwargs):
            yield chunk


def _build_mock_model(mock_llm: MockLLMModel) -> Model:
    return cast(Model, _MockRuntimeModel(mock_llm))


def _seed_plan(session: Session, n: int) -> None:
    """Inject a linear n-task plan into session state."""
    tasks = [
        TaskItem(id=f"t{i}", title=f"step-{i}")
        for i in range(1, n + 1)
    ]
    # Chain tasks sequentially (each depends on the previous)
    for i, task in enumerate(tasks):
        if i > 0:
            task.depends_on = [tasks[i - 1].id]

    plan = TaskPlan(goal="test plan", tasks=tasks)
    session.update_state(
        {
            "deepagent": {
                "iteration": 0,
                "task_plan": plan.to_dict(),
            }
        }
    )


class _IterationCapture(AgentRail):
    """Record the query each outer iteration receives.

    Priority 5 (< TaskCompletionRail.priority=10) ensures this
    fires AFTER TaskCompletionRail has applied the template, so
    ``queries`` reflects the effective query sent to the inner agent.
    """

    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self.queries: List[str] = []
        self.count: int = 0

    async def before_task_iteration(
        self, ctx: AgentCallbackContext,
    ) -> None:
        self.count += 1
        q = getattr(ctx.inputs, "query", None) or ""
        self.queries.append(str(q))


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestTaskCompletionRailSystem(unittest.IsolatedAsyncioTestCase):
    """TaskCompletionRail — system-level tests."""

    async def asyncSetUp(self) -> None:
        await Runner.start()

    async def asyncTearDown(self) -> None:
        await Runner.stop()

    # -- UC-1: MaxRounds ------------------------------------------------

    async def test_uc1_max_rounds_stops_loop(self) -> None:
        """Loop stops after max_rounds even when more tasks remain."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("done")] * 20)
        model = _build_mock_model(mock_llm)

        capture = _IterationCapture()
        rail = TaskCompletionRail(max_rounds=3)

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            rails=[rail],
            restrict_to_work_dir=False,
        )
        agent.add_rail(capture)

        # Pre-seed 5-task plan; max_rounds=3 should stop it at 3.
        session = Session(
            session_id=f"uc1_{uuid.uuid4().hex}"
        )
        _seed_plan(session, 5)

        await agent.invoke({"query": "do something"}, session=session)

        self.assertEqual(capture.count, 3)

    # -- UC-3: task_instruction template ---------------------------------

    async def test_uc3_task_instruction_wraps_first_query(self) -> None:
        """task_instruction template is applied to iteration queries.

        When a task plan is active, each round's query comes from the
        plan task title.  The template wraps that title.
        """
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("step done")] * 10)
        model = _build_mock_model(mock_llm)

        template = "请完成以下任务：{query}"
        capture = _IterationCapture()
        rail = TaskCompletionRail(
            task_instruction=template,
            max_rounds=2,
        )

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            rails=[rail],
            restrict_to_work_dir=False,
        )
        agent.add_rail(capture)

        # Pre-seed 2 tasks; round query = plan task title "step-1", "step-2"
        session = Session(
            session_id=f"uc3_{uuid.uuid4().hex}"
        )
        _seed_plan(session, 2)

        await agent.invoke({"query": "write a file"}, session=session)

        self.assertGreaterEqual(len(capture.queries), 1)
        # Template is applied; task title from plan is embedded.
        self.assertIn("请完成以下任务：", capture.queries[0])
        self.assertIn("step-1", capture.queries[0])

    # -- UC-4: CustomPredicateEvaluator stops loop -----------------------

    async def test_uc4_custom_predicate_stops_loop(self) -> None:
        """CustomPredicateEvaluator stops the loop after N rounds."""
        from openjiuwen.harness.schema.stop_condition import (
            CustomPredicateEvaluator,
            StopEvaluationContext,
        )

        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")] * 10)
        model = _build_mock_model(mock_llm)

        # Stop after exactly 2 rounds via a custom predicate.
        def _stop_after_two(ctx: StopEvaluationContext) -> bool:
            return ctx.iteration >= 2

        rail = TaskCompletionRail(
            evaluators=[CustomPredicateEvaluator(_stop_after_two)],
        )
        capture = _IterationCapture()

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            rails=[rail],
            restrict_to_work_dir=False,
        )
        agent.add_rail(capture)

        # Pre-seed 5 tasks so the loop would run without the evaluator.
        session = Session(
            session_id=f"uc4_{uuid.uuid4().hex}"
        )
        _seed_plan(session, 5)

        await agent.invoke({"query": "hello"}, session=session)
        self.assertEqual(capture.count, 2)

    # -- UC-5: Timeout --------------------------------------------------

    async def test_uc5_timeout_stops_loop(self) -> None:
        """Loop stops when timeout_seconds is exceeded."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("ok")] * 20)
        model = _build_mock_model(mock_llm)

        rail = TaskCompletionRail(timeout_seconds=0.6)
        capture = _IterationCapture()

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            rails=[rail],
            restrict_to_work_dir=False,
        )
        # Add capture BEFORE invoke so it gets registered during initialization.
        agent.add_rail(capture)

        # react_agent is available after configure() (before invoke).
        # Patch its invoke to be slow (~0.4 s per call).
        assert agent.react_agent is not None
        original_invoke = agent.react_agent.invoke

        async def _slow_invoke(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0.4)
            return await original_invoke(*args, **kwargs)

        agent.react_agent.invoke = _slow_invoke  # type: ignore[method-assign]

        # Pre-seed 10 tasks so loop would run indefinitely without timeout.
        session = Session(
            session_id=f"uc5_{uuid.uuid4().hex}"
        )
        _seed_plan(session, 10)

        await agent.invoke({"query": "loop forever"}, session=session)

        # With 0.6 s timeout and ~0.4 s per round, at most 2 rounds.
        self.assertLessEqual(capture.count, 3)
        self.assertGreaterEqual(capture.count, 1)

    # -- UC-2: CompletionPromise with mock LLM ---------------------------

    @pytest.mark.asyncio
    async def test_uc2_completion_promise_mock_llm(self) -> None:
        """CompletionPromise — loop stops when model outputs the tag.

        MockLLMModel returns a response containing the promise tag so
        the loop stops after exactly 1 round without a real API call.
        """
        promise_token = "TASK_DONE"
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_text_response(
                    f"分析完成。<promise>{promise_token}</promise>"
                )
            ]
            * 5
        )
        model = _build_mock_model(mock_llm)

        rail = TaskCompletionRail(
            completion_promise=promise_token,
            max_rounds=5,
        )
        capture = _IterationCapture()

        agent = create_deep_agent(
            model=model,
            enable_task_loop=True,
            rails=[rail],
            restrict_to_work_dir=False,
        )
        agent.add_rail(capture)

        result = await Runner.run_agent(
            agent, {"query": "请分析：2 + 2 等于多少？"}
        )

        self.assertIsInstance(result, dict)
        self.assertIn("output", result)
        # Loop should stop after 1 round because promise is fulfilled.
        self.assertEqual(
            capture.count,
            1,
            f"Expected 1 round, got {capture.count}. "
            f"Output: {result.get('output', '')[:200]}",
        )


if __name__ == "__main__":
    unittest.main()
