# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for Case and EvaluatedCase data models."""

import pytest
from pydantic import ValidationError

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.core.foundation.tool import ToolInfo


def make_case(inputs=None, label=None, **kwargs):
    """Factory for creating test Case instances."""
    return Case(inputs=inputs or {"query": "test"}, label=label or {"answer": "expected"}, **kwargs)


class TestCase:
    """Test Case model."""

    @classmethod
    def test_minimal_creation(cls):
        """Create case with minimal required fields."""
        case = make_case()
        assert case.inputs == {"query": "test"}
        assert case.label == {"answer": "expected"}
        assert case.case_id is not None
        assert case.tools is None

    @classmethod
    def test_case_id_auto_generated(cls):
        """Auto-generate case_id when not provided."""
        case = make_case()
        # uuid.uuid4().hex generates 32 character string
        assert len(case.case_id) == 32

    @classmethod
    def test_case_id_custom(cls):
        """Support custom case_id."""
        case = make_case(case_id="custom_id")
        assert case.case_id == "custom_id"

    @classmethod
    def test_tools_optional(cls):
        """tools field is optional."""
        case = make_case()
        assert case.tools is None

    @classmethod
    def test_inputs_validation(cls):
        """inputs must have at least one key."""
        with pytest.raises(ValidationError):
            Case(inputs={}, label={"a": "b"})

    @classmethod
    def test_label_validation(cls):
        """label must have at least one key."""
        with pytest.raises(ValidationError):
            Case(inputs={"q": "a"}, label={})

    @classmethod
    def test_serialization(cls):
        """Test model serialization."""
        case = make_case(inputs={"q": "test"}, case_id="test_id")
        data = case.model_dump()
        assert data["inputs"] == {"q": "test"}
        assert data["label"] == {"answer": "expected"}
        assert data["case_id"] == "test_id"

    @classmethod
    def test_json_serialization(cls):
        """Test JSON serialization."""
        case = make_case(inputs={"q": "test"})
        json_str = case.model_dump_json()
        assert "test" in json_str


class TestEvaluatedCase:
    """Test EvaluatedCase model."""

    @classmethod
    def test_full_creation(cls):
        """Create evaluated case with all fields."""
        case = make_case()
        ec = EvaluatedCase(case=case, answer={"output": "result"}, score=0.85, reason="mostly correct")
        assert ec.case is case
        assert ec.answer == {"output": "result"}
        assert ec.score == 0.85
        assert ec.reason == "mostly correct"
        assert ec.per_metric is None

    @classmethod
    def test_default_values(cls):
        """Test default values."""
        case = make_case()
        ec = EvaluatedCase(case=case)
        assert ec.answer is None
        assert ec.score == 0.0
        assert ec.reason == ""
        assert ec.per_metric is None

    @classmethod
    def test_score_upper_bound(cls):
        """score clamped to maximum 1.0."""
        case = make_case()
        ec = EvaluatedCase(case=case, score=1.5)
        assert ec.score == 1.0

    @classmethod
    def test_score_lower_bound(cls):
        """score clamped to minimum 0.0."""
        case = make_case()
        ec = EvaluatedCase(case=case, score=-0.5)
        assert ec.score == 0.0

    @classmethod
    def test_property_delegation(cls):
        """Properties delegate to underlying case."""
        case = make_case(inputs={"q": "test"}, label={"a": "ans"}, case_id="id123")
        ec = EvaluatedCase(case=case)
        assert ec.inputs == {"q": "test"}
        assert ec.label == {"a": "ans"}
        assert ec.case_id == "id123"

    @classmethod
    def test_tools_delegation(cls):
        """Tools property delegates correctly."""
        mock_tool = ToolInfo(name="test_tool", description="A test tool")
        case = make_case(tools=[mock_tool])
        ec = EvaluatedCase(case=case)
        assert ec.tools == [mock_tool]

    @classmethod
    def test_per_metric_storage(cls):
        """per_metric stores per-metric scores."""
        case = make_case()
        ec = EvaluatedCase(case=case, per_metric={"exact_match": 1.0, "llm_judge": 0.8})
        assert ec.per_metric == {"exact_match": 1.0, "llm_judge": 0.8}

    @classmethod
    def test_model_dump_includes_all_fields(cls):
        """Test serialization includes all fields."""
        case = make_case(case_id="id1")
        ec = EvaluatedCase(case=case, answer={"out": "x"}, score=0.9, reason="good", per_metric={"metric": 0.9})
        data = ec.model_dump()
        assert data["score"] == 0.9
        assert data["answer"] == {"out": "x"}
        assert data["reason"] == "good"
        assert data["per_metric"] == {"metric": 0.9}
