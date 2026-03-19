# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WorkflowAgent basic stream UT

Covers ST: test_workflow_agent_stream_with_interrupt_recovery (no-interrupt
part), test_end_stream_output_should_have_end_node_stream (batch End)

Two invocation modes:
  1. agent.stream() - direct call
  2. Runner.run_agent_streaming() - runner-managed call

Uses mock nodes (no real LLM), validates:
  - stream yields at least one chunk
  - chunks contain a workflow_final OutputSchema
  - workflow_final payload echoes the input query
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
from openjiuwen.core.session.stream import OutputSchema

from tests.unit_tests.core.workflow.mock_nodes import (
    MockStartNode,
    MockEndNode,
    Node1,
)
from .mock_workflow_agent import MockWorkflowAgent as WorkflowAgent

os.environ.setdefault("LLM_SSL_VERIFY", "false")


class TestWorkflowAgentStream(unittest.IsolatedAsyncioTestCase):
    """WorkflowAgent basic stream test (mock-based).

    Corresponds to ST: test_end_stream_output (batch End, no interrupt)
    """

    async def asyncSetUp(self):
        await Runner.start()

    async def asyncTearDown(self):
        await Runner.stop()

    @staticmethod
    def _build_simple_workflow() -> Workflow:
        """Build a simple workflow: start -> node_a -> end"""
        card = WorkflowCard(
            id="test_stream_workflow",
            version="1.0",
            name="stream_test",
            description="Simple workflow for stream test",
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
            id="test_stream_agent",
            version="1.0",
            description="stream test agent",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([workflow])
        return agent

    # ---- Mode 1: agent.stream() direct call ----

    @pytest.mark.asyncio
    async def test_stream_direct(self):
        """agent.stream() completes a simple workflow end-to-end.

        Validates:
          - at least one chunk is yielded
          - chunks contain a workflow_final OutputSchema
          - workflow_final payload contains the expected result
        """
        workflow = self._build_simple_workflow()
        agent = self._create_agent(workflow)

        chunks = []
        conv_id = str(uuid.uuid4())
        async for chunk in agent.stream({
            "query": "hello",
            "conversation_id": conv_id,
        }):
            chunks.append(chunk)

        self.assertGreater(len(chunks), 0, "stream should yield chunks")

        workflow_final_chunks = []
        for c in chunks:
            if isinstance(c, OutputSchema) and c.type == "workflow_final":
                workflow_final_chunks.append(c)

        self.assertEqual(
            len(workflow_final_chunks), 1,
            "should have exactly one workflow_final chunk",
        )

        payload = workflow_final_chunks[0].payload
        self.assertIn("result", payload)
        self.assertEqual(payload["result"], "hello")

        # check get chat history from context
        chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
        self.assertEqual(len(chat_history), 2)
        self.assertEqual(chat_history[0].role, "user")
        self.assertEqual(chat_history[1].role, "assistant")

    # ---- Mode 2: Runner.run_agent_streaming() ----

    @pytest.mark.asyncio
    async def test_stream_via_runner(self):
        """Runner.run_agent_streaming() streams a simple workflow.

        Uses @workflow_provider factory pattern, exercises the
        Runner-managed streaming path.
        """
        @workflow_provider(
            workflow_id="test_stream_workflow",
            workflow_name="stream_test",
            workflow_version="1.0",
            workflow_description="Simple workflow for stream test",
        )
        def create_workflow():
            """Factory: creates a fresh workflow instance."""
            return self._build_simple_workflow()

        config = WorkflowAgentConfig(
            id="test_stream_runner_agent",
            version="1.0",
            description="stream test agent (runner)",
            workflows=[],
        )
        agent = WorkflowAgent(config)
        agent.add_workflows([create_workflow])

        chunks = []
        conv_id = str(uuid.uuid4())
        async for chunk in Runner.run_agent_streaming(
            agent,
            {
                "query": "hello",
                "conversation_id": conv_id,
            },
        ):
            chunks.append(chunk)

        self.assertGreater(len(chunks), 0, "stream should yield chunks")

        workflow_final_chunks = []
        for c in chunks:
            if isinstance(c, OutputSchema) and c.type == "workflow_final":
                workflow_final_chunks.append(c)
        self.assertEqual(
            len(workflow_final_chunks), 1,
            "should have exactly one workflow_final chunk",
        )

        payload = workflow_final_chunks[0].payload
        self.assertIn("result", payload)
        self.assertEqual(payload["result"], "hello")

        # check get chat history from context
        chat_history = agent.context_engine.get_context(session_id=conv_id).get_messages()
        self.assertEqual(len(chat_history), 2)
        self.assertEqual(chat_history[0].role, "user")
        self.assertEqual(chat_history[1].role, "assistant")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
