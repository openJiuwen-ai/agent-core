# coding: utf-8
"""Tests for _step() per-operator candidate generation."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _make_multi_operator_optimizer() -> SkillDocumentOptimizer:
    opt = SkillDocumentOptimizer(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[
            Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
        ]),
    )
    op_a = SkillDocumentOperator("skill_a", initial_content="base A")
    op_b = SkillDocumentOperator("skill_b", initial_content="base B")
    opt.bind(operators={op_a.operator_id: op_a, op_b.operator_id: op_b})
    return opt


class TestMultiOperatorStep:
    """_step() per-operator candidate generation."""

    @staticmethod
    def test_one_changed_one_unchanged():
        """2 operators, only one changed → candidate includes only changed."""
        opt = _make_multi_operator_optimizer()
        op_ids = list(opt._operators.keys())

        opt._epoch_base_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        opt._current_skill_by_operator = {op_ids[0]: "new A", op_ids[1]: "base B"}

        result = opt._step()
        assert len(result) == 2  # base + candidate

        base = result[0]
        candidate = result[1]

        # Base has both operators
        assert (op_ids[0], SKILL_CONTENT_TARGET) in base
        assert (op_ids[1], SKILL_CONTENT_TARGET) in base

        # Candidate has the changed operator
        assert (op_ids[0], SKILL_CONTENT_TARGET) in candidate
        assert candidate[(op_ids[0], SKILL_CONTENT_TARGET)] == "new A"

    @staticmethod
    def test_both_changed():
        """2 operators, both changed → candidate includes both."""
        opt = _make_multi_operator_optimizer()
        op_ids = list(opt._operators.keys())

        opt._epoch_base_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        opt._current_skill_by_operator = {op_ids[0]: "new A", op_ids[1]: "new B"}

        result = opt._step()
        assert len(result) == 2

        candidate = result[1]
        assert candidate[(op_ids[0], SKILL_CONTENT_TARGET)] == "new A"
        assert candidate[(op_ids[1], SKILL_CONTENT_TARGET)] == "new B"

    @staticmethod
    def test_neither_changed():
        """2 operators, neither changed → only base returned."""
        opt = _make_multi_operator_optimizer()
        op_ids = list(opt._operators.keys())

        opt._epoch_base_skill_by_operator = {op_ids[0]: "same A", op_ids[1]: "same B"}
        opt._current_skill_by_operator = {op_ids[0]: "same A", op_ids[1]: "same B"}

        result = opt._step()
        assert len(result) == 1  # only base
