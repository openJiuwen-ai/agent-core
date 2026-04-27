# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for TrajectoryBuilder with member_id support."""

from __future__ import annotations

from openjiuwen.agent_evolving.trajectory.builder import TrajectoryBuilder
from openjiuwen.agent_evolving.trajectory.types import (
    ToolCallDetail,
    TrajectoryStep,
)


class TestTrajectoryBuilderMemberId:
    """Tests for TrajectoryBuilder member_id support."""

    def test_build_with_member_id(self):
        """Builder with member_id sets meta['member_id'] on trajectory."""
        builder = TrajectoryBuilder(
            session_id="test-session",
            source="online",
            member_id="agent-001",
        )
        builder.record_step(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(tool_name="read_file", call_args="test.txt"),
            )
        )
        traj = builder.build()
        assert traj.meta.get("member_id") == "agent-001"

    def test_build_without_member_id(self):
        """Builder without member_id produces empty meta dict."""
        builder = TrajectoryBuilder(
            session_id="test-session",
            source="online",
        )
        traj = builder.build()
        assert traj.meta == {}

    def test_member_id_is_stored_on_builder(self):
        """member_id is accessible via builder.member_id."""
        builder = TrajectoryBuilder(
            session_id="test-session",
            source="online",
            member_id="leader-1",
        )
        assert builder.member_id == "leader-1"

    def test_member_id_none_default(self):
        """Default member_id is None."""
        builder = TrajectoryBuilder(
            session_id="test-session",
            source="online",
        )
        assert builder.member_id is None
