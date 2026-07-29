# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    ROUND_LEVEL_FALLBACK_MARKER,
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
    _CompressTarget,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ModelRequestConfig, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.session.agent import Session
from tests.unit_tests.core.context_engine._stream_state_helpers import (
    assert_context_state_pair,
    capture_context_compression_states,
)


class _TestableRoundLevelCompressor(RoundLevelCompressor):
    def __init__(self, config: RoundLevelCompressorConfig):
        super().__init__(config)
        self.compress_until_target_result = None

    async def build_memory_message_for_test(self, summary: str, target: _CompressTarget, context):
        return await self._build_memory_message(summary, target, context)

    def build_compression_user_prompt_for_test(self, **kwargs) -> str:
        return self._build_compression_user_prompt(**kwargs)

    async def _compress_until_target(self, *args, **kwargs):
        return self.compress_until_target_result


def create_tool_call(tool_call_id: str, name: str, arguments: str = "") -> ToolCall:
    return ToolCall(id=tool_call_id, name=name, type="function", arguments=arguments)


class TestRoundLevelCompressor:
    @pytest.mark.asyncio
    async def test_trigger_get_context_window_uses_context_ratio_threshold(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=50,
            )
        )
        context = MagicMock()
        context._context_window_tokens = 100
        context._model_context_window_tokens = {}
        context_window = ContextWindow(
            system_messages=[],
            context_messages=[UserMessage(content="u")],
            tools=[],
        )
        compressor._count_context_window_tokens = MagicMock(return_value=89)
        assert await compressor.trigger_get_context_window(context, context_window) is False

        compressor._count_context_window_tokens = MagicMock(return_value=90)
        assert await compressor.trigger_get_context_window(context, context_window) is True

    @pytest.mark.asyncio
    async def test_trigger_add_messages_uses_model_context_ratio_when_model_window_is_smaller(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=50,
                model=ModelRequestConfig(model="small-model"),
            )
        )
        context = MagicMock()
        context.get_messages.return_value = [UserMessage(content="old")]
        context._context_window_tokens = 1000
        context._model_context_window_tokens = {"small-model": 100}

        compressor._count_context_window_tokens = MagicMock(return_value=89)
        assert await compressor.trigger_add_messages(context, [UserMessage(content="new")]) is False

        compressor._count_context_window_tokens = MagicMock(return_value=90)
        assert await compressor.trigger_add_messages(context, [UserMessage(content="new")]) is True

    @pytest.mark.asyncio
    async def test_streams_state_when_round_level_compressor_triggers_on_get(self):
        session = Session(session_id="round-level-compressor-stream-session")
        engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
        ctx = await engine.create_context(
            "test_ctx",
            session,
            history_messages=[
                UserMessage(content="old request"),
                AssistantMessage(content="old answer"),
            ],
            processors=[
                (
                    "RoundLevelCompressor",
                    RoundLevelCompressorConfig(
                        trigger_context_ratio=0.9,
                        target_total_tokens=1,
                    ),
                )
            ],
        )
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        processor.trigger_get_context_window = AsyncMock(return_value=True)  # type: ignore[method-assign]
        processor.on_get_context_window = AsyncMock(
            return_value=(
                ContextEvent(event_type=processor.processor_type(), messages_to_modify=[0, 1]),
                ContextWindow(
                    system_messages=[],
                    context_messages=[UserMessage(content="compressed")],
                    tools=[],
                ),
            )
        )  # type: ignore[method-assign]

        _, states = await capture_context_compression_states(
            session,
            lambda: ctx.get_context_window(),
        )

        assert_context_state_pair(
            states,
            processor_type="RoundLevelCompressor",
            phase="get_context_window",
        )
        assert "modified 2 messages" in states[1].summary

    @pytest.mark.asyncio
    async def test_build_memory_message_returns_plain_user_message(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=50,
            )
        )
        context = MagicMock()
        target = _CompressTarget(
            block_id="block_1",
            scope="ongoing_react",
            start_idx=0,
            end_idx=0,
            messages=[AssistantMessage(content="analysis state")],
        )

        message = await compressor.build_memory_message_for_test(
            "User Requirements:\n- Keep intent.",
            target,
            context,
        )

        assert isinstance(message, UserMessage)
        assert message.content.startswith(ROUND_LEVEL_FALLBACK_MARKER)
        assert "processor: RoundLevelCompressor" in message.content

    @pytest.mark.asyncio
    async def test_on_get_context_window_reports_original_message_range(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=50,
            )
        )
        compressor.compress_until_target_result = [
            UserMessage(
                content=(
                    f"{ROUND_LEVEL_FALLBACK_MARKER}\n"
                    "processor: RoundLevelCompressor\n"
                    "Summary:\ncompressed"
                )
            )
        ]
        context = MagicMock()
        context.token_counter.return_value = None
        context_window = ContextWindow(
            system_messages=[],
            context_messages=[
                UserMessage(content="u" * 90),
                AssistantMessage(content="a" * 90),
                UserMessage(content="x" * 90),
                AssistantMessage(content="y" * 90),
            ],
            tools=[],
        )

        event, updated_context_window = await compressor.on_get_context_window(context, context_window)

        assert event is not None
        assert event.messages_to_modify == [0, 1, 2, 3]
        assert event.compact_summary.startswith(ROUND_LEVEL_FALLBACK_MARKER)
        assert "compressed" in event.compact_summary
        assert len(updated_context_window.context_messages) == 1
        assert updated_context_window.context_messages[0].content.startswith(ROUND_LEVEL_FALLBACK_MARKER)
        context.set_messages.assert_called_once_with(updated_context_window.context_messages)

    @staticmethod
    def test_build_compression_user_prompt_includes_ongoing_and_completed_requirements():
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=50,
            )
        )
        context = MagicMock()
        context.token_counter.return_value = None

        prompt_text = compressor.build_compression_user_prompt_for_test(
            context_messages=[
                UserMessage(content="request"),
                AssistantMessage(content="working"),
                UserMessage(content="another request"),
                AssistantMessage(content="final answer"),
            ],
            targets=[
                _CompressTarget(
                    block_id="block_1",
                    scope="ongoing_react",
                    start_idx=0,
                    end_idx=1,
                    messages=[
                        UserMessage(content="request"),
                        AssistantMessage(content="working"),
                    ],
                ),
                _CompressTarget(
                    block_id="block_2",
                    scope="completed_react",
                    start_idx=2,
                    end_idx=3,
                    messages=[
                        UserMessage(content="another request"),
                        AssistantMessage(content="final answer"),
                    ],
                ),
            ],
            context=context,
            phase_name="phase_1",
            target_tokens=300,
            keep_recent_messages=0,
            system_messages=None,
            tools=None,
        )

        assert "User Requirements" in prompt_text
        assert "Final Result" in prompt_text
        assert "Do not weaken or over-compress the user's original request" in prompt_text

    @staticmethod
    def test_compression_call_budget_uses_configured_context_window_when_smaller_than_call_limit():
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=500,
                compression_call_max_tokens=1000,
                model=ModelRequestConfig(model="test-model"),
            )
        )
        token_counter = MagicMock()
        token_counter.count_messages.return_value = 50
        context = MagicMock()
        context.token_counter.return_value = token_counter
        context._context_window_tokens = 40
        context._model_context_window_tokens = {"test-model": 200}

        assert compressor._is_under_compression_call_budget("system", "prompt", context) is False

        token_counter.count_messages.return_value = 39
        assert compressor._is_under_compression_call_budget("system", "prompt", context) is True

    @staticmethod
    def test_compression_call_budget_uses_model_context_window_when_smaller_than_call_limit():
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=500,
                compression_call_max_tokens=1000,
                model=ModelRequestConfig(model="test-model"),
            )
        )
        token_counter = MagicMock()
        token_counter.count_messages.return_value = 75
        context = MagicMock()
        context.token_counter.return_value = token_counter
        context._context_window_tokens = 300
        context._model_context_window_tokens = {"test-model": 70}

        assert compressor._is_under_compression_call_budget("system", "prompt", context) is False

        token_counter.count_messages.return_value = 70
        assert compressor._is_under_compression_call_budget("system", "prompt", context) is True

    @pytest.mark.asyncio
    async def test_build_json_replacements_skips_replacement_that_exceeds_effective_budget(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=500,
                compression_call_max_tokens=1000,
                model=ModelRequestConfig(model="test-model"),
            )
        )
        context = MagicMock()
        context._context_window_tokens = 50
        context._model_context_window_tokens = {"test-model": 500}
        context.token_counter.return_value = None
        target = _CompressTarget(
            block_id="block_1",
            scope="completed_react",
            start_idx=0,
            end_idx=0,
            messages=[AssistantMessage(content="original" * 100)],
        )

        replacements = await compressor._build_json_replacements(
            context,
            [target],
            {"blocks": [{"block_id": "block_1", "summary": "summary" * 200}]},
        )

        assert replacements == []

    @pytest.mark.asyncio
    async def test_build_json_replacements_reinjects_team_collaboration_per_target(self):
        compressor = _TestableRoundLevelCompressor(
            RoundLevelCompressorConfig(
                trigger_context_ratio=0.9,
                target_total_tokens=500,
                compression_call_max_tokens=1000,
            )
        )
        context = MagicMock()
        context.token_counter.return_value = None
        compressor._has_compression_benefit = MagicMock(return_value=True)
        target_one = _CompressTarget(
            block_id="block_1",
            scope="completed_react",
            start_idx=0,
            end_idx=1,
            messages=[
                AssistantMessage(
                    content="",
                    tool_calls=[
                        create_tool_call(
                            "tc-claim",
                            "claim_task",
                            '{"task_id": "42", "status": "claimed"}',
                        )
                    ],
                ),
                ToolMessage(content="Task #42 pending -> claimed", tool_call_id="tc-claim"),
            ],
        )
        target_two = _CompressTarget(
            block_id="block_2",
            scope="completed_react",
            start_idx=2,
            end_idx=3,
            messages=[
                AssistantMessage(
                    content="",
                    tool_calls=[
                        create_tool_call(
                            "tc-complete",
                            "member_complete_task",
                            '{"task_id": "99", "note": "done and verified"}',
                        )
                    ],
                ),
                ToolMessage(content="Task #99 completed", tool_call_id="tc-complete"),
            ],
        )

        replacements = await compressor._build_json_replacements(
            context,
            [target_one, target_two],
            {
                "blocks": [
                    {"block_id": "block_1", "summary": "summary one"},
                    {"block_id": "block_2", "summary": "summary two"},
                ]
            },
        )

        assert len(replacements) == 2
        first_messages = replacements[0][2]
        second_messages = replacements[1][2]
        assert len(first_messages) == 2
        assert len(second_messages) == 2
        assert "认领任务 #42 [claim_task]" in first_messages[1].content
        assert "Task #42 pending -> claimed" in first_messages[1].content
        assert "99" not in first_messages[1].content
        assert "完成自己负责的任务 #99，备注：done and verified [member_complete_task]" in second_messages[1].content
        assert "Task #99 completed" in second_messages[1].content
        assert "42" not in second_messages[1].content
