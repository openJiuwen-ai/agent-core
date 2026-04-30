# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import AsyncIterator, List, Optional, Union
from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    BaseMessage,
    BaseModelClient,
    BaseOutputParser,
    ModelClientConfig,
    ModelRequestConfig,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo


class MockModelClient(BaseModelClient):
    """Mock LLM client for testing"""
    __client_name__ = "MockTestLLM"

    def __init__(
            self,
            model_config: ModelRequestConfig,
            model_client_config: ModelClientConfig,
    ) -> None:
        super().__init__(model_config, model_client_config)
        self.responses: List[str] = []

    def set_response(self, response: str):
        """Set the response to return"""
        self.responses = [response]

    def set_responses(self, responses: List[str]):
        """Set multiple responses to return in sequence"""
        self.responses = responses

    def get_next_response(self) -> AssistantMessage:
        """Get next response"""
        if self.responses:
            content = self.responses.pop(0)
        else:
            content = '{"result": "mock_response"}'
        return AssistantMessage(content=content)

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
        return self.get_next_response()

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
        result = self.get_next_response()
        yield result

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ):
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
    ):
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
    ):
        pass


@pytest.fixture
def mock_model():
    """Create a mock Model instance for testing"""
    from openjiuwen.core.foundation.llm import Model

    client_config = ModelClientConfig(
        client_provider="MockTestLLM",
        api_key="test_key",
        api_base="https://test.api",
        verify_ssl=False,
    )
    request_config = ModelRequestConfig(model="test-model")

    return Model(
        model_client_config=client_config,
        model_config=request_config
    )


@pytest.fixture
def sample_plugin_data():
    """Sample plugin data for testing"""
    return [
        {
            "plugin_id": "plugin_001",
            "plugin_name": "Calculator Plugin",
            "plugin_desc": "A plugin for calculations",
            "plugin_version": "1.0.0",
            "tools": [
                {
                    "tool_id": "tool_add",
                    "tool_name": "Add",
                    "desc": "Add two numbers",
                    "code": "def add(a, b): return a + b",
                    "language": "python",
                    "input_parameters": [
                        {"name": "a", "desc": "First number", "type": 2},
                        {"name": "b", "desc": "Second number", "type": 2}
                    ],
                    "output_parameters": [
                        {"name": "result", "desc": "Sum", "type": 2}
                    ]
                }
            ]
        }
    ]


@pytest.fixture
def sample_workflow_data():
    """Sample workflow data for testing"""
    return {
        "workflow_id": "wf_001",
        "workflow_name": "Test Workflow",
        "workflow_version": "1.0.0",
        "workflow_desc": "A test workflow",
        "input_parameters": [],
        "output_parameters": []
    }
