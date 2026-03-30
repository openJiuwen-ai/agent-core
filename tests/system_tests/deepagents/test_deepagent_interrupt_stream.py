# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents import DeepAgent, DeepAgentConfig
from openjiuwen.deepagents.rails import ConfirmInterruptRail
from tests.system_tests.agent.react_agent.interrupt.test_base import (
    ReadTool,
    WriteTool,
    API_KEY,
    API_BASE,
    MODEL_NAME,
    MODEL_PROVIDER,
)


def create_model():
    """Create Model instance for testing."""
    client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        verify_ssl=False,
    )
    model_config = ModelRequestConfig(model=MODEL_NAME)
    return Model(model_client_config=client_config, model_config=model_config)


class TestDeepAgentInterruptStream:
    """Test DeepAgent stream interrupt output."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not API_KEY or not API_BASE, reason="API_KEY and API_BASE required")
    async def test_deepagent_stream_interrupt_resume(self):
        """Test DeepAgent stream interrupt and resume flow."""
        await Runner.start()
        try:
            card = AgentCard(id="test_deepagent_resume_agent", name="TestDeepAgentResume")
            agent = DeepAgent(card)

            model = create_model()

            config = DeepAgentConfig(
                model=model,
                enable_task_loop=True,
                max_iterations=5,
            )
            agent.configure(config)

            read_tool = ReadTool()
            write_tool = WriteTool()
            Runner.resource_mgr.add_tool(read_tool)
            Runner.resource_mgr.add_tool(write_tool)
            agent.ability_manager.add(read_tool.card)
            agent.ability_manager.add(write_tool.card)

            rail = ConfirmInterruptRail(tool_names=["write"])
            agent.add_rail(rail)

            outputs1 = []
            interrupt_detected = False
            tool_call_id = None

            async for output in Runner.run_agent_streaming(
                    agent=agent,
                    inputs={"query": "请写入文件 test.txt 内容为 hello world", "conversation_id": "test_resume_1"},
            ):
                outputs1.append(output)
                if output.type == INTERACTION:
                    interrupt_detected = True
                    tool_call_id = output.payload.id

            assert interrupt_detected, "Should detect interrupt"
            assert tool_call_id is not None, "Should get tool_call_id"
            assert write_tool.invoke_count == 0

            interactive_input = InteractiveInput()
            interactive_input.update(tool_call_id, {
                "approved": True,
                "feedback": "Confirm",
                "auto_confirm": False
            })

            outputs2 = []
            second_interrupt_detected = False

            async for output in Runner.run_agent_streaming(
                    agent=agent,
                    inputs={"query": interactive_input, "conversation_id": "test_resume_1"},
            ):
                outputs2.append(output)
                if output.type == INTERACTION:
                    second_interrupt_detected = True

            assert not second_interrupt_detected, "Should not interrupt after confirm"
            assert write_tool.invoke_count == 1, f"Expected write invoke_count=1, got {write_tool.invoke_count}"
        finally:
            await Runner.stop()
