#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# import pytest
from unittest.mock import patch
from typing import List, AsyncIterator, Union, Optional

import pytest

from openjiuwen.core.foundation.llm import (
    ModelRequestConfig, ModelClientConfig, AssistantMessage, Model, BaseModelClient,
    BaseMessage, BaseOutputParser, AssistantMessageChunk, UserMessage
)
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse, VideoGenerationResponse, \
    AudioGenerationResponse
from openjiuwen.core.foundation.tool import ToolInfo

from openjiuwen.dev_tools.prompt_builder import FeedbackPromptBuilder
import openjiuwen.dev_tools.prompt_builder.builder.prompt_zh as TEMPLATE_ZH

MOCK_INTENT = '''```json{"intent": "true",
"optimized_feedback": "[优化后的反馈信息]",
                            "optimization_directions": "[联想并提示其他优化方向的建议]"}```'''


class MockModelClient(BaseModelClient):
    """Mock 大模型，返回预定义的响应"""
    __client_name__ = "MocKFeedbackLLM"

    def __init__(
            self,
            model_client_config: Optional[ModelClientConfig],
            model_config: ModelRequestConfig = None,
    ):
        pass

    def _get_next_response(self, messages) -> AssistantMessage:
        """获取下一个响应"""
        return AssistantMessage(content="".join(MOCK_INTENT + msg.content for msg in messages))

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

    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        pass

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        pass

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        pass


@pytest.mark.asyncio
async def test_feedback_prompt_builder_general():
    builder = FeedbackPromptBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKFeedbackLLM", api_base="", api_key="")
    )
    prompt = "你是一个旅行助手"
    feedback = "丰富一下"
    response = await builder.build(prompt=prompt, feedback=feedback, mode="general")
    # 构建预期的消息内容
    expected_messages = TEMPLATE_ZH.PROMPT_FEEDBACK_GENERAL_TEMPLATE.format(
        dict(original_prompt=prompt, suggestion=feedback)
    ).to_messages()
    expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
    assert response == expected_content


@pytest.mark.asyncio
async def test_feedback_prompt_builder_insert():
    builder = FeedbackPromptBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKFeedbackLLM", api_base="", api_key="")
    )
    insert_tag = "[用户要插入的位置]"
    prompt = "你是一个旅行助手"
    feedback = "丰富一下"
    response = await builder.build(prompt=prompt, feedback=feedback, mode="insert", start_pos=3)
    # 构建预期的消息内容
    expected_messages = TEMPLATE_ZH.PROMPT_FEEDBACK_INSERT_TEMPLATE.format(
        dict(original_prompt=prompt[:3] + insert_tag + prompt[3:],
             suggestion="[优化后的反馈信息]"
             )
    ).to_messages()
    expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
    assert response == expected_content


@pytest.mark.asyncio
async def test_feedback_prompt_builder_select():
    builder = FeedbackPromptBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKFeedbackLLM", api_base="", api_key="")
    )

    prompt = "你是一个旅行助手"
    feedback = "丰富一下"
    response = await builder.build(prompt=prompt + MOCK_INTENT, feedback=feedback,
                                   mode="select", start_pos=0, end_pos=3)
    # 构建预期的消息内容
    expected_messages = TEMPLATE_ZH.PROMPT_FEEDBACK_SELECT_TEMPLATE.format(
        dict(original_prompt=prompt + MOCK_INTENT,
             suggestion="[优化后的反馈信息]",
             pending_optimized_prompt=prompt[0:3]
             )
    ).to_messages()
    expected_content = "".join(MOCK_INTENT + msg.content for msg in expected_messages)
    assert response == expected_content
