# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent outer-loop system test.

Covers:
1) multi-step TaskPlan execution in outer task loop
2) steer injection into next round query
3) follow_up triggering an extra round
"""
from __future__ import annotations

import asyncio
import unittest
import uuid
from typing import Any, Dict, List, Optional, Set

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.deep_agent import DeepAgent
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)


class ControlledReactAgent:
    """Deterministic inner agent used by system test.

    The test can block specific invoke calls and release them
    to control timing for steer/follow_up injection.
    """

    def __init__(self, blocked_calls: Optional[Set[int]] = None) -> None:
        self.invoke_calls: List[Dict[str, Any]] = []
        self._call_started: asyncio.Queue[int] = asyncio.Queue()
        self._blocked_calls = blocked_calls or set()
        self._gates: Dict[int, asyncio.Event] = {}

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Optional[Any] = None,
    ) -> Dict[str, Any]:
        call_no = len(self.invoke_calls) + 1
        self.invoke_calls.append(
            {
                "call_no": call_no,
                "inputs": inputs,
                "session": session,
            }
        )
        await self._call_started.put(call_no)

        if call_no in self._blocked_calls:
            gate = self._gates.setdefault(call_no, asyncio.Event())
            await gate.wait()

        return {
            "output": f"ok:{inputs.get('query', '')}",
            "result_type": "answer",
            "call_no": call_no,
        }

    async def wait_call_started(
        self,
        call_no: int,
        timeout: float = 5.0,
    ) -> None:
        while True:
            got = await asyncio.wait_for(
                self._call_started.get(),
                timeout=timeout,
            )
            if got == call_no:
                return

    def release_call(self, call_no: int) -> None:
        gate = self._gates.setdefault(call_no, asyncio.Event())
        gate.set()


class TestDeepAgentOuterLoopSystem(unittest.IsolatedAsyncioTestCase):
    """System-level validation for DeepAgent outer loop."""

    @staticmethod
    def _seed_multi_step_plan(session: Session) -> TaskPlan:
        plan = TaskPlan(
            goal="验证外循环能力",
            tasks=[
                TaskItem(
                    id="t1",
                    title="step-1",
                    description="first planned step",
                ),
                TaskItem(
                    id="t2",
                    title="step-2",
                    description="second planned step",
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

    @pytest.mark.asyncio
    async def test_outer_loop_multistep_with_steer_follow_up(self):
        """End-to-end outer loop:

        - executes pre-seeded 2-step plan
        - steer affects the next round query
        - follow_up triggers one extra round
        """
        session = Session(
            session_id=f"deepagent_outer_loop_sys_{uuid.uuid4().hex}"
        )
        seeded = self._seed_multi_step_plan(session)

        agent = DeepAgent(
            AgentCard(name="deep_outer_loop_sys", description="system-test")
        ).configure(
            DeepAgentConfig(
                enable_task_loop=True,
                max_iterations=8,
            )
        )
        fake_react = ControlledReactAgent(blocked_calls={1, 2})
        agent.set_react_agent(fake_react, initialized=True)

        invoke_task = asyncio.create_task(
            agent.invoke({"query": "base query"}, session=session)
        )

        # Round 1 starts -> inject steer for round 2, then release.
        await fake_react.wait_call_started(1)
        await agent.steer("please format as bullet points", session=session)
        fake_react.release_call(1)

        # Round 2 starts -> inject follow_up to request one extra round.
        await fake_react.wait_call_started(2)
        await agent.follow_up("继续补充一个检查步骤", session=session)
        fake_react.release_call(2)

        # follow_up should cause one additional round (call 3).
        await fake_react.wait_call_started(3)
        result = await asyncio.wait_for(invoke_task, timeout=10.0)

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("result_type"), "answer")

        # 2 planned rounds + 1 follow_up round.
        self.assertEqual(len(fake_react.invoke_calls), 3)

        # steer is drained at next round start and injected into query.
        second_query = fake_react.invoke_calls[1]["inputs"]["query"]
        self.assertIn("[STEERING]", second_query)
        self.assertIn("please format as bullet points", second_query)

        # Persisted TaskPlan should mark planned tasks as completed.
        persisted = session.get_state("deepagent")
        self.assertIsInstance(persisted, dict)
        plan = TaskPlan.from_dict(persisted.get("task_plan"))
        self.assertEqual(plan.goal, seeded.goal)
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(plan.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(plan.tasks[1].status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

