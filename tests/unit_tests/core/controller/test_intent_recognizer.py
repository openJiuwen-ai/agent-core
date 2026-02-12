# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import unittest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller import ControllerConfig, IntentRecognizer, TaskManager, InputEvent, TextDataFrame, \
    IntentType, Task, TaskStatus, Intent
from openjiuwen.core.foundation.llm import (
    ModelClientConfig, ModelRequestConfig, Model, AssistantMessage, ToolCall, UsageMetadata
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.core.single_agent import AbilityManager
from tests.unit_tests.fixtures.mock_llm import (
    mock_llm_context,
    create_tool_call_response,
    create_text_response,
)


class TestIntentRecognizer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create a mock model and add it to resource manager
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="API_KEY",
                api_base="API_BASE",
                verify_ssl=False),
            model_config=ModelRequestConfig(model="MODEL_NAME"),
        )
        Runner.resource_mgr.add_model(
            model_id="ds_model",
            model=lambda: model,
        )

        controller_config = ControllerConfig(
            enable_intent_recognition=True,
            intent_llm_id="ds_model"
        )

        self.task_manager = TaskManager(controller_config)
        self.ability_manager = AbilityManager()
        self.context_engine = ContextEngine()

        self.intent_recognizer = IntentRecognizer(
            config=controller_config,
            task_manager=self.task_manager,
            ability_manager=self.ability_manager,
            context_engine=self.context_engine
        )

    async def test_intent_recognizer(self):
        input_events = [
            InputEvent(input_data=[TextDataFrame(text="帮我搜索北京今天天气")]),
            InputEvent(input_data=[TextDataFrame(text="不搜了，改成昨天天气")]),
        ]
        session_id = "session_id"
        session = create_agent_session(session_id=session_id)

        # Mock LLM responses for the first input event
        # First call: create_task tool call
        # Second call: empty response (no tool calls) to end the loop
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_tool_call_response(
                    "create_task",
                    json.dumps({
                        "confidence": 0.9,
                        "task_description": "搜索北京今天天气"
                    }, ensure_ascii=False)
                ),
                create_text_response("任务已创建"),  # Empty response to end the loop
            ])

            intents = await self.intent_recognizer.recognize(input_events[0], session)
            assert len(intents) == 1
            assert intents[0].intent_type == IntentType.CREATE_TASK

            await self.task_manager.add_task(
                Task(
                    session_id=session_id,
                    task_id=intents[0].target_task_id,
                    task_type="custom",
                    description=intents[0].target_task_description,
                    priority=1,
                    status=TaskStatus.WORKING,
                )
            )

        # Mock LLM responses for the second input event
        # First call: cancel_task and create_task tool calls (2 intents in one response)
        # Second call: empty response (no tool calls) to end the loop
        with mock_llm_context() as mock_llm:
            # Get the task_id from the first intent for the cancel_task call
            existing_task_id = intents[0].target_task_id
            
            # Create a response with multiple tool calls
            multi_tool_response = AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_cancel_1",
                        type="function",
                        name="cancel_task",
                        arguments=json.dumps({
                            "confidence": 0.9,
                            "task_id": existing_task_id
                        }, ensure_ascii=False)
                    ),
                    ToolCall(
                        id="call_create_1",
                        type="function",
                        name="create_task",
                        arguments=json.dumps({
                            "confidence": 0.9,
                            "task_description": "搜索北京昨天天气"
                        }, ensure_ascii=False)
                    ),
                ],
                usage_metadata=UsageMetadata(
                    model_name="mock-model",
                    # finish_reason="tool_calls"
                )
            )
            
            mock_llm.set_responses([
                multi_tool_response,
                create_text_response("任务已更新"),  # Empty response to end the loop
            ])

            intents = await self.intent_recognizer.recognize(input_events[1], session)
            assert len(intents) == 2

    @unittest.skip("LLM API required")
    async def test_real_case(self):

        input_events = [
            InputEvent(input_data=[TextDataFrame(text="帮我搜索北京今天天气")]),
            InputEvent(input_data=[TextDataFrame(text="不搜了，改成昨天天气")]),
        ]
        session_id = "session_id"
        session = create_agent_session(session_id=session_id)

        intents = await self.intent_recognizer.recognize(input_events[0], session)
        assert intents[0].intent_type == IntentType.CREATE_TASK
        await self.task_manager.add_task(
            Task(
                session_id=session_id,
                task_id=intents[0].target_task_id,
                task_type="custom",
                description=intents[0].target_task_description,
                priority=1,
                status=TaskStatus.WORKING,
            )
        )

        # Mock LLM responses for the second input event
        intents = await self.intent_recognizer.recognize(input_events[1], session)
