#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, AsyncIterator, Union, Optional

import pytest

import openjiuwen.dev_tools.prompt_builder.builder.prompt_zh as TEMPLATE_ZH
from openjiuwen.core.foundation.llm.schema import AudioGenerationResponse, VideoGenerationResponse, \
    ImageGenerationResponse
from openjiuwen.dev_tools.prompt_builder import MetaTemplateBuilder
from openjiuwen.dev_tools.prompt_builder.builder.meta_template_builder import META_TEMPLATE_NAME_PREFIX
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    ModelRequestConfig, ModelClientConfig, AssistantMessage, BaseModelClient,
    BaseMessage, BaseOutputParser, AssistantMessageChunk, UserMessage
)
from openjiuwen.core.foundation.llm.model import _CLIENT_TYPE_REGISTRY
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.prompt import PromptTemplate


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

_CLIENT_TYPE_REGISTRY["MocKMetaTemplateLLM"] = MockModelClient


@pytest.mark.asyncio
async def test_register_custom_template():
    builder = MetaTemplateBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKMetaTemplateLLM", api_base="", api_key="")
    )

    template = "this is a string meta template"
    builder.register_meta_template("custom_general", template)
    meta_template = builder.get_meta_template(META_TEMPLATE_NAME_PREFIX + "custom_general")
    assert meta_template.content == template
    builder.pop_meta_template(META_TEMPLATE_NAME_PREFIX + "custom_general")

    # register string-Template template
    template = PromptTemplate(content="this is a string meta template")
    builder.register_meta_template("custom_general", template)
    meta_template = builder.get_meta_template(META_TEMPLATE_NAME_PREFIX + "custom_general")
    assert meta_template.content == template.content
    builder.pop_meta_template(META_TEMPLATE_NAME_PREFIX + "custom_general")

    # register invalid type template
    template = ("this is a invalid tuple meta template", )
    with pytest.raises(BaseError) as context:
        builder.register_meta_template("custom_general", template)
    assert context.value.code == StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR.code


@pytest.mark.asyncio
async def test_build_with_default_meta_template():
    builder = MetaTemplateBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKMetaTemplateLLM", api_base="", api_key="")
    )
    response = await builder.build(prompt="你是一个旅行助手")
    assert response == (
        TEMPLATE_ZH.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE.content[0].content +
        TEMPLATE_ZH.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE.format(
            dict(instruction="你是一个旅行助手")).content[0].content
    )

    response = await builder.build(prompt="你是一个旅行助手", template_type="general")
    assert response == (
        TEMPLATE_ZH.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE.content[0].content +
        TEMPLATE_ZH.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE.format(
            dict(instruction="你是一个旅行助手")).content[0].content
    )

    response = await builder.build(prompt="你是一个旅行助手", template_type="plan")
    assert response == (
        TEMPLATE_ZH.PROMPT_BUILD_PLAN_META_SYSTEM_TEMPLATE.content[0].content +
        TEMPLATE_ZH.PROMPT_BUILD_PLAN_META_USER_TEMPLATE.format(
            dict(instruction="你是一个旅行助手", tools="None")).content[0].content
    )


@pytest.mark.asyncio
async def test_build_with_custom_meta_template():
    builder = MetaTemplateBuilder(
        ModelRequestConfig(model=""), ModelClientConfig(client_provider="MocKMetaTemplateLLM", api_base="", api_key="")
    )
    template = "you are a custom meta template"
    with pytest.raises(BaseError) as context:
        response = await builder.build(prompt="你是一个旅行助手", template_type="other")
    assert context.value.code == StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR.code

    with pytest.raises(BaseError) as context:
        builder.register_meta_template("custom_general", template)
        response = await builder.build(prompt="你是一个旅行助手", template_type="other",
                                 custom_template_name="not_defined")
    assert context.value.code == StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR.code

    response = await builder.build(prompt="你是一个旅行助手", template_type="other",
                             custom_template_name="custom_general")
    assert response == template
