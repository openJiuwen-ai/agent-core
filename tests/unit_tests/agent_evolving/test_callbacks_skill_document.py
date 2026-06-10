# coding: utf-8
"""Tests for SkillDocumentCallbacks and related edit_apply helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.callbacks.skill_document_callbacks import SkillDocumentCallbacks
from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.edit_apply import (
    SLOW_UPDATE_END,
    SLOW_UPDATE_START,
    extract_slow_update_content,
    replace_slow_update_field,
)
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.trainer.progress import Progress

_TEST_CASE = Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0")


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[_TEST_CASE]),
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


# ── replace_slow_update_field tests ───────────────────────────────────────


class TestReplaceSlowUpdateField:
    @staticmethod
    def test_no_markers_appends():
        skill = "# My Skill\n\nSome content."
        result = replace_slow_update_field(skill, "Be careful with X")
        assert SLOW_UPDATE_START in result
        assert SLOW_UPDATE_END in result
        assert "Be careful with X" in result
        # Original content preserved
        assert "# My Skill" in result
        assert "Some content." in result

    @staticmethod
    def test_existing_markers_replaces():
        skill = f"# Skill\n\n{SLOW_UPDATE_START}\nold guidance\n{SLOW_UPDATE_END}\n"
        result = replace_slow_update_field(skill, "new guidance")
        assert "old guidance" not in result
        assert "new guidance" in result
        assert result.count(SLOW_UPDATE_START) == 1
        assert result.count(SLOW_UPDATE_END) == 1

    @staticmethod
    def test_empty_content():
        skill = f"# Skill\n\n{SLOW_UPDATE_START}\nold\n{SLOW_UPDATE_END}\n"
        result = replace_slow_update_field(skill, "")
        assert SLOW_UPDATE_START in result
        assert SLOW_UPDATE_END in result

    @staticmethod
    def test_preserves_content_before_and_after_markers():
        skill = f"BEFORE\n{SLOW_UPDATE_START}\nold\n{SLOW_UPDATE_END}\nAFTER"
        result = replace_slow_update_field(skill, "new")
        assert "BEFORE" in result
        assert "AFTER" in result


class TestExtractSlowUpdateContent:
    @staticmethod
    def test_no_markers():
        assert extract_slow_update_content("# Skill\nNo markers") == ""

    @staticmethod
    def test_with_content():
        skill = f"# Skill\n{SLOW_UPDATE_START}\nmy guidance\n{SLOW_UPDATE_END}\n"
        assert extract_slow_update_content(skill) == "my guidance"

    @staticmethod
    def test_empty_region():
        skill = f"{SLOW_UPDATE_START}\n{SLOW_UPDATE_END}"
        assert extract_slow_update_content(skill) == ""


# ── SkillDocumentCallbacks tests ─────────────────────────────────────────


class TestSkillDocumentCallbacks:
    @staticmethod
    def test_on_train_epoch_end_calls_run_epoch_end():
        """Verify the callback bridges sync→async correctly."""
        opt = _make_optimizer()
        opt._current_skill_content = "# Skill"

        with patch.object(opt, "run_epoch_end", new_callable=AsyncMock) as mock_run:
            cb = SkillDocumentCallbacks(opt)
            progress = Progress(current_epoch=1)
            eval_info = [EvaluatedCase(case=_TEST_CASE, score=0.5, reason="ok")]
            cb.on_train_epoch_end(MagicMock(), progress, eval_info)

        mock_run.assert_called_once_with(epoch=1, val_results=eval_info)

    @staticmethod
    @pytest.mark.asyncio
    async def test_epoch_0_skips_slow_update_and_meta_skill():
        """epoch < 1 should not call _run_slow_update or _run_meta_skill."""
        opt = _make_optimizer()
        opt._current_skill_content = "# Skill"
        opt._use_slow_update = True
        opt._use_meta_skill = True

        with (
            patch.object(opt, "_run_slow_update", new_callable=AsyncMock) as mock_su,
            patch.object(opt, "_run_meta_skill", new_callable=AsyncMock) as mock_ms,
        ):
            await opt.run_epoch_end(epoch=0)

        mock_su.assert_not_called()
        mock_ms.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_empty_prev_comparison_skips_slow_update():
        """First epoch after resume: _prev_epoch_comparison is empty."""
        opt = _make_optimizer()
        opt._current_skill_content = "# Skill"
        opt._use_slow_update = True
        opt._use_meta_skill = True
        opt._prev_epoch_comparison = []
        opt._prev_epoch_skill = "prev skill"

        # slow_update should be called but return early due to empty comparison
        with patch.object(opt, "_sync_skill_to_operator") as mock_sync:
            await opt.run_epoch_end(epoch=1)

        # sync should NOT be called because slow_update has nothing to compare
        mock_sync.assert_not_called()

    @staticmethod
    @pytest.mark.asyncio
    async def test_slow_update_injects_into_markers():
        """slow_update result should be injected into skill document markers."""
        opt = _make_optimizer()
        opt._current_skill_content = "# Skill v2"
        opt._prev_epoch_skill = "# Skill v1"
        opt._use_slow_update = True
        opt._use_meta_skill = False
        opt._prev_epoch_comparison = [{"case_id": "c1", "curr_score": 0.8}]
        opt._curr_epoch_comparison = [{"case_id": "c1", "curr_score": 0.4}]

        mock_result = MagicMock()
        mock_result.slow_update_content = "Be careful with regression X"

        with (
            patch(
                "openjiuwen.agent_evolving.optimizer.skill_document.slow_update.run_slow_update",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch.object(opt, "_sync_skill_to_operator") as mock_sync,
        ):
            await opt.run_epoch_end(epoch=1)

        assert SLOW_UPDATE_START in opt._current_skill_content
        assert "Be careful with regression X" in opt._current_skill_content
        mock_sync.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_meta_skill_updates_context_only():
        """meta_skill should only update _meta_skill_context, not skill document."""
        opt = _make_optimizer()
        skill_before = "# Skill v2"
        opt._current_skill_content = skill_before
        opt._prev_epoch_skill = "# Skill v1"
        opt._use_slow_update = False
        opt._use_meta_skill = True
        opt._prev_epoch_comparison = [{"case_id": "c1", "curr_score": 0.8}]
        opt._curr_epoch_comparison = [{"case_id": "c1", "curr_score": 0.4}]

        with patch(
            "openjiuwen.agent_evolving.optimizer.skill_document.meta_skill.run_meta_skill",
            new_callable=AsyncMock,
            return_value="Focus on error patterns",
        ):
            await opt.run_epoch_end(epoch=1)

        assert opt._meta_skill_context == "Focus on error patterns"
        # Skill document should be unchanged
        assert opt._current_skill_content == skill_before

    @staticmethod
    @pytest.mark.asyncio
    async def test_run_epoch_end_saves_prev_epoch_state():
        """After run_epoch_end, prev state should be updated."""
        opt = _make_optimizer()
        opt._current_skill_content = "# Current"
        opt._curr_epoch_comparison = [{"case_id": "c1", "curr_score": 0.5}]
        opt._step_buffer = [{"step": 0, "n_edits": 3}]
        opt._use_slow_update = False
        opt._use_meta_skill = False

        await opt.run_epoch_end(epoch=0)

        assert opt._prev_epoch_skill == "# Current"
        assert opt._prev_epoch_comparison == [{"case_id": "c1", "curr_score": 0.5}]
        assert opt._curr_epoch_comparison == []
        assert opt._step_buffer == []
