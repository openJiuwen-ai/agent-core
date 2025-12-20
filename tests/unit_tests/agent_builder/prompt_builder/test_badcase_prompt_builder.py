#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from unittest.mock import patch
from typing import List, Any, Dict, Iterator, AsyncIterator

from openjiuwen.agent_builder.prompt_builder.builder.badcase_prompt_builder import BadCasePromptBuilder
from openjiuwen.agent_builder.tune.base import EvaluatedCase, Case
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.utils.llm.messages import AIMessage
import openjiuwen.agent_builder.prompt_builder.builder.utils as TEMPLATE


class MockLLMModel(BaseModelClient):
    """Mock 大模型，返回预定义的响应"""

    def __init__(self, api_key: str, api_base: str, **kwargs):
        super().__init__(api_key=api_key, api_base=api_base)

    def _get_next_response(self, messages) -> AIMessage:
        """获取下一个响应"""
        return AIMessage(content="".join(msg.get("content") for msg in messages))

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


def test_bad_case_prompt_builder():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="https://api.openai.com")
    with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="https://api.openai.com"
            )
        )
        builder = BadCasePromptBuilder(config)
        prompt = "bad_case test prompt"
        INFORMATION_EXTRACTION_CASES = [
            EvaluatedCase(case=Case(
                inputs={"query": "test input"},
                label={"label": "test label"}),
                answer={"answer": "test answer"}
            ),
            EvaluatedCase(case=Case(
                inputs={"query": "test input"},
                label={"label": "test label"}),
                answer={"answer": "test answer"}
            )
        ]
        response = builder.build(prompt, cases=INFORMATION_EXTRACTION_CASES)
        parse_str = re.findall(r"<summary>((?:(?!</summary>).)*?)</summary>", TEMPLATE.PROMPT_BAD_CASE_ANALYZE_TEMPLATE.content[0].content, re.DOTALL)
        assert response == TEMPLATE.PROMPT_BAD_CASE_OPTIMIZE_TEMPLATE.format(
            dict(original_prompt=prompt, feedback=parse_str[0])).content[0].content
