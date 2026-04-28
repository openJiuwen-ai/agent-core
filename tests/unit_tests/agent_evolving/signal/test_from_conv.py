# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ConversationSignalDetector."""

from typing import List

from openjiuwen.agent_evolving.signal.base import EvolutionCategory, make_signal_fingerprint
from openjiuwen.agent_evolving.signal.from_conv import ConversationSignalDetector
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)


def _build_trajectory_from_messages(messages: List[dict]) -> Trajectory:
    """Convert message list to Trajectory for testing.

    Creates Trajectory with LLM steps containing messages and tool steps
    containing tool results.
    """
    steps: List[TrajectoryStep] = []
    tool_call_id_to_result: dict = {}

    # First pass: collect tool call IDs and results
    for msg in messages:
        role = msg.get("role", "")
        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            if tool_call_id:
                tool_call_id_to_result[tool_call_id] = msg

    # Build steps
    llm_messages: List[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role in ("user", "assistant", "system"):
            llm_messages.append(msg)
            if role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id and tc_id in tool_call_id_to_result:
                        result_msg = tool_call_id_to_result[tc_id]
                        tool_step = TrajectoryStep(
                            kind="tool",
                            detail=ToolCallDetail(
                                tool_name=tc.get("name", ""),
                                call_result=result_msg.get("content", ""),
                                tool_call_id=tc_id,
                            ),
                        )
                        steps.append(tool_step)

    if llm_messages:
        steps.insert(0, TrajectoryStep(
            kind="llm",
            detail=LLMCallDetail(model="test-model", messages=llm_messages),
        ))

    return Trajectory(execution_id="test-exec", steps=steps)


def _build_team_member_trajectory(
    member_id: str,
    tool_name: str,
    tool_args: dict,
    tool_result: str = "",
    meta: dict = None,
) -> Trajectory:
    """Build a Trajectory with team member context for collaboration signal testing."""
    steps = [
        TrajectoryStep(
            kind="tool",
            detail=ToolCallDetail(
                tool_name=tool_name,
                call_args=tool_args,
                call_result=tool_result,
            ),
            meta=meta or {},
        ),
    ]
    return Trajectory(
        execution_id=f"exec-{member_id}",
        session_id="session-team",
        source="online",
        steps=steps,
        meta={"member_id": member_id, "team_id": "team-1"},
    )


class TestConversationSignalDetector:
    """Tests for ConversationSignalDetector.detect(Trajectory)."""

    def test_empty_trajectory_returns_empty_signals(self) -> None:
        """Empty trajectory should return empty signal list."""
        detector = ConversationSignalDetector()
        trajectory = Trajectory(execution_id="test", steps=[])
        signals = detector.detect(trajectory)
        assert signals == []

    def test_execution_failure_signal(self) -> None:
        """Tool result with failure keywords should produce execution_failure signal."""
        messages = [
            {"role": "user", "content": "Run the code"},
            {
                "role": "assistant",
                "content": "I'll run it",
                "tool_calls": [
                    {"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "name": "bash",
                "content": "Error: command failed with exit code 1",
            },
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.signal_type == "execution_failure"
        assert signal.evolution_type == EvolutionCategory.SKILL_EXPERIENCE
        assert "failed" in signal.excerpt.lower()

    def test_user_correction_signal(self) -> None:
        """User message with correction keywords should produce user_correction signal."""
        messages = [
            {"role": "user", "content": "Use the read_file tool"},
            {
                "role": "assistant",
                "content": "I'll read the file",
                "tool_calls": [
                    {"id": "tc_1", "name": "read_file", "type": "function", "arguments": "{}"}
                ],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        # Should have user_correction signal
        correction_signals = [s for s in signals if s.signal_type == "user_correction"]
        assert len(correction_signals) == 1
        signal = correction_signals[0]
        assert signal.signal_type == "user_correction"
        assert signal.section == "Examples"

    def test_script_artifact_signal(self) -> None:
        """Successful code execution should produce script_artifact signal."""
        messages = [
            {"role": "user", "content": "Write a script"},
            {
                "role": "assistant",
                "content": "Here's a script",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "name": "python_exec",
                        "type": "function",
                        "arguments": '{"code": "print(\'hello world\')\\nfor i in range(10): print(i)"}',
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "name": "python_exec",
                "content": "hello world\n0\n1\n2\n...",
            },
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        script_signals = [s for s in signals if s.signal_type == "script_artifact"]
        assert len(script_signals) == 1
        signal = script_signals[0]
        assert signal.signal_type == "script_artifact"
        assert signal.section == "Scripts"

    def test_fingerprint_consistency_with_signal_detector(self) -> None:
        """ConversationSignalDetector signals should match SignalDetector fingerprints."""
        messages = [
            {"role": "user", "content": "Run the code"},
            {
                "role": "assistant",
                "content": "I'll run it",
                "tool_calls": [
                    {"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "name": "bash",
                "content": "Error: command failed",
            },
        ]

        # SignalDetector (alias for ConversationSignalDetector)
        detector = ConversationSignalDetector()
        # Test both input types: List[dict] and Trajectory
        signals_from_messages = detector.detect(messages)
        trajectory = _build_trajectory_from_messages(messages)
        signals_from_trajectory = detector.detect(trajectory)

        # Both should produce same fingerprints
        fingerprints_from_messages = [make_signal_fingerprint(s) for s in signals_from_messages]
        fingerprints_from_trajectory = [make_signal_fingerprint(s) for s in signals_from_trajectory]

        fingerprints_from_messages.sort()
        fingerprints_from_trajectory.sort()

        assert fingerprints_from_messages == fingerprints_from_trajectory

    def test_signal_deduplication(self) -> None:
        """Multiple similar failures should be deduplicated."""
        messages = [
            {"role": "user", "content": "Run multiple commands"},
            {
                "role": "assistant",
                "content": "Running...",
                "tool_calls": [
                    {"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"},
                    {"id": "tc_2", "name": "bash", "type": "function", "arguments": "{}"},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "name": "bash",
                "content": "Error: command failed with exit code 1",
            },
            {
                "role": "tool",
                "tool_call_id": "tc_2",
                "name": "bash",
                "content": "Error: command failed with exit code 1",
            },
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        # Should deduplicate to 1 signal (same type, tool_name, excerpt)
        failure_signals = [s for s in signals if s.signal_type == "execution_failure"]
        assert len(failure_signals) == 1

    def test_existing_skills_filter(self) -> None:
        """Detector with existing_skills should resolve skill_name correctly."""
        messages = [
            {"role": "user", "content": "Read SKILL.md"},
            {
                "role": "assistant",
                "content": "Reading...",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "name": "read_file",
                        "type": "function",
                        "arguments": '{"path": "/skills/my_skill/SKILL.md"}',
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "tc_1",
                "name": "read_file",
                "content": "# My Skill\\n...",
            },
            {"role": "user", "content": "不对，应该用另一个方法"},
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector(existing_skills={"my_skill"})
        signals = detector.detect(trajectory)

        # Should have user_correction signal with skill_name="my_skill"
        correction_signals = [s for s in signals if s.signal_type == "user_correction"]
        assert len(correction_signals) == 1
        assert correction_signals[0].skill_name == "my_skill"


class TestCollaborationSignalDetection:
    """Tests for collaboration signal detection in team member context.

    Collaboration signals are detected when AgentSkill acts as TeamSkill member:
    - collaboration_send: send_message to other member
    - collaboration_claim: claim_task by teammate
    - collaboration_view: view_task status check
    - collaboration_receive: receive context from parent_invoke_id
    - collaboration_failure: collaboration-related error/timeout
    """

    def test_collaboration_send_signal(self) -> None:
        """send_message to other member should produce collaboration_send signal."""
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="send_message",
            tool_args={"to_member_name": "coder", "message": "请完成数据分析"},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration_send"]
        assert len(collab_signals) == 1
        signal = collab_signals[0]
        assert signal.section == "Collaboration"
        assert signal.evolution_type == EvolutionCategory.SKILL_EXPERIENCE
        assert signal.context.get("from_member") == "researcher"
        assert signal.context.get("to_member") == "coder"

    def test_collaboration_claim_signal(self) -> None:
        """claim_task should produce collaboration_claim signal."""
        trajectory = _build_team_member_trajectory(
            member_id="coder",
            tool_name="claim_task",
            tool_args={"task_id": "task-123"},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration_claim"]
        assert len(collab_signals) == 1
        signal = collab_signals[0]
        assert signal.section == "Collaboration"
        assert signal.context.get("member_id") == "coder"
        assert signal.context.get("task_id") == "task-123"

    def test_collaboration_view_signal(self) -> None:
        """view_task should produce collaboration_view signal."""
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="view_task",
            tool_args={},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration_view"]
        assert len(collab_signals) == 1
        signal = collab_signals[0]
        assert signal.section == "Collaboration"
        assert signal.context.get("member_id") == "researcher"

    def test_collaboration_receive_signal(self) -> None:
        """Step with parent_invoke_id should produce collaboration_receive signal."""
        trajectory = _build_team_member_trajectory(
            member_id="coder",
            tool_name="write_file",
            tool_args={"path": "output.py"},
            meta={"parent_invoke_id": "invoke-researcher-001"},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration_receive"]
        assert len(collab_signals) == 1
        signal = collab_signals[0]
        assert signal.section == "Collaboration"
        assert signal.context.get("member_id") == "coder"
        assert signal.context.get("parent_invoke_id") == "invoke-researcher-001"

    def test_collaboration_failure_signal(self) -> None:
        """Collaboration-related error should produce collaboration_failure signal."""
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="send_message",
            tool_args={"to_member_name": "coder"},
            tool_result="Error: member coder failed to respond - timeout",
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration_failure"]
        assert len(collab_signals) == 1
        signal = collab_signals[0]
        assert signal.section == "Collaboration"
        assert "timeout" in signal.excerpt.lower()

    def test_no_collaboration_signals_for_standalone_agent(self) -> None:
        """Standalone agent (no member_id) should not produce collaboration signals."""
        # Standalone agent trajectory (no member_id, source=standalone)
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="send_message",
                    call_args={"to_member_name": "other"},
                ),
            ),
        ]
        trajectory = Trajectory(
            execution_id="standalone-exec",
            session_id="session-1",
            source="standalone",
            steps=steps,
            meta={},  # No member_id
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type.startswith("collaboration_")]
        assert len(collab_signals) == 0

    def test_no_collaboration_signals_for_non_collaborative_tools(self) -> None:
        """Internal tools (bash, python) should not produce collaboration signals."""
        trajectory = _build_team_member_trajectory(
            member_id="coder",
            tool_name="bash",
            tool_args={"command": "python script.py"},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type.startswith("collaboration_")]
        assert len(collab_signals) == 0

    def test_multiple_collaboration_signals_from_single_trajectory(self) -> None:
        """Trajectory with multiple collaboration actions should produce multiple signals."""
        steps = [
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="view_task",
                    call_args={},
                ),
                start_time_ms=100,
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="claim_task",
                    call_args={"task_id": "t1"},
                ),
                meta={"parent_invoke_id": "p1"},
                start_time_ms=200,
            ),
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="send_message",
                    call_args={"to_member_name": "leader"},
                ),
                start_time_ms=300,
            ),
        ]
        trajectory = Trajectory(
            execution_id="multi-collab",
            session_id="session-team",
            source="online",
            steps=steps,
            meta={"member_id": "teammate-1", "team_id": "team-1"},
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        # Should have: view_task (collaboration_view), claim_task (collaboration_claim + collaboration_receive),
        # send_message (collaboration_send) = 4 signals
        collab_signals = [s for s in signals if s.signal_type.startswith("collaboration_")]
        assert len(collab_signals) == 4

        signal_types = {s.signal_type for s in collab_signals}
        assert "collaboration_view" in signal_types
        assert "collaboration_claim" in signal_types
        assert "collaboration_receive" in signal_types
        assert "collaboration_send" in signal_types

    def test_send_message_to_self_not_collaboration(self) -> None:
        """send_message to same member should not produce collaboration_send signal."""
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="send_message",
            tool_args={"to_member_name": "researcher"},  # Same as member_id
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        # Should NOT have collaboration_send (sending to self)
        collab_send_signals = [s for s in signals if s.signal_type == "collaboration_send"]
        assert len(collab_send_signals) == 0