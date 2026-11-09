# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure-code prompt-too-long fallback: truncation and engine orchestration."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
    PtlFallbackConfig,
    StringLengthCounter,
)
from openjiuwen.core.context_engine.recovery import (
    TRUNCATE_MARKER_METADATA_KEY,
    parse_context_limit,
    recover_phase_b,
    truncate_overflowed_context,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent import AgentCard


def _tool_call(call_id: str, name: str = "search") -> ToolCall:
    return ToolCall(id=call_id, type="function", name=name, arguments="{}")


def _plain_round(index: int, chars: int = 6000):
    return [
        UserMessage(content=f"question-{index} " + "u" * chars),
        AssistantMessage(content=f"answer-{index} " + "a" * chars),
    ]


def _tool_round(index: int, result_chars: int = 6000):
    return [
        UserMessage(content=f"question-{index}"),
        AssistantMessage(content="", tool_calls=[_tool_call(f"tc-{index}")]),
        ToolMessage(content="r" * result_chars, tool_call_id=f"tc-{index}"),
        AssistantMessage(content=f"answer-{index}"),
    ]


def _marker_messages(context):
    return [
        message
        for message in context.get_messages()
        if isinstance(message.metadata, dict) and message.metadata.get(TRUNCATE_MARKER_METADATA_KEY)
    ]


class _FakeContext:
    """Minimal ModelContext surface used by the truncation/archive helpers."""

    def __init__(self, messages, workspace_dir=""):
        self._messages = list(messages)
        self._workspace_dir = workspace_dir

    def get_messages(self, size=None, with_history=True):
        return list(self._messages)

    def set_messages(self, messages, with_history=True):
        self._messages = list(messages)

    def token_counter(self):
        return StringLengthCounter()

    def context_window_tokens(self):
        return 6000

    def workspace_dir(self):
        return self._workspace_dir

    def session_id(self):
        return "s1"

    def context_id(self):
        return "c1"


@pytest.fixture
def session():
    return create_agent_session("ptl_session", card=AgentCard(id="ptl_agent"))


@pytest.fixture
def engine():
    return ContextEngine(ContextEngineConfig())


class TestParseContextLimit:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("This model's maximum context length is 128000 tokens.", 128000),
            ("Range of input length should be [1, 1000000]", 1_000_000),
            ("prompt is too long: 250000 tokens > 200000 maximum", 200_000),
            (
                "The input (30000 tokens) is longer than the model's context length (16384 tokens)",
                16_384,
            ),
            ("You requested 300000 tokens but the max is 262144", 262_144),
            ("auth failed", None),
            ("max_tokens: 2000 output limit", None),
        ],
    )
    def test_extracts_provider_budget(self, text, expected):
        assert parse_context_limit([text]) == expected

    def test_ignores_output_token_caps(self):
        assert parse_context_limit(["max_tokens: 2000 and output limit 2000"]) is None


class TestOverflowTruncate:
    @pytest.mark.asyncio
    async def test_drops_oldest_round_and_keeps_newest(self, engine, session):
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        newest = [message.content for message in context.get_messages()[-2:]]

        result = truncate_overflowed_context(
            context,
            error_texts=["This model's maximum context length is 12000 tokens."],
            target_ratio=0.8,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert result.dropped_rounds == 1
        assert result.limit_tokens == 12000
        assert [message.content for message in context.get_messages()[-2:]] == newest
        assert len(_marker_messages(context)) == 1

    @pytest.mark.asyncio
    async def test_keeps_configured_newest_rounds(self, engine, session):
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        newest_two = [message.content for message in context.get_messages()[-4:]]

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 2000 tokens"],
            keep_last_rounds=2,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert result.dropped_rounds == 1
        remaining = context.get_messages()[-4:]
        # Newest two rounds are retained; the truncation marker is merged into
        # the first retained assistant message, so user questions stay intact.
        assert remaining[0].content == newest_two[0]
        assert remaining[1].content.startswith(newest_two[1])
        assert remaining[2].content == newest_two[2]
        assert remaining[3].content == newest_two[3]

    @pytest.mark.asyncio
    async def test_dropped_round_keeps_tool_pairing(self, engine, session):
        messages = _tool_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            keep_last_rounds=2,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        remaining = context.get_messages()
        assert not any(
            isinstance(message, AssistantMessage) and message.tool_calls and message.tool_calls[0].id == "tc-1"
            for message in remaining
        )
        assert not any(isinstance(message, ToolMessage) and message.tool_call_id == "tc-1" for message in remaining)

    @pytest.mark.asyncio
    async def test_single_round_falls_back_to_oldest_tool_exchange(self, engine, session):
        messages = [
            UserMessage(content="please search"),
            AssistantMessage(content="", tool_calls=[_tool_call("tc-1")]),
            ToolMessage(content="r" * 30000, tool_call_id="tc-1"),
        ]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert result.dropped_rounds == 0
        assert result.dropped_tool_exchanges == 1
        remaining = context.get_messages()
        # Exactly one user message remains: the marker is merged into it and
        # never emitted as a second consecutive user message.
        assert [message.role for message in remaining] == ["user"]
        assert remaining[0].content.startswith("please search")
        assert remaining[0].metadata.get(TRUNCATE_MARKER_METADATA_KEY) is True
        assert not any(isinstance(message, ToolMessage) for message in remaining)

    @pytest.mark.asyncio
    async def test_in_flight_round_is_never_dropped(self, engine, session):
        messages = _plain_round(1) + [
            UserMessage(content="current question"),
            AssistantMessage(content="", tool_calls=[_tool_call("tc-current")]),
            ToolMessage(content="r" * 500, tool_call_id="tc-current"),
        ]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 4000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert result.dropped_rounds == 1
        remaining = context.get_messages()
        assert any(message.content == "current question" for message in remaining)
        assert any(isinstance(message, ToolMessage) and message.tool_call_id == "tc-current" for message in remaining)

    @pytest.mark.asyncio
    async def test_dropped_messages_are_archived(self, tmp_path):
        context = _FakeContext(
            _plain_round(1) + _plain_round(2),
            workspace_dir=str(tmp_path),
        )

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=True,
        )

        assert result.changed is True
        assert result.archive_memory_id
        recall_roots = list((tmp_path / "context").glob("*_context/compression_recall"))
        assert recall_roots, "archive root should exist"
        assert any(path.is_dir() for path in recall_roots[0].iterdir())

    @pytest.mark.asyncio
    async def test_leading_system_message_is_preserved(self, engine, session):
        from openjiuwen.core.foundation.llm import SystemMessage

        messages = [SystemMessage(content="system prompt")] + _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 6000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert context.get_messages()[0].content == "system prompt"

    @pytest.mark.asyncio
    async def test_previous_marker_is_replaced_not_stacked(self, engine, session):
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        first = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )
        second = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            attempt=2,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert first.changed is True
        assert second.changed is True
        assert len(_marker_messages(context)) == 1

    @pytest.mark.asyncio
    async def test_tool_exchange_in_protected_round_is_never_dropped(self, engine, session):
        # Only round 1 is droppable; round 2 is the protected newest round and
        # carries a huge tool result. Whole-round dropping cannot fit the
        # budget, but the protected round's tool exchange must survive.
        messages = _plain_round(1) + [
            UserMessage(content="question-2"),
            AssistantMessage(content="", tool_calls=[_tool_call("tc-2")]),
            ToolMessage(content="r" * 30000, tool_call_id="tc-2"),
            AssistantMessage(content="answer-2"),
        ]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        assert result.dropped_tool_exchanges == 0
        remaining = context.get_messages()
        assert any(isinstance(message, ToolMessage) and message.tool_call_id == "tc-2" for message in remaining)
        assert any(message.content == "question-2" for message in remaining)

    @pytest.mark.asyncio
    async def test_marker_merge_keeps_role_alternation(self, engine, session):
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert result.changed is True
        roles = [message.role for message in context.get_messages()]
        assert roles == ["user", "assistant"]
        assert all(left != right for left, right in zip(roles, roles[1:]))

    @pytest.mark.asyncio
    async def test_embedded_marker_is_restored_not_stacked(self, engine, session):
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        first = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            attempt=1,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )
        first_marker_count = sum(str(message.content).count("CONTEXT_TRUNCATED") for message in context.get_messages())

        second = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            attempt=2,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )
        second_marker_count = sum(str(message.content).count("CONTEXT_TRUNCATED") for message in context.get_messages())

        assert first.changed is True
        assert second.changed is True
        assert first_marker_count == 1
        assert second_marker_count == 1
        assert any(str(message.content).startswith("question-3") for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_repeated_attempts_shrink_the_target(self, engine, session):
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        first = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            attempt=1,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )
        second = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 12000 tokens"],
            attempt=2,
            keep_last_rounds=1,
            archive_dropped_rounds=False,
        )

        assert first.target_tokens == 9600
        assert second.target_tokens == 7680

    @pytest.mark.asyncio
    async def test_no_complete_round_makes_no_change(self, engine, session):
        context = await engine.create_context(
            context_id="ctx",
            session=session,
            history_messages=[UserMessage(content="only the current question")],
        )

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 1000 tokens"],
            archive_dropped_rounds=False,
        )

        assert result.changed is False
        assert len(context.get_messages()) == 1

    @pytest.mark.asyncio
    async def test_provider_limit_below_fixed_overhead_refuses_truncation(self):
        context = _FakeContext(_plain_round(1))
        original = list(context.get_messages())
        # The payload carries a large system prompt that the stored context
        # does not: the provider limit cannot cover the fixed overhead.
        payload = [
            SystemMessage(content="s" * 24000),
            UserMessage(content="question-1"),
            AssistantMessage(content="answer-1"),
        ]

        result = truncate_overflowed_context(
            context,
            error_texts=["maximum context length is 3000 tokens"],
            messages=payload,
            archive_dropped_rounds=False,
        )

        assert result.changed is False
        assert result.dropped_rounds == 0
        assert result.limit_source == "provider"
        assert context.get_messages() == original

    @pytest.mark.asyncio
    async def test_limit_source_is_context_when_error_has_no_number(self):
        context = _FakeContext(_plain_round(1) + _plain_round(2))

        result = truncate_overflowed_context(
            context,
            error_texts=["the prompt is too long"],
            archive_dropped_rounds=False,
        )

        assert result.limit_source == "context"
        assert result.limit_tokens == 6000

    @pytest.mark.asyncio
    async def test_archive_failure_does_not_block_truncation(self, tmp_path):
        context = _FakeContext(
            _plain_round(1) + _plain_round(2),
            workspace_dir=str(tmp_path),
        )
        with patch(
            "openjiuwen.core.context_engine.processor.forked.compressor.recall.archive.archive_compression_messages",
            side_effect=OSError("archive unavailable"),
        ):
            result = truncate_overflowed_context(
                context,
                error_texts=["maximum context length is 6000 tokens"],
                archive_dropped_rounds=True,
            )

        assert result.changed is True
        assert result.archive_memory_id == ""


class TestRecoverFromModelExceptionFallback:
    @pytest.mark.asyncio
    async def test_absent_fallback_keeps_legacy_behaviour(self, engine, session):
        context = await engine.create_context(context_id="ctx", session=session, history_messages=_plain_round(1))
        before = [message.content for message in context.get_messages()]

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is False
        assert [message.content for message in context.get_messages()] == before

    @pytest.mark.asyncio
    async def test_phase_b_disabled_keeps_legacy_behaviour(self, session):
        engine = ContextEngine(ContextEngineConfig(ptl_fallback_config=PtlFallbackConfig()))
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="noop")
        trace = []

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
            attempted_recoveries=trace,
        )

        assert recovered is False
        assert [message.content for message in context.get_messages()] == [m.content for m in messages]
        assert trace == []

    @pytest.mark.asyncio
    async def test_phase_b_disabled_still_uses_phase_a(self, session):
        engine = ContextEngine(ContextEngineConfig(ptl_fallback_config=PtlFallbackConfig()))
        context = await engine.create_context(
            context_id="ctx", session=session, history_messages=_plain_round(1) + _plain_round(2)
        )
        engine.compress_context = AsyncMock(return_value="compressed")

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is True
        engine.compress_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_phase_b_disabled_keeps_compressor_exception(self, session):
        engine = ContextEngine(ContextEngineConfig(ptl_fallback_config=PtlFallbackConfig()))
        context = await engine.create_context(
            context_id="ctx", session=session, history_messages=_plain_round(1) + _plain_round(2)
        )
        engine.compress_context = AsyncMock(side_effect=RuntimeError("compressor exploded"))

        with pytest.raises(RuntimeError, match="compressor exploded"):
            await engine.recover_from_model_exception(
                context_id="ctx",
                session=session,
                context=context,
                exception=RuntimeError("maximum context length is 6000 tokens"),
            )

    @pytest.mark.asyncio
    async def test_phase_b_enabled_does_not_swallow_cancellation(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        context = await engine.create_context(
            context_id="ctx", session=session, history_messages=_plain_round(1) + _plain_round(2)
        )
        engine.compress_context = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await engine.recover_from_model_exception(
                context_id="ctx",
                session=session,
                context=context,
                exception=RuntimeError("maximum context length is 6000 tokens"),
            )

    @pytest.mark.asyncio
    async def test_truncation_after_phase_a_changes_nothing(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="noop")
        session.commit = AsyncMock()

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is True
        assert len(context.get_messages()) < len(messages)
        session.commit.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_phase_a_is_skipped_once_attempted(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        context = await engine.create_context(
            context_id="ctx", session=session, history_messages=_plain_round(1) + _plain_round(2)
        )
        engine.compress_context = AsyncMock(return_value="noop")
        session.commit = AsyncMock()

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
            attempted_recoveries=["phase_a_compress"],
        )

        assert recovered is True
        engine.compress_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_phase_a_failure_falls_back_to_truncation(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        context = await engine.create_context(
            context_id="ctx", session=session, history_messages=_plain_round(1) + _plain_round(2)
        )
        engine.compress_context = AsyncMock(side_effect=RuntimeError("compressor exploded"))
        session.commit = AsyncMock()
        attempted = []

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
            attempted_recoveries=attempted,
        )

        assert recovered is True
        engine.compress_context.assert_awaited_once()
        assert attempted == ["phase_a_compress", "phase_b_truncate"]

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_undo_truncation(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="noop")
        session.commit = AsyncMock(side_effect=RuntimeError("disk full"))

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is True
        assert len(context.get_messages()) < len(messages)

    @pytest.mark.asyncio
    async def test_streaming_chunks_disable_ptl_recovery(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 3000 tokens"),
            streaming=True,
            stream_chunks_emitted=1,
        )

        assert recovered is False
        assert len(context.get_messages()) == len(messages)

    @pytest.mark.asyncio
    async def test_streaming_ptl_before_first_chunk_recovers(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="noop")
        session.commit = AsyncMock()

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
            streaming=True,
            stream_chunks_emitted=0,
        )

        assert recovered is True
        assert len(context.get_messages()) < len(messages)

    @pytest.mark.asyncio
    async def test_phase_a_timeout_falls_back_to_truncation(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    phase_a_timeout_s=0.01,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)

        async def slow_compress(**kwargs):
            await asyncio.sleep(0.2)
            return "compressed"

        engine.compress_context = slow_compress
        session.commit = AsyncMock()

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is True
        assert any("[CONTEXT_TRUNCATED]" in str(message.content) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_phase_a_busy_falls_back_to_truncation(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="busy")
        session.commit = AsyncMock()

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is True
        assert any("[CONTEXT_TRUNCATED]" in str(message.content) for message in context.get_messages())

    @pytest.mark.asyncio
    async def test_cross_phase_skips_a_after_success_then_truncates(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    max_retries=3,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2) + _plain_round(3)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        engine.compress_context = AsyncMock(return_value="compressed")
        session.commit = AsyncMock()
        trace = []

        first = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            attempt=1,
            attempted_recoveries=trace,
            exception=RuntimeError("maximum context length is 12000 tokens"),
        )
        assert first is True
        assert trace == ["phase_a_compress"]

        engine.compress_context.reset_mock()
        second = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            attempt=2,
            attempted_recoveries=trace,
            exception=RuntimeError("maximum context length is 12000 tokens"),
        )

        assert second is True
        engine.compress_context.assert_not_awaited()
        assert trace == ["phase_a_compress", "phase_b_truncate"]

    @pytest.mark.asyncio
    async def test_phase_b_entry_point_truncates_context(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = _plain_round(1) + _plain_round(2)
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        session.commit = AsyncMock()

        recovered = await recover_phase_b(
            engine,
            context_id="ctx",
            session=session,
            context=context,
            error_texts=["maximum context length is 6000 tokens"],
            messages=None,
            tools=None,
            attempt=1,
            config=engine.config.ptl_fallback_config,
        )

        assert recovered is True
        assert len(context.get_messages()) < len(messages)

    @pytest.mark.asyncio
    async def test_phase_a_timeout_restores_context_before_phase_b(self, session):
        engine = ContextEngine(
            ContextEngineConfig(
                ptl_fallback_config=PtlFallbackConfig(
                    enable_pure_code_truncate=True,
                    phase_a_timeout_s=0.05,
                    archive_dropped_rounds=False,
                )
            )
        )
        messages = [UserMessage(content="q"), AssistantMessage(content="a")]
        context = await engine.create_context(context_id="ctx", session=session, history_messages=messages)
        original = [message.content for message in context.get_messages()]

        async def partial_then_hang(**_kwargs):
            # Simulate a processor that mutated the context before a later
            # processor (or the persistence step) was cancelled by the timeout.
            context.set_messages([UserMessage(content="half-compressed")])
            await asyncio.sleep(5)
            return "compressed"

        engine.compress_context = partial_then_hang

        recovered = await engine.recover_from_model_exception(
            context_id="ctx",
            session=session,
            context=context,
            exception=RuntimeError("maximum context length is 6000 tokens"),
        )

        assert recovered is False
        assert [message.content for message in context.get_messages()] == original

    @pytest.mark.asyncio
    async def test_archive_runs_off_the_event_loop(self):
        context = _FakeContext(_plain_round(1) + _plain_round(2), workspace_dir="unused")
        archive_threads = []

        def fake_archive(**_kwargs):
            archive_threads.append(threading.current_thread())
            return SimpleNamespace(memory_id="mem-1")

        config = PtlFallbackConfig(enable_pure_code_truncate=True, archive_dropped_rounds=True)
        with patch(
            "openjiuwen.core.context_engine.processor.forked.compressor.recall.archive.archive_compression_messages",
            side_effect=fake_archive,
        ):
            recovered = await recover_phase_b(
                object(),
                context_id="c1",
                session=None,
                context=context,
                error_texts=["maximum context length is 3000 tokens"],
                messages=None,
                tools=None,
                attempt=1,
                config=config,
            )

        assert recovered is True
        assert archive_threads, "archive should run"
        assert archive_threads[0] is not threading.main_thread()

    def test_ptl_fallback_state_and_budget(self):
        absent = ContextEngine(ContextEngineConfig())
        assert absent.ptl_fallback_state() == "absent"
        assert absent.model_recovery_max_attempts() == 1

        disabled = ContextEngine(ContextEngineConfig(ptl_fallback_config=PtlFallbackConfig(max_retries=5)))
        assert disabled.ptl_fallback_state() == "disabled"
        assert disabled.model_recovery_max_attempts() == 1

        enabled = ContextEngine(
            ContextEngineConfig(ptl_fallback_config=PtlFallbackConfig(enable_pure_code_truncate=True, max_retries=4))
        )
        assert enabled.ptl_fallback_state() == "enabled"
        assert enabled.model_recovery_max_attempts() == 4

    def test_config_rejects_unknown_marker_placeholder(self):
        with pytest.raises(ValidationError):
            PtlFallbackConfig(marker_template="{unknown}")

    def test_config_rejects_unknown_key(self):
        with pytest.raises(ValidationError):
            PtlFallbackConfig(max_retry=2)

    def test_fallback_is_absent_by_default(self):
        # ``None`` is the uninstallable state: no fallback package is needed
        # and the engine keeps its legacy single-compression path.
        assert ContextEngineConfig().ptl_fallback_config is None

    def test_config_defaults_to_phase_b_disabled(self):
        config = PtlFallbackConfig()
        assert config.is_phase_b_enabled() is False
        assert config.phase_a_timeout_s == 0.0

    def test_config_rejects_timeout_without_phase_b(self):
        with pytest.raises(ValidationError):
            PtlFallbackConfig(phase_a_timeout_s=1.0)
