# coding: utf-8
"""Tests for slow_update module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.optimizer.skill_document.slow_update import (
    build_comparison_text,
    run_slow_update,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import SlowUpdateResult


class TestBuildComparisonText:
    @staticmethod
    def test_all_categories():
        """Four cases covering regression, improvement, persistent failure, stable success."""
        prev = [
            {"case_id": "c1", "curr_score": 0.8, "curr_reason": "good"},
            {"case_id": "c2", "curr_score": 0.3, "curr_reason": "bad"},
            {"case_id": "c3", "curr_score": 0.2, "curr_reason": "bad"},
            {"case_id": "c4", "curr_score": 0.9, "curr_reason": "good"},
        ]
        curr = [
            {"case_id": "c1", "curr_score": 0.4, "curr_reason": "regressed"},  # regression: 0.8→0.4
            {"case_id": "c2", "curr_score": 0.8, "curr_reason": "improved"},  # improvement: 0.3→0.8
            {"case_id": "c3", "curr_score": 0.3, "curr_reason": "still bad"},  # persistent failure: both <0.5
            {"case_id": "c4", "curr_score": 0.85, "curr_reason": "still good"},  # stable success: both >=0.7
        ]
        text = build_comparison_text(prev, curr)
        assert "Regressions" in text or "regression" in text.lower()
        assert "Improvements" in text or "improvement" in text.lower()
        assert "Persistent" in text or "persistent" in text.lower()
        assert "Stable" in text or "stable" in text.lower()
        assert "c1" in text
        assert "c2" in text
        assert "c3" in text
        assert "c4" in text

    @staticmethod
    def test_empty_prev():
        curr = [{"case_id": "c1", "curr_score": 0.5, "curr_reason": "ok"}]
        text = build_comparison_text([], curr)
        assert "no prior" in text.lower() or "no previous" in text.lower() or not text.strip()

    @staticmethod
    def test_empty_curr():
        prev = [{"case_id": "c1", "curr_score": 0.5, "curr_reason": "ok"}]
        text = build_comparison_text(prev, [])
        assert "no current" in text.lower() or "no data" in text.lower() or not text.strip()

    @staticmethod
    def test_both_empty():
        text = build_comparison_text([], [])
        assert not text.strip() or "no" in text.lower()

    @staticmethod
    def test_no_matching_ids():
        prev = [{"case_id": "a1", "curr_score": 0.5, "curr_reason": "ok"}]
        curr = [{"case_id": "b1", "curr_score": 0.6, "curr_reason": "ok"}]
        text = build_comparison_text(prev, curr)
        # Should handle gracefully — no matched pairs
        assert isinstance(text, str)

    @staticmethod
    def test_partial_overlap():
        prev = [
            {"case_id": "c1", "curr_score": 0.5, "curr_reason": "ok"},
            {"case_id": "c2", "curr_score": 0.3, "curr_reason": "bad"},
        ]
        curr = [
            {"case_id": "c1", "curr_score": 0.7, "curr_reason": "improved"},
            {"case_id": "c3", "curr_score": 0.9, "curr_reason": "new case"},
        ]
        text = build_comparison_text(prev, curr)
        assert "c1" in text  # matched pair should appear


class TestRunSlowUpdate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_success():
        llm = MagicMock()
        response_data = json.dumps(
            {
                "reasoning": "Previous guidance helped with X but missed Y",
                "slow_update_content": "When encountering Y, always check Z first",
            }
        )
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_slow_update(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="## Regressions\nc1: 0.8 → 0.4",
                prev_guidance="Be thorough",
            )
        assert isinstance(result, SlowUpdateResult)
        assert "Y" in result.slow_update_content
        assert result.reasoning

    @staticmethod
    @pytest.mark.asyncio
    async def test_invalid_json():
        llm = MagicMock()
        mock_invoke = AsyncMock(return_value="this is not json at all")
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_slow_update(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert isinstance(result, SlowUpdateResult)
        assert result.slow_update_content == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_response():
        llm = MagicMock()
        mock_invoke = AsyncMock(return_value="")
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_slow_update(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert isinstance(result, SlowUpdateResult)
        assert result.slow_update_content == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_missing_slow_update_content_field():
        llm = MagicMock()
        response_data = json.dumps(
            {
                "reasoning": "some reasoning",
                # missing slow_update_content
            }
        )
        mock_invoke = AsyncMock(return_value=response_data)
        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.invoke_text_with_retry",
            mock_invoke,
        ):
            result = await run_slow_update(
                llm,
                "test-model",
                prev_skill="# Skill v1",
                curr_skill="# Skill v2",
                comparison_text="some comparison",
            )
        assert isinstance(result, SlowUpdateResult)
        assert result.slow_update_content == ""
