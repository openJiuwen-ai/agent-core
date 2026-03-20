# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SubagentRail."""
from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest

from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.rails.subagent_rail import SubagentRail
from openjiuwen.deepagents.schema.config import SubAgentConfig

_TASK_SYSTEM_PROMPT = "openjiuwen.deepagents.prompts.sections.task_tool.build_task_system_prompt"


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
    def test_init_default_language():
        """Test SubagentRail initialization with default language."""
        rail = SubagentRail()
        assert rail.language == "cn"
        assert rail.tools is None

    @staticmethod
    def test_init_custom_language():
        """Test SubagentRail initialization with custom language."""
        rail = SubagentRail(language="en")
        assert rail.language == "en"
        assert rail.tools is None

    @staticmethod
    def test_priority_attribute():
        """Test that priority is correctly set."""
        rail = SubagentRail()
        assert rail.priority == 95

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    def test_init_with_subagents(mock_create_task_tool, mock_runner):
        """Test init method when subagents are configured."""
        mock_tool = _make_tool_mock()
        mock_tool.card.id = "test_tool_id"
        mock_create_task_tool.return_value = [mock_tool]

        mock_resource_mgr = Mock()
        mock_runner.resource_mgr = mock_resource_mgr

        mock_agent = Mock()
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
    @patch("openjiuwen.deepagents.rails.subagent_rail.logger")
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
        mock_tool.name = "test_tool"
        mock_tool.card = Mock()
        mock_tool.card.id = "test_tool_id"

        mock_agent = Mock()
        mock_agent.ability_manager = Mock()

        rail = SubagentRail()
        rail.tools = [mock_tool]

        with patch("openjiuwen.deepagents.rails.subagent_rail.Runner") as mock_runner:
            mock_resource_mgr = Mock()
            mock_runner.resource_mgr = mock_resource_mgr

            rail.uninit(mock_agent)

            mock_agent.ability_manager.remove.assert_called_once_with("test_tool")
            mock_resource_mgr.remove_tool.assert_called_once_with("test_tool_id")

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.logger")
    def test_uninit_without_tools(mock_logger):
        """Test uninit method when no tools are registered."""
        mock_agent = Mock()

        rail = SubagentRail()
        rail.tools = None
        rail.uninit(mock_agent)

        mock_logger.info.assert_not_called()

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
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
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    def test_extract_agent_meta_with_deepagent_fallback(mock_create, mock_runner):
        """DeepAgent-like mock without card uses fallback meta in available_agents."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]

        sub = Mock()
        sub.card = None

        mock_parent_agent = Mock()
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
    @patch(_TASK_SYSTEM_PROMPT)
    def test_inject_prompt_with_dict_message(mock_build):
        """Fallback path appends task prompt to dict system message."""
        mock_build.return_value = "Injected prompt"

        rail = SubagentRail()
        ctx = Mock()
        ctx.inputs = Mock()
        ctx.inputs.messages = [
            {"role": "system", "content": "Original system message"},
            {"role": "user", "content": "User message"},
        ]
        ctx.extra = {}
        rail.tools = [Mock()]

        asyncio.run(rail.before_model_call(ctx))

        assert "Original system message" in ctx.inputs.messages[0]["content"]
        assert "Injected prompt" in ctx.inputs.messages[0]["content"]

    @staticmethod
    @patch(_TASK_SYSTEM_PROMPT)
    def test_inject_prompt_with_systemmessage(mock_build):
        """Fallback path appends task prompt to SystemMessage."""
        mock_build.return_value = "Injected prompt"

        rail = SubagentRail()
        ctx = Mock()
        ctx.inputs = Mock()
        system_msg = SystemMessage(content="Original system message")
        ctx.inputs.messages = [system_msg, {"role": "user", "content": "User message"}]
        ctx.extra = {}
        rail.tools = [Mock()]

        asyncio.run(rail.before_model_call(ctx))

        assert "Original system message" in system_msg.content
        assert "Injected prompt" in system_msg.content

    @staticmethod
    @patch(_TASK_SYSTEM_PROMPT)
    def test_inject_prompt_duplicate_avoidance(mock_build):
        """Fallback path does not append when prompt substring already present."""
        mock_build.return_value = "Injected prompt"

        rail = SubagentRail()
        ctx = Mock()
        ctx.inputs = Mock()
        original_content = "Original system message"
        ctx.inputs.messages = [
            {"role": "system", "content": original_content + "\n\nInjected prompt"}
        ]
        ctx.extra = {}
        rail.tools = [Mock()]

        asyncio.run(rail.before_model_call(ctx))

        assert ctx.inputs.messages[0]["content"] == original_content + "\n\nInjected prompt"

    @staticmethod
    @patch(_TASK_SYSTEM_PROMPT)
    def test_inject_prompt_no_system_message(mock_build):
        """Fallback path inserts SystemMessage when none exists."""
        mock_build.return_value = "New system prompt"

        rail = SubagentRail()
        ctx = Mock()
        ctx.inputs = Mock()
        ctx.inputs.messages = [{"role": "user", "content": "User message"}]
        ctx.extra = {}
        rail.tools = [Mock()]

        asyncio.run(rail.before_model_call(ctx))

        assert len(ctx.inputs.messages) == 2
        assert isinstance(ctx.inputs.messages[0], SystemMessage)
        assert ctx.inputs.messages[0].content == "New system prompt"

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.build_task_section")
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    def test_replace_system_message_with_dict(mock_create, mock_runner, mock_build_section):
        """Builder path replaces dict system message content with built prompt."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]
        mock_build_section.return_value = "task section content"

        prompt_builder = Mock()
        prompt_builder.build.return_value = "Replaced content"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(_prompt_builder=prompt_builder)

        rail = SubagentRail()
        rail.init(mock_agent)

        ctx = Mock()
        ctx.inputs = Mock()
        ctx.inputs.messages = [
            {"role": "system", "content": "Original content"},
            {"role": "user", "content": "User message"},
        ]
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        assert ctx.inputs.messages[0]["content"] == "Replaced content"

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.build_task_section")
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    def test_replace_system_message_with_systemmessage(mock_create, mock_runner, mock_build_section):
        """Builder path replaces SystemMessage.content with built prompt."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]
        mock_build_section.return_value = "task section content"

        prompt_builder = Mock()
        prompt_builder.build.return_value = "Replaced content"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(_prompt_builder=prompt_builder)

        rail = SubagentRail()
        rail.init(mock_agent)

        ctx = Mock()
        ctx.inputs = Mock()
        system_msg = SystemMessage(content="Original content")
        ctx.inputs.messages = [system_msg, {"role": "user", "content": "User message"}]
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        assert system_msg.content == "Replaced content"

    @staticmethod
    @patch("openjiuwen.deepagents.rails.subagent_rail.build_task_section")
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    def test_replace_system_message_insert_new(mock_create, mock_runner, mock_build_section):
        """Builder path inserts SystemMessage when no system role exists."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]
        mock_build_section.return_value = "task section content"

        prompt_builder = Mock()
        prompt_builder.build.return_value = "New system content"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(_prompt_builder=prompt_builder)

        rail = SubagentRail()
        rail.init(mock_agent)

        ctx = Mock()
        ctx.inputs = Mock()
        ctx.inputs.messages = [{"role": "user", "content": "User message"}]
        ctx.extra = {}

        asyncio.run(rail.before_model_call(ctx))

        assert len(ctx.inputs.messages) == 2
        assert isinstance(ctx.inputs.messages[0], SystemMessage)
        assert ctx.inputs.messages[0].content == "New system content"

    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.deepagents.rails.subagent_rail.build_task_section")
    @patch("openjiuwen.deepagents.rails.subagent_rail.Runner")
    @patch("openjiuwen.deepagents.rails.subagent_rail.create_task_tool")
    async def test_before_model_call_with_builder(
        mock_create, mock_runner, mock_build_task_section
    ):
        """Builder path adds task section, builds prompt, and updates system message."""
        mock_runner.resource_mgr = Mock()
        mock_tool = _make_tool_mock()
        mock_create.return_value = [mock_tool]
        mock_build_task_section.return_value = "task section content"

        prompt_builder = Mock()
        prompt_builder.build.return_value = "built prompt"

        mock_agent = Mock()
        mock_agent.deep_config.subagents = [_minimal_subagent_spec()]
        mock_agent.ability_manager = Mock()
        mock_agent.configure_mock(_prompt_builder=prompt_builder)

        rail = SubagentRail()
        rail.init(mock_agent)

        ctx = Mock()
        ctx.inputs = Mock()
        ctx.inputs.messages = [{"role": "system", "content": "old"}]
        ctx.extra = {}

        await rail.before_model_call(ctx)

        prompt_builder.add_section.assert_called_once_with("task section content")
        prompt_builder.build.assert_called_once()
        assert ctx.inputs.messages[0]["content"] == "built prompt"

    @staticmethod
    @pytest.mark.asyncio
    async def test_before_model_call_no_tools():
        """before_model_call returns immediately when tools is None."""
        ctx = Mock()

        rail = SubagentRail()
        rail.tools = None

        await rail.before_model_call(ctx)

    @staticmethod
    @pytest.mark.asyncio
    @patch(_TASK_SYSTEM_PROMPT)
    async def test_before_model_call_avoid_duplicate_injection(mock_build):
        """When extra marks injection done, fallback path skips building task prompt."""
        mock_tool = Mock()
        ctx = Mock()
        ctx.extra = {"_task_tool_prompt_injected": True}

        rail = SubagentRail()
        rail.tools = [mock_tool]

        await rail.before_model_call(ctx)

        mock_build.assert_not_called()

    @staticmethod
    def test_all_method():
        """Test that __all__ exports the correct class."""
        from openjiuwen.deepagents.rails.subagent_rail import __all__

        assert __all__ == ["SubagentRail"]
