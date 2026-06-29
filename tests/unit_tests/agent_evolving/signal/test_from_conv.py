# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ConversationSignalDetector."""

import unittest
from typing import List

from openjiuwen.agent_evolving.signal.base import EvolutionCategory, make_signal_fingerprint
from openjiuwen.agent_evolving.signal.from_conv import ConversationSignalDetector
from openjiuwen.agent_evolving.trajectory.types import (
    LLMCallDetail,
    ToolCallDetail,
    Trajectory,
    TrajectoryStep,
)


class _FakeLLMResponse:
    """Minimal stand-in for an LLM response object exposing ``.content``."""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Async LLM client mock for ``detect_async`` correction-judgment tests.

    Records every call and returns the preset ``content`` (or raises) so tests
    can assert the detector's LLM path without a real model.
    """

    def __init__(self, content: str = "", *, raise_on_invoke: bool = False) -> None:
        self._content = content
        self._raise_on_invoke = raise_on_invoke
        self.invoke_count = 0
        self.last_prompt: str | None = None

    async def invoke(self, model: str, messages: list) -> _FakeLLMResponse:
        _ = model  # required by interface, unused in mock
        self.invoke_count += 1
        self.last_prompt = messages[-1]["content"] if messages else ""
        if self._raise_on_invoke:
            raise RuntimeError("llm boom")
        return _FakeLLMResponse(self._content)


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
        steps.insert(
            0,
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(model="test-model", messages=llm_messages),
            ),
        )

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
                "tool_calls": [{"id": "tc_1", "name": "read_file", "type": "function", "arguments": "{}"}],
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


class TestConversationSignalDetectorAsync(unittest.IsolatedAsyncioTestCase):
    """Tests for the LLM-based user-correction detection added in 15c68a27.

    Covers ``detect_async``, ``_batch_judge_corrections``, ``_invoke_correction_llm``,
    ``_parse_correction_response`` and ``_build_correction_context``.
    """

    async def test_detect_async_without_llm_falls_back_to_regex(self) -> None:
        """No llm/model configured -> detect_async mirrors sync regex detection."""
        messages = [
            {"role": "user", "content": "Run the code"},
            {
                "role": "assistant",
                "content": "I'll run it",
                "tool_calls": [{"id": "tc_1", "name": "bash", "type": "function", "arguments": "{}"}],
            },
            {"role": "tool", "tool_call_id": "tc_1", "name": "bash", "content": "Error: command failed"},
        ]
        detector = ConversationSignalDetector()  # no llm/model

        sync_signals = detector.detect(messages)
        async_signals = await detector.detect_async(messages)

        self.assertEqual(
            [make_signal_fingerprint(s) for s in sync_signals],
            [make_signal_fingerprint(s) for s in async_signals],
        )
        self.assertTrue(async_signals)
        self.assertEqual(async_signals[0].signal_type, "execution_failure")

    async def test_detect_async_llm_correction_produces_signal(self) -> None:
        """When LLM judges a user message as correction, a user_correction signal is emitted."""
        # User content has NO correction regex keyword -> regex path would miss it;
        # only the LLM judgment should surface a correction here.
        raw = '[{"msg_index": 0, "is_correction": true, "reason": "user redirects", "excerpt": "换个方法"}]'
        detector = ConversationSignalDetector(llm=_FakeLLM(content=raw), model="dummy-model", language="cn")
        messages = [
            {"role": "user", "content": "换个方法试试"},
        ]

        signals = await detector.detect_async(messages)

        corrections = [s for s in signals if s.signal_type == "user_correction"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0].section, "Examples")
        self.assertEqual(corrections[0].excerpt, "换个方法")

    async def test_detect_async_llm_no_correction_skips_signal(self) -> None:
        """When LLM judges no correction, no user_correction signal is produced."""
        raw = '[{"msg_index": 0, "is_correction": false}]'
        detector = ConversationSignalDetector(llm=_FakeLLM(content=raw), model="dummy-model", language="cn")
        messages = [
            {"role": "user", "content": "换个方法试试"},  # would match regex, but LLM overrides
        ]

        signals = await detector.detect_async(messages)

        self.assertEqual([s for s in signals if s.signal_type == "user_correction"], [])

    async def test_detect_async_llm_failure_falls_back_to_regex(self) -> None:
        """When the LLM call raises, detect_async falls back to the regex path."""
        detector = ConversationSignalDetector(
            llm=_FakeLLM(content="", raise_on_invoke=True), model="dummy-model", language="cn"
        )
        messages = [
            {"role": "user", "content": "不对，应该换一种方式"},  # matches _CORRECTION_PATTERN
        ]

        signals = await detector.detect_async(messages)

        corrections = [s for s in signals if s.signal_type == "user_correction"]
        self.assertEqual(len(corrections), 1)

    async def test_detect_async_llm_unparseable_response_falls_back_to_regex(self) -> None:
        """When LLM returns non-JSON (after retry), detect_async falls back to regex."""
        detector = ConversationSignalDetector(
            llm=_FakeLLM(content="not json at all"), model="dummy-model", language="cn"
        )
        messages = [
            {"role": "user", "content": "不对，应该换一种方式"},
        ]

        signals = await detector.detect_async(messages)

        corrections = [s for s in signals if s.signal_type == "user_correction"]
        self.assertEqual(len(corrections), 1)
        # parse failed twice (initial + one retry) before falling back
        self.assertEqual(detector._llm.invoke_count, 2)

    async def test_batch_judge_corrections_returns_empty_when_no_user_messages(self) -> None:
        """No user messages -> LLM is not called and an empty dict is returned."""
        detector = ConversationSignalDetector(llm=_FakeLLM(), model="dummy-model")
        messages = [
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "tc_1", "content": "ok"},
        ]

        result = await detector._batch_judge_corrections(messages)

        self.assertEqual(result, {})
        self.assertEqual(detector._llm.invoke_count, 0)

    async def test_batch_judge_corrections_filters_non_correction_entries(self) -> None:
        """Only is_correction=true entries land in the result map."""
        raw = (
            '[{"msg_index": 0, "is_correction": false}, '
            '{"msg_index": 0, "is_correction": true, "reason": "r", "excerpt": "e"}]'
        )
        detector = ConversationSignalDetector(llm=_FakeLLM(content=raw), model="dummy-model")
        messages = [{"role": "user", "content": "whatever"}]

        result = await detector._batch_judge_corrections(messages)

        self.assertIn(0, result)
        self.assertTrue(result[0]["is_correction"])

    async def test_batch_judge_corrections_returns_none_on_unparseable(self) -> None:
        """Unparseable LLM output (after retry) yields None so callers fall back."""
        detector = ConversationSignalDetector(llm=_FakeLLM(content="###"), model="dummy-model")
        messages = [{"role": "user", "content": "whatever"}]

        result = await detector._batch_judge_corrections(messages)

        self.assertIsNone(result)
        self.assertEqual(detector._llm.invoke_count, 2)

    async def test_invoke_correction_llm_returns_none_on_exception(self) -> None:
        """_invoke_correction_llm swallows exceptions and returns None."""
        detector = ConversationSignalDetector(llm=_FakeLLM(raise_on_invoke=True), model="dummy-model")

        result = await detector._invoke_correction_llm("prompt")

        self.assertIsNone(result)

    async def test_invoke_correction_llm_extracts_content_field(self) -> None:
        """_invoke_correction_llm returns response.content when present."""
        detector = ConversationSignalDetector(llm=_FakeLLM(content="payload"), model="dummy-model")

        result = await detector._invoke_correction_llm("prompt")

        self.assertEqual(result, "payload")

    def test_parse_correction_response_plain_json(self) -> None:
        raw = '[{"msg_index": 0, "is_correction": true, "reason": "r", "excerpt": "e"}]'
        parsed = ConversationSignalDetector._parse_correction_response(raw)
        self.assertEqual(parsed, [{"msg_index": 0, "is_correction": True, "reason": "r", "excerpt": "e"}])

    def test_parse_correction_response_strips_code_fences(self) -> None:
        raw = '```json\n[{"msg_index": 0, "is_correction": true}]\n```'
        parsed = ConversationSignalDetector._parse_correction_response(raw)
        self.assertEqual(parsed, [{"msg_index": 0, "is_correction": True}])

    def test_parse_correction_response_tolerates_trailing_commas(self) -> None:
        raw = '[{"msg_index": 0, "is_correction": true,},]'
        parsed = ConversationSignalDetector._parse_correction_response(raw)
        self.assertEqual(parsed, [{"msg_index": 0, "is_correction": True}])

    def test_parse_correction_response_extracts_embedded_array(self) -> None:
        raw = 'prefix text [{"msg_index": 0, "is_correction": true}] trailing'
        parsed = ConversationSignalDetector._parse_correction_response(raw)
        self.assertEqual(parsed, [{"msg_index": 0, "is_correction": True}])

    def test_parse_correction_response_none_on_garbage(self) -> None:
        self.assertIsNone(ConversationSignalDetector._parse_correction_response(""))
        self.assertIsNone(ConversationSignalDetector._parse_correction_response(None))
        self.assertIsNone(ConversationSignalDetector._parse_correction_response("no json here"))
        # Non-list JSON should also be rejected
        self.assertIsNone(ConversationSignalDetector._parse_correction_response('{"a": 1}'))

    def test_build_correction_context_includes_assistant_tool_calls_and_tool(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "running it now",
                "tool_calls": [{"name": "bash"}, {"name": "read_file"}],
            },
            {"role": "tool", "name": "bash", "content": "Error: failed"},
        ]

        context = ConversationSignalDetector._build_correction_context(messages)

        self.assertIn("[assistant] tool_calls: bash, read_file", context)
        self.assertIn("[bash]", context)
        self.assertIn("Error: failed", context)

    def test_build_correction_context_truncates_to_max_chars(self) -> None:
        """Context assembly stops once the max char budget is exceeded."""
        long_tool = {"role": "tool", "name": "bash", "content": "x" * 500}
        messages = [long_tool, dict(long_tool), dict(long_tool)]

        context = ConversationSignalDetector._build_correction_context(messages, max_chars=300)

        self.assertLessEqual(len(context), 300)

    def test_build_correction_context_empty_when_no_relevant_messages(self) -> None:
        messages = [{"role": "user", "content": "only a user message"}]

        context = ConversationSignalDetector._build_correction_context(messages)

        self.assertEqual(context, "")

    def test_build_correction_context_skips_empty_assistant_with_tool_calls(self) -> None:
        """An assistant message with empty content is skipped even if it has tool_calls."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "bash"}],
            },
            {"role": "tool", "name": "bash", "content": "ok"},
        ]

        context = ConversationSignalDetector._build_correction_context(messages)

        self.assertNotIn("[assistant]", context)
        self.assertIn("[bash]", context)


if __name__ == "__main__":
    unittest.main()
