# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for ChatAgent.invoke auto-session management.

Verifies that ChatAgent.invoke() works correctly when called WITHOUT
an explicit session parameter. This is a regression test for the bug
where self._session was not initialized, causing an AttributeError.

Related issue:
    - Bug: ChatAgent.invoke accesses self._session without initialization
    - Fix: Initialize self._session = None in __init__ and auto-create
      session when none is provided
"""
import os
import unittest
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ModelClientConfig,
    ModelRequestConfig,
    UsageMetadata,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.dev_tools.tune import (
    ChatAgent,
    create_chat_agent,
    create_chat_agent_config,
)
from openjiuwen.core.single_agent.legacy import LLMCallConfig

os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

_MODEL_CLIENT_CONFIG = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-fake",
    api_base="https://mock.api/v1",
    verify_ssl=False,
)
_MODEL_REQUEST_CONFIG = ModelRequestConfig(model="gpt-3.5-turbo")


def _create_mock_assistant_message(content: str = "mock response"):
    """Create a mock AssistantMessage for the LLM response."""
    return AssistantMessage(
        content=content,
        usage_metadata=UsageMetadata(model_name="mock-model"),
    )


class TestChatAgentInvokeAutoSession(unittest.IsolatedAsyncioTestCase):
    """ChatAgent.invoke() auto-session management tests."""

    async def asyncSetUp(self):
        """Start Runner before each test."""
        await Runner.start()

    async def asyncTearDown(self):
        """Stop Runner after each test."""
        await Runner.stop()

    async def _create_chat_agent(self) -> ChatAgent:
        """Create a ChatAgent with mock model config for testing.

        Returns:
            ChatAgent instance ready for invoke
        """
        system_prompt = [{"role": "system", "content": "You are a test assistant."}]
        config = create_chat_agent_config(
            agent_id="test_chat_agent",
            agent_version="1.0.0",
            description="Test chat agent for unit testing",
            model=LLMCallConfig(
                model=_MODEL_REQUEST_CONFIG,
                model_client=_MODEL_CLIENT_CONFIG,
                system_prompt=system_prompt,
            ),
        )
        agent = create_chat_agent(config)
        return agent

    @pytest.mark.asyncio
    async def test_invoke_without_session_does_not_raise_attribute_error(self):
        """Call agent.invoke() without session.

        This should NOT raise AttributeError for self._session.
        After the fix, _session is initialized in __init__ and
        a new session is auto-created when none is provided.
        """
        mock_response = _create_mock_assistant_message(
            "Hello, I am a test assistant."
        )

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            return_value=mock_response,
        ):
            agent = await self._create_chat_agent()
            result = await agent.invoke({"query": "hello"})
            self.assertIsNotNone(result)
            self.assertIn("output", result)

    @pytest.mark.asyncio
    async def test_invoke_with_explicit_session(self):
        """Call agent.invoke() with an explicit session parameter.

        This should work correctly and use the provided session
        instead of auto-creating one.
        """
        mock_response = _create_mock_assistant_message(
            "Response with session."
        )

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            return_value=mock_response,
        ):
            agent = await self._create_chat_agent()
            session = create_agent_session(
                session_id="test_session_001",
                card=AgentCard(id="test_chat_agent"),
            )
            result = await agent.invoke(
                {"query": "hello"},
                session=session,
            )
            self.assertIsNotNone(result)
            self.assertIn("output", result)

    @pytest.mark.asyncio
    async def test_invoke_with_conversation_id(self):
        """Call agent.invoke() with conversation_id in inputs.

        The conversation_id should be extracted and used for
        the auto-created session.
        """
        mock_response = _create_mock_assistant_message(
            "Response with conv_id."
        )

        with patch(
            "openjiuwen.core.foundation.llm.model.Model.invoke",
            return_value=mock_response,
        ):
            agent = await self._create_chat_agent()
            result = await agent.invoke({
                "query": "hello",
                "conversation_id": "my_custom_conv",
            })
            self.assertIsNotNone(result)
            self.assertIn("output", result)
