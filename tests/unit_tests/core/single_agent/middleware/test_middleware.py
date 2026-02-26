# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import unittest
from unittest.mock import patch, MagicMock, call

import pytest

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent import AgentCard, ReActAgentConfig, ReActAgent
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.single_agent.middleware.base import AgentMiddleware, AgentCallbackContext

from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


class LogPrintMiddleware(AgentMiddleware):
    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called before agent.invoke() starts."""
        logger.info("before_invoke")

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Called after agent.invoke() completes."""
        logger.info("after_invoke")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called before LLM is invoked."""
        logger.info("before_model_call")

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Called after LLM response is received."""
        logger.info("after_model_call")

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Called before a tool is executed."""
        logger.info("before_tool_call")

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Called after a tool execution completes."""
        logger.info("after_tool_call")


class TestMiddlewareMock(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ.setdefault("LLM_SSL_VERIFY", "false")

        self.model_config = self._create_model()
        self.client_config = self._create_client_model()
        self.prompt_template = self._create_prompt_template()

        self.agent_card = AgentCard(
            description="数学计算助手",
        )
        self.react_agent_config = ReActAgentConfig(
            model_config_obj=self.model_config,
            model_client_config=self.client_config,
            prompt_template=self.prompt_template,
        )
        self.react_agent = ReActAgent(card=self.agent_card).configure(self.react_agent_config)

        self.log_print_middleware = LogPrintMiddleware()
        await self.react_agent.register_middleware(self.log_print_middleware)
        self.add_tool = self._create_add_tool()
        self.react_agent.ability_manager.add(self.add_tool.card)

    @staticmethod
    def _create_model():
        return ModelRequestConfig(
            model="gpt-3.5-turbo",
            temperature=0.8,
            top_p=0.9
        )

    @staticmethod
    def _create_client_model():
        return ModelClientConfig(
           client_provider="OpenAI",
           api_key="mock_key",
           api_base="mock_url",
           timeout=30,
           verify_ssl=False,
        )

    @staticmethod
    def _create_add_tool():
        return LocalFunction(
            card=ToolCard(
                id="add",
                name="add",
                description="加法运算",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"description": "第一个加数", "type": "number"},
                        "b": {"description": "第二个加数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )

    @staticmethod
    def _create_prompt_template():
        return [
            dict(
                role="system",
                content="你是一个数学计算助手，在适当的时候调用工具来完成计算任务。"
            )
        ]

    @pytest.mark.asyncio
    async def test_middleware_executes_when_react_agent_invoke(self):
        """Test middleware methods are executed when ReActAgent.invoke() is called."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([
            create_tool_call_response("add", '{"a": 1, "b": 2}'),
            create_text_response("根据计算结果，1+2=3"),
        ])
        with patch.object(self.react_agent, "_get_llm", return_value=mock_llm), \
            patch("openjiuwen.core.common.logging.logger.info") as mock_logger_info:
            await self.react_agent.invoke(
                {"conversation_id": "test_session", "query": "计算1+2"}
            )
            mock_logger_info.assert_any_call("before_invoke")
            mock_logger_info.assert_any_call("after_invoke")
            mock_logger_info.assert_any_call("before_model_call")
            mock_logger_info.assert_any_call("after_model_call")
            mock_logger_info.assert_any_call("before_tool_call")
            mock_logger_info.assert_any_call("after_tool_call")

    @pytest.mark.asyncio
    async def test_middleware_executes_when_react_agent_invoke_with_no_toolcall(self):
        """Test middleware methods are executed when ReActAgent.invoke() is called with no toolcall."""
        mock_llm = MockLLMModel()
        mock_llm.set_responses([create_text_response("finish")])
        with patch.object(self.react_agent, "_get_llm", return_value=mock_llm), \
            patch("openjiuwen.core.common.logging.logger.info") as mock_logger_info:
            await self.react_agent.invoke(
                {"conversation_id": "test_session", "query": "计算1+2"}
            )
            mock_logger_info.assert_any_call("before_invoke")
            mock_logger_info.assert_any_call("after_invoke")
            mock_logger_info.assert_any_call("before_model_call")
            mock_logger_info.assert_any_call("after_model_call")
            assert call("before_tool_call") not in mock_logger_info.call_args_list
            assert call("after_tool_call") not in mock_logger_info.call_args_list

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
