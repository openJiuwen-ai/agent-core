# coding: utf-8
"""Tests for the _backward epoch orchestrator and step buffer helpers."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch, RawPatch
from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _make_cases(n: int = 3) -> CaseLoader:
    return CaseLoader(cases=[
        Case(inputs={"q": f"q{i}"}, label={"a": f"a{i}"}, case_id=f"c{i}")
        for i in range(n)
    ])


def _eval_case(case_id: str = "c0", score: float = 0.5, reason: str = "ok") -> EvaluatedCase:
    """Create an EvaluatedCase with a properly constructed Case."""
    return EvaluatedCase(
        case=Case(inputs={"q": case_id}, label={"a": case_id}, case_id=case_id),
        score=score,
        reason=reason,
    )


def _make_optimizer(**overrides) -> SkillDocumentOptimizer:
    defaults = dict(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=_make_cases(3),
        batch_size=2,
        accumulation=1,
        steps_per_epoch=1,
    )
    defaults.update(overrides)
    return SkillDocumentOptimizer(**defaults)


def _make_optimizer_with_operator(skill_content: str = "initial skill", **overrides) -> SkillDocumentOptimizer:
    opt = _make_optimizer(**overrides)
    op = SkillDocumentOperator("test_skill", initial_content=skill_content)
    opt.bind(operators={op.operator_id: op})
    return opt


class TestBackward:
    """Tests for _backward: the full epoch orchestrator (rollout -> reflect -> aggregate -> select -> apply)."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_reads_skill_from_operator():
        opt = _make_optimizer_with_operator("my skill content")

        # Mock rollout to return minimal results
        eval_cases = [
            _eval_case(case_id="c0", score=0.3, reason="bad"),
            _eval_case(case_id="c1", score=0.4, reason="bad"),
        ]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock(), MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted batch")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])
        assert opt._epoch_base_skill_content == "my skill_content" or opt._epoch_base_skill_content == "my skill content"

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_increments_global_step():
        opt = _make_optimizer_with_operator("skill")

        eval_cases = [_eval_case("c0", score=0.5, reason="ok")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        initial_step = opt._global_step
        await opt._backward([])
        assert opt._global_step == initial_step + 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_calls_full_pipeline():
        """Verify rollout -> format -> reflect -> aggregate -> select chain."""
        opt = _make_optimizer_with_operator("skill")

        eval_cases = [_eval_case("c0", score=0.3, reason="fail")]
        mock_trajectories = [MagicMock()]
        opt._rollout = AsyncMock(return_value=(eval_cases, mock_trajectories))
        opt._format_batch = MagicMock(return_value="formatted output")
        opt._reflect = AsyncMock(return_value=[
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content="new", target="", source_type="failure")], reasoning="r"),
                source_type="failure",
            ),
        ])
        opt._aggregate = AsyncMock(return_value=Patch(
            edits=[Edit(op="append", content="new", target="", source_type="failure")],
            reasoning="merged",
        ))
        opt._select = AsyncMock(return_value=[
            Edit(op="append", content="new", target="", source_type="failure"),
        ])

        await opt._backward([])

        opt._rollout.assert_called_once()
        opt._format_batch.assert_called_once()
        opt._reflect.assert_called_once()
        opt._aggregate.assert_called_once()
        opt._select.assert_called_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_applies_edits_to_skill():
        """When edits are selected, skill content should be updated and synced."""
        opt = _make_optimizer_with_operator("original skill content")

        eval_cases = [_eval_case("c0", score=0.3, reason="fail")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(
            edits=[Edit(op="append", content="\n## New Section\nAdded content", target="", source_type="failure")],
            reasoning="r",
        ))
        opt._select = AsyncMock(return_value=[
            Edit(op="append", content="\n## New Section\nAdded content", target="", source_type="failure"),
        ])

        await opt._backward([])

        # Skill should have been updated
        assert "New Section" in opt._current_skill_content
        # Operator should have the new skill
        op = next(iter(opt._operators.values()))
        assert "New Section" in op.get_state()["skill_content"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_no_edits_leaves_skill_unchanged():
        opt = _make_optimizer_with_operator("unchanged skill")

        eval_cases = [_eval_case("c0", score=0.8, reason="good")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="no edits"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])

        op = next(iter(opt._operators.values()))
        assert op.get_state()["skill_content"] == "unchanged skill"
        assert opt._ranked_patch == Patch(edits=[], reasoning="no edits")

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_sets_gradient_for_step():
        """After _backward, the parameter gradient should contain the final skill."""
        opt = _make_optimizer_with_operator("base skill")

        eval_cases = [_eval_case("c0", score=0.3, reason="bad")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(
            edits=[Edit(op="append", content=" improvement", target="", source_type="failure")],
            reasoning="r",
        ))
        opt._select = AsyncMock(return_value=[
            Edit(op="append", content=" improvement", target="", source_type="failure"),
        ])

        await opt._backward([])

        param = next(iter(opt._parameters.values()))
        gradient = param.get_gradient(SKILL_CONTENT_TARGET)
        assert gradient is not None
        assert "improvement" in gradient

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_records_step_buffer():
        opt = _make_optimizer_with_operator("skill")

        eval_cases = [_eval_case("c0", score=0.5, reason="ok")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])
        assert len(opt._step_buffer) == 1
        assert "step" in opt._step_buffer[0]
        assert "n_edits" in opt._step_buffer[0]

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_accumulation_loop():
        """With accumulation=2, rollout should be called twice per step."""
        opt = _make_optimizer_with_operator("skill", accumulation=2)

        eval_cases = [_eval_case("c0", score=0.5, reason="ok")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])
        assert opt._rollout.call_count == 2
        assert opt._reflect.call_count == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_multi_step():
        """With steps_per_epoch=2, the full pipeline runs twice."""
        opt = _make_optimizer_with_operator("skill", steps_per_epoch=2)

        eval_cases = [_eval_case("c0", score=0.5, reason="ok")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])
        assert opt._global_step == 2
        assert len(opt._step_buffer) == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_tracks_comparison_pairs_on_last_step():
        """curr_epoch_comparison should be populated on the last step only."""
        opt = _make_optimizer_with_operator("skill", steps_per_epoch=2, accumulation=1)

        eval_cases = [_eval_case("c0", score=0.7, reason="improved")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="empty"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])
        # Only last step records comparison pairs
        assert len(opt._curr_epoch_comparison) > 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_backward_exports_artifacts(tmp_path):
        opt = _make_optimizer_with_operator("base skill", artifact_dir=str(tmp_path))

        eval_cases = [_eval_case("c0", score=0.3, reason="fail")]
        opt._rollout = AsyncMock(return_value=(eval_cases, [MagicMock(steps=[])]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[
            RawPatch(
                patch=Patch(
                    edits=[Edit(op="append", content=" improvement", target="", source_type="failure")],
                    reasoning="reflect",
                ),
                source_type="failure",
            ),
        ])
        opt._aggregate = AsyncMock(return_value=Patch(
            edits=[Edit(op="append", content=" improvement", target="", source_type="failure")],
            reasoning="merged",
        ))
        opt._select = AsyncMock(return_value=[
            Edit(op="append", content=" improvement", target="", source_type="failure"),
        ])

        await opt._backward([])

        epoch_dir = tmp_path / "epoch_0"
        step_dir = epoch_dir / "step_0"
        assert (epoch_dir / "skill_before.md").exists()
        assert (epoch_dir / "skill_after.md").exists()
        assert (step_dir / "trajectories.jsonl").exists()
        assert (step_dir / "eval_results.json").exists()
        assert (step_dir / "raw_patches.json").exists()
        assert (step_dir / "merged_patch.json").exists()
        assert (step_dir / "selected_edits.json").exists()
        assert (step_dir / "applied_diff.patch").exists()
        metrics = json.loads((step_dir / "metrics.json").read_text())
        assert metrics["n_selected_edits"] == 1
        # Verify structure of comparison entries
        entry = opt._curr_epoch_comparison[0]
        assert "case_id" in entry
        assert "curr_score" in entry
        assert entry["curr_score"] == 0.3


class TestStepBuffer:
    """Tests for step buffer helper methods."""

    @staticmethod
    def test_build_step_buffer_entry_with_no_patch():
        opt = _make_optimizer()
        opt._global_step = 5
        opt._ranked_patch = None

        entry = opt._build_step_buffer_entry(0)
        assert entry["step"] == 5
        assert entry["n_edits"] == 0
        assert entry["failure_patterns"] == []
        assert entry["rejected_edits"] == []

    @staticmethod
    def test_build_step_buffer_entry_with_patch():
        opt = _make_optimizer()
        opt._global_step = 3
        opt._ranked_patch = Patch(
            edits=[
                Edit(op="append", content="fix error handling", target="", source_type="failure"),
                Edit(op="replace", content="improve", target="old", source_type="success"),
            ],
            reasoning="test",
        )

        entry = opt._build_step_buffer_entry(1)
        assert entry["n_edits"] == 2

    @staticmethod
    def test_extract_failure_patterns_filters_by_source():
        opt = _make_optimizer()
        opt._ranked_patch = Patch(
            edits=[
                Edit(op="append", content="failure fix 1", target="", source_type="failure"),
                Edit(op="replace", content="success improvement", target="old", source_type="success"),
                Edit(op="append", content="failure fix 2", target="", source_type="failure"),
            ],
            reasoning="test",
        )

        patterns = opt._extract_failure_patterns()
        assert len(patterns) == 2
        assert "failure fix 1" in patterns[0]
        assert "failure fix 2" in patterns[1]

    @staticmethod
    def test_extract_failure_patterns_max_three():
        opt = _make_optimizer()
        opt._ranked_patch = Patch(
            edits=[
                Edit(op="append", content=f"failure {i}", target="", source_type="failure")
                for i in range(10)
            ],
            reasoning="test",
        )

        patterns = opt._extract_failure_patterns()
        assert len(patterns) == 3

    @staticmethod
    def test_extract_failure_patterns_truncates_long_content():
        opt = _make_optimizer()
        long_content = "x" * 200
        opt._ranked_patch = Patch(
            edits=[Edit(op="append", content=long_content, target="", source_type="failure")],
            reasoning="test",
        )

        patterns = opt._extract_failure_patterns()
        assert len(patterns[0]) == 100

    @staticmethod
    def test_extract_failure_patterns_no_patch():
        opt = _make_optimizer()
        opt._ranked_patch = None
        assert opt._extract_failure_patterns() == []

    @staticmethod
    def test_extract_rejected_edits_returns_empty():
        opt = _make_optimizer()
        assert opt._extract_rejected_edits() == []

    @staticmethod
    def test_format_step_buffer_empty():
        opt = _make_optimizer()
        opt._step_buffer = []
        assert opt._format_step_buffer() == ""

    @staticmethod
    def test_format_step_buffer_with_entries():
        opt = _make_optimizer()
        opt._step_buffer = [
            {"step": 1, "n_edits": 3, "failure_patterns": ["err1"], "rejected_edits": []},
            {"step": 2, "n_edits": 1, "failure_patterns": [], "rejected_edits": []},
        ]

        result = opt._format_step_buffer()
        assert "Step 1: 3 edits applied" in result
        assert "Step 2: 1 edits applied" in result
        assert "Failure patterns" in result

    @staticmethod
    def test_format_step_buffer_with_rejected_edits():
        opt = _make_optimizer()
        opt._step_buffer = [
            {"step": 1, "n_edits": 2, "failure_patterns": [], "rejected_edits": ["rejected1"]},
        ]

        result = opt._format_step_buffer()
        assert "Rejected edits" in result

    @staticmethod
    def test_format_step_buffer_without_failure_or_rejected():
        opt = _make_optimizer()
        opt._step_buffer = [
            {"step": 1, "n_edits": 0, "failure_patterns": [], "rejected_edits": []},
        ]

        result = opt._format_step_buffer()
        assert "Step 1: 0 edits applied" in result
        assert "Failure patterns" not in result
        assert "Rejected edits" not in result


class TestFormatMetaSkillContext:
    @staticmethod
    def test_empty_context():
        opt = _make_optimizer()
        opt._meta_skill_context = ""
        assert opt._format_meta_skill_context() == ""

    @staticmethod
    def test_non_empty_context():
        opt = _make_optimizer()
        opt._meta_skill_context = "remember: focus on error handling"
        assert opt._format_meta_skill_context() == "remember: focus on error handling"


class TestBuildAnalystPrompt:
    @staticmethod
    def test_with_step_buffer_context():
        opt = _make_optimizer()
        prompt = opt._build_analyst_prompt(
            template_name="analyst_error",
            skill_content="my skill",
            trajectories_text="trajectory data",
            step_buffer_context="Step 1: 3 edits applied",
            meta_skill_context="",
        )
        assert "Previous Steps in This Epoch" in prompt
        assert "Step 1: 3 edits applied" in prompt

    @staticmethod
    def test_with_meta_skill_context():
        opt = _make_optimizer()
        prompt = opt._build_analyst_prompt(
            template_name="analyst_error",
            skill_content="my skill",
            trajectories_text="trajectory data",
            step_buffer_context="",
            meta_skill_context="focus on clarity",
        )
        assert "Optimizer Memory" in prompt
        assert "focus on clarity" in prompt

    @staticmethod
    def test_with_both_contexts():
        opt = _make_optimizer()
        prompt = opt._build_analyst_prompt(
            template_name="analyst_error",
            skill_content="my skill",
            trajectories_text="trajectory data",
            step_buffer_context="step info",
            meta_skill_context="meta info",
        )
        assert "Previous Steps" in prompt
        assert "Optimizer Memory" in prompt

    @staticmethod
    def test_success_template():
        opt = _make_optimizer()
        prompt = opt._build_analyst_prompt(
            template_name="analyst_success",
            skill_content="my skill",
            trajectories_text="success data",
            step_buffer_context="",
            meta_skill_context="",
        )
        assert "Successful Trajectories" in prompt

    @staticmethod
    def test_error_template():
        opt = _make_optimizer()
        prompt = opt._build_analyst_prompt(
            template_name="analyst_error",
            skill_content="my skill",
            trajectories_text="error data",
            step_buffer_context="",
            meta_skill_context="",
        )
        assert "Failed Trajectories" in prompt


class TestParseReflectResponseEdgeCases:
    @staticmethod
    def test_non_dict_patch_data():
        """When patch_data is not a dict, return None."""
        opt = _make_optimizer()
        import json
        raw = json.dumps({"patch": "not a dict"})
        result = opt._parse_reflect_response(raw, "failure")
        assert result is None

    @staticmethod
    def test_non_list_edits_data():
        """When edits_data is not a list, return None."""
        opt = _make_optimizer()
        import json
        raw = json.dumps({"patch": {"edits": "not a list"}})
        result = opt._parse_reflect_response(raw, "failure")
        assert result is None

    @staticmethod
    def test_non_dict_edit_items_skipped():
        """Non-dict items in edits list are skipped."""
        opt = _make_optimizer()
        import json
        raw = json.dumps({
            "patch": {
                "edits": [
                    "not a dict",
                    42,
                    {"op": "append", "content": "valid"},
                ],
            },
        })
        result = opt._parse_reflect_response(raw, "failure")
        assert result is not None
        assert len(result.patch.edits) == 1
        assert result.patch.edits[0].op == "append"


class TestReadSkillFromOperatorEdgeCases:
    @staticmethod
    def test_no_operators_returns_empty():
        opt = _make_optimizer()
        # No operators bound
        assert opt._read_skill_from_operator() == ""

    @staticmethod
    def test_operator_with_empty_state():
        opt = _make_optimizer()
        op = MagicMock()
        op.get_state.return_value = {}
        op.operator_id = "test_op"
        opt._operators = {"test_op": op}

        assert opt._read_skill_from_operator() == ""


class TestSkillSyncEdgeCases:
    @staticmethod
    def test_sync_skill_to_multiple_operators():
        opt = _make_optimizer()
        op1 = MagicMock()
        op2 = MagicMock()
        opt._operators = {"op1": op1, "op2": op2}

        opt._sync_skill_to_operator("new skill")
        op1.set_parameter.assert_called_once_with(SKILL_CONTENT_TARGET, "new skill")
        op2.set_parameter.assert_called_once_with(SKILL_CONTENT_TARGET, "new skill")
