# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

SKILL_UTIL_PATH = "openjiuwen.core.single_agent.skills.SkillUtil"


class TestSkillLazyInit:
    """Tests for BaseAgent.lazy_init_skill / register_skill lifecycle."""

    @staticmethod
    def _make_agent() -> ReActAgent:
        return ReActAgent(card=AgentCard(name="t", description="d"))

    @staticmethod
    def _make_config(sys_operation_id: str = None) -> ReActAgentConfig:
        cfg = ReActAgentConfig()
        cfg.prompt_template = [{"role": "system", "content": "hi"}]
        cfg.sys_operation_id = sys_operation_id
        return cfg

    @pytest.mark.asyncio
    async def test_configure_creates_skill_util_and_lazy_init_is_idempotent(self):
        """configure() creates SkillUtil; a second lazy_init_skill() does not recreate it."""
        agent = self._make_agent()
        mock_skill_util = MagicMock()
        mock_skill_util.has_skill.return_value = False

        with patch(SKILL_UTIL_PATH, return_value=mock_skill_util) as mock_ctor:
            agent.configure(self._make_config("sys_1"))
            mock_ctor.assert_called_once_with("sys_1")

            agent.lazy_init_skill()
            mock_ctor.assert_called_once_with("sys_1")

    @pytest.mark.asyncio
    async def test_reconfigure_updates_sys_operation_id_without_recreating(self):
        """Re-configuring with a new sys_operation_id updates the existing SkillUtil."""
        agent = self._make_agent()
        mock_skill_util = MagicMock()
        mock_skill_util.has_skill.return_value = False

        with patch(SKILL_UTIL_PATH, return_value=mock_skill_util) as mock_ctor:
            agent.configure(self._make_config("sys_old"))
            mock_ctor.assert_called_once_with("sys_old")

            mock_skill_util.set_sys_operation_id.reset_mock()

            agent.configure(self._make_config("sys_new"))
            mock_skill_util.set_sys_operation_id.assert_called_once_with("sys_new")
            # SkillUtil constructor was NOT called again
            mock_ctor.assert_called_once_with("sys_old")

    @pytest.mark.asyncio
    async def test_register_skill_raises_when_sys_operation_id_missing(self):
        """register_skill() raises AttributeError when sys_operation_id is None."""
        agent = self._make_agent()
        agent.configure(self._make_config(sys_operation_id=None))

        agent.lazy_init_skill = MagicMock(wraps=agent.lazy_init_skill)

        with pytest.raises(AttributeError):
            await agent.register_skill("/tmp/skills")

        agent.lazy_init_skill.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_skill_triggers_lazy_init_and_delegates(self):
        """register_skill() lazy-inits SkillUtil then delegates to register_skills()."""
        agent = self._make_agent()

        with patch.object(agent, "lazy_init_skill", return_value=None):
            agent.configure(self._make_config("sys_2"))

        mock_skill_util = MagicMock()
        mock_skill_util.register_skills = AsyncMock()

        with patch(SKILL_UTIL_PATH, return_value=mock_skill_util) as mock_ctor:
            await agent.register_skill("/tmp/skills")

            mock_ctor.assert_called_once_with("sys_2")
            mock_skill_util.register_skills.assert_awaited_once_with("/tmp/skills", agent)
