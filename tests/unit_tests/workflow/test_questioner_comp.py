#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import unittest
from unittest.mock import patch

from jiuwen.core.runtime.interaction.interaction import InteractionOutput
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.runtime.wrapper import TaskRuntime
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.component.end_comp import End
from jiuwen.core.component.questioner_comp import FieldInfo, QuestionerConfig, QuestionerComponent
from jiuwen.core.component.start_comp import Start
from jiuwen.core.graph.executable import Input
from jiuwen.core.runtime.interaction.interactive_input import InteractiveInput
from jiuwen.core.runtime.workflow import WorkflowRuntime
from jiuwen.core.workflow.base import Workflow


class MockLLMModel:
    pass


class QuestionerTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    @staticmethod
    def invoke_workflow(inputs: Input, context: Runtime, flow: Workflow):
        loop = asyncio.get_event_loop()
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, runtime=context.create_workflow_runtime()))
        loop.run_until_complete(feature)
        return feature.result()

    @staticmethod
    def invoke_workflow_with_workflow_context(inputs: Input, runtime: WorkflowRuntime, flow: Workflow):
        loop = asyncio.get_event_loop()
        feature = asyncio.ensure_future(flow.invoke(inputs=inputs, runtime=runtime))
        loop.run_until_complete(feature)
        return feature.result()

    @staticmethod
    def _create_context(session_id):
        return TaskRuntime(trace_id=session_id)

    @patch("jiuwen.core.component.questioner_comp.QuestionerDirectReplyHandler._invoke_llm_for_extraction")
    @patch("jiuwen.core.component.questioner_comp.QuestionerDirectReplyHandler._build_llm_inputs")
    @patch("jiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model")
    def test_invoke_questioner_component_in_workflow_repeat_ask(self, mock_get_model, mock_llm_inputs,
                                                                mock_extraction):
        mock_get_model.return_value = MockLLMModel()
        mock_prompt_template = [
            dict(role="system", content="系统提示词"),
            dict(role="user", content="你是一个AI助手")
        ]
        mock_llm_inputs.return_value = mock_prompt_template
        mock_extraction.return_value = dict(location="hangzhou")

        flow = Workflow()

        key_fields = [
            FieldInfo(field_name="location", description="地点", required=True),
            FieldInfo(field_name="time", description="时间", required=True, default_value="today")
        ]

        start_component = Start(
            {
                "inputs": [
                    {"id": "query", "type": "String", "required": "true", "sourceType": "ref"}
                ]
            }
        )
        end_component = End({"responseTemplate": "{{location}} | {{time}}"})

        model_config = ModelConfig(model_provider="openai")
        questioner_config = QuestionerConfig(
            model=model_config,
            question_content="查询什么城市的天气",
            extract_fields_from_response=True,
            field_names=key_fields,
            with_chat_history=False,
        )
        questioner_component = QuestionerComponent(questioner_comp_config=questioner_config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component,
                          inputs_schema={"location": "${questioner.location}", "time": "${questioner.time}"})
        flow.add_workflow_comp("questioner", questioner_component, inputs_schema={"query": "${s.query}"})

        flow.add_connection("s", "questioner")
        flow.add_connection("questioner", "e")

        session_id = "test_questioner"
        workflow_context = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        first_question = self.invoke_workflow_with_workflow_context({"query": "你好"}, workflow_context, flow)
        first_question = first_question.result[0] if first_question else dict()
        payload = first_question.payload
        if isinstance(payload, InteractionOutput) and payload.id is not None:
            component_id = payload.id
        else:
            assert False
        user_input = InteractiveInput()
        user_input.update(component_id, "地点是杭州")

        workflow_context = TaskRuntime(trace_id=session_id).create_workflow_runtime()
        final_result = self.invoke_workflow_with_workflow_context(user_input, workflow_context, flow)
        assert final_result.result.get("responseContent") == "hangzhou | today"
