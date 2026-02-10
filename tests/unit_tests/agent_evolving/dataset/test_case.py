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

    @staticmethod
    def test_minimal_creation():
        """Create case with minimal required fields."""
        case = make_case()
        assert case.inputs == {"query": "test"}
        assert case.label == {"answer": "expected"}
        assert case.case_id is not None
        assert case.tools is None

    @staticmethod
    def test_case_id_auto_generated():
        """Auto-generate case_id when not provided."""
        case = make_case()
        # uuid.uuid4().hex generates 32 character string
        assert len(case.case_id) == 32

    @staticmethod
    def test_case_id_custom():
        """Support custom case_id."""
        case = make_case(case_id="custom_id")
        assert case.case_id == "custom_id"

    @staticmethod
    def test_tools_optional():
        """tools field is optional."""
        case = make_case()
        assert case.tools is None

    @staticmethod
    def test_inputs_validation():
        """inputs must have at least one key."""
        with pytest.raises(ValidationError):
            Case(inputs={}, label={"a": "b"})

    @staticmethod
    def test_label_validation():
        """label must have at least one key."""
        with pytest.raises(ValidationError):
            Case(inputs={"q": "a"}, label={})

    @staticmethod
    def test_serialization():
        """Test model serialization."""
        case = make_case(inputs={"q": "test"}, case_id="test_id")
        data = case.model_dump()
        assert data["inputs"] == {"q": "test"}
        assert data["label"] == {"answer": "expected"}
        assert data["case_id"] == "test_id"

    @staticmethod
    def test_json_serialization():
        """Test JSON serialization."""
        case = make_case(inputs={"q": "test"})
        json_str = case.model_dump_json()
        assert "test" in json_str


class TestEvaluatedCase:
    """Test EvaluatedCase model."""

    @staticmethod
    def test_full_creation():
        """Create evaluated case with all fields."""
        case = make_case()
        ec = EvaluatedCase(case=case, answer={"output": "result"}, score=0.85, reason="mostly correct")
        assert ec.case is case
        assert ec.answer == {"output": "result"}
        assert ec.score == 0.85
        assert ec.reason == "mostly correct"
        assert ec.per_metric is None

    @staticmethod
    def test_default_values():
        """Test default values."""
        case = make_case()
        ec = EvaluatedCase(case=case)
        assert ec.answer is None
        assert ec.score == 0.0
        assert ec.reason == ""
        assert ec.per_metric is None

    @staticmethod
    def test_score_upper_bound():
        """score clamped to maximum 1.0."""
        case = make_case()
        ec = EvaluatedCase(case=case, score=1.5)
        assert ec.score == 1.0

    @staticmethod
    def test_score_lower_bound():
        """score clamped to minimum 0.0."""
        case = make_case()
        ec = EvaluatedCase(case=case, score=-0.5)
        assert ec.score == 0.0

    @staticmethod
    def test_property_delegation():
        """Properties delegate to underlying case."""
        case = make_case(inputs={"q": "test"}, label={"a": "ans"}, case_id="id123")
        ec = EvaluatedCase(case=case)
        assert ec.inputs == {"q": "test"}
        assert ec.label == {"a": "ans"}
        assert ec.case_id == "id123"

    @staticmethod
    def test_tools_delegation():
        """Tools property delegates correctly."""
        mock_tool = ToolInfo(name="test_tool", description="A test tool")
        case = make_case(tools=[mock_tool])
        ec = EvaluatedCase(case=case)
        assert ec.tools == [mock_tool]

    @staticmethod
    def test_per_metric_storage():
        """per_metric stores per-metric scores."""
        case = make_case()
        ec = EvaluatedCase(case=case, per_metric={"exact_match": 1.0, "llm_judge": 0.8})
        assert ec.per_metric == {"exact_match": 1.0, "llm_judge": 0.8}

    @staticmethod
    def test_model_dump_includes_all_fields():
        """Test serialization includes all fields."""
        case = make_case(case_id="id1")
        ec = EvaluatedCase(case=case, answer={"out": "x"}, score=0.9, reason="good", per_metric={"metric": 0.9})
        data = ec.model_dump()
        assert data["score"] == 0.9
        assert data["answer"] == {"out": "x"}
        assert data["reason"] == "good"
        assert data["per_metric"] == {"metric": 0.9}
