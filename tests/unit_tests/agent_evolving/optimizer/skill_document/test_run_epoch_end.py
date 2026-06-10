# coding: utf-8
"""Tests for run_epoch_end, _run_slow_update, and _run_meta_skill flows."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import SlowUpdateResult
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[
            Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
        ]),
        batch_size=2,
        accumulation=1,
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


def _make_optimizer_with_operator(skill_content: str = "initial skill", **overrides) -> SkillDocumentOptimizer:
    opt = _make_optimizer(**overrides)
    op = SkillDocumentOperator("test_skill", initial_content=skill_content)
    opt.bind(operators={op.operator_id: op})
    return opt


class TestRunEpochEnd:
    @staticmethod
    @pytest.mark.asyncio
    async def test_epoch_0_skips_slow_and_meta():
        opt = _make_optimizer_with_operator(
            "skill", use_slow_update=True, use_meta_skill=True,
        )
        opt._run_slow_update = AsyncMock()
        opt._run_meta_skill = AsyncMock()

        await opt.run_epoch_end(epoch=0)

        opt._run_slow_update.assert_not_called()
        opt._run_meta_skill.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_epoch_1_runs_slow_and_meta():
        opt = _make_optimizer_with_operator(
            "skill", use_slow_update=True, use_meta_skill=True,
        )
        opt._run_slow_update = AsyncMock()
        opt._run_meta_skill = AsyncMock()

        await opt.run_epoch_end(epoch=1)

        opt._run_slow_update.assert_called_once_with(1)
        opt._run_meta_skill.assert_called_once_with(1)

    @staticmethod
    @pytest.mark.asyncio
    async def test_saves_prev_epoch_state():
        opt = _make_optimizer_with_operator("skill")
        opt._current_skill_content = "epoch 1 skill"
        opt._curr_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.8, "curr_reason": "good"},
        ]

        await opt.run_epoch_end(epoch=1)

        assert opt._prev_epoch_skill == "skill"
        assert len(opt._prev_epoch_comparison) == 1
        assert opt._prev_epoch_comparison[0]["case_id"] == "c0"

    @staticmethod
    @pytest.mark.asyncio
    async def test_refreshes_committed_skill_before_epoch_end_updates():
        opt = _make_optimizer_with_operator("base skill")
        opt._current_skill_content = "rejected candidate"
        opt._epoch_base_skill_content = "base skill"
        opt._last_candidate_skill_content = "rejected candidate"
        opt._use_slow_update = False
        opt._use_meta_skill = False

        await opt.run_epoch_end(epoch=1)

        assert opt._current_skill_content == "base skill"
        assert opt._prev_epoch_skill == "base skill"

    @staticmethod
    @pytest.mark.asyncio
    async def test_exports_gate_result_after_candidate_rejected(tmp_path):
        opt = _make_optimizer_with_operator(
            "base skill",
            artifact_dir=str(tmp_path),
            use_slow_update=False,
            use_meta_skill=False,
        )
        opt._current_skill_content = "rejected candidate"
        opt._epoch_base_skill_content = "base skill"
        opt._last_candidate_skill_content = "rejected candidate"
        eval_results = [
            EvaluatedCase(
                case=Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
                score=0.8,
                reason="selected base",
            ),
        ]

        await opt.run_epoch_end(epoch=0, val_results=eval_results)

        data = json.loads((tmp_path / "epoch_0" / "gate_result.json").read_text())
        assert data["decision"] == "base"
        assert data["base_score"] == 0.8
        assert data["candidate_score"] is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_clears_curr_epoch_comparison():
        opt = _make_optimizer_with_operator("skill")
        opt._curr_epoch_comparison = [{"case_id": "c0"}]

        await opt.run_epoch_end(epoch=0)
        assert opt._curr_epoch_comparison == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_clears_step_buffer():
        opt = _make_optimizer_with_operator("skill")
        opt._step_buffer = [{"step": 1, "n_edits": 3}]

        await opt.run_epoch_end(epoch=0)
        assert opt._step_buffer == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_slow_update_disabled():
        opt = _make_optimizer_with_operator(
            "skill", use_slow_update=False, use_meta_skill=True,
        )
        opt._run_slow_update = AsyncMock()
        opt._run_meta_skill = AsyncMock()

        await opt.run_epoch_end(epoch=1)

        opt._run_slow_update.assert_not_called()
        opt._run_meta_skill.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_meta_skill_disabled():
        opt = _make_optimizer_with_operator(
            "skill", use_slow_update=True, use_meta_skill=False,
        )
        opt._run_slow_update = AsyncMock()
        opt._run_meta_skill = AsyncMock()

        await opt.run_epoch_end(epoch=1)

        opt._run_slow_update.assert_called_once()
        opt._run_meta_skill.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_higher_epoch_numbers():
        opt = _make_optimizer_with_operator(
            "skill", use_slow_update=True, use_meta_skill=True,
        )
        opt._run_slow_update = AsyncMock()
        opt._run_meta_skill = AsyncMock()

        await opt.run_epoch_end(epoch=5)

        opt._run_slow_update.assert_called_once_with(5)
        opt._run_meta_skill.assert_called_once_with(5)


class TestRunSlowUpdate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_skips_when_no_prev_comparison():
        opt = _make_optimizer_with_operator("skill")
        opt._prev_epoch_comparison = []

        await opt._run_slow_update(epoch=1)
        # No error, no crash

    @staticmethod
    @pytest.mark.asyncio
    async def test_calls_slow_update_and_injects():
        skill_with_markers = (
            "before\n"
            "<!-- SLOW_UPDATE_START -->\n"
            "old slow content\n"
            "<!-- SLOW_UPDATE_END -->\n"
            "after\n"
        )
        opt = _make_optimizer_with_operator(skill_with_markers)
        opt._prev_epoch_comparison = [
            {"case_id": "c0", "prev_score": 0.5, "curr_score": 0.7, "curr_reason": "improved"},
        ]
        opt._curr_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.3, "curr_reason": "regressed"},
        ]
        opt._current_skill_content = skill_with_markers

        mock_result = SlowUpdateResult(
            reasoning="needs more examples",
            slow_update_content="## Key Guidance\nAdd examples",
            action="success",
        )

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.run_slow_update",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            await opt._run_slow_update(epoch=1)

        assert "Key Guidance" in opt._current_skill_content

    @staticmethod
    @pytest.mark.asyncio
    async def test_handles_parse_failed_action():
        opt = _make_optimizer_with_operator("skill content")
        opt._prev_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.5},
        ]
        opt._curr_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.3, "curr_reason": "regressed"},
        ]
        opt._current_skill_content = "skill content"

        mock_result = SlowUpdateResult(
            reasoning="",
            slow_update_content="",
            action="parse_failed",
        )

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.run_slow_update",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            await opt._run_slow_update(epoch=1)

        # Skill unchanged on parse failure
        assert opt._current_skill_content == "skill content"


class TestRunMetaSkill:
    @staticmethod
    @pytest.mark.asyncio
    async def test_updates_meta_skill_context():
        opt = _make_optimizer_with_operator("skill")
        opt._prev_epoch_skill = "previous skill content"
        opt._prev_epoch_comparison = [
            {"case_id": "c0", "curr_score": 0.8, "curr_reason": "good"},
        ]

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.run_meta_skill",
            new_callable=AsyncMock,
            return_value="focus on error handling patterns",
        ):
            await opt._run_meta_skill(epoch=1)

        assert opt._meta_skill_context == "focus on error handling patterns"

    @staticmethod
    @pytest.mark.asyncio
    async def test_skips_when_no_prev_skill():
        """Guard: if _prev_epoch_skill is empty, skip meta_skill."""
        opt = _make_optimizer_with_operator("skill")
        opt._prev_epoch_skill = ""

        await opt._run_meta_skill(epoch=1)
        assert opt._meta_skill_context == ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_result_keeps_context_empty():
        opt = _make_optimizer_with_operator("skill")
        opt._prev_epoch_skill = "previous skill"
        opt._prev_epoch_comparison = [{"case_id": "c0", "curr_score": 0.5}]

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.run_meta_skill",
            new_callable=AsyncMock,
            return_value="",
        ):
            await opt._run_meta_skill(epoch=1)

        assert opt._meta_skill_context == ""
