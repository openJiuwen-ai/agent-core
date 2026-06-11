# coding: utf-8
"""Tests for per-operator _backward() with multiple operators."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.scheduler import build_scheduler
from openjiuwen.agent_evolving.optimizer.skill_document.types import Edit, Patch, RawPatch
from openjiuwen.agent_evolving.trajectory.types import Trajectory, TrajectoryStep
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _eval_case(case_id: str, score: float = 0.5, reason: str = "") -> EvaluatedCase:
    case = Case(inputs={"q": "x"}, label={"a": "y"}, case_id=case_id)
    return EvaluatedCase(case=case, score=score, reason=reason)


def _make_multi_operator_optimizer(n_ops: int = 2) -> SkillDocumentOptimizer:
    opt = SkillDocumentOptimizer(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[
            Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
        ]),
    )
    ops = {}
    for i in range(n_ops):
        op = SkillDocumentOperator(f"skill_{i}", initial_content=f"content {i}")
        ops[op.operator_id] = op
    opt.bind(operators=ops)
    return opt


class TestMultiOperatorBackward:
    """Multi-operator _backward() end-to-end tests."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_single_operator_still_works():
        """Single operator backward should work identically to before."""
        opt = SkillDocumentOptimizer(
            agent=MagicMock(),
            evaluator=MagicMock(),
            llm=MagicMock(),
            model="test-model",
            train_cases=CaseLoader(cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]),
        )
        op = SkillDocumentOperator("test_skill", initial_content="skill")
        opt.bind(operators={op.operator_id: op})

        opt._rollout = AsyncMock(return_value=([_eval_case("c0", 0.3)], [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[
            RawPatch(patch=Patch(edits=[Edit(op="append", content="new")]), source_type="failure"),
        ])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[Edit(op="append", content="new")], reasoning="r"))
        opt._select = AsyncMock(return_value=[Edit(op="append", content="new")])

        await opt._backward([])

        assert "new" in opt._current_skill_content
        assert len(opt._current_skill_by_operator) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_two_operators_both_get_patches():
        """Two operators with attributed data both get patches applied."""
        opt = _make_multi_operator_optimizer(2)
        op_ids = list(opt._operators.keys())

        # Create trajectories tagged with each operator
        def make_traj(op_id, case_id="c0"):
            return Trajectory(
                execution_id=f"exec-{case_id}",
                steps=[TrajectoryStep(kind="llm", meta={"operator_id": op_id})],
            )

        eval_cases = [_eval_case("c0", 0.3)]
        traj_a = make_traj(op_ids[0])
        opt._rollout = AsyncMock(return_value=(eval_cases, [traj_a]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content="patch for op")]),
                source_type="failure",
            ),
        ])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[Edit(op="append", content="applied")], reasoning="r"))
        opt._select = AsyncMock(return_value=[Edit(op="append", content="applied")])

        await opt._backward([])

        # Both operators should have updated skill content
        assert len(opt._current_skill_by_operator) == 2
        # The attributed operator should have been updated
        for op_id in op_ids:
            assert opt._current_skill_by_operator[op_id] != ""

    @staticmethod
    @pytest.mark.asyncio
    async def test_per_operator_state_populated():
        """After _backward, per-operator dicts should be populated."""
        opt = SkillDocumentOptimizer(
            agent=MagicMock(),
            evaluator=MagicMock(),
            llm=MagicMock(),
            model="test-model",
            train_cases=CaseLoader(cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]),
        )
        op = SkillDocumentOperator("test_skill", initial_content="skill")
        opt.bind(operators={op.operator_id: op})

        opt._rollout = AsyncMock(return_value=([_eval_case("c0", 0.8)], [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])
        opt._aggregate = AsyncMock(return_value=Patch(edits=[], reasoning="no edits"))
        opt._select = AsyncMock(return_value=[])

        await opt._backward([])

        assert opt._epoch_base_skill_by_operator
        assert opt._last_candidate_skill_by_operator

    @staticmethod
    @pytest.mark.asyncio
    async def test_raw_patches_are_routed_by_declared_operator_id():
        """Multi-operator backward should validate and route RawPatch.operator_id."""
        opt = _make_multi_operator_optimizer(2)
        op_ids = list(opt._operators.keys())

        opt._rollout = AsyncMock(return_value=([_eval_case("c0", 0.3)], [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content=" routed")]),
                source_type="failure",
                operator_id=op_ids[1],
            ),
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content=" wrong")]),
                source_type="failure",
                operator_id="unknown",
            ),
            RawPatch(
                patch=Patch(edits=[Edit(op="append", content=" empty")]),
                source_type="failure",
            ),
        ])

        async def aggregate(patches, skill_content):
            edits = [edit for raw_patch in patches for edit in raw_patch.patch.edits]
            return Patch(edits=edits, reasoning="merged")

        opt._aggregate = AsyncMock(side_effect=aggregate)
        opt._select = AsyncMock(side_effect=lambda edits, **_: edits)

        await opt._backward([])

        assert opt._current_skill_by_operator[op_ids[0]] == "content 0"
        assert "routed" in opt._current_skill_by_operator[op_ids[1]]
        assert "wrong" not in opt._current_skill_by_operator[op_ids[1]]
        assert "empty" not in opt._current_skill_by_operator[op_ids[1]]

    @staticmethod
    @pytest.mark.asyncio
    async def test_scheduler_advances_once_per_step_for_multiple_operators():
        """All operators in the same optimizer step should see the same budget."""
        opt = _make_multi_operator_optimizer(2)
        op_ids = list(opt._operators.keys())
        opt._steps_per_epoch = 2
        opt._scheduler = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=2)

        opt._rollout = AsyncMock(return_value=([_eval_case("c0", 0.3)], [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")

        async def reflect(*_, skill_content, **__):
            op_id = op_ids[0] if skill_content == "content 0" else op_ids[1]
            return [
                RawPatch(
                    patch=Patch(edits=[Edit(op="append", content=f" {op_id}")]),
                    source_type="failure",
                    operator_id=op_id,
                ),
            ]

        opt._reflect = AsyncMock(side_effect=reflect)

        async def aggregate(patches, skill_content):
            edits = [edit for raw_patch in patches for edit in raw_patch.patch.edits]
            return Patch(edits=edits, reasoning="merged")

        opt._aggregate = AsyncMock(side_effect=aggregate)
        seen_budgets = []

        async def select(edits, budget, **_):
            seen_budgets.append(budget)
            return edits

        opt._select = AsyncMock(side_effect=select)

        await opt._backward([])

        assert seen_budgets == [6, 6, 2, 2]
        assert opt._scheduler.state_dict()["current_step"] == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_step_metrics_and_buffer_sum_all_operators(tmp_path):
        """Multi-operator edit counts should not be overwritten by the last operator."""
        opt = _make_multi_operator_optimizer(2)
        opt._artifact_dir = str(tmp_path)
        opt._artifact_exporter = opt._artifact_exporter.__class__(str(tmp_path))

        opt._rollout = AsyncMock(return_value=([_eval_case("c0", 0.3)], [MagicMock()]))
        opt._format_batch = MagicMock(return_value="formatted")
        opt._reflect = AsyncMock(return_value=[])

        async def aggregate(patches, skill_content):
            if skill_content == "content 0":
                return Patch(edits=[Edit(op="append", content=" a")], reasoning="op0")
            return Patch(
                edits=[
                    Edit(op="append", content=" b"),
                    Edit(op="append", content=" c"),
                ],
                reasoning="op1",
            )

        opt._aggregate = AsyncMock(side_effect=aggregate)
        opt._select = AsyncMock(side_effect=lambda edits, **_: edits)

        await opt._backward([])

        metrics = json.loads((tmp_path / "epoch_0" / "step_0" / "metrics.json").read_text())
        assert metrics["n_merged_edits"] == 3
        assert metrics["n_selected_edits"] == 3
        assert sorted(metrics["n_selected_edits_by_operator"].values()) == [1, 2]
        assert opt._step_buffer[0]["n_edits"] == 3
        assert sorted(opt._step_buffer[0]["n_edits_by_operator"].values()) == [1, 2]
