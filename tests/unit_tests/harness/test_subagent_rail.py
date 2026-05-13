# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SubagentRail."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.rails.subagent.session_rail import SessionRail
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.logger")
    def test_init_without_subagents(mock_logger):
        """Test init method when no subagents are configured."""
        mock_agent = Mock()
        mock_agent.deep_config.subagents = []

        rail = SubagentRail()
        rail.init(mock_agent)

        mock_logger.info.assert_called_once_with(
            "[SubagentRail] No subagents configured, skipping"
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

        with patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner") as mock_runner:
            mock_resource_mgr = Mock()
            mock_runner.resource_mgr = mock_resource_mgr

            rail.uninit(mock_agent)

            mock_agent.ability_manager.remove.assert_called_once_with("test_tool")
            mock_resource_mgr.remove_tool.assert_called_once_with("test_tool_id")

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.logger")
    def test_uninit_without_tools(mock_logger):
        """Test uninit method when no tools are registered."""
        mock_agent = Mock()

        rail = SubagentRail()
        rail.tools = None
        rail.uninit(mock_agent)

        mock_logger.info.assert_called_once_with("[SubagentRail] Unregistered sync task tools")

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
        assert '"agent_name": agent description" in available_agents'

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.create_task_tool")
    async def test_before_model_call_sync_is_noop(mock_create, mock_runner):
        """before_model_call is a no-op in sync mode (enable_async_subagent=False)."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        system_prompt_builder = Mock()
        system_prompt_builder.language = "cn"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(system_prompt_builder=system_prompt_builder)

        rail = SubagentRail()  # default: enable_async_subagent=False
        rail.init(mock_agent)

        ctx = Mock()
        await rail.before_model_call(ctx)

        system_prompt_builder.add_section.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_before_model_call_no_tools():
        """before_model_call returns immediately when tools is None."""
        ctx = Mock()

        rail = SubagentRail()
        rail.tools = None

        await rail.before_model_call(ctx)


class TestSubagentRailAsyncMode:
    """Test cases for SubagentRail with enable_async_subagent=True."""

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.build_session_tools")
    def test_async_init_registers_session_tools(mock_build_session_tools, mock_runner):
        """SubagentRail(enable_async_subagent=True) registers session tools."""
        mock_tool = _make_tool_mock()
        mock_tool.card.id = "session_tool_id"
        mock_build_session_tools.return_value = [mock_tool]

        mock_resource_mgr = Mock()
        mock_runner.resource_mgr = mock_resource_mgr

        mock_agent = Mock()
        mock_agent.system_prompt_builder = Mock()
        mock_agent.system_prompt_builder.language = "cn"
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()

        rail = SubagentRail(enable_async_subagent=True)
        rail.init(mock_agent)

        mock_build_session_tools.assert_called_once()
        mock_runner.resource_mgr.add_tool.assert_called_once_with([mock_tool])
        mock_agent.ability_manager.add.assert_called_once_with(mock_tool.card)
        mock_agent.set_session_toolkit.assert_called_once()
        assert rail.tools == [mock_tool]
        assert rail._toolkit is not None

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.build_session_tools")
    def test_async_init_without_subagents_skips(mock_build_session_tools, mock_runner):
        """No subagents configured: async branch also skips registration."""
        mock_agent = Mock()
        mock_agent.deep_config.subagents = []

        rail = SubagentRail(enable_async_subagent=True)
        rail.init(mock_agent)

        mock_build_session_tools.assert_not_called()
        assert rail.tools is None

    @staticmethod
    def test_async_uninit_clears_toolkit():
        """uninit in async mode clears session toolkit and tools."""
        mock_tool = _make_tool_mock()
        mock_tool.card = Mock()
        mock_tool.card.name = "session_tool"
        mock_tool.card.id = "session_tool_id"

        mock_agent = Mock()
        mock_agent.ability_manager = Mock()

        rail = SubagentRail(enable_async_subagent=True)
        rail.tools = [mock_tool]
        rail._toolkit = Mock()

        with patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner") as mock_runner:
            mock_resource_mgr = Mock()
            mock_runner.resource_mgr = mock_resource_mgr

            rail.uninit(mock_agent)

            mock_agent.ability_manager.remove.assert_called_once_with("session_tool")
            mock_resource_mgr.remove_tool.assert_called_once_with("session_tool_id")
            mock_agent.set_session_toolkit.assert_called_once_with(None)

    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.build_session_tools")
    async def test_async_before_model_call_injects_section(mock_build, mock_runner):
        """before_model_call in async mode calls add_section on the builder."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_build.return_value = [mock_tool]

        system_prompt_builder = Mock()
        system_prompt_builder.language = "cn"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(system_prompt_builder=system_prompt_builder)

        rail = SubagentRail(enable_async_subagent=True)
        rail.init(mock_agent)
        rail.system_prompt_builder = system_prompt_builder

        with patch(
            "openjiuwen.harness.prompts.sections.session_tools.build_session_tools_section"
        ) as mock_build_section:
            mock_build_section.return_value = "mock section"

            ctx = Mock()
            await rail.before_model_call(ctx)

            system_prompt_builder.add_section.assert_called_once_with("mock section")

    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.Runner")
    @patch("openjiuwen.harness.rails.subagent.subagent_rail.build_session_tools")
    async def test_async_before_model_call_removes_section_when_none(mock_build, mock_runner):
        """before_model_call calls remove_section when build returns None."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_build.return_value = [mock_tool]

        system_prompt_builder = Mock()
        system_prompt_builder.language = "cn"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(system_prompt_builder=system_prompt_builder)

        rail = SubagentRail(enable_async_subagent=True)
        rail.init(mock_agent)
        rail.system_prompt_builder = system_prompt_builder

        with patch(
            "openjiuwen.harness.prompts.sections.session_tools.build_session_tools_section"
        ) as mock_build_section:
            mock_build_section.return_value = None

            ctx = Mock()
            await rail.before_model_call(ctx)

            system_prompt_builder.remove_section.assert_called_once()


class TestSessionRailShim:
    """Test cases for SessionRail deprecation shim."""

    @staticmethod
    def test_session_rail_is_subagent_rail_subclass():
        """SessionRail is a subclass of SubagentRail."""
        assert issubclass(SessionRail, SubagentRail)

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.session_rail.logger")
    def test_session_rail_logs_deprecation(mock_logger):
        """SessionRail() logs a deprecation warning."""
        rail = SessionRail()
        mock_logger.warning.assert_called_once()
        assert "deprecated" in mock_logger.warning.call_args[0][0].lower()
        assert "SubagentRail" in mock_logger.warning.call_args[0][0]
        assert rail.enable_async_subagent is True

    @staticmethod
    @patch("openjiuwen.harness.rails.subagent.session_rail.logger")
    def test_session_rail_inherits_async_semantics(mock_logger):
        """SessionRail instance behaves like SubagentRail(enable_async_subagent=True)."""
        rail = SessionRail()
        assert isinstance(rail, SubagentRail)
        assert rail.enable_async_subagent is True
