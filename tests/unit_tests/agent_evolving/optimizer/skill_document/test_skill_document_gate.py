# coding: utf-8
"""Tests for _step() gate behavior: base/candidate return and diff check."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.protocols import SKILL_CONTENT_TARGET
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


def _make_optimizer_with_operator(skill_content: str = "initial skill") -> SkillDocumentOptimizer:
    opt = SkillDocumentOptimizer(
        agent=MagicMock(),
        evaluator=MagicMock(),
        llm=MagicMock(),
        model="test-model",
        train_cases=CaseLoader(cases=[
            Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
        ]),
    )
    op = SkillDocumentOperator("test_skill", initial_content=skill_content)
    opt.bind(operators={op.operator_id: op})
    return opt


class TestStepGate:
    @staticmethod
    def test_returns_list():
        opt = _make_optimizer_with_operator()
        # Simulate _backward having written a gradient
        param = next(iter(opt._parameters.values()))
        param.set_gradient(SKILL_CONTENT_TARGET, "new skill")
        opt._epoch_base_skill_content = "old skill"

        result = opt._step()
        assert isinstance(result, list)

    @staticmethod
    def test_base_and_candidate_when_different():
        opt = _make_optimizer_with_operator("old skill")
        param = next(iter(opt._parameters.values()))
        param.set_gradient(SKILL_CONTENT_TARGET, "new skill")
        opt._epoch_base_skill_content = "old skill"

        result = opt._step()
        assert len(result) == 2  # base + candidate

    @staticmethod
    def test_only_base_when_same():
        """R3: base == candidate -> only return base."""
        opt = _make_optimizer_with_operator("same skill")
        param = next(iter(opt._parameters.values()))
        param.set_gradient(SKILL_CONTENT_TARGET, "same skill")
        opt._epoch_base_skill_content = "same skill"

        result = opt._step()
        assert len(result) == 1  # only base

    @staticmethod
    def test_base_contains_original_skill():
        opt = _make_optimizer_with_operator("original")
        param = next(iter(opt._parameters.values()))
        param.set_gradient(SKILL_CONTENT_TARGET, "modified")
        opt._epoch_base_skill_content = "original"

        result = opt._step()
        op_id = opt._operators[next(iter(opt._operators))].operator_id
        base = result[0]
        assert (op_id, SKILL_CONTENT_TARGET) in base
        assert base[(op_id, SKILL_CONTENT_TARGET)] == "original"

    @staticmethod
    def test_candidate_contains_new_skill():
        opt = _make_optimizer_with_operator("original")
        param = next(iter(opt._parameters.values()))
        param.set_gradient(SKILL_CONTENT_TARGET, "modified")
        opt._epoch_base_skill_content = "original"

        result = opt._step()
        op_id = opt._operators[next(iter(opt._operators))].operator_id
        candidate = result[1]
        assert (op_id, SKILL_CONTENT_TARGET) in candidate
        assert candidate[(op_id, SKILL_CONTENT_TARGET)] == "modified"

    @staticmethod
    def test_no_gradient_returns_empty():
        opt = _make_optimizer_with_operator("skill")
        # No gradient set
        opt._epoch_base_skill_content = "skill"
        result = opt._step()
        assert len(result) == 0


class TestSkillSync:
    @staticmethod
    def test_read_skill_from_operator():
        opt = _make_optimizer_with_operator("my skill content")
        content = opt._read_skill_from_operator()
        assert content == "my skill content"

    @staticmethod
    def test_sync_skill_to_operator():
        opt = _make_optimizer_with_operator("initial")
        op = next(iter(opt._operators.values()))
        cb = MagicMock()
        op._on_parameter_updated = cb

        opt._sync_skill_to_operator("updated skill")
        cb.assert_called_once_with(SKILL_CONTENT_TARGET, "updated skill")
        assert op.get_state()["skill_content"] == "updated skill"
