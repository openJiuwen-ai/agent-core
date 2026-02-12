# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.mark.asyncio
async def test_lazy_init_skill_create_on_configure():
    with patch.object(ReActAgent, "_init_memory_scope", return_value=None):
        agent = ReActAgent(card=AgentCard(name="t", description="d"))

    cfg = ReActAgentConfig()
    cfg.prompt_template = [{"role": "system", "content": "hi"}]
    cfg.sys_operation_id = "sys_1"

    mock_skill_util = MagicMock()
    mock_skill_util.has_skill.return_value = False

    with patch("openjiuwen.core.single_agent.skills.SkillUtil", return_value=mock_skill_util) as mock_ctor:
        agent.configure(cfg)
        mock_ctor.assert_called_once_with("sys_1")
        agent.lazy_init_skill()
        mock_ctor.assert_called_once_with("sys_1")


@pytest.mark.asyncio
async def test_lazy_skill_init_update_when_exists_and_sysop_changed():
    with patch.object(ReActAgent, "_init_memory_scope", return_value=None):
        agent = ReActAgent(card=AgentCard(name="t", description="d"))

    existing_skill_util = MagicMock()
    existing_skill_util.set_sys_operation_id = MagicMock()
    existing_skill_util.has_skill.return_value = False

    with patch("openjiuwen.core.single_agent.skills.SkillUtil", return_value=existing_skill_util) as mock_ctor:
        old_cfg = ReActAgentConfig()
        old_cfg.prompt_template = [{"role": "system", "content": "hi"}]
        old_cfg.sys_operation_id = "sys_old"
        agent.configure(old_cfg)

        mock_ctor.assert_called_once_with("sys_old")

        existing_skill_util.set_sys_operation_id.reset_mock()

        new_cfg = ReActAgentConfig()
        new_cfg.prompt_template = [{"role": "system", "content": "hi"}]
        new_cfg.sys_operation_id = "sys_new"
        agent.configure(new_cfg)

        existing_skill_util.set_sys_operation_id.assert_called_once_with("sys_new")
        mock_ctor.assert_called_once_with("sys_old")


@pytest.mark.asyncio
async def test_register_skill_fails_when_sys_operation_id_missing():
    with patch.object(ReActAgent, "_init_memory_scope", return_value=None):
        agent = ReActAgent(card=AgentCard(name="t", description="d"))

    cfg = ReActAgentConfig()
    cfg.prompt_template = [{"role": "system", "content": "hi"}]
    cfg.sys_operation_id = None
    agent.configure(cfg)

    agent.lazy_init_skill = MagicMock(wraps=agent.lazy_init_skill)

    with pytest.raises(AttributeError):
        await agent.register_skill("/tmp/skills")

    agent.lazy_init_skill.assert_called_once()


@pytest.mark.asyncio
async def test_register_skill_triggers_lazy_init_and_calls_register_skills():
    with patch.object(ReActAgent, "_init_memory_scope", return_value=None):
        agent = ReActAgent(card=AgentCard(name="t", description="d"))

    cfg = ReActAgentConfig()
    cfg.prompt_template = [{"role": "system", "content": "hi"}]
    cfg.sys_operation_id = "sys_2"

    with patch.object(agent, "lazy_init_skill", return_value=None):
        agent.configure(cfg)

    mock_skill_util = MagicMock()
    mock_skill_util.register_skills = AsyncMock()

    with patch("openjiuwen.core.single_agent.skills.SkillUtil", return_value=mock_skill_util) as mock_ctor:
        await agent.register_skill("/tmp/skills")

        mock_ctor.assert_called_once_with("sys_2")
        mock_skill_util.register_skills.assert_awaited_once_with("/tmp/skills", agent)