# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for ReActAgentEvolve (using Mock LLM)

Tests ReActAgentEvolve using AgentCard + ReActAgentConfig.

## Test Scenarios

1. AgentCard + ReActAgentConfig creation and configuration
2. configure() chained calls
3. get_operators() method returns evolvable operators
4. invoke method tool call scenarios
5. stream method tests
6. Configuration update tests

## Mock Strategy

- Use shared MockLLMModel class
- Inject mock instance via patch ReActAgentEvolve._get_llm
- Mock Session and ContextEngine for test isolation
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
from openjiuwen.core.single_agent.agents.react_agent_evolve import ReActAgentEvolve
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_text_response,
    create_tool_call_response,
)


def _create_mock_skill_util():
    """Create mock skill util"""
    mock_skill_util = MagicMock()
    mock_skill_util.has_skill.return_value = False
    mock_skill_util.get_skill_prompt.return_value = "You are a helpful assistant."
    return mock_skill_util


class TestReActAgentEvolveConfig:
    """Tests for ReActAgentEvolve configuration class"""

    @staticmethod
    def test_config_default_values():
        """Test configuration default values"""
        config = ReActAgentConfig()
        assert config.mem_scope_id == ""
        assert config.model_name == ""
        assert config.model_provider == "openai"
        assert config.api_key == ""
        assert config.api_base == ""
        assert config.prompt_template_name == ""
        assert config.prompt_template == []
        assert config.max_iterations == 5

    @staticmethod
    def test_config_chained_configuration():
        """Test chained configuration calls"""
        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_model_provider(provider="openai", api_key="test_key", api_base="https://api.test.com")
            .configure_prompt_template([{"role": "system", "content": "You are a helpful assistant"}])
            .configure_context_engine(max_context_message_num=100, default_window_round_num=20, enable_reload=True)
            .configure_max_iterations(10)
        )

        assert config.model_name == "gpt-4"
        assert config.model_provider == "openai"
        assert config.api_key == "test_key"
        assert config.api_base == "https://api.test.com"
        assert len(config.prompt_template) == 1
        assert config.max_iterations == 10

    @staticmethod
    def test_configure_mem_scope():
        """Test memory scope configuration"""
        config = ReActAgentConfig().configure_mem_scope("test_scope")
        assert config.mem_scope_id == "test_scope"


class TestReActAgentEvolveCreation:
    """Tests for ReActAgentEvolve creation"""

    @staticmethod
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_agent_creation_with_card(mock_skill_util_class, _mock_init_memory):
        """Test agent creation with AgentCard"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent_evolve", description="Test ReActAgentEvolve")

        agent = ReActAgentEvolve(card=card)

        assert agent.card.name == "test_agent_evolve"
        assert agent.card.description == "Test ReActAgentEvolve"
        assert len(agent.card.id) > 0

    @staticmethod
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_agent_configure_method(mock_skill_util_class, _):
        """Test agent configure method"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")

        config = ReActAgentConfig().configure_model("gpt-4").configure_max_iterations(10)

        agent = ReActAgentEvolve(card=card)
        result = agent.configure(config)

        # Verify configure returns self (supports chaining)
        assert result is agent


class TestReActAgentEvolveOperators:
    """Tests for ReActAgentEvolve Operator related functionality"""

    @staticmethod
    def _create_add_tool_card():
        """Create addition tool Card"""
        return ToolCard(
            name="add",
            description="Addition operation",
            input_params={
                "type": "object",
                "properties": {
                    "a": {"description": "First number", "type": "number"},
                    "b": {"description": "Second number", "type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    @staticmethod
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_get_operators_returns_tool_operator(mock_skill_util_class, _):
        """Test get_operators returns tool Operator"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent_evolve", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        operators = agent.get_operators()
        # Should contain react_tool operator
        assert "react_tool" in operators


def _create_add_tool_card():
    """Create addition tool Card"""
    return ToolCard(
        name="add",
        description="Addition operation",
        input_params={
            "type": "object",
            "properties": {
                "a": {"description": "First number", "type": "number"},
                "b": {"description": "Second number", "type": "number"},
            },
            "required": ["a", "b"],
        },
    )


def _create_mock_session():
    """Create mock session"""
    return MagicMock()


def _create_mock_context_and_engine():
    """Create mock context and context_engine"""
    mock_context = MagicMock()
    mock_context.add_messages = AsyncMock()
    mock_context.get_context_window = AsyncMock(
        return_value=MagicMock(get_messages=MagicMock(return_value=[]), get_tools=MagicMock(return_value=None))
    )

    mock_context_engine = MagicMock()
    mock_context_engine.save_contexts = AsyncMock()
    mock_context_engine.create_context = AsyncMock(return_value=mock_context)

    return mock_context, mock_context_engine


def _create_test_config():
    """Create test configuration"""
    return (
        ReActAgentConfig()
        .configure_model("gpt-4")
        .configure_model_provider(provider="openai", api_key="test_key", api_base="https://api.test.com")
        .configure_prompt_template([{"role": "system", "content": "You are a math assistant"}])
        .configure_max_iterations(5)
    )


class TestReActAgentEvolveInvoke:
    """Tests for ReActAgentEvolve invoke method"""

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_pure_conversation(mock_skill_util_class, _):
        """Test pure conversation (no tool call)"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_text_response("Hello! I am a math assistant."),
            ]
        )

        mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_mock_session()

        card = AgentCard(name="test_agent_evolve", description="Math assistant")
        agent = ReActAgentEvolve(card=card)
        agent.configure(_create_test_config())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"conversation_id": "test_session", "query": "Hello"}, session=mock_session)

        assert isinstance(result, dict)
        assert result["result_type"] == "answer"
        assert "Hello" in result["output"]
        assert mock_llm.call_count == 1

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_with_tool_call(mock_skill_util_class, _):
        """Test tool call scenario - LLM decides to call a tool"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_tool_call_response("add", '{"a": 1, "b": 2}'),
                create_text_response("1+2=3"),
            ]
        )

        mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_mock_session()

        card = AgentCard(name="test_agent_evolve", description="Math assistant")
        agent = ReActAgentEvolve(card=card)
        agent.configure(_create_test_config())
        agent.ability_manager.add(_create_add_tool_card())
        agent.context_engine = mock_context_engine

        # Mock tool operator via get_operators()
        tool_op = agent.get_operators()["react_tool"]
        tool_op.invoke = AsyncMock(return_value=[(3, MagicMock())])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke(
                {"conversation_id": "test_session", "query": "Calculate 1+2"}, session=mock_session
            )

        assert isinstance(result, dict)
        assert result["result_type"] == "answer"
        assert "3" in result["output"]
        assert mock_llm.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_multi_turn_tool_calls(mock_skill_util_class, _):
        """Test multi-turn tool call scenario"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_tool_call_response("add", '{"a": 1, "b": 2}'),
                create_text_response("Result: 3"),
            ]
        )

        mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_mock_session()

        card = AgentCard(name="test_agent_evolve", description="Math assistant")
        agent = ReActAgentEvolve(card=card)
        agent.configure(_create_test_config())
        agent.ability_manager.add(_create_add_tool_card())
        agent.context_engine = mock_context_engine

        # Mock tool operator via get_operators()
        tool_op = agent.get_operators()["react_tool"]
        tool_op.invoke = AsyncMock(return_value=[(3, MagicMock())])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "Calculate 1+2"}, session=mock_session)

        assert isinstance(result, dict)
        assert result["result_type"] == "answer"
        assert "3" in result["output"]
        assert mock_llm.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_max_iterations_reached(mock_skill_util_class, _):
        """Test max iterations reached"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        # Always return tool call, never return final answer
        mock_llm.set_responses(
            [
                create_tool_call_response("add", '{"a": 1, "b": 2}'),
                create_tool_call_response("add", '{"a": 3, "b": 4}'),
                create_tool_call_response("add", '{"a": 5, "b": 6}'),
            ]
        )

        mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_mock_session()

        # Set max_iterations to 2
        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_model_provider(provider="openai", api_key="test_key", api_base="https://api.test.com")
            .configure_max_iterations(2)
        )

        card = AgentCard(name="test_agent_evolve", description="Math assistant")
        agent = ReActAgentEvolve(card=card)
        agent.configure(config)
        agent.ability_manager.add(_create_add_tool_card())
        agent.context_engine = mock_context_engine

        # Mock tool operator via get_operators()
        tool_op = agent.get_operators()["react_tool"]
        tool_op.invoke = AsyncMock(return_value=[(3, MagicMock())])

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke({"query": "Keep calculating"}, session=mock_session)

        assert isinstance(result, dict)
        assert result["result_type"] == "error"
        assert "Max iterations" in result["output"]

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_with_string_input(mock_skill_util_class, _):
        """Test string input format"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_text_response("This is a response to string input"),
            ]
        )

        mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_mock_session()

        card = AgentCard(name="test_agent_evolve", description="Math assistant")
        agent = ReActAgentEvolve(card=card)
        agent.configure(_create_test_config())
        agent.context_engine = mock_context_engine

        with patch.object(agent, "_get_llm", return_value=mock_llm):
            result = await agent.invoke("This is a string query", session=mock_session)

        assert isinstance(result, dict)
        assert result["result_type"] == "answer"

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_missing_query_raises_error(mock_skill_util_class, _):
        """Test missing query field raises error"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        with pytest.raises(ValueError) as exc_info:
            await agent.invoke({"conversation_id": "test"})

        assert "query" in str(exc_info.value)

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_invoke_invalid_input_raises_error(mock_skill_util_class, _):
        """Test invalid input type raises error"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        with pytest.raises(ValueError) as exc_info:
            await agent.invoke(12345)

        assert "must be dict" in str(exc_info.value)


def _create_stream_session():
    """Create mock session with stream behavior"""
    import asyncio

    mock_session = AsyncMock()
    data_queue = asyncio.Queue()

    async def mock_write_stream(data):
        await data_queue.put(data)

    async def mock_post_run():
        await data_queue.put(None)

    async def mock_stream_iterator():
        while True:
            data = await data_queue.get()
            if data is None:
                break
            yield data

    mock_session.write_stream = mock_write_stream
    mock_session.post_run = mock_post_run
    mock_session.stream_iterator = mock_stream_iterator
    return mock_session


class TestReActAgentEvolveStream:
    """Tests for ReActAgentEvolve stream method"""

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    async def test_stream_yields_final_result(mock_skill_util_class, _):
        """Test stream returns final result"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        mock_llm = MockLLMModel()
        mock_llm.set_responses(
            [
                create_text_response("This is a streaming response"),
            ]
        )

        _mock_context, mock_context_engine = _create_mock_context_and_engine()
        mock_session = _create_stream_session()

        card = AgentCard(name="test_agent_evolve", description="Stream test agent")
        config = (
            ReActAgentConfig()
            .configure_model("gpt-4")
            .configure_model_provider(provider="openai", api_key="test_key", api_base="https://api.test.com")
            .configure_max_iterations(5)
        )
        agent = ReActAgentEvolve(card=card)
        agent.configure(config)
        agent.context_engine = mock_context_engine

        results = []
        with patch.object(agent, "_get_llm", return_value=mock_llm):
            async for result in agent.stream({"query": "Stream test"}, session=mock_session):
                results.append(result)

        assert len(results) > 0


class TestReActAgentEvolveConfigUpdate:
    """Tests for ReActAgentEvolve configuration update"""

    @staticmethod
    @patch.object(ReActAgentEvolve, "_init_memory_scope", return_value=None)
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_configure_updates_context_engine_on_limit_change(mock_skill_util_class, _):
        """Test context_engine is updated when window limit changes"""
        mock_skill_util_class.return_value = _create_mock_skill_util()

        card = AgentCard(name="test_agent_evolve", description="Config update test")
        agent = ReActAgentEvolve(card=card)
        old_context_engine = agent.context_engine

        new_config = ReActAgentConfig().configure_context_engine(default_window_round_num=20)
        agent.configure(new_config)

        assert agent.context_engine is not old_context_engine


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
