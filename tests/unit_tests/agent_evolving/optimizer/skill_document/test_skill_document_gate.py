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


class TestPerOperatorSkillIO:
    """Tests for _read_skills_from_operators / _sync_skill_to_operator_by_id / _sync_skills_to_operators."""

    @staticmethod
    def test_read_skills_from_operators_single():
        opt = _make_optimizer_with_operator("skill A")
        result = opt._read_skills_from_operators()
        assert isinstance(result, dict)
        assert len(result) == 1
        assert next(iter(result.values())) == "skill A"

    @staticmethod
    def test_read_skills_from_operators_multiple():
        opt = SkillDocumentOptimizer(
            agent=MagicMock(),
            evaluator=MagicMock(),
            llm=MagicMock(),
            model="test-model",
            train_cases=CaseLoader(cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]),
        )
        op_a = SkillDocumentOperator("skill_a", initial_content="content A")
        op_b = SkillDocumentOperator("skill_b", initial_content="content B")
        opt.bind(operators={op_a.operator_id: op_a, op_b.operator_id: op_b})

        result = opt._read_skills_from_operators()
        assert len(result) == 2
        assert result[op_a.operator_id] == "content A"
        assert result[op_b.operator_id] == "content B"

    @staticmethod
    def test_sync_skill_to_operator_by_id():
        opt = SkillDocumentOptimizer(
            agent=MagicMock(),
            evaluator=MagicMock(),
            llm=MagicMock(),
            model="test-model",
            train_cases=CaseLoader(cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]),
        )
        op_a = SkillDocumentOperator("skill_a", initial_content="old A")
        op_b = SkillDocumentOperator("skill_b", initial_content="old B")
        opt.bind(operators={op_a.operator_id: op_a, op_b.operator_id: op_b})

        opt._sync_skill_to_operator_by_id(op_a.operator_id, "new A")
        assert op_a.get_state()["skill_content"] == "new A"
        assert op_b.get_state()["skill_content"] == "old B"

    @staticmethod
    def test_sync_skills_to_operators():
        opt = SkillDocumentOptimizer(
            agent=MagicMock(),
            evaluator=MagicMock(),
            llm=MagicMock(),
            model="test-model",
            train_cases=CaseLoader(cases=[
                Case(inputs={"q": "x"}, label={"a": "y"}, case_id="c0"),
            ]),
        )
        op_a = SkillDocumentOperator("skill_a", initial_content="old A")
        op_b = SkillDocumentOperator("skill_b", initial_content="old B")
        opt.bind(operators={op_a.operator_id: op_a, op_b.operator_id: op_b})

        opt._sync_skills_to_operators({
            op_a.operator_id: "batch A",
            op_b.operator_id: "batch B",
        })
        assert op_a.get_state()["skill_content"] == "batch A"
        assert op_b.get_state()["skill_content"] == "batch B"


class TestPerOperatorStateInit:
    """Verify per-operator dict fields are initialized in __init__."""

    @staticmethod
    def test_dict_fields_initialized():
        opt = _make_optimizer_with_operator()
        assert isinstance(opt._current_skill_by_operator, dict)
        assert isinstance(opt._epoch_base_skill_by_operator, dict)
        assert isinstance(opt._last_candidate_skill_by_operator, dict)
        assert isinstance(opt._ranked_patch_by_operator, dict)


class TestInferGateDecision:
    """Tests for _infer_gate_decision per-operator dict comparison."""

    @staticmethod
    def _make_multi_op_optimizer() -> SkillDocumentOptimizer:
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

    @staticmethod
    def test_all_operators_match_base_returns_base():
        opt = TestInferGateDecision._make_multi_op_optimizer()
        op_ids = list(opt._operators.keys())
        opt._epoch_base_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        opt._current_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        opt._last_candidate_skill_by_operator = {op_ids[0]: "cand A", op_ids[1]: "cand B"}
        assert opt._infer_gate_decision() == "base"

    @staticmethod
    def test_all_operators_match_candidate_returns_candidate():
        opt = TestInferGateDecision._make_multi_op_optimizer()
        op_ids = list(opt._operators.keys())
        opt._epoch_base_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        opt._current_skill_by_operator = {op_ids[0]: "cand A", op_ids[1]: "cand B"}
        opt._last_candidate_skill_by_operator = {op_ids[0]: "cand A", op_ids[1]: "cand B"}
        assert opt._infer_gate_decision() == "candidate"

    @staticmethod
    def test_mixed_base_and_candidate_returns_unknown():
        opt = TestInferGateDecision._make_multi_op_optimizer()
        op_ids = list(opt._operators.keys())
        opt._epoch_base_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "base B"}
        # One operator reverted to base, other kept candidate
        opt._current_skill_by_operator = {op_ids[0]: "base A", op_ids[1]: "cand B"}
        opt._last_candidate_skill_by_operator = {op_ids[0]: "cand A", op_ids[1]: "cand B"}
        assert opt._infer_gate_decision() == "unknown"

    @staticmethod
    def test_fallback_to_legacy_when_dict_empty():
        """When _epoch_base_skill_by_operator is empty, fall back to legacy string comparison."""
        opt = _make_optimizer_with_operator("my skill")
        opt._epoch_base_skill_by_operator = {}
        opt._epoch_base_skill_content = "my skill"
        opt._current_skill_content = "my skill"
        assert opt._infer_gate_decision() == "base"

    @staticmethod
    def test_fallback_candidate_match():
        opt = _make_optimizer_with_operator("original")
        opt._epoch_base_skill_by_operator = {}
        opt._epoch_base_skill_content = "original"
        opt._last_candidate_skill_content = "improved"
        opt._current_skill_content = "improved"
        assert opt._infer_gate_decision() == "candidate"

    @staticmethod
    def test_fallback_no_match_returns_unknown():
        opt = _make_optimizer_with_operator("something else")
        opt._epoch_base_skill_by_operator = {}
        opt._epoch_base_skill_content = "original"
        opt._last_candidate_skill_content = "improved"
        opt._current_skill_content = "something else"
        assert opt._infer_gate_decision() == "unknown"
