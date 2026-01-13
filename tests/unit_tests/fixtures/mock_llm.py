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
    mock_llm = MockLLMModel(api_key="mock", api_base="mock")
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
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional
from unittest.mock import patch, MagicMock

from openjiuwen.core.foundation.llm import (
    AIMessage,
    BaseModelClient,
    UsageMetadata,
)
from openjiuwen.core.foundation.tool import ToolCall


class MockLLMModel(BaseModelClient):
    """Mock LLM model that returns predefined responses.

    This class inherits from BaseModelClient and implements all required
    methods to simulate LLM behavior in tests.

    Attributes:
        call_count: Number of times the model has been called.
        responses: List of predefined AIMessage responses.
        call_history: List of messages received in each call.
    """

    def __init__(self, api_key: str = "mock", api_base: str = "mock", **kwargs):
        """Initialize MockLLMModel.

        Args:
            api_key: Mock API key (not used).
            api_base: Mock API base URL (not used).
            **kwargs: Additional arguments (ignored).
        """
        super().__init__(api_key=api_key, api_base=api_base)
        self.call_count = 0
        self.responses: List[AIMessage] = []
        self.call_history: List[List[Dict]] = []

    def set_responses(self, responses: List[AIMessage]) -> None:
        """Set predefined responses for the mock model.

        Args:
            responses: List of AIMessage objects to return in order.
        """
        self.responses = responses
        self.call_count = 0
        self.call_history = []

    def _get_next_response(self) -> AIMessage:
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
            return AIMessage(content="Default mock response")

    def _invoke(
        self,
        model_name: str,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = 0.1,
        top_p: Optional[float] = 0.1,
        **kwargs: Any
    ) -> AIMessage:
        """Synchronous invocation.

        Args:
            model_name: Model name (ignored).
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            **kwargs: Additional arguments (ignored).

        Returns:
            The next predefined AIMessage response.
        """
        self.call_history.append(messages)
        return self._get_next_response()

    async def _ainvoke(
        self,
        model_name: str,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = 0.1,
        top_p: Optional[float] = 0.1,
        **kwargs: Any
    ) -> AIMessage:
        """Asynchronous invocation.

        Args:
            model_name: Model name (ignored).
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            **kwargs: Additional arguments (ignored).

        Returns:
            The next predefined AIMessage response.
        """
        self.call_history.append(messages)
        return self._get_next_response()

    def _stream(
        self,
        model_name: str,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = 0.1,
        top_p: Optional[float] = 0.1,
        **kwargs: Any
    ) -> Iterator[Any]:
        """Synchronous streaming.

        Args:
            model_name: Model name (ignored).
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            **kwargs: Additional arguments (ignored).

        Yields:
            The next predefined AIMessage response.
        """
        self.call_history.append(messages)
        result = self._get_next_response()
        yield result

    async def _astream(
        self,
        model_name: str,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = 0.1,
        top_p: Optional[float] = 0.1,
        **kwargs: Any
    ) -> AsyncIterator[Any]:
        """Asynchronous streaming.

        Args:
            model_name: Model name (ignored).
            messages: Input messages.
            tools: Available tools (ignored).
            temperature: Temperature parameter (ignored).
            top_p: Top-p parameter (ignored).
            **kwargs: Additional arguments (ignored).

        Yields:
            The next predefined AIMessage response.
        """
        self.call_history.append(messages)
        result = self._get_next_response()
        yield result


def create_text_response(
    content: str,
    model_name: str = "mock-model",
    finish_reason: str = "stop"
) -> AIMessage:
    """Create a text response AIMessage.

    Args:
        content: The text content of the response.
        model_name: Model name for metadata.
        finish_reason: Finish reason for metadata.

    Returns:
        AIMessage with the specified text content.
    """
    return AIMessage(
        content=content,
        usage_metadata=UsageMetadata(
            model_name=model_name,
            finish_reason=finish_reason
        )
    )


def create_tool_call_response(
    tool_name: str,
    arguments: str,
    tool_call_id: Optional[str] = None,
    model_name: str = "mock-model"
) -> AIMessage:
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

    return AIMessage(
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
) -> AIMessage:
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
    return AIMessage(
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
            "openjiuwen.core.foundation.llm.model_utils.model_factory."
            "ModelFactory.get_model",
            return_value=mock_llm
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

    with patches[0]:
        if len(patches) > 1:
            with patches[1]:
                yield mock_llm
        else:
            yield mock_llm
