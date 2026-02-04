# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Mock LLM Model for unit testing.

This module provides a mock LLM implementation that returns predefined
responses, enabling fast and reliable unit tests without real API calls.

Usage:
    from tests.unit_tests.fixtures.mock_llm import (
        MockLLMModel,
        create_text_response,
        create_tool_call_response,
        mock_llm_context,
    )

    # Create mock LLM with predefined responses
    mock_llm = MockLLMModel()
    mock_llm.set_responses([
        create_text_response("Hello!"),
        create_tool_call_response("add", '{"a": 1, "b": 2}'),
    ])

    # Use context manager for patching
    with mock_llm_context() as mock_llm:
        mock_llm.set_responses([...])
        # Your test code here
"""
from contextlib import contextmanager
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union
from unittest.mock import patch, MagicMock

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseModelClient,
    ModelClientConfig,
    ModelRequestConfig,
    UsageMetadata,
)
from openjiuwen.core.foundation.llm.schema.message import BaseMessage, UserMessage
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.tool import ToolInfo


class MockLLMModel(BaseModelClient):
    """Mock LLM model that returns predefined responses.

    This class inherits from BaseModelClient and implements all required
    methods to simulate LLM behavior in tests.

    Attributes:
        call_count: Number of times the model has been called.
        responses: List of predefined AIMessage responses.
        call_history: List of messages received in each call.
    """

    def __init__(self, **kwargs):
        """Initialize MockLLMModel.

        Args:
            **kwargs: Optional overrides for configs.
                - model_config: ModelRequestConfig
                - model_client_config: ModelClientConfig
        """
        # BaseModelClient.__init__ performs config validation, so we provide minimal
        # valid defaults suitable for unit tests.
        model_config = kwargs.pop(
            "model_config",
            ModelRequestConfig(model_name="mock-model"),
        )
        model_client_config = kwargs.pop(
            "model_client_config",
            ModelClientConfig(
                client_provider="OpenAI",
                api_key="mock-api-key",
                api_base="http://mock-api-base",
                verify_ssl=False,
            ),
        )
        super().__init__(model_config=model_config, model_client_config=model_client_config)
        self.call_count = 0
        self.responses: List[AssistantMessage] = []
        self.call_history: List[List[Dict]] = []

    def set_responses(self, responses: List[AssistantMessage]) -> None:
        """Set predefined responses for the mock model.

        Args:
            responses: List of AssistantMessage objects to return in order.
        """
        self.responses = responses
        self.call_count = 0
        self.call_history = []

    def _get_next_response(self) -> AssistantMessage:
        """Get the next response from the predefined list.

        Returns:
            The next AIMessage in the sequence, or a default response
            if all predefined responses have been used.
        """
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        else:
            return AssistantMessage(content="Default mock response")

    async def invoke(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs
    ) -> AssistantMessage:
        """Asynchronously invoke LLM.

        Args:
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            model: Model name (ignored).
            max_tokens: Max tokens (ignored).
            stop: Stop sequence (ignored).
            output_parser: Output parser (ignored).
            timeout: Timeout (ignored).
            **kwargs: Additional arguments (ignored).

        Returns:
            The next predefined AssistantMessage response.
        """
        self.call_history.append(messages if isinstance(messages, list) else [messages])
        return self._get_next_response()

    async def stream(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        max_tokens: Optional[int] = None,
        stop: Union[Optional[str], None] = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronously stream invoke LLM.

        Args:
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            model: Model name (ignored).
            max_tokens: Max tokens (ignored).
            stop: Stop sequence (ignored).
            output_parser: Output parser (ignored).
            timeout: Timeout (ignored).
            **kwargs: Additional arguments (ignored).

        Yields:
            AssistantMessageChunk: Streaming response chunk.
        """
        self.call_history.append(messages if isinstance(messages, list) else [messages])
        result = self._get_next_response()
        # Convert AssistantMessage to AssistantMessageChunk for streaming
        chunk = AssistantMessageChunk(
            content=result.content,
            tool_calls=result.tool_calls,
            usage_metadata=result.usage_metadata
        )
        yield chunk

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
        """Mock image generation; returns dummy ImageGenerationResponse.

        For unit tests that don't care about image content, this method simply
        returns placeholder data to satisfy the BaseModelClient interface.
        """
        return ImageGenerationResponse(
            model=model or self.model_config.model_name,
            images=[f"mock://image/{i}" for i in range(n or 1)],
            images_base64=[],
        )

    async def generate_speech(
        self,
        messages: List[UserMessage],
        *,
        model: Optional[str] = None,
        voice: Optional[str] = "Cherry",
        language_type: Optional[str] = "Auto",
        **kwargs
    ) -> AudioGenerationResponse:
        """Mock speech generation; returns dummy AudioGenerationResponse."""
        return AudioGenerationResponse(
            model=model or self.model_config.model_name,
            audio_url="mock://audio/0",
            audio_data=b"",
            duration=0.0,
        )

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
        """Mock video generation; returns dummy VideoGenerationResponse."""
        return VideoGenerationResponse(
            model=model or self.model_config.model_name,
            video_url="mock://video/0",
            video_data=b"",
            duration=float(duration or 0),
            resolution=resolution,
        )


def create_text_response(
    content: str,
    model_name: str = "mock-model",
    finish_reason: str = "stop"
) -> AssistantMessage:
    """Create a text response AIMessage.

    Args:
        content: The text content of the response.
        model_name: Model name for metadata.
        finish_reason: Finish reason for metadata.

    Returns:
        AIMessage with the specified text content.
    """
    return AssistantMessage(
        content=content,
        usage_metadata=UsageMetadata(
            model_name=model_name,
        )
    )


def create_tool_call_response(
    tool_name: str,
    arguments: str,
    tool_call_id: Optional[str] = None,
    model_name: str = "mock-model"
) -> AssistantMessage:
    """Create a tool call response AIMessage.

    Args:
        tool_name: Name of the tool to call.
        arguments: JSON string of tool arguments.
        tool_call_id: Optional tool call ID (auto-generated if not provided).
        model_name: Model name for metadata.

    Returns:
        AIMessage with a tool call.
    """
    if tool_call_id is None:
        tool_call_id = f"mock_call_{tool_name}"

    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id=tool_call_id,
                type="function",
                name=tool_name,
                arguments=arguments
            )
        ],
        usage_metadata=UsageMetadata(
            model_name=model_name,
            finish_reason="tool_calls"
        )
    )


def create_json_response(
    data: Dict[str, Any],
    model_name: str = "mock-model"
) -> AssistantMessage:
    """Create a JSON response AIMessage.

    This is useful for mocking responses from components like Questioner
    that expect JSON-formatted field extraction results.

    Args:
        data: Dictionary to serialize as JSON content.
        model_name: Model name for metadata.

    Returns:
        AIMessage with JSON-formatted content.
    """
    import json
    return AssistantMessage(
        content=json.dumps(data, ensure_ascii=False),
        usage_metadata=UsageMetadata(
            model_name=model_name,
            finish_reason="stop"
        )
    )


@contextmanager
def mock_llm_context(
    mock_memory: bool = True
) -> Iterator[MockLLMModel]:
    """Context manager for mocking LLM calls.

    This context manager patches ModelFactory.get_model to return a
    MockLLMModel instance, and optionally patches LongTermMemory.

    Args:
        mock_memory: Whether to also mock LongTermMemory.set_scope_config.

    Yields:
        MockLLMModel instance that will be used for all LLM calls.

    Example:
        with mock_llm_context() as mock_llm:
            mock_llm.set_responses([
                create_text_response("Hello!"),
            ])
            # Your test code here
    """
    mock_llm = MockLLMModel()

    patches = [
        patch(
            'openjiuwen.core.foundation.llm.model.Model.invoke',
            side_effect=mock_llm.invoke
        ),
        patch(
            'openjiuwen.core.foundation.llm.model.Model.stream',
            side_effect=mock_llm.stream
        )
    ]

    if mock_memory:
        patches.append(
            patch(
                "openjiuwen.core.memory.long_term_memory."
                "LongTermMemory.set_scope_config",
                return_value=MagicMock()
            )
        )

    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield mock_llm
