#!/usr/bin/python3.10
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

import unittest
from unittest.mock import MagicMock
import os

from openjiuwen.agent.common.schema import WorkflowSchema
from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.agent.controller.config.reasoner_config import IntentDetectionConfig
from openjiuwen.core.agent.controller.reasoner.agent_reasoner import AgentReasoner
from openjiuwen.core.agent.controller.reasoner.intent_detection import IntentDetection
from openjiuwen.core.agent.message.message import Message, MessageContent, MessageSource, MessageType, SourceType
from openjiuwen.core.agent.task.task import TaskInput
from openjiuwen.core.common.logging import logger
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.utils.llm.base import BaseModelInfo


API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

class TestAgentReasoner(unittest.IsolatedAsyncioTestCase):
    """测试AgentReasoner类的单元测试"""
    
    async def asyncSetUp(self):
        """每个测试用例前的设置"""
        # 创建mock对象
        self.mock_agent_config = AgentConfig(
            id="test_agent_id",
            model=ModelConfig(
                model_provider=MODEL_PROVIDER,
                model_info=BaseModelInfo(
                    model_name=MODEL_NAME,
                    api_base=API_BASE,
                    api_key=API_KEY
                )
            ),
            workflows=[
                WorkflowSchema(
                    id="weather_workflow_id",
                    name="天气",
                    description="天气工作流"
                )
            ]
        )
        self.mock_intent_config = IntentDetectionConfig(
            user_prompt="请判断用户意图",
            category_list=["天气", "name1", "name2"],
            chat_history_max_turn=10,
        )
        self.mock_context_engine = MagicMock(spec=ContextEngine)
        self.mock_runtime = MagicMock(spec=Runtime)
        self.mock_runtime.get_model.return_value = None
        self.mock_runtime.executable_id.return_value = "test_executable_id"
        self.mock_runtime.session_id.return_value = "test_session_id"
        
        # 创建AgentReasoner实例
        self.agent_reasoner = AgentReasoner(
            config=self.mock_agent_config,
            context_engine=self.mock_context_engine,
            runtime=self.mock_runtime
        )
        
        # 创建IntentDetection实例
        self.mock_intent_detection = IntentDetection(
            intent_config=self.mock_intent_config,
            agent_config=self.mock_agent_config,
            context_engine=self.mock_context_engine,
            runtime=self.mock_runtime
        )
        
    @unittest.skip("skip test_process_message_with_intent_detection")
    async def test_process_message_with_intent_detection(self):
        """测试process_message方法正常调用intent_detection处理消息"""
        # 设置测试数据
        test_message = Message(
            msg_id="test_message_id",
            msg_type=MessageType.USER_INPUT,
            content=MessageContent(
                text="天气"),
            source=MessageSource(
                conversation_id="test_conversation_id",
                source_type=SourceType.USER
            )
        )
        
        # 预期的任务列表
        expected_task_input = TaskInput(
            target_id="weather_workflow_id",
            target_name="天气",
            arguments=MessageContent(text="天气")
        )
        
        # 设置intent_detection模块
        self.agent_reasoner.set_intent_detection(self.mock_intent_detection)

        # 执行测试
        result = await self.agent_reasoner.process_message(test_message)
        
        # 验证结果
        self.assertEqual(result[0].input, expected_task_input)

if __name__ == "__main__":
    unittest.main()