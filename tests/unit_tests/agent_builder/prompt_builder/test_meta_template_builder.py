#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Any, Dict, Iterator, AsyncIterator
from unittest.mock import patch
import pytest

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.common.configs.model_config import ModelConfig
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.prompt.template.template import Template
from openjiuwen.core.utils.llm.base import BaseModelInfo
from openjiuwen.core.utils.llm.messages import AIMessage
from openjiuwen.agent_builder.prompt_builder.builder.meta_template_builder import (MetaTemplateBuilder,
                                                                                   META_TEMPLATE_NAME_PREFIX)
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
        return self._get_next_response([])

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
        result = self._get_next_response([])
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
        result = self._get_next_response([])
        yield result


def test_register_custom_template():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="mock_api")
    config = ModelConfig(
        model_provider="",
        model_info=BaseModelInfo(
            api_key="sk-fake",
            api_base="mock_api"
        )
    )
    with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        # register string template
        mock_get_model.return_value = mock_llm
        builder = MetaTemplateBuilder(config)
        template = "this is a string meta template"
        builder.register_meta_template("custom_general", template)
        meta_template = builder._meta_template_manager.get(META_TEMPLATE_NAME_PREFIX + "custom_general")
        assert meta_template.content == template
        builder._meta_template_manager.pop(META_TEMPLATE_NAME_PREFIX + "custom_general")

        # register string-Template template
        template = Template(content="this is a string meta template")
        builder.register_meta_template("custom_general", template)
        meta_template = builder._meta_template_manager.get(META_TEMPLATE_NAME_PREFIX + "custom_general")
        assert meta_template.content == template.content
        builder._meta_template_manager.pop(META_TEMPLATE_NAME_PREFIX + "custom_general")

        # register invalid type template
        template = ("this is a invalid tuple meta template", )
        with pytest.raises(JiuWenBaseException) as context:
            builder.register_meta_template("custom_general", template)
        assert context.value.error_code == StatusCode.AGENT_BUILDER_META_TEMPLATE_REGISTER_ERROR.code


def test_build_with_default_meta_template():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="mock_api")
    with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="mock_api"
            )
        )
        builder = MetaTemplateBuilder(config)
        response = builder.build(prompt="你是一个旅行助手")
        assert response == (
            TEMPLATE.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE.content[0].content +
            TEMPLATE.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE.format(
                dict(instruction="你是一个旅行助手")).content[0].content
        )

        response = builder.build(prompt="你是一个旅行助手", template_type="general")
        assert response == (
            TEMPLATE.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE.content[0].content +
            TEMPLATE.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE.format(
                dict(instruction="你是一个旅行助手")).content[0].content
        )

        response = builder.build(prompt="你是一个旅行助手", template_type="plan")
        assert response == (
            TEMPLATE.PROMPT_BUILD_PLAN_META_SYSTEM_TEMPLATE.content[0].content +
            TEMPLATE.PROMPT_BUILD_PLAN_META_USER_TEMPLATE.format(
                dict(instruction="你是一个旅行助手", tools="None")).content[0].content
        )


def test_build_with_custom_meta_template():
    mock_llm = MockLLMModel(api_key="mock_key", api_base="mock_api")
    template = "you are a custom meta template"
    with patch('openjiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model') as mock_get_model:
        mock_get_model.return_value = mock_llm
        config = ModelConfig(
            model_provider="",
            model_info=BaseModelInfo(
                api_key="sk-fake",
                api_base="mock_api"
            )
        )
        builder = MetaTemplateBuilder(config)

        with pytest.raises(JiuWenBaseException) as context:
            response = builder.build(prompt="你是一个旅行助手", template_type="other")
        assert context.value.error_code == StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code

        with pytest.raises(JiuWenBaseException) as context:
            builder.register_meta_template("custom_general", template)
            response = builder.build(prompt="你是一个旅行助手", template_type="other",
                                     custom_template_name="not_defined")
        assert context.value.error_code == StatusCode.AGENT_BUILDER_META_TEMPLATE_ERROR.code

        response = builder.build(prompt="你是一个旅行助手", template_type="other",
                                 custom_template_name="custom_general")
        assert response == template
