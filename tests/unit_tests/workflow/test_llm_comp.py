#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import sys
import types
from typing import Any, Union, List, Dict, AsyncIterator

import pytest
from unittest.mock import Mock, patch, AsyncMock

from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.component.common.configs.model_config import ModelConfig
from jiuwen.core.utils.llm.messages import AIMessage, BaseMessage, ToolInfo
from jiuwen.core.utils.llm.messages_chunk import BaseMessageChunk
from jiuwen.core.workflow.base import Workflow
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.component.llm_comp import LLMCompConfig, LLMExecutable, LLMComponent
from jiuwen.core.runtime.workflow import WorkflowRuntime, NodeRuntime
from jiuwen.core.runtime.wrapper import WrappedNodeRuntime
from jiuwen.core.utils.llm.base import BaseModelInfo, BaseChatModel

from tests.unit_tests.workflow.test_mock_node import MockStartNode, MockEndNode

fake_base = types.ModuleType("base")
fake_base.logger = Mock()

fake_exception_module = types.ModuleType("base")
fake_exception_module.JiuWenBaseException = Mock()

sys.modules["jiuwen.core.common.logging.base"] = fake_base
sys.modules["jiuwen.core.common.exception.base"] = fake_exception_module



USER_FIELDS = "userFields"


@pytest.fixture
def fake_node_ctx():
    return WrappedNodeRuntime(NodeRuntime(WorkflowRuntime(), "test"))


@pytest.fixture
def fake_input():
    return lambda **kw: {USER_FIELDS: kw}


@pytest.fixture
def fake_model_config() -> ModelConfig:
    """构造一个最小可用的 ModelConfig"""
    return ModelConfig(
        model_provider="openai",
        model_info=BaseModelInfo(
            api_key="sk-fake",
            api_base="mock_path",
            model_name="mock_name",
            temperature=0.8,
            top_p=0.9,
            streaming=False,
            timeout=30.0,
        ),
    )

class FakeModel(BaseChatModel):
    def __init__(self, api_key, api_base):
        super().__init__(api_key=api_key, api_base=api_base)

    async def astream(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any) -> AsyncIterator[
        BaseMessageChunk]:
        yield BaseMessageChunk(role="assistant", content="mocked response")

    async def ainvoke(self, messages: Union[List[BaseMessage], List[Dict], str],
                      tools: Union[List[ToolInfo], List[Dict]] = None, **kwargs: Any):
        return BaseMessageChunk(role="assistant", content="mocked response")


@patch(
    "jiuwen.core.utils.llm.model_utils.model_factory.ModelFactory.get_model",
    autospec=True,
)
class TestLLMExecutableInvoke:

    @pytest.mark.asyncio
    async def test_invoke_success(
            self,
            mock_get_model,
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )
        exe = LLMExecutable(config)

        fake_llm = FakeModel(api_base="1111", api_key="ssss")

        mock_get_model.return_value = fake_llm

        output = await exe.invoke(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock())

        assert output == {'result': 'mocked response'}

    @pytest.mark.asyncio
    async def test_stream_success(
            self,
            mock_get_model,
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(
            model=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {query}"}],
            response_format={"type": "text"},
            output_config={"result": {
                "type": "string",
                "required": True,
            }},
        )
        exe = LLMExecutable(config)

        fake_llm = AsyncMock()

        async def mock_stream_response(*, model_name: str, messages: list, **kwargs):
            for chunk in ["mocked ", "response"]:
                yield AIMessage(content=chunk)

        fake_llm.astream = mock_stream_response
        mock_get_model.return_value = fake_llm

        chunks = []
        async for chunk in exe.stream(fake_input(userFields=dict(query="pytest")), fake_node_ctx, context=Mock()):
            chunks.append(chunk)

        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_invoke_llm_exception(
            self,
            mock_get_model,
            fake_node_ctx,
            fake_input,
            fake_model_config,
    ):
        config = LLMCompConfig(model=fake_model_config, template_content=[{"role": "user", "content": "Hello {name}"}], response_format={"type": "text"},)
        try:
            exe = LLMExecutable(config)
        except JiuWenBaseException as e:
            assert e.error_code == StatusCode.LLM_COMPONENT_RESPONSE_FORMAT_CONFIG_ERROR.code

    @pytest.mark.asyncio
    async def test_llm_in_workflow(
            self,
            mock_get_model,
            fake_model_config,
    ):
        runtime = WorkflowRuntime()

        fake_llm = FakeModel(api_key="111", api_base="ssss")
        mock_get_model.return_value = fake_llm

        flow = Workflow()
        flow.set_start_comp("start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${user.inputs.a}",
                                "b": "${user.inputs.b}"})

        flow.set_end_comp(
            "end",
            MockEndNode("end"),
            inputs_schema={"a": "${a.result}", "b": "${b.result}"},
        )

        config = LLMCompConfig(
            model=fake_model_config,
            template_content=[{"role": "user", "content": "Hello {name}"}],
            response_format={"type": "text"},
            output_config={"result": {"type": "string", "required": True}},
        )
        llm_comp = LLMComponent(config)
        flow.add_workflow_comp("llm", llm_comp,
                               inputs_schema={"a": "${start.a}",
                                              "userFields": {"query": "${start.query}"}})

        flow.add_connection("start", "llm")
        flow.add_connection("llm", "end")

        result = await flow.invoke(inputs={"a": 2, "userFields": dict(query="pytest")}, runtime=runtime)
        assert result is not None
