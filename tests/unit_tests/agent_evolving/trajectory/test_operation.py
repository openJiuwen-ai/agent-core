# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for trajectory types and operations."""

import pytest

from openjiuwen.agent_evolving.trajectory.operation import get_steps_for_case_operator, iter_steps
from openjiuwen.agent_evolving.trajectory.types import (
    ExecutionSpec,
    Trajectory,
    TrajectoryStep,
    UpdateKey,
    Updates,
)


def make_step(kind="llm", op_id="op1", **kwargs):
    """Factory for creating TrajectoryStep instances."""
    defaults = dict(
        kind=kind,
        operator_id=op_id,
        agent_id=None,
        role=None,
        node_id=None,
        inputs=None,
        outputs=None,
        error=None,
        start_time_ms=None,
        end_time_ms=None,
        meta=None,
    )
    defaults.update(kwargs)
    if defaults["inputs"] is None:
        defaults["inputs"] = {}
    if defaults["outputs"] is None:
        defaults["outputs"] = {}
    if defaults["meta"] is None:
        defaults["meta"] = {}
    return TrajectoryStep(**defaults)


def make_trajectory(case_id="case1", steps=None, **kwargs):
    """Factory for creating Trajectory instances."""
    defaults = dict(
        case_id=case_id,
        execution_id="exec1",
        trace_id=None,
        steps=steps or [],
        edges=None,
    )
    defaults.update(kwargs)
    return Trajectory(**defaults)


def make_execution_spec(case_id="case1", exec_id="exec1", **kwargs):
    """Factory for creating ExecutionSpec instances."""
    defaults = dict(
        case_id=case_id,
        execution_id=exec_id,
        seed=None,
        tags=None,
    )
    defaults.update(kwargs)
    return ExecutionSpec(**defaults)


class TestExecutionSpec:
    """Test ExecutionSpec dataclass."""

    @staticmethod
    def test_minimal_creation():
        """Create with required fields."""
        spec = make_execution_spec()
        assert spec.case_id == "case1"
        assert spec.execution_id == "exec1"
        assert spec.seed is None
        assert spec.tags is None

    @staticmethod
    def test_full_creation():
        """Create with all fields."""
        spec = make_execution_spec(seed=42, tags={"key": "value"})
        assert spec.seed == 42
        assert spec.tags == {"key": "value"}


class TestTrajectoryStep:
    """Test TrajectoryStep dataclass."""

    @staticmethod
    def test_creation():
        """Create step with fields."""
        step = make_step(
            kind="llm",
            op_id="op1",
            inputs={"query": "hello"},
            outputs={"response": "world"},
        )
        assert step.kind == "llm"
        assert step.operator_id == "op1"
        assert step.inputs == {"query": "hello"}


class TestTrajectory:
    """Test Trajectory dataclass."""

    @staticmethod
    def test_minimal_creation():
        """Create with minimal fields."""
        step = make_step()
        traj = make_trajectory(case_id="case1", steps=[step])
        assert traj.case_id == "case1"
        assert len(traj.steps) == 1

    @staticmethod
    def test_creation_with_edges():
        """Create with edges."""
        step1 = make_step(kind="llm", op_id="op1")
        step2 = make_step(kind="tool", op_id="op2")
        traj = make_trajectory(
            case_id="case1",
            steps=[step1, step2],
            edges=[(0, 1)],
        )
        assert traj.edges == [(0, 1)]


class TestUpdateKey:
    """Test UpdateKey type alias."""

    @staticmethod
    def test_tuple_creation():
        """UpdateKey is a tuple."""
        key: UpdateKey = ("op1", "system_prompt")
        assert key == ("op1", "system_prompt")
        assert key[0] == "op1"
        assert key[1] == "system_prompt"


class TestUpdates:
    """Test Updates type alias."""

    @staticmethod
    def test_dict_creation():
        """Updates is a dict."""
        updates: Updates = {
            ("op1", "system_prompt"): "new prompt",
            ("op1", "user_prompt"): "new user",
        }
        assert ("op1", "system_prompt") in updates


class TestIterSteps:
    """Test iter_steps function."""

    @staticmethod
    def test_no_filter_returns_all():
        """No filter returns all steps."""
        step1 = make_step(kind="llm", op_id="op1")
        step2 = make_step(kind="tool", op_id="op2")
        traj = make_trajectory(steps=[step1, step2])
        result = list(iter_steps([traj]))
        assert len(result) == 2

    @staticmethod
    def test_filter_by_case_id():
        """Filter by case_id."""
        step = make_step()
        traj1 = make_trajectory(case_id="case1", steps=[step])
        traj2 = make_trajectory(case_id="case2", steps=[step])
        result = list(iter_steps([traj1, traj2], case_id="case1"))
        assert len(result) == 1
        # case_id is on Trajectory, not TrajectoryStep
        # Filter works correctly by returning steps from matching trajectory

    @staticmethod
    def test_filter_by_operator_id():
        """Filter by operator_id."""
        step1 = make_step(kind="llm", op_id="op1")
        step2 = make_step(kind="llm", op_id="op2")
        traj = make_trajectory(steps=[step1, step2])
        result = list(iter_steps([traj], operator_id="op1"))
        assert len(result) == 1
        assert result[0].operator_id == "op1"

    @staticmethod
    def test_filter_by_kind():
        """Filter by kind."""
        step1 = make_step(kind="llm", op_id="op1")
        step2 = make_step(kind="tool", op_id="op2")
        traj = make_trajectory(steps=[step1, step2])
        result = list(iter_steps([traj], kind="llm"))
        assert len(result) == 1
        assert result[0].kind == "llm"

    @staticmethod
    def test_combined_filters():
        """Combined filters."""
        step1 = make_step(kind="llm", op_id="op1")
        step2 = make_step(kind="tool", op_id="op1")
        traj = make_trajectory(steps=[step1, step2])
        result = list(iter_steps([traj], operator_id="op1", kind="llm"))
        assert len(result) == 1
        assert result[0].kind == "llm"

    @staticmethod
    def test_empty_trajectories():
        """Empty trajectories returns empty."""
        assert list(iter_steps([])) == []

    @staticmethod
    def test_no_matching_steps():
        """No steps match filter."""
        step = make_step(kind="llm", op_id="op1")
        traj = make_trajectory(steps=[step])
        result = list(iter_steps([traj], kind="tool"))
        assert result == []


class TestGetStepsForCaseOperator:
    """Test get_steps_for_case_operator function."""

    @staticmethod
    def test_default_kind_is_llm():
        """Default kind is llm."""
        step = make_step(kind="llm", op_id="op1")
        traj = make_trajectory(steps=[step])
        result = get_steps_for_case_operator([traj], "case1", "op1")
        assert len(result) == 1

    @staticmethod
    def test_custom_kind():
        """Custom kind."""
        step = make_step(kind="tool", op_id="op1")
        traj = make_trajectory(steps=[step])
        result = get_steps_for_case_operator([traj], "case1", "op1", kind="tool")
        assert len(result) == 1
        assert result[0].kind == "tool"
