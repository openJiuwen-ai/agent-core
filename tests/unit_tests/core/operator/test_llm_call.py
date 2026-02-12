# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.llm_call module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.operator.llm_call import LLMCallOperator


class TestLLMCallOperator:
    """Tests for LLMCallOperator class."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM model."""
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=MagicMock(content="response"))
        llm.stream = MagicMock(return_value=AsyncMock())
        return llm

    @pytest.fixture
    def operator(self, mock_llm):
        """Create a LLMCallOperator instance."""
        return LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="You are a helpful assistant.",
            user_prompt="Answer: {{query}}",
            freeze_user_prompt=False,
        )

    @staticmethod
    def test_operator_id_default(operator):
        """Test default operator_id."""
        assert operator.operator_id == "llm_call"

    @staticmethod
    def test_operator_id_custom():
        """Test custom operator_id."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=MagicMock(),
            system_prompt="sys",
            user_prompt="{{query}}",
            llm_call_id="custom_id",
        )
        assert op.operator_id == "custom_id"

    @staticmethod
    def test_get_tunables_both_prompts(mock_llm):
        """Test get_tunables returns both prompts when not frozen."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=False,
        )
        tunables = op.get_tunables()
        assert "system_prompt" in tunables
        assert "user_prompt" in tunables
        assert tunables["system_prompt"].kind == "prompt"
        assert tunables["user_prompt"].kind == "prompt"

    @staticmethod
    def test_get_tunables_frozen_system_prompt(mock_llm):
        """Test get_tunables excludes frozen system prompt."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
            freeze_user_prompt=False,
        )
        tunables = op.get_tunables()
        assert "system_prompt" not in tunables
        assert "user_prompt" in tunables

    @staticmethod
    def test_get_tunables_frozen_user_prompt(mock_llm):
        """Test get_tunables excludes frozen user prompt."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=True,
        )
        tunables = op.get_tunables()
        assert "system_prompt" in tunables
        assert "user_prompt" not in tunables

    @staticmethod
    def test_get_tunables_both_frozen(mock_llm):
        """Test get_tunables returns empty dict when both frozen."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
            freeze_user_prompt=True,
        )
        tunables = op.get_tunables()
        assert tunables == {}

    @staticmethod
    def test_set_parameter_system_prompt(operator):
        """Test set_parameter for system_prompt."""
        operator.set_parameter("system_prompt", "New system prompt")
        assert operator.get_system_prompt().content == "New system prompt"

    @staticmethod
    def test_set_parameter_user_prompt(operator):
        """Test set_parameter for user_prompt."""
        operator.set_parameter("user_prompt", "New: {{query}}")
        assert operator.get_user_prompt().content == "New: {{query}}"

    @staticmethod
    def test_set_parameter_frozen_system_prompt(mock_llm):
        """Test set_parameter ignores frozen system prompt."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="original",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
        )
        original = op.get_system_prompt().content
        op.set_parameter("system_prompt", "New prompt")
        assert op.get_system_prompt().content == original

    @staticmethod
    def test_set_parameter_frozen_user_prompt(mock_llm):
        """Test set_parameter ignores frozen user prompt."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="original {{query}}",
            freeze_user_prompt=True,
        )
        original = op.get_user_prompt().content
        op.set_parameter("user_prompt", "New: {{query}}")
        assert op.get_user_prompt().content == original

    @staticmethod
    def test_get_state(operator):
        """Test get_state returns prompt contents."""
        state = operator.get_state()
        assert "system_prompt" in state
        assert "user_prompt" in state
        assert state["system_prompt"] == "You are a helpful assistant."
        assert state["user_prompt"] == "Answer: {{query}}"

    @staticmethod
    def test_load_state(operator):
        """Test load_state restores prompt contents."""
        operator.load_state(
            {
                "system_prompt": "Loaded system",
                "user_prompt": "Loaded: {{query}}",
            }
        )
        assert operator.get_system_prompt().content == "Loaded system"
        assert operator.get_user_prompt().content == "Loaded: {{query}}"

    @staticmethod
    def test_load_state_partial(operator):
        """Test load_state with partial state."""
        operator.load_state({"system_prompt": "Partial load"})
        # user_prompt should remain unchanged
        assert operator.get_user_prompt().content == "Answer: {{query}}"

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_basic(mock_llm, operator):
        """Test basic invoke functionality."""
        mock_response = MagicMock()
        mock_response.content = "Hello!"
        mock_llm.invoke = AsyncMock(return_value=mock_response)

        mock_session = MagicMock()
        mock_session.set_current_operator_id = MagicMock()

        result = await operator.invoke(
            inputs={"query": "test query"},
            session=mock_session,
        )

        mock_llm.invoke.assert_called_once()
        mock_session.set_current_operator_id.assert_any_call("llm_call")
        mock_session.set_current_operator_id.assert_any_call(None)
        assert result.content == "Hello!"

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_history(mock_llm, operator):
        """Test invoke with conversation history."""
        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm.invoke = AsyncMock(return_value=mock_response)

        history = [MagicMock()]
        await operator.invoke(
            inputs={"query": "new question"},
            session=MagicMock(),
            history=history,
        )

        call_kwargs = mock_llm.invoke.call_args.kwargs
        messages = call_kwargs["messages"]
        # Should contain history
        assert len(messages) >= 3  # system + history + user

    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_with_tools(mock_llm, operator):
        """Test invoke with tool definitions."""
        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm.invoke = AsyncMock(return_value=mock_response)

        tools = [{"name": "get_weather", "description": "Get weather"}]
        await operator.invoke(
            inputs={"query": "test"},
            session=MagicMock(),
            tools=tools,
        )

        call_kwargs = mock_llm.invoke.call_args.kwargs
        assert call_kwargs["tools"] == tools

    @staticmethod
    def test_get_freeze_system_prompt(mock_llm):
        """Test get_freeze_system_prompt getter."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
        )
        assert op.get_freeze_system_prompt() is True

    @staticmethod
    def test_get_freeze_user_prompt(mock_llm):
        """Test get_freeze_user_prompt getter."""
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=True,
        )
        assert op.get_freeze_user_prompt() is True

    @staticmethod
    def test_set_freeze_system_prompt(operator):
        """Test set_freeze_system_prompt setter."""
        operator.set_freeze_system_prompt(True)
        assert operator.get_freeze_system_prompt() is True

    @staticmethod
    def test_set_freeze_user_prompt(operator):
        """Test set_freeze_user_prompt setter."""
        operator.set_freeze_user_prompt(True)
        assert operator.get_freeze_user_prompt() is True

    @staticmethod
    def test_on_parameter_updated_callback(mock_llm):
        """Test on_parameter_updated callback is invoked."""
        callback = MagicMock()
        op = LLMCallOperator(
            model_name="gpt-4",
            llm=mock_llm,
            system_prompt="sys",
            user_prompt="{{query}}",
            on_parameter_updated=callback,
        )
        op.set_parameter("system_prompt", "New prompt")
        callback.assert_called_once_with("system_prompt", "New prompt")

    @staticmethod
    def test_update_system_prompt(operator):
        """Test update_system_prompt method."""
        operator.update_system_prompt("Updated system prompt")
        assert operator.get_system_prompt().content == "Updated system prompt"

    @staticmethod
    def test_update_user_prompt(operator):
        """Test update_user_prompt method."""
        operator.update_user_prompt("Updated: {{query}}")
        assert operator.get_user_prompt().content == "Updated: {{query}}"


class TestLLMCallOperatorStream:
    """Tests for streaming functionality."""

    @staticmethod
    @pytest.fixture
    def mock_llm_stream():
        """Create mock LLM with streaming support."""
        llm = MagicMock()

        async def mock_stream(*args, **kwargs):
            chunks = [MagicMock(content="Hel"), MagicMock(content="lo!")]
            for chunk in chunks:
                yield chunk

        llm.stream = mock_stream
        return llm

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_basic(mock_llm_stream):
        """Test basic streaming functionality."""
        op = LLMCallOperator(
            model_name="test",
            llm=mock_llm_stream,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=False,
        )
        mock_session = MagicMock()
        mock_session.set_current_operator_id = MagicMock()

        chunks = []
        async for chunk in op.stream(
            inputs={"query": "hi"},
            session=mock_session,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_context_cleanup(mock_llm_stream):
        """Test that operator context is cleaned up after streaming."""
        op = LLMCallOperator(
            model_name="test",
            llm=mock_llm_stream,
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=False,
        )
        mock_session = MagicMock()
        mock_session.set_current_operator_id = MagicMock()

        async for _ in op.stream(inputs={"query": "hi"}, session=mock_session):
            pass

        # Context should be cleared at the end
        mock_session.set_current_operator_id.assert_any_call(None)
