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
        **kwargs: Any,
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

        # Steer is passed as a shared asyncio.Queue via
        # _steering_queue in the inputs dict (no longer
        # concatenated into the query string).
        second_inputs = fake_react.invoke_calls[1]["inputs"]
        second_query = second_inputs["query"]
        self.assertNotIn("[STEERING]", second_query)
        sq = second_inputs.get("_steering_queue")
        self.assertIsNotNone(sq)
        # The ControlledReactAgent does not drain the queue,
        # so the steer message should still be pending.
        msgs = []
        while not sq.empty():
            try:
                msgs.append(sq.get_nowait())
            except asyncio.QueueEmpty:
                break
        self.assertIn(
            "please format as bullet points", msgs
        )

        # Persisted TaskPlan should mark planned tasks as completed.
        persisted = session.get_state("deepagent")
        self.assertIsInstance(persisted, dict)
        plan = TaskPlan.from_dict(persisted.get("task_plan"))
        self.assertEqual(plan.goal, seeded.goal)
        self.assertEqual(len(plan.tasks), 2)
        self.assertEqual(plan.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(plan.tasks[1].status, TaskStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_multiple_follow_ups_consumed_in_order(self):
        """3 follow-ups arrive together -> 3 extra rounds in FIFO order."""
        session = Session(
            session_id=f"fifo_{uuid.uuid4().hex}"
        )
        plan = TaskPlan(
            goal="fifo-test",
            tasks=[
                TaskItem(id="t1", title="step-1"),
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

        agent = DeepAgent(
            AgentCard(
                name="fifo_test",
                description="t",
            )
        ).configure(
            DeepAgentConfig(
                enable_task_loop=True,
                max_iterations=10,
            )
        )
        fake_react = ControlledReactAgent(
            blocked_calls={1}
        )
        agent.set_react_agent(
            fake_react, initialized=True
        )

        invoke_task = asyncio.create_task(
            agent.invoke(
                {"query": "base"}, session=session
            )
        )

        # Round 1 blocked -> inject 3 follow-ups
        await fake_react.wait_call_started(1)
        await agent.follow_up(
            "first_fu", session=session
        )
        await agent.follow_up(
            "second_fu", session=session
        )
        await agent.follow_up(
            "third_fu", session=session
        )
        fake_react.release_call(1)

        result = await asyncio.wait_for(
            invoke_task, timeout=10.0
        )

        # 1 planned + 3 follow-up = 4 rounds
        self.assertEqual(
            len(fake_react.invoke_calls), 4
        )
        # FIFO order
        self.assertEqual(
            fake_react.invoke_calls[1]["inputs"][
                "query"
            ],
            "first_fu",
        )
        self.assertEqual(
            fake_react.invoke_calls[2]["inputs"][
                "query"
            ],
            "second_fu",
        )
        self.assertEqual(
            fake_react.invoke_calls[3]["inputs"][
                "query"
            ],
            "third_fu",
        )

    @pytest.mark.asyncio
    async def test_follow_ups_persisted_in_state_during_round(
        self,
    ):
        """After drain, remaining follow-ups are persisted in session state."""
        session = Session(
            session_id=f"persist_{uuid.uuid4().hex}"
        )
        plan = TaskPlan(
            goal="persist-test",
            tasks=[
                TaskItem(id="t1", title="step-1"),
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

        agent = DeepAgent(
            AgentCard(
                name="persist_test",
                description="t",
            )
        ).configure(
            DeepAgentConfig(
                enable_task_loop=True,
                max_iterations=10,
            )
        )
        fake_react = ControlledReactAgent(
            blocked_calls={1, 2}
        )
        agent.set_react_agent(
            fake_react, initialized=True
        )

        invoke_task = asyncio.create_task(
            agent.invoke(
                {"query": "base"}, session=session
            )
        )

        # Round 1 blocked -> inject 2 follow-ups
        await fake_react.wait_call_started(1)
        await agent.follow_up(
            "fu_alpha", session=session
        )
        await agent.follow_up(
            "fu_beta", session=session
        )
        fake_react.release_call(1)

        # Round 2 starts (consuming "fu_alpha")
        await fake_react.wait_call_started(2)
        persisted = session.get_state("deepagent")
        pending = persisted.get(
            "pending_follow_ups", []
        )
        # "fu_alpha" was popped as current_query,
        # "fu_beta" should still be in the buffer
        self.assertEqual(pending, ["fu_beta"])

        fake_react.release_call(2)
        await asyncio.wait_for(
            invoke_task, timeout=10.0
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
