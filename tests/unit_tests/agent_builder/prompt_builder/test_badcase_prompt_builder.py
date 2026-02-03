#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import List, AsyncIterator, Union, Optional

import pytest

from openjiuwen.dev_tools.prompt_builder import BadCasePromptBuilder
from openjiuwen.dev_tools.tune import EvaluatedCase, Case
from openjiuwen.core.foundation.llm import (
    ModelRequestConfig, ModelClientConfig, AssistantMessage, Model, BaseModelClient,
    BaseMessage, BaseOutputParser, AssistantMessageChunk
)
from openjiuwen.core.foundation.llm.model import _CLIENT_TYPE_REGISTRY
from openjiuwen.core.foundation.tool import ToolInfo
import openjiuwen.dev_tools.prompt_builder.builder.prompt_zh as TEMPLATE_ZH


class MockModelClient(BaseModelClient):
    """Mock 大模型，返回预定义的响应"""

    def __init__(
            self,
            model_client_config: Optional[ModelClientConfig],
            model_config: ModelRequestConfig = None,
    ):
        pass

    def _get_next_response(self, messages) -> AssistantMessage:
        """获取下一个响应"""
        return AssistantMessage(content="".join(msg.content for msg in messages))

    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AssistantMessage:
        """同步调用"""
        return self._get_next_response(messages)

    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        result = self._get_next_response()
        yield result

_CLIENT_TYPE_REGISTRY["MocKBadCaseLLM"] = MockModelClient


@pytest.mark.asyncio
async def test_bad_case_prompt_builder():
    builder = BadCasePromptBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKBadCaseLLM", api_base="", api_key="")
    )
    prompt = "bad_case test prompt"
    information_extraction_cases = [
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
    response = await builder.build(prompt, cases=information_extraction_cases)
    parse_str = re.findall(
        r"<summary>((?:(?!</summary>).)*?)</summary>",
        TEMPLATE_ZH.PROMPT_BAD_CASE_ANALYZE_TEMPLATE.content[0].content,
        re.DOTALL
    )
    assert response == TEMPLATE_ZH.PROMPT_BAD_CASE_OPTIMIZE_TEMPLATE.format(
        dict(original_prompt=prompt, feedback=parse_str[0])).content[0].content
