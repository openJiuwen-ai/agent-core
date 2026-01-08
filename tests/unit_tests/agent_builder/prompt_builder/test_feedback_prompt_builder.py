#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# import pytest
from unittest.mock import patch
from typing import List, Any, Dict, Iterator, AsyncIterator

from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.foundation.llm import AIMessage

from openjiuwen.dev_tools.prompt_builder import FeedbackPromptBuilder
import openjiuwen.dev_tools.prompt_builder.builder.utils as TEMPLATE

MOCK_INTENT = '''```json{"intent": "true",
"optimized_feedback": "[优化后的反馈信息]",
                            "optimization_directions": "[联想并提示其他优化方向的建议]"}```'''


class MockLLMModel(BaseModelClient):
    """Mock 大模型，返回预定义的响应"""

    def __init__(self, api_key: str, api_base: str, **kwargs):
        super().__init__(api_key=api_key, api_base=api_base)

    def _get_next_response(self, messages) -> AIMessage:
        """获取下一个响应"""
        return AIMessage(content="".join(MOCK_INTENT + msg.get("content") for msg in messages))

    def _invoke(
            self,
            model_name: str,
            messages: List[Dict],
            tools: List[Dict] = None,
            temperature: float = 0.1,
            top_p: float = 0.1,
            **kwargs: Any
    ) -> AIMessage:
        """同步调用"""
        return self._get_next_response(messages)

    async def _ainvoke(
            self,
            model_name: str,
            messages: List[Dict],
            tools: List[Dict] = None,
            temperature: float = 0.1,
            top_p: float = 0.1,
            **kwargs: Any
    ) -> AIMessage:
        """异步调用"""
        return self._get_next_response()

    def _stream(
            self,
            model_name: str,
            messages: List[Dict],
            tools: List[Dict] = None,
            temperature: float = 0.1,
            top_p: float = 0.1,
            **kwargs: Any
    ) -> Iterator[Any]:
        """流式返回"""
        result = self._get_next_response()
        yield result

    async def _astream(
            self,
            model_name: str,
            messages: List[Dict],
            tools: List[Dict] = None,
            temperature: float = 0.1,
            top_p: float = 0.1,
            **kwargs: Any
    ) -> AsyncIterator[Any]:
        """异步流式返回"""
        result = self._get_next_response()
        yield result


def test_feedback_prompt_builder_general():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="https://api.openai.com")
    with patch('openjiuwen.core.foundation.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="https://api.openai.com"
            )
        )
        builder = FeedbackPromptBuilder(config)
        prompt = "你是一个旅行助手"
        feedback = "丰富一下"
        response = builder.build(prompt=prompt, feedback=feedback, mode="general")
        # 构建预期的消息内容
        expected_messages = TEMPLATE.PROMPT_FEEDBACK_GENERAL_TEMPLATE.format(
            dict(original_prompt=prompt, suggestion=feedback)
        ).to_messages()
        expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
        assert response == expected_content


def test_feedback_prompt_builder_insert():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="https://api.openai.com")
    with patch('openjiuwen.core.foundation.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="https://api.openai.com"
            )
        )
        builder = FeedbackPromptBuilder(config)
        insert_tag = "[用户要插入的位置]"
        prompt = "你是一个旅行助手"
        feedback = "丰富一下"
        response = builder.build(prompt=prompt, feedback=feedback, mode="insert", start_pos=3)
        # 构建预期的消息内容
        expected_messages = TEMPLATE.PROMPT_FEEDBACK_INSERT_TEMPLATE.format(
            dict(original_prompt=prompt[:3] + insert_tag + prompt[3:],
                 suggestion="[优化后的反馈信息]"
            )
        ).to_messages()
        expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
        assert response == expected_content


def test_feedback_prompt_builder_select():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="https://api.openai.com")
    with patch('openjiuwen.core.foundation.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="https://api.openai.com"
            )
        )
        builder = FeedbackPromptBuilder(config)

        prompt = "你是一个旅行助手"
        feedback = "丰富一下"
        response = builder.build(prompt=prompt + MOCK_INTENT, feedback=feedback, mode="select", start_pos=0, end_pos=3)
        # 构建预期的消息内容
        expected_messages = TEMPLATE.PROMPT_FEEDBACK_SELECT_TEMPLATE.format(
            dict(original_prompt=prompt + MOCK_INTENT,
                 suggestion="[优化后的反馈信息]",
                 pending_optimized_prompt=prompt[0:3]
            )
        ).to_messages()
        expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
        assert response == expected_content