# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ConversationSignalDetector."""

import unittest
from typing import List

from openjiuwen.agent_evolving.signal.base import EvolutionCategory, EvolutionSignal, make_signal_fingerprint
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


class TestConversationSignalDetector(unittest.TestCase):
    """Tests for ConversationSignalDetector.detect(Trajectory)."""

    def test_empty_trajectory_returns_empty_signals(self) -> None:
        """Empty trajectory should return empty signal list."""
        detector = ConversationSignalDetector()
        trajectory = Trajectory(execution_id="test", steps=[])
        signals = detector.detect(trajectory)
        self.assertEqual(signals, [])

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

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.signal_type, "execution_failure")
        self.assertEqual(signal.evolution_type, EvolutionCategory.SKILL_EXPERIENCE)
        self.assertIn("failed", signal.excerpt.lower())

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
        self.assertEqual(len(correction_signals), 1)
        signal = correction_signals[0]
        self.assertEqual(signal.signal_type, "user_correction")
        self.assertEqual(signal.section, "Examples")

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
        self.assertEqual(len(script_signals), 1)
        signal = script_signals[0]
        self.assertEqual(signal.signal_type, "script_artifact")
        self.assertEqual(signal.section, "Scripts")

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

        self.assertEqual(fingerprints_from_messages, fingerprints_from_trajectory)

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
        self.assertEqual(len(failure_signals), 1)

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
        self.assertEqual(len(correction_signals), 1)
        self.assertEqual(correction_signals[0].skill_name, "my_skill")


if __name__ == "__main__":
    unittest.main()