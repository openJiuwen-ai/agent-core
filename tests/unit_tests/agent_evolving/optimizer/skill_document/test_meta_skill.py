# coding: utf-8
"""Tests for meta_skill module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.optimizer.skill_document.meta_skill import run_meta_skill


class TestRunMetaSkill:
    @staticmethod
    @pytest.mark.asyncio
    async def test_success():
        llm = MagicMock()
        response_data = json.dumps(
            {
                "reasoning": "Edits targeting error handling helped; vague rules hurt",
                "meta_skill_content": "Prefer specific if/then rules over general guidelines",
            }
        )
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_meta_skill(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="## Improvements\nc2: 0.3 → 0.8",
                prev_meta_skill="Be specific",
            )
        assert result == "Prefer specific if/then rules over general guidelines"

    @staticmethod
    @pytest.mark.asyncio
    async def test_invalid_json():
        llm = MagicMock()
        mock_invoke = AsyncMock(return_value="not json")
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_meta_skill(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert result == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_missing_field():
        llm = MagicMock()
        response_data = json.dumps({"reasoning": "ok"})  # missing meta_skill_content
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_meta_skill(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert result == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_prev_meta_skill():
        llm = MagicMock()
        response_data = json.dumps(
            {
                "reasoning": "First epoch analysis",
                "meta_skill_content": "Focus on error patterns",
            }
        )
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_meta_skill(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
                prev_meta_skill="",
            )
        assert result == "Focus on error patterns"

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_response():
        llm = MagicMock()
        mock_invoke = AsyncMock(return_value="")
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_meta_skill(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert result == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_prompt_includes_context():
        """Verify the prompt sent to LLM contains all context."""
        llm = MagicMock()
        response_data = json.dumps(
            {
                "reasoning": "ok",
                "meta_skill_content": "result",
            }
        )
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.invoke_text_with_retry",
            mock_invoke,
        ) as m:
            await run_meta_skill(
                llm,
                "test-model",
                prev_skill="PREV_SKILL",
                curr_skill="CURR_SKILL",
                comparison_text="COMPARISON_TEXT",
                prev_meta_skill="PREV_META",
            )
        call_args = m.call_args
        prompt = call_args.kwargs.get("prompt", "") or call_args.args[2] if len(call_args.args) > 2 else ""
        assert "PREV_SKILL" in prompt
        assert "CURR_SKILL" in prompt
        assert "COMPARISON_TEXT" in prompt
        assert "PREV_META" in prompt
