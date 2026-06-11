# coding: utf-8
"""Tests for RawPatch operator_id routing validation."""

import logging
from unittest.mock import MagicMock

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.optimizer.skill_document.skill_document_optimizer import (
    SkillDocumentOptimizer,
)
from openjiuwen.agent_evolving.optimizer.skill_document.types import Patch, RawPatch
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


def _patch(operator_id: str = "") -> RawPatch:
    return RawPatch(patch=Patch(edits=[]), source_type="failure", operator_id=operator_id)


class TestSingleOperatorAutoFill:
    @staticmethod
    def test_auto_fills_empty_operator_id():
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        patches = [_patch(""), _patch("")]

        result = opt._validate_raw_patch_operator_id(patches, {op_id})
        assert len(result) == 2
        for p in result:
            assert p.operator_id == op_id

    @staticmethod
    def test_preserves_existing_operator_id():
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        patches = [_patch(op_id)]

        result = opt._validate_raw_patch_operator_id(patches, {op_id})
        assert len(result) == 1
        assert result[0].operator_id == op_id

    @staticmethod
    def test_discards_unknown_operator_id(caplog):
        opt = _make_optimizer(n_operators=1)
        op_id = next(iter(opt._operators))
        patches = [_patch("unknown_op")]

        with caplog.at_level(logging.WARNING):
            result = opt._validate_raw_patch_operator_id(patches, {op_id})

        assert result == []
        assert "unknown operator_id" in caplog.text


class TestMultiOperatorRouting:
    @staticmethod
    def test_discards_empty_operator_id(caplog):
        opt = _make_optimizer(n_operators=2)
        valid_ids = set(opt._operators.keys())
        patches = [_patch(""), _patch(next(iter(valid_ids)))]

        with caplog.at_level(logging.WARNING):
            result = opt._validate_raw_patch_operator_id(patches, valid_ids)

        assert len(result) == 1
        assert "empty operator_id" in caplog.text

    @staticmethod
    def test_discards_unknown_operator_id(caplog):
        opt = _make_optimizer(n_operators=2)
        valid_ids = set(opt._operators.keys())
        patches = [_patch("unknown_op"), _patch(next(iter(valid_ids)))]

        with caplog.at_level(logging.WARNING):
            result = opt._validate_raw_patch_operator_id(patches, valid_ids)

        assert len(result) == 1
        assert "unknown operator_id" in caplog.text

    @staticmethod
    def test_keeps_valid_patches():
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        patches = [_patch(op_ids[0]), _patch(op_ids[1])]

        result = opt._validate_raw_patch_operator_id(patches, set(op_ids))
        assert len(result) == 2

    @staticmethod
    def test_no_broadcast_apply():
        """Operator A's patch is valid only for operator A, not operator B."""
        opt = _make_optimizer(n_operators=2)
        op_ids = list(opt._operators.keys())
        # Patch tagged for operator A
        patches = [_patch(op_ids[0])]

        # Validate against both operators — patch should be kept
        result_all = opt._validate_raw_patch_operator_id(patches, set(op_ids))
        assert len(result_all) == 1

        # But when we filter patches for operator B's aggregate,
        # only patches with operator_id == B should be used
        b_patches = [p for p in result_all if p.operator_id == op_ids[1]]
        assert len(b_patches) == 0
