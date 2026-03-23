# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent basic invoke UT

Covers ST: test_real_workflow_agent_invoke
Two invocation modes:
  1. agent.invoke() - direct call
  2. Runner.run_agent() - runner-managed call

Uses mock nodes (no real LLM), validates:
  - result_type == 'answer'
  - output.state == COMPLETED
  - output.result contains expected data
"""
import os
import uuid
import unittest

import pytest

from openjiuwen.core.single_agent.legacy import (
    WorkflowAgentConfig,
    workflow_provider,
)
from openjiuwen.core.workflow import WorkflowCard, Workflow
from openjiuwen.core.runner import Runner
from tests.unit_tests.core.workflow.mock_nodes import (
    MockStartNode,
    MockEndNode,
    Node1,
)
from .mock_workflow_agent import MockWorkflowAgent as WorkflowAgent

os.environ.setdefault("LLM_SSL_VERIFY", "false")


class TestWorkflowAgentInvoke(unittest.IsolatedAsyncioTestCase):
    """WorkflowAgent basic invoke test (mock-based).

    Corresponds to ST: test_real_workflow_agent_invoke
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_simple_workflow() -> Workflow:
        """Build a simple workflow: start -> node_a -> end"""
        card = WorkflowCard(
            id="test_invoke_workflow",
            version="1.0",
            name="invoke_test",
            description="Simple workflow for invoke test",
        )
        flow = Workflow(card=card)

        flow.set_start_comp(
            "start",
            MockStartNode("start"),
            inputs_schema={"query": "${query}"},
        )
        flow.add_workflow_comp(
            "node_a",
            Node1("node_a"),
            inputs_schema={"output": "${start.query}"},
        )
        flow.set_end_comp(
            "end",
            MockEndNode("end"),
            inputs_schema={"result": "${node_a.output}"},
        )

        flow.add_connection("start", "node_a")
        flow.add_connection("node_a", "end")

        return flow

    @staticmethod
    def _create_agent(workflow: Workflow) -> WorkflowAgent:
        """Create a WorkflowAgent with the given workflow."""
        config = WorkflowAgentConfig(
            id="test_invoke_agent",
            version="1.0",
            description="invoke test agent",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    # ---- Mode 1: agent.invoke() direct call ----

    @pytest.mark.asyncio
    async def test_invoke_direct(self):
        """agent.invoke() completes a simple workflow end-to-end.

        Validates:
          - result is a dict with result_type='answer'
          - output.state == COMPLETED
          - output.result echoes the input query
        """
        workflow = self._build_simple_workflow()
        agent = self._create_agent(workflow)

        conversation_id = str(uuid.uuid4())
        result = await agent.invoke({
            "query": "hello",
            "conversation_id": conversation_id
        })

        self.assertIsInstance(result, dict)
        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"].state.name, "COMPLETED")
        self.assertEqual(result["output"].result, {"result": "hello"})

        # check get chat history from context
        chat_history = agent.context_engine.get_context(session_id=conversation_id).get_messages()
        self.assertEqual(len(chat_history), 2)
        self.assertEqual(chat_history[0].role, "user")
        self.assertEqual(chat_history[1].role, "assistant")

    # ---- Mode 2: Runner.run_agent() with workflow_provider ----

    @pytest.mark.asyncio
    async def test_invoke_via_runner(self):
        """Runner.run_agent() completes a simple workflow end-to-end.

        Uses @workflow_provider factory pattern instead of direct
        Workflow instance, exercises the Runner-managed invocation path.
        """
        @workflow_provider(
            workflow_id="test_invoke_workflow",
            workflow_name="invoke_test",
            workflow_version="1.0",
            workflow_description="Simple workflow for invoke test",
        )
        def create_workflow():
            """Factory: creates a fresh workflow instance."""
            return self._build_simple_workflow()

        config = WorkflowAgentConfig(
            id="test_invoke_runner_agent",
            version="1.0",
            description="invoke test agent (runner)",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([create_workflow])

        conversation_id = str(uuid.uuid4())
        result = await Runner.run_agent(
            agent,
            {
                "query": "hello",
                "conversation_id": conversation_id,
            },
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"].state.name, "COMPLETED")
        self.assertEqual(result["output"].result, {"result": "hello"})

        # check get chat history from context
        chat_history = agent.context_engine.get_context(session_id=conversation_id).get_messages()
        self.assertEqual(len(chat_history), 2)
        self.assertEqual(chat_history[0].role, "user")
        self.assertEqual(chat_history[1].role, "assistant")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
