# coding: utf-8
"""Tests for _attribute() — per-operator failure/success attribution."""

import asyncio
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.trajectory.types import Trajectory, TrajectoryStep
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _make_optimizer(n_operators: int = 1) -> SkillDocumentOptimizer:
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
    for i in range(n_operators):
        op = SkillDocumentOperator(f"skill_{i}", initial_content=f"content {i}")
        ops[op.operator_id] = op
    opt.bind(operators=ops)
    return opt


def _make_case_pair(score: float = 0.3):
    """Return (trajectory, evaluated_case, case) tuple."""
    case = Case(inputs={"q": "test"}, label={"a": "expected"})
    ec = EvaluatedCase(case=case, score=score)
    traj = Trajectory(execution_id=f"exec-{case.case_id}", steps=[])
    return traj, ec, case


def _make_case_with_operator_steps(operator_ids: list[str], score: float = 0.3):
    """Return (trajectory, evaluated_case, case) with steps tagged by operator_id."""
    case = Case(inputs={"q": "test"}, label={"a": "expected"})
    ec = EvaluatedCase(case=case, score=score)
    steps = [
        TrajectoryStep(kind="llm", meta={"operator_id": op_id})
        for op_id in operator_ids
    ]
    traj = Trajectory(execution_id=f"exec-{case.case_id}", steps=steps)
    return traj, ec, case


class TestSingleOperatorShortCircuit:
    """Single operator: all cases attributed to sole operator, no LLM call."""

    @staticmethod
    def test_all_failures_to_sole_operator():
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        failures = [_make_case_pair(score=0.2)]
        skill_contents = {op_id: "skill content"}

        result = asyncio.run(
            opt._attribute(
                failure_batch=failures,
                success_batch=[],
                skill_contents=skill_contents,
            )
        )

        assert op_id in result
        assert len(result[op_id].failures) == 1
        assert result[op_id].operator_id == op_id

    @staticmethod
    def test_all_successes_to_sole_operator():
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        successes = [_make_case_pair(score=0.9)]
        skill_contents = {op_id: "skill content"}

        result = asyncio.run(
            opt._attribute(
                failure_batch=[],
                success_batch=successes,
                skill_contents=skill_contents,
            )
        )

        assert op_id in result
        assert len(result[op_id].successes) == 1

    @staticmethod
    def test_no_llm_call_for_single_operator():
        """Single operator should never trigger LLM."""
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        failures = [_make_case_pair(score=0.2)]
        successes = [_make_case_pair(score=0.9)]
        skill_contents = {op_id: "skill content"}

        asyncio.run(
            opt._attribute(
                failure_batch=failures,
                success_batch=successes,
                skill_contents=skill_contents,
            )
        )

        # LLM should not be called
        opt._llm.invoke.assert_not_called()


class TestMultiOperatorAttribution:
    """Multi operator: rule-based attribution using trajectory metadata."""

    @staticmethod
    def test_failure_attributed_to_participating_operator():
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        skill_contents = {op_ids[0]: "content A", op_ids[1]: "content B"}

        # Failure trajectory only involves operator 0
        failures = [_make_case_with_operator_steps([op_ids[0]], score=0.2)]
        successes = []

        result = asyncio.run(
            opt._attribute(
                failure_batch=failures,
                success_batch=successes,
                skill_contents=skill_contents,
            )
        )

        assert op_ids[0] in result
        assert len(result[op_ids[0]].failures) == 1

    @staticmethod
    def test_success_attributed_to_all_participating_operators():
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        skill_contents = {op_ids[0]: "content A", op_ids[1]: "content B"}

        # Success trajectory involves both operators
        successes = [_make_case_with_operator_steps(op_ids, score=0.9)]
        failures = []

        result = asyncio.run(
            opt._attribute(
                failure_batch=failures,
                success_batch=successes,
                skill_contents=skill_contents,
            )
        )

        # Both operators should receive the success
        for op_id in op_ids:
            assert op_id in result
            assert len(result[op_id].successes) == 1

    @staticmethod
    def test_conservative_when_no_operator_id_in_steps():
        """When trajectory has no operator_id metadata, attribute to all operators."""
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        skill_contents = {op_ids[0]: "content A", op_ids[1]: "content B"}

        # Failure trajectory with no operator_id in steps
        failures = [_make_case_pair(score=0.2)]
        successes = []

        result = asyncio.run(
            opt._attribute(
                failure_batch=failures,
                success_batch=successes,
                skill_contents=skill_contents,
            )
        )

        # Conservative: both operators get the failure
        for op_id in op_ids:
            assert op_id in result
            assert len(result[op_id].failures) == 1


class TestEmptyBatches:
    """Empty failure/success batches handled gracefully."""

    @staticmethod
    def test_both_empty_single_operator():
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        skill_contents = {op_id: "skill content"}

        result = asyncio.run(
            opt._attribute(
                failure_batch=[],
                success_batch=[],
                skill_contents=skill_contents,
            )
        )

        # Either empty result or single operator with empty lists
        if op_id in result:
            assert result[op_id].failures == []
            assert result[op_id].successes == []

    @staticmethod
    def test_both_empty_multi_operator():
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        skill_contents = {op_ids[0]: "A", op_ids[1]: "B"}

        result = asyncio.run(
            opt._attribute(
                failure_batch=[],
                success_batch=[],
                skill_contents=skill_contents,
            )
        )

        assert isinstance(result, dict)
