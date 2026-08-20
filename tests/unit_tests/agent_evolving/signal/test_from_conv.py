# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ConversationSignalDetector."""

from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.signal.base import make_signal_fingerprint
from openjiuwen.agent_evolving.signal.from_conv import ConversationSignalDetector
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from openjiuwen.core.foundation.llm import ToolMessage


def _build_trajectory_from_messages(messages: List[dict]) -> Trajectory:
    """Convert test messages into canonical LLM/tool spans."""
    spans: list[dict] = []
    pending_prompt: list[dict] = []
    span_index = 0

    def add_llm(prompt: list[dict], completion: dict | None) -> None:
        nonlocal span_index
        values: dict[str, object] = {}
        for index, message in enumerate(prompt):
            values[f"{semconv.GEN_AI_PROMPT}.{index}.role"] = message.get("role", "")
            values[f"{semconv.GEN_AI_PROMPT}.{index}.content"] = message.get("content", "")
        if completion is not None:
            values[f"{semconv.GEN_AI_COMPLETION}.0.role"] = completion.get("role", "assistant")
            values[f"{semconv.GEN_AI_COMPLETION}.0.content"] = completion.get("content", "")
            if completion.get("tool_calls"):
                values[semconv.GEN_AI_TOOL_CALLS] = completion["tool_calls"]
        values[semconv.GEN_AI_REQUEST_MODEL] = "test-model"
        spans.append(
            {
                "traceId": "trace-test",
                "spanId": f"llm-{span_index}",
                "name": "llm.call",
                "attributes": attributes_from_map(values),
            }
        )
        span_index += 1

    for message in messages:
        role = str(message.get("role", ""))
        if role == "tool":
            tool_id = str(message.get("tool_call_id") or "")
            tool_attrs = {
                semconv.GEN_AI_TOOL_NAME: message.get("name", ""),
                semconv.GEN_AI_TOOL_INPUT: message.get("input", {}),
                semconv.GEN_AI_TOOL_OUTPUT: message.get("content", ""),
            }
            if tool_id:
                tool_attrs[semconv.GEN_AI_TOOL_ID] = tool_id
            spans.append(
                {
                    "traceId": "trace-test",
                    "spanId": f"tool-{span_index}",
                    "name": f"tool.{message.get('name') or 'unknown'}",
                    "attributes": attributes_from_map(tool_attrs),
                }
            )
            span_index += 1
        elif role == "assistant" and message.get("tool_calls"):
            add_llm(pending_prompt, message)
            pending_prompt = []
        else:
            pending_prompt.append(message)
    if pending_prompt:
        add_llm(pending_prompt, None)

    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map({"openjiuwen.trajectory_id": "test-exec"})},
                    "scopeSpans": [{"spans": spans}],
                }
            ]
        }
    )


def _build_team_member_trajectory(
    member_id: str,
    tool_name: str,
    tool_args: dict,
    tool_result: str = "",
    meta: dict = None,
) -> Trajectory:
    """Build a Trajectory with team member context for collaboration signal testing."""
    tool_attrs = {
        semconv.GEN_AI_TOOL_NAME: tool_name,
        semconv.GEN_AI_TOOL_INPUT: tool_args,
        semconv.GEN_AI_TOOL_OUTPUT: tool_result,
        semconv.AT_TEAM_ID: "team-1",
        semconv.AT_MEMBER_ID: member_id,
    }
    if meta:
        tool_attrs.update(meta)
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                "openjiuwen.trajectory_id": f"exec-{member_id}",
                                semconv.AT_SESSION_ID: "session-team",
                                semconv.AT_TEAM_ID: "team-1",
                                semconv.AT_MEMBER_ID: member_id,
                            }
                        )
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-team",
                                    "spanId": "tool-1",
                                    "name": f"tool.{tool_name}",
                                    "attributes": attributes_from_map(tool_attrs),
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )


class TestConversationSignalDetector:
    """Tests for ConversationSignalDetector.detect(Trajectory)."""

    def test_empty_trajectory_returns_empty_signals(self) -> None:
        """Empty trajectory should return empty signal list."""
        detector = ConversationSignalDetector()
        trajectory = Trajectory.from_otlp(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": attributes_from_map({"openjiuwen.trajectory_id": "test"})},
                        "scopeSpans": [{"spans": []}],
                    }
                ]
            }
        )
        signals = detector.detect(trajectory)
        assert signals == []

    def test_trajectory_with_message_objects_does_not_require_dict_get(self) -> None:
        """Object-style messages in trajectory should not crash signal detection."""
        detector = ConversationSignalDetector()
        trajectory = Trajectory.from_otlp(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": attributes_from_map({"openjiuwen.trajectory_id": "message-object"})},
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "trace-message",
                                        "spanId": "llm-1",
                                        "name": "llm.call",
                                        "attributes": attributes_from_map(
                                            {
                                                f"{semconv.GEN_AI_PROMPT}.0.role": "system",
                                                f"{semconv.GEN_AI_PROMPT}.0.content": "system prompt",
                                            }
                                        ),
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        )

        signals = detector.detect_trajectory_signals(trajectory)

        assert signals == []

    def test_message_objects_with_tool_message_do_not_require_dict_get(self) -> None:
        """Object-style ToolMessage inputs should use attribute access."""
        detector = ConversationSignalDetector()
        messages = [
            {
                "role": "assistant",
                "content": "Running command",
                "tool_calls": [{"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}],
            },
            ToolMessage(
                content="Error: command failed with exit code 1",
                tool_call_id="tc_1",
                name="bash",
            ),
        ]

        signals = detector.detect(messages)  # type: ignore[arg-type]

        assert [signal.signal_type for signal in signals] == ["execution_failure"]
        assert signals[0].context == {
            "source": "passive_conversation",
            "tool_name": "bash",
        }

    def test_execution_failure_signal(self) -> None:
        """Tool result with failure keywords should produce execution_failure signal."""
        messages = [
            {"role": "user", "content": "Run the code"},
            {
                "role": "assistant",
                "content": "I'll run it",
                "tool_calls": [{"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}],
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
        assert "failed" in signal.excerpt.lower()
        assert signal.context == {
            "source": "passive_conversation",
            "tool_name": "bash",
        }

    def test_rule_detection_ignores_user_feedback_messages(self) -> None:
        """Rule detector should not directly convert user messages into feedback signals."""
        messages = [
            {"role": "user", "content": "Use the read_file tool"},
            {
                "role": "assistant",
                "content": "I'll read the file",
                "tool_calls": [{"id": "tc_1", "name": "read_file", "type": "function", "arguments": "{}"}],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        trajectory = _build_trajectory_from_messages(messages)

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        assert signals == []

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
        assert signal.context == {
            "source": "passive_conversation",
            "tool_name": "python_exec",
        }

    def test_detect_trajectory_signals_can_filter_script_artifacts(self) -> None:
        trajectory = _build_trajectory_from_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
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
                    "content": "hello world\n0\n1\n2",
                },
            ]
        )
        detector = ConversationSignalDetector()

        signals = detector.detect_trajectory_signals(
            trajectory,
            signal_types={"execution_failure"},
        )

        assert [signal.signal_type for signal in signals] == []

    def test_fingerprint_consistency_with_signal_detector(self) -> None:
        """ConversationSignalDetector signals should match SignalDetector fingerprints."""
        messages = [
            {"role": "user", "content": "Run the code"},
            {
                "role": "assistant",
                "content": "I'll run it",
                "tool_calls": [{"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}],
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

    def test_detect_trajectory_signals_derives_messages_from_trajectory(self) -> None:
        trajectory = _build_trajectory_from_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "tc_1", "name": "bash", "arguments": "{}"}],
                },
                {
                    "role": "tool",
                    "tool_call_id": "tc_1",
                    "name": "bash",
                    "content": "Error: command failed",
                },
            ]
        )

        detector = ConversationSignalDetector()
        signals = detector.detect_trajectory_signals(trajectory)

        assert [signal.signal_type for signal in signals] == ["execution_failure"]

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

        # Should deduplicate to 1 signal (same type, context.tool_name, excerpt)
        failure_signals = [s for s in signals if s.signal_type == "execution_failure"]
        assert len(failure_signals) == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_uses_llm_judgment() -> None:
        messages = [
            {"role": "user", "content": "Use the read_file tool"},
            {
                "role": "assistant",
                "content": "I'll read the file",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "name": "read_file",
                        "type": "function",
                        "arguments": '{"path": "/skills/my_skill/SKILL.md"}',
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc_1", "content": "file content"},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        llm = MagicMock()
        llm.invoke = AsyncMock(
            return_value={"content": '{"is_feedback": true, "excerpt": "不对，你应该先检查文件是否存在"}'}
        )

        detector = ConversationSignalDetector(existing_skills={"my_skill"}).bind_llm(
            llm=llm,
            model="test-model",
        )
        signals = await detector.detect_user_intent(messages)

        assert len(signals) == 1
        assert signals[0].signal_type == "user_intent"
        assert signals[0].skill_name == "my_skill"
        assert signals[0].context == {"source": "passive_conversation"}

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_without_bound_llm_uses_rule_fallback() -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/my_skill/SKILL.md"}]},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        detector = ConversationSignalDetector(existing_skills={"my_skill"})

        signals = await detector.detect_user_intent(messages)

        assert len(signals) == 1
        assert signals[0].signal_type == "user_intent"
        assert signals[0].excerpt == "不对，你应该先检查文件是否存在"

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_requires_normalized_messages() -> None:
        trajectory = _build_trajectory_from_messages([])
        detector = ConversationSignalDetector()

        with pytest.raises(TypeError, match="normalized messages"):
            await detector.detect_user_intent(trajectory)  # type: ignore[arg-type]

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_message_feedback_is_deprecated_alias_for_user_intent() -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/my_skill/SKILL.md"}]},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        detector = ConversationSignalDetector(existing_skills={"my_skill"})

        with pytest.warns(DeprecationWarning, match="detect_user_message_feedback"):
            signals = await detector.detect_user_message_feedback(messages)

        assert len(signals) == 1
        assert signals[0].signal_type == "user_intent"
        assert signals[0].section == "Instructions"

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_invalid_json_uses_rule_fallback() -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/my_skill/SKILL.md"}]},
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        llm = MagicMock()
        llm.invoke = AsyncMock(return_value={"content": "not-json"})
        detector = ConversationSignalDetector(existing_skills={"my_skill"}).bind_llm(
            llm=llm,
            model="test-model",
        )

        signals = await detector.detect_user_intent(messages)

        assert len(signals) == 1
        assert signals[0].signal_type == "user_intent"
        assert signals[0].excerpt == "不对，你应该先检查文件是否存在"

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_returns_empty_when_llm_fails_and_rule_does_not_match() -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"arguments": "/skills/my_skill/SKILL.md"}]},
            {"role": "user", "content": "你好"},
        ]
        llm = MagicMock()
        llm.invoke = AsyncMock(side_effect=RuntimeError("llm down"))
        detector = ConversationSignalDetector(existing_skills={"my_skill"}).bind_llm(
            llm=llm,
            model="test-model",
        )

        signals = await detector.detect_user_intent(messages)

        assert signals == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_detect_user_intent_ignores_skill_path_in_non_read_tool() -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "name": "bash",
                        "arguments": '{"command": "cat /skills/my_skill/SKILL.md"}',
                    }
                ],
            },
            {"role": "user", "content": "不对，你应该先检查文件是否存在"},
        ]
        detector = ConversationSignalDetector(existing_skills={"my_skill"})

        signals = await detector.detect_user_intent(messages)

        assert signals == []


class TestConversationSignalDetectorCollaborationBoundary:
    """Ordinary conversation detector leaves team collaboration to team evolution."""

    def test_team_member_collaboration_activity_does_not_emit_collaboration_signal(self) -> None:
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="send_message",
            tool_args={"to_member_name": "coder", "message": "请完成数据分析"},
            tool_result="sent",
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        collab_signals = [s for s in signals if s.signal_type == "collaboration"]
        assert len(collab_signals) == 0

    def test_team_member_collaboration_tool_failure_uses_execution_failure_signal(self) -> None:
        trajectory = _build_team_member_trajectory(
            member_id="researcher",
            tool_name="send_message",
            tool_args={"to_member_name": "coder"},
            tool_result="Error: member coder failed to respond - timeout",
        )

        detector = ConversationSignalDetector()
        signals = detector.detect(trajectory)

        assert [signal.signal_type for signal in signals] == ["execution_failure"]
        assert signals[0].context.get("tool_name") == "send_message"
