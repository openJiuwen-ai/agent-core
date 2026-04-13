# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for TrajectoryBuilder - unified trajectory assembler."""

from openjiuwen.agent_evolving.trajectory.builder import TrajectoryBuilder
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    TrajectoryStep,
)


def make_step(kind="llm", detail=None, error=None):
    """Factory for creating TrajectoryStep instances."""
    return TrajectoryStep(
        kind=kind,
        error=error,
        detail=detail,
        meta={},
    )


class TestTrajectoryBuilder:
    """Test TrajectoryBuilder functionality."""

    @staticmethod
    def test_builder_initialization():
        """Initialize builder with required fields."""
        builder = TrajectoryBuilder(
            session_id="session_123",
            source="online",
            case_id="case_456",
        )
        assert builder.session_id == "session_123"
        assert builder.source == "online"
        assert builder.case_id == "case_456"
        assert builder.steps == []
        assert builder.cost == {"input_tokens": 0, "output_tokens": 0}

    @staticmethod
    def test_builder_without_case_id():
        """Builder works without optional case_id."""
        builder = TrajectoryBuilder(
            session_id="session_123",
            source="offline",
        )
        assert builder.case_id is None

    @staticmethod
    def test_record_single_step():
        """Record a single step."""
        builder = TrajectoryBuilder(session_id="s1", source="online")
        step = make_step(kind="llm")

        builder.record_step(step)

        assert len(builder.steps) == 1
        assert builder.steps[0].kind == "llm"

    @staticmethod
    def test_record_multiple_steps():
        """Record multiple steps (order preserved by list position)."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        step1 = make_step(kind="llm")
        step2 = make_step(kind="tool")
        step3 = make_step(kind="llm")

        builder.record_step(step1)
        builder.record_step(step2)
        builder.record_step(step3)

        assert len(builder.steps) == 3
        assert builder.steps[0].kind == "llm"
        assert builder.steps[1].kind == "tool"
        assert builder.steps[2].kind == "llm"

    @staticmethod
    def test_build_returns_trajectory():
        """Build returns a valid Trajectory."""
        builder = TrajectoryBuilder(
            session_id="session_123",
            source="online",
            case_id="case_456",
        )
        step = make_step(kind="llm")
        builder.record_step(step)

        trajectory = builder.build()

        assert trajectory.session_id == "session_123"
        assert trajectory.source == "online"
        assert trajectory.case_id == "case_456"
        assert len(trajectory.steps) == 1
        assert trajectory.execution_id is not None

    @staticmethod
    def test_build_with_empty_steps():
        """Build with no steps returns empty trajectory."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        trajectory = builder.build()

        assert trajectory.steps == []
        assert trajectory.cost is None

    @staticmethod
    def test_cost_accumulation_from_llm_detail():
        """Cost accumulates from LLM steps with usage info."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        detail = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        step = make_step(kind="llm", detail=detail)
        builder.record_step(step)

        trajectory = builder.build()

        assert trajectory.cost == {"input_tokens": 10, "output_tokens": 5}

    @staticmethod
    def test_cost_accumulation_multiple_llm_steps():
        """Cost accumulates across multiple LLM steps."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        detail1 = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        detail2 = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hello"}],
            usage={"prompt_tokens": 20, "completion_tokens": 10},
        )

        builder.record_step(make_step(kind="llm", detail=detail1))
        builder.record_step(make_step(kind="tool"))
        builder.record_step(make_step(kind="llm", detail=detail2))

        trajectory = builder.build()

        assert trajectory.cost == {"input_tokens": 30, "output_tokens": 15}

    @staticmethod
    def test_cost_not_accumulated_for_tool_steps():
        """Tool steps do not contribute to cost."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        detail = ToolCallDetail(
            tool_name="test_tool",
            tool_description="A test tool",
        )
        step = make_step(kind="tool", detail=detail)
        builder.record_step(step)

        trajectory = builder.build()

        assert trajectory.cost is None

    @staticmethod
    def test_cost_not_accumulated_without_usage():
        """LLM steps without usage info do not contribute to cost."""
        builder = TrajectoryBuilder(session_id="s1", source="online")

        detail = LLMCallDetail(
            model="gpt-4",
            messages=[{"role": "user", "content": "hi"}],
            usage=None,
        )
        step = make_step(kind="llm", detail=detail)
        builder.record_step(step)

        trajectory = builder.build()

        assert trajectory.cost is None

    @staticmethod
    def test_different_sources():
        """Builder works with different source values."""
        online_builder = TrajectoryBuilder(
            session_id="s1", source="online"
        )
        offline_builder = TrajectoryBuilder(
            session_id="s2", source="offline"
        )

        online_traj = online_builder.build()
        offline_traj = offline_builder.build()

        assert online_traj.source == "online"
        assert offline_traj.source == "offline"
