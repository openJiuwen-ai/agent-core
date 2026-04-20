# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for ReActAgentEvolve

ReActAgentEvolve extends ReActAgent with operator parameter synchronization.
These tests focus on the unique functionality added by ReActAgentEvolve:
- Operator initialization and management
- Parameter synchronization callbacks
- Parameter transfer between agents
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.operator import LLMCallOperator, ToolCallOperator
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig
from openjiuwen.core.single_agent.agents.react_agent_evolve import ReActAgentEvolve
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _create_mock_skill_util():
    """Create mock skill util"""
    mock_skill_util = MagicMock()
    mock_skill_util.has_skill.return_value = False
    mock_skill_util.get_skill_prompt.return_value = "You are a helpful assistant."
    return mock_skill_util


class TestReActAgentEvolveCreation:
    """Tests for ReActAgentEvolve creation and operator initialization"""

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_agent_creation_with_card(mock_skill_util_class):
        """Test agent creation with AgentCard initializes operators"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent_evolve", description="Test ReActAgentEvolve")

        agent = ReActAgentEvolve(card=card)

        assert agent.card.name == "test_agent_evolve"
        assert agent.card.description == "Test ReActAgentEvolve"
        # Operators should be initialized
        assert hasattr(agent, "_llm_op")
        assert hasattr(agent, "_tool_op")

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_operators_initialized_on_creation(mock_skill_util_class):
        """Test that operators are properly initialized on agent creation"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")

        agent = ReActAgentEvolve(card=card)

        # LLM operator should be initialized
        assert agent._llm_op is not None
        assert isinstance(agent._llm_op, LLMCallOperator)
        assert agent._llm_op.operator_id == "react_llm"

        # Tool operator should be initialized
        assert agent._tool_op is not None
        assert isinstance(agent._tool_op, ToolCallOperator)
        assert agent._tool_op.operator_id == "react_tool"


class TestReActAgentEvolveOperators:
    """Tests for ReActAgentEvolve Operator related functionality"""

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_get_operators_returns_operators(mock_skill_util_class):
        """Test get_operators returns both operators"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent_evolve", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        operators = agent.get_operators()

        # Should contain both operators
        assert "react_llm" in operators
        assert "react_tool" in operators
        assert isinstance(operators["react_llm"], LLMCallOperator)
        assert isinstance(operators["react_tool"], ToolCallOperator)

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_get_operators_returns_empty_when_operators_none(mock_skill_util_class):
        """Test get_operators handles None operators"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent_evolve", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        # Manually set operators to None
        agent._llm_op = None
        agent._tool_op = None

        operators = agent.get_operators()
        assert operators == {}


class TestReActAgentEvolveParameterSync:
    """Tests for parameter synchronization callbacks"""

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_llm_parameter_updated_syncs_to_config(mock_skill_util_class):
        """Test _on_llm_parameter_updated syncs to config.prompt_template"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        config = ReActAgentConfig().configure_prompt_template(
            [{"role": "system", "content": "Original prompt"}]
        )

        agent = ReActAgentEvolve(card=card)
        agent.configure(config)

        # Trigger callback with new system prompt
        new_prompt = [{"role": "system", "content": "Updated prompt"}]
        agent._on_llm_parameter_updated("system_prompt", new_prompt)

        # Config should be updated
        assert agent._config.prompt_template == new_prompt

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_llm_parameter_updated_with_string(mock_skill_util_class):
        """Test _on_llm_parameter_updated handles string prompt"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        # Trigger callback with string prompt
        agent._on_llm_parameter_updated("system_prompt", "String prompt")

        # Config should be converted to list format
        assert agent._config.prompt_template == [{"role": "system", "content": "String prompt"}]

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_llm_parameter_updated_ignores_other_params(mock_skill_util_class):
        """Test _on_llm_parameter_updated ignores non-system_prompt params"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        original_prompt = [{"role": "system", "content": "Original"}]
        agent = ReActAgentEvolve(card=card)
        agent._config.prompt_template = original_prompt

        # Trigger callback with user_prompt (should be ignored)
        agent._on_llm_parameter_updated("user_prompt", "Some value")

        # Config should not change
        assert agent._config.prompt_template == original_prompt

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_tool_parameter_updated_syncs_to_ability_manager(mock_skill_util_class):
        """Test _on_tool_parameter_updated syncs to ability_manager"""
        from openjiuwen.core.foundation.tool.base import ToolCard

        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        # Add a tool to ability_manager
        tool_card = ToolCard(name="tool1", description="Original desc")
        agent.ability_manager.add(tool_card)

        # Trigger callback with new description
        agent._on_tool_parameter_updated("tool_description", {"tool1": "Updated desc"})

        # ToolCard should be updated
        assert tool_card.description == "Updated desc"

    @staticmethod
    @patch("openjiuwen.core.single_agent.skills.SkillUtil")
    def test_tool_parameter_updated_ignores_invalid(mock_skill_util_class):
        """Test _on_tool_parameter_updated ignores invalid params"""
        mock_skill_util_class.return_value = _create_mock_skill_util()
        card = AgentCard(name="test_agent", description="Test agent")
        agent = ReActAgentEvolve(card=card)

        # Should not raise with wrong param name
        agent._on_tool_parameter_updated("wrong_param", {"tool1": "desc"})

        # Should not raise with non-dict value
        agent._on_tool_parameter_updated("tool_description", "not a dict")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
