#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os

import pytest

from openjiuwen.core.common.constants.enums import ControllerType
from openjiuwen.core.single_agent import (
    AgentCard, ReActAgent, WorkflowAgentConfig, WorkflowSchema, create_react_agent_config
)
from openjiuwen.core.application.agents_for_studio.workflow_agent import WorkflowAgent
from openjiuwen.core.foundation.llm import ModelConfig, BaseModelInfo
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import RunnerConfig, MessageQueueConfig, DistributedConfig, PulsarConfig
from openjiuwen.core.session.stream import OutputSchema, TraceSchema
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import WorkflowCard
from tests.unit_tests.core.workflow.mock_nodes import MockStartNode, Node1, MockEndNode

API_BASE = os.getenv("API_BASE")
API_KEY = os.getenv("API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openai")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires real uv sync --extra pulsar and llm")
class TestAdapterTest:
    def setup_method(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")
        os.environ.setdefault("RESTFUL_SSL_VERIFY", "false")

        pulsar_mq = RunnerConfig(
            distributed_mode=True,
            distributed_config=DistributedConfig(
                request_timeout=15.0,
                message_queue_config=MessageQueueConfig(
                    type="pulsar",
                    pulsar_config=PulsarConfig(
                        max_workers=8,
                        url="pulsar://localhost:6650",
                    ),
                )
            )
        )
        Runner.set_config(pulsar_mq)

    def teardown_method(self):
        """Clean up test environment"""
        # Reset Runner configuration
        from openjiuwen.core.runner.runner_config import DEFAULT_RUNNER_CONFIG
        Runner.set_config(DEFAULT_RUNNER_CONFIG)

    async def _create_and_register_agent(self, agent_id, agent_version="0.0.1"):
        """Create and register a React single_agent with common configuration"""
        # Create single_agent configuration
        react_agent_config = create_react_agent_config(
            agent_id=agent_id,
            agent_version=agent_version,
            description="AI助手",
            model=self._create_model(),
            prompt_template=[]
        )

        # Create single_agent instance
        react_agent: ReActAgent = ReActAgent(react_agent_config)

        # Register single_agent with runner
        Runner.resource_mgr.add_agent(AgentCard(id=agent_id), react_agent)
        return react_agent

    @staticmethod
    def _create_model():
        return ModelConfig(
            model_provider=MODEL_PROVIDER,
            model_info=BaseModelInfo(
                model=MODEL_NAME,
                api_base=API_BASE,
                api_key=API_KEY,
                temperature=0.7,
                top_p=0.9,
                timeout=30
            )
        )

    async def test_adapter_invoke(self):
        """Test that adapter using real single_agent outputs correctly"""

        await Runner.start()

        try:
            agent = await self._create_and_register_agent("weather-single_agent")
            client = RemoteAgent(agent_id="weather-single_agent")
            result = await client.invoke({"query": "你好"})
            assert result["output"] is not None
        finally:
            await Runner.stop()

    async def test_adapter_stream(self):
        """Test that adapter using real single_agent streams correctly"""
        await Runner.start()

        try:
            react_agent = await self._create_and_register_agent("weather-single_agent-stream")
            client = RemoteAgent(agent_id="weather-single_agent-stream")
            chunks = []
            async for chunk in client.stream({"query": "你好"}):
                print(f"Stream chunk received: {chunk}")
                chunks.append(chunk)
                assert isinstance(chunk, (OutputSchema, TraceSchema)), \
                    f"Chunk must be OutputSchema, TraceSchema, or CustomSchema, got {type(chunk)}"
            assert len(chunks) > 0
        finally:
            await Runner.stop()

    @staticmethod
    def _build_workflow(name, id, version):
        workflow_card = WorkflowCard(
                id=id,
                version=version,
                name=name,
        )
        flow = Workflow(card=workflow_card)
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={
                                "query": "${query}"})
        flow.add_workflow_comp("node_a", Node1("node_a"),
                               inputs_schema={
                                   "output": "${start.query}"})
        flow.set_end_comp("end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${node_a.output}"})
        flow.add_connection("start", "node_a")
        flow.add_connection("node_a", "end")
        return flow

    async def test_react_agent_invoke_with_adapter(self):
        try:
            await Runner.start()
            id = "test_workflow"
            name = "test_workflow"
            version = "1"
            description = "test_workflow"
            workflow1 = self._build_workflow(name, id, version)
            test_workflow_schema = WorkflowSchema(
                id=id,
                version=version,
                name=name,
                description=description,
                inputs={"query": {
                    "type": "string",
                }},
            )
            workflow_config = WorkflowAgentConfig(
                workflows=[test_workflow_schema],
                controller_type=ControllerType.WorkflowController
            )
            agent = WorkflowAgent(workflow_config)
            agent.bind_workflows([workflow1])
            Runner.resource_mgr.add_workflow(WorkflowCard(id=id + "_" + version), workflow1)
            Runner.resource_mgr.add_agent(AgentCard(id="workflow-single_agent"), agent)
            # Simulate client sending request
            client = RemoteAgent(agent_id="workflow-single_agent")
            Runner.resource_mgr.add_agent(AgentCard(id="remote-workflow-single_agent"), agent=client)
            response = await Runner.run_agent("remote-workflow-single_agent", {"query": "London"})
            print(f"response: {response}")
            assert response['result_type'] == 'answer'
            assert response['output'].result == {'result': 'London'}
            assert response['output'].state.name == 'COMPLETED'

        finally:
            Runner.resource_mgr.remove_agent(id="remote-workflow-single_agent")
            Runner.resource_mgr.remove_agent(id="workflow-single_agent")

            await Runner.stop()
