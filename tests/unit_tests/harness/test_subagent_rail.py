# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SubagentRail."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.subagent_rail import SubagentRail
from openjiuwen.harness.schema.config import SubAgentConfig

_TASK_SYSTEM_PROMPT = "openjiuwen.harness.prompts.sections.task_tool.build_task_system_prompt"


def _minimal_subagent_spec() -> SubAgentConfig:
    """Return a minimal SubAgentConfig for init tests that only need a non-empty subagent list."""
    return SubAgentConfig(
        agent_card=AgentCard(name="stub_agent", description="Stub description"),
        system_prompt="Stub prompt",
    )


def _make_tool_mock() -> Mock:
    mock_tool = Mock()
    mock_tool.card = Mock()
    return mock_tool


class TestSubagentRail:
    """Test cases for SubagentRail class."""

    @staticmethod
    def test_priority_attribute():
        """Test that priority is correctly set."""
        rail = SubagentRail()
        assert rail.priority == 95

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_init_with_subagents(mock_create_task_tool, mock_runner):
        """Test init method when subagents are configured."""
        mock_tool = _make_tool_mock()
        mock_tool.card.id = "test_tool_id"
        mock_create_task_tool.return_value = [mock_tool]

        mock_resource_mgr = Mock()
        mock_runner.resource_mgr = mock_resource_mgr

        mock_agent = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.deep_config.subagents = [
            SubAgentConfig(
                agent_card=AgentCard(name="test_agent", description="Test agent"),
                system_prompt="Test prompt",
            )
        ]
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_create_task_tool.assert_called_once()
        mock_runner.resource_mgr.add_tool.assert_called_once_with([mock_tool])
        mock_agent.ability_manager.add.assert_called_once_with(mock_tool.card)
        assert rail.tools == [mock_tool]

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.logger")
    def test_init_without_subagents(mock_logger):
        """Test init method when no subagents are configured."""
        mock_agent = Mock()
        mock_agent.deep_config.subagents = []

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_logger.info.assert_called_once_with(
            "[SubagentRail] No subagents configured, skipping task tool registration"
        )
        assert rail.tools is None

    @staticmethod
    def test_uninit_with_tools():
        """Test uninit method when tools are registered."""
        mock_tool = Mock()
        mock_tool.card = Mock()
        mock_tool.card.name = "test_tool"
        mock_tool.card.id = "test_tool_id"

        mock_agent = Mock()
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.tools = [mock_tool]

        with patch("openjiuwen.harness.rails.subagent_rail.Runner") as mock_runner:
            mock_resource_mgr = Mock()
            mock_runner.resource_mgr = mock_resource_mgr

            rail.uninit(mock_agent)

            mock_agent.ability_manager.remove.assert_called_once_with("test_tool")
            mock_resource_mgr.remove_tool.assert_called_once_with("test_tool_id")

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.logger")
    def test_uninit_without_tools(mock_logger):
        """Test uninit method when no tools are registered."""
        mock_agent = Mock()

        rail = SubagentRail()
        rail.tools = None
        rail.uninit(mock_agent)

        mock_logger.info.assert_not_called()

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_build_available_agents_description_with_subagents(mock_create, mock_runner):
        """Exercise available-agents formatting via init(create_task_tool kwargs)."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        subagents = [
            SubAgentConfig(
                agent_card=AgentCard(
                    name="research_agent", description="Research specialist"
                ),
                system_prompt="Research prompt",
            ),
            SubAgentConfig(
                agent_card=AgentCard(name="code_agent", description="Code specialist"),
                system_prompt="Code prompt",
            ),
        ]

        mock_agent = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.deep_config.subagents = subagents
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "available_agents" in call_args.kwargs
        available_agents = call_args.kwargs["available_agents"]
        assert '"research_agent": Research specialist' in available_agents
        assert '"code_agent": Code specialist' in available_agents

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_build_available_agents_description_with_general_purpose(mock_create, mock_runner):
        """When general-purpose is explicit, it appears once in available_agents."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        subagents = [
            SubAgentConfig(
                agent_card=AgentCard(
                    name="general-purpose",
                    description="Custom general purpose agent",
                ),
                system_prompt="General prompt",
            )
        ]

        mock_agent = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.deep_config.subagents = subagents
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "available_agents" in call_args.kwargs
        available_agents = call_args.kwargs["available_agents"]
        assert "general-purpose" in available_agents
        assert available_agents.count("general-purpose") == 1

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_extract_agent_meta_with_subagentspec(mock_create, mock_runner):
        """SubAgentConfig card name/description appear in available_agents passed to create_task_tool."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        spec = SubAgentConfig(
            agent_card=AgentCard(name="test_agent", description="Test description"),
            system_prompt="Test prompt",
        )

        mock_agent = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.deep_config.subagents = [spec]
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "available_agents" in call_args.kwargs
        available_agents = call_args.kwargs["available_agents"]
        assert '"test_agent": Test description' in available_agents

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_extract_agent_meta_with_deepagent(mock_create, mock_runner):
        """DeepAgent-like mock with card contributes name/description to available_agents."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        sub = Mock()
        sub.card = Mock()
        sub.card.name = "agent_name"
        sub.card.description = "agent description"

        mock_parent_agent = Mock()
        mock_parent_agent.system_prompt_builder = Mock()
        mock_parent_agent.system_prompt_builder.language = "cn"
        mock_parent_agent.deep_config.subagents = [sub]
        mock_parent_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_parent_agent)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "available_agents" in call_args.kwargs
        available_agents = call_args.kwargs["available_agents"]
        assert '"agent_name": agent description' in available_agents

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    def test_extract_agent_meta_with_deepagent_fallback(mock_create, mock_runner):
        """DeepAgent-like mock without card uses fallback meta in available_agents."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        sub = Mock()
        sub.card = None

        mock_parent_agent = Mock()
        mock_parent_agent.system_prompt_builder = Mock()
        mock_parent_agent.system_prompt_builder.language = "cn"
        mock_parent_agent.deep_config.subagents = [sub]
        mock_parent_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.init(mock_parent_agent)

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "available_agents" in call_args.kwargs
        available_agents = call_args.kwargs["available_agents"]
        assert '"general-purpose": DeepAgent instance' in available_agents

    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.harness.rails.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent_rail.create_task_tool")
    async def test_before_model_call_with_builder(mock_create, mock_runner):
        """before_model_call is a no-op after task_tool moved into tools section."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        system_prompt_builder = Mock()
        system_prompt_builder.language = "cn"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(system_prompt_builder=system_prompt_builder)

        rail = SubagentRail()
        rail.init(mock_agent)

        ctx = Mock()

        await rail.before_model_call(ctx)

        system_prompt_builder.remove_section.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_before_model_call_no_tools():
        """before_model_call returns immediately when tools is None."""
        ctx = Mock()

        rail = SubagentRail()
        rail.tools = None

        await rail.before_model_call(ctx)

    @staticmethod
    def test_all_method():
        """Test that __all__ exports the correct class."""
        from openjiuwen.harness.rails.subagent_rail import __all__

        assert __all__ == ["SubagentRail"]
