# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for skill_document types and protocol constants."""

import pytest

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.skill_document.types import (
    AttributedBatch,
    Edit,
    EditOp,
    Patch,
    RawPatch,
    SlowUpdateResult,
)
from openjiuwen.agent_evolving.trajectory.types import Trajectory
from openjiuwen.agent_evolving.protocols import (
    SKILL_CONTENT_TARGET,
    SKILL_DOCUMENT_DOMAIN,
)


class TestEditOp:
    """EditOp literal restricts to 4 valid values."""

    @staticmethod
    def test_valid_ops():
        valid: list[EditOp] = ["append", "insert_after", "replace", "delete"]
        assert len(valid) == 4


class TestEdit:
    """Edit frozen dataclass."""

    @staticmethod
    def test_frozen():
        e = Edit(op="append", content="new section")
        with pytest.raises(AttributeError):
            setattr(e, "op", "replace")

    @staticmethod
    def test_defaults():
        e = Edit(op="append", content="x")
        assert e.target == ""
        assert e.support_count == 0
        assert e.source_type == "failure"

    @staticmethod
    def test_all_fields():
        e = Edit(op="replace", content="new", target="old", support_count=3, source_type="success")
        assert e.op == "replace"
        assert e.content == "new"
        assert e.target == "old"
        assert e.support_count == 3
        assert e.source_type == "success"


class TestPatch:
    """Patch frozen dataclass."""

    @staticmethod
    def test_frozen():
        p = Patch(edits=[])
        with pytest.raises(AttributeError):
            setattr(p, "edits", [Edit(op="append", content="x")])

    @staticmethod
    def test_defaults():
        p = Patch(edits=[])
        assert p.edits == []
        assert p.reasoning == ""

    @staticmethod
    def test_with_edits():
        edits = [Edit(op="append", content="a"), Edit(op="delete", content="", target="old")]
        p = Patch(edits=edits, reasoning="cleanup")
        assert len(p.edits) == 2
        assert p.reasoning == "cleanup"


class TestRawPatch:
    """RawPatch frozen dataclass."""

    @staticmethod
    def test_frozen():
        rp = RawPatch(patch=Patch(edits=[]), source_type="failure")
        with pytest.raises(AttributeError):
            setattr(rp, "source_type", "success")

    @staticmethod
    def test_defaults():
        rp = RawPatch(patch=Patch(edits=[]), source_type="failure")
        assert rp.batch_size == 0
        assert rp.failure_summary == ""

    @staticmethod
    def test_operator_id_default():
        rp = RawPatch(patch=Patch(edits=[]), source_type="failure")
        assert rp.operator_id == ""

    @staticmethod
    def test_operator_id_set():
        rp = RawPatch(patch=Patch(edits=[]), source_type="failure", operator_id="op_a")
        assert rp.operator_id == "op_a"

    @staticmethod
    def test_operator_id_frozen():
        rp = RawPatch(patch=Patch(edits=[]), source_type="failure", operator_id="op_a")
        with pytest.raises(AttributeError):
            setattr(rp, "operator_id", "op_b")

    @staticmethod
    def test_all_fields():
        p = Patch(edits=[Edit(op="append", content="x")], reasoning="r")
        rp = RawPatch(patch=p, source_type="success", batch_size=5, failure_summary="none")
        assert rp.patch is p
        assert rp.source_type == "success"
        assert rp.batch_size == 5
        assert rp.failure_summary == "none"


class TestAttributedBatch:
    """AttributedBatch frozen dataclass."""

    @staticmethod
    def test_construction():
        case = Case(inputs={"q": "a"}, label={"a": "b"})
        ec = EvaluatedCase(case=case)
        traj = Trajectory(execution_id="exec-1", steps=[])
        batch = AttributedBatch(
            operator_id="op_a",
            failures=[(traj, ec, case)],
            successes=[],
        )
        assert batch.operator_id == "op_a"
        assert len(batch.failures) == 1
        assert batch.successes == []

    @staticmethod
    def test_frozen():
        batch = AttributedBatch(operator_id="op_a", failures=[], successes=[])
        with pytest.raises(AttributeError):
            setattr(batch, "operator_id", "op_b")

    @staticmethod
    def test_empty_batches():
        batch = AttributedBatch(operator_id="op_x", failures=[], successes=[])
        assert batch.operator_id == "op_x"
        assert batch.failures == []
        assert batch.successes == []


class TestSlowUpdateResult:
    """SlowUpdateResult frozen dataclass."""

    @staticmethod
    def test_frozen():
        s = SlowUpdateResult(reasoning="r", slow_update_content="c", action="update")
        with pytest.raises(AttributeError):
            setattr(s, "action", "skip")

    @staticmethod
    def test_fields():
        s = SlowUpdateResult(reasoning="improve", slow_update_content="guidance", action="update")
        assert s.reasoning == "improve"
        assert s.slow_update_content == "guidance"
        assert s.action == "update"

    @staticmethod
    def test_skip_action():
        s = SlowUpdateResult(reasoning="no change needed", slow_update_content="", action="skip")
        assert s.action == "skip"


class TestProtocolConstants:
    """Protocol constants for skill document domain."""

    @staticmethod
    def test_skill_document_domain():
        assert SKILL_DOCUMENT_DOMAIN == "skill_document"

    @staticmethod
    def test_skill_content_target():
        assert SKILL_CONTENT_TARGET == "skill_content"
