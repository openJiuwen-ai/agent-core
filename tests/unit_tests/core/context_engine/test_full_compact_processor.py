# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolCall, ToolMessage, UserMessage


def create_tool_call(tool_call_id: str, name: str, arguments: str = "") -> ToolCall:
    return ToolCall(id=tool_call_id, name=name, type="function", arguments=arguments)


async def create_context_with_full_compact(
    config: FullCompactProcessorConfig,
    history_messages=None,
):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        None,
        history_messages=history_messages or [],
        processors=[("FullCompactProcessor", config)],
    )


class TestFullCompactProcessor:
    @pytest.mark.asyncio
    async def test_trigger_add_messages_true_when_combined_tokens_exceed_threshold(self):
        config = FullCompactProcessorConfig(
            trigger_total_tokens=20,
            compression_call_max_tokens=2000,
            messages_to_keep=2,
        )
        ctx = await create_context_with_full_compact(
            config,
            history_messages=[UserMessage(content="trigger message " * 30)],
        )
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        triggered = await processor.trigger_add_messages(
            ctx,
            [AssistantMessage(content="new assistant " + ("payload " * 20))],
        )
        assert triggered is True

    @pytest.mark.asyncio
    async def test_on_add_messages_uses_session_memory_candidate_after_prior_full_compact(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=1,
                compression_call_max_tokens=2000,
                messages_to_keep=1,
            )
        )
        ctx = await create_context_with_full_compact(
            processor.config,
            history_messages=[
                UserMessage(content="before compact"),
                SystemMessage(content=f"{processor.config.marker}\nConversation compacted"),
                UserMessage(content="recent user"),
                AssistantMessage(content="recent assistant"),
            ],
        )
        processor._build_session_memory_messages = AsyncMock(return_value=(["should not be used"], None))  # type: ignore[method-assign]
        processor._build_full_compact_messages = AsyncMock(return_value=ctx.get_messages())  # type: ignore[method-assign]

        with pytest.raises(Exception, match="messages should be a BaseMessage or a list of BaseMessage"):
            await processor.on_add_messages(ctx, [UserMessage(content="new message")])

        processor._build_session_memory_messages.assert_awaited_once()  # type: ignore[attr-defined]

    def test_select_messages_after_session_memory_prefers_context_message_id(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        prefix = [
            UserMessage(content="old-a", metadata={"context_message_id": "msg-1"}),
            UserMessage(content="old-b", metadata={"context_message_id": "msg-2"}),
        ]
        active_messages = [
            UserMessage(content="keep-1", metadata={"context_message_id": "msg-3"}),
            AssistantMessage(content="keep-2", metadata={"context_message_id": "msg-4"}),
        ]

        preserved = processor._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime={
                "notes_upto_message_id": "msg-2",
                "last_summarized_message_count": 1,
            },
            has_compaction_boundary=False,
        )

        assert preserved is None

    def test_select_messages_after_session_memory_rewinds_unsafe_anchor_to_completed_round(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        assistant_with_tools = AssistantMessage(
            content="",
            tool_calls=[create_tool_call("tc-unsafe", "read_file")],
            metadata={"context_message_id": "msg-4"},
        )
        tool_message = ToolMessage(
            content="tool output", tool_call_id="tc-unsafe", metadata={"context_message_id": "msg-5"}
        )
        prefix = [
            UserMessage(content="u1", metadata={"context_message_id": "msg-1"}),
            AssistantMessage(content="a1", metadata={"context_message_id": "msg-2"}),
        ]
        active_messages = [
            UserMessage(content="u2", metadata={"context_message_id": "msg-3"}),
            assistant_with_tools,
            tool_message,
            AssistantMessage(content="a2", metadata={"context_message_id": "msg-6"}),
        ]

        preserved = processor._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime={
                "notes_upto_message_id": "msg-4",
            },
            has_compaction_boundary=False,
        )

        assert preserved is None

    def test_select_messages_after_session_memory_ignores_prefix_anchor_after_boundary(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        prefix = [
            UserMessage(content="old-u", metadata={"context_message_id": "msg-1"}),
            AssistantMessage(content="old-a", metadata={"context_message_id": "msg-2"}),
        ]
        active_messages = [
            UserMessage(content="recent-u", metadata={"context_message_id": "msg-3"}),
            AssistantMessage(content="recent-a", metadata={"context_message_id": "msg-4"}),
        ]

        preserved = processor._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime={
                "notes_upto_message_id": "msg-2",
                "last_summarized_message_count": 2,
            },
            has_compaction_boundary=True,
        )

        assert preserved is None

    def test_select_messages_after_session_memory_accepts_synthetic_summary_anchor(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        active_messages = [
            UserMessage(
                content=processor._build_session_memory_message("notes body", True),
                metadata={"context_message_id": "msg-1"},
            ),
            UserMessage(content="recent-u", metadata={"context_message_id": "msg-2"}),
            AssistantMessage(content="recent-a", metadata={"context_message_id": "msg-3"}),
        ]

        preserved = processor._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime={
                "notes_upto_message_id": "msg-1",
            },
            has_compaction_boundary=True,
        )

        assert preserved == active_messages[1:]

    def test_select_messages_after_session_memory_requires_context_id_anchor_before_first_boundary(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        active_messages = [
            UserMessage(content="u1"),
            AssistantMessage(content="a1"),
            UserMessage(content="u2"),
            AssistantMessage(content="a2"),
        ]

        preserved = processor._select_messages_after_session_memory(
            active_messages=active_messages,
            session_memory_runtime={
                "last_summarized_message_count": 2,
            },
            has_compaction_boundary=False,
        )

        assert preserved is None

    def test_group_messages_by_api_round_splits_user_and_following_assistant_tool_messages(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        messages = [
            UserMessage(content="u1"),
            AssistantMessage(
                content="",
                tool_calls=[create_tool_call("tc-1", "read_file")],
            ),
            ToolMessage(content="tool-1", tool_call_id="tc-1"),
            AssistantMessage(content="a1"),
            UserMessage(content="u2"),
            AssistantMessage(content="a2"),
        ]

        groups = processor._group_messages_by_api_round(messages)
        assert len(groups) == 3
        assert [msg.content for msg in groups[0]] == ["u1", "", "tool-1"]
        assert [msg.content for msg in groups[1]] == ["a1"]
        assert [msg.content for msg in groups[2]] == ["u2", "a2"]

    def test_extract_tool_result_hint_respects_configured_tool_name_list(self):
        from openjiuwen.core.context_engine.processor.compressor.util import (
            extract_tool_result_hint,
        )

        grep_hint = extract_tool_result_hint("grep", '{"count": 3}', ["read_file"])
        read_hint = extract_tool_result_hint(
            "read_file",
            '{"file_path": "/tmp/a.txt", "line_count": 7}',
            ["read_file"],
        )

        assert grep_hint == ""
        assert read_hint == "result_path=/tmp/a.txt lines=7"

    def test_build_reinjected_state_messages_preserves_original_api_round_structure(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        source_messages = [
            UserMessage(content="read the skill"),
            AssistantMessage(
                content="",
                tool_calls=[create_tool_call("tc-skill", "read_file", '{"file_path": "/skills/demo/SKILL.md"}')],
            ),
            ToolMessage(content='{"content":"# Demo Skill"}', tool_call_id="tc-skill"),
            AssistantMessage(content="skill loaded"),
        ]

        reinjected = processor.build_reinjected_state_messages(
            context=MagicMock(),
            source_messages=source_messages,
            messages_to_keep=[],
            summary_message=UserMessage(content="summary"),
            boundary_message=SystemMessage(content="boundary"),
            builder_names=["skills"],
        )

        assert [type(message) for message in reinjected] == [UserMessage]

    def test_session_memory_manager_select_unsummarized_messages_prefers_message_id(self):
        from openjiuwen.core.context_engine.context.session_memory_manager import (
            SessionMemoryManager,
            SessionMemoryConfig,
        )

        messages = [
            UserMessage(content="a", metadata={"context_message_id": "msg-1"}),
            AssistantMessage(content="same", metadata={"context_message_id": "msg-2"}),
            AssistantMessage(content="same", metadata={"context_message_id": "msg-3"}),
        ]
        selected = SessionMemoryManager(SessionMemoryConfig())._select_unsummarized_messages(
            messages,
            "msg-2",
        )

        assert selected == [messages[2]]

    def test_find_message_index_by_context_message_id_uses_stable_message_id(self):
        from openjiuwen.core.context_engine.context.session_memory_manager import (
            find_message_index_by_context_message_id,
        )

        messages = [
            UserMessage(content="before", metadata={"context_message_id": "msg-1"}),
            ToolMessage(content="tool-old", tool_call_id="tc-1", metadata={"context_message_id": "msg-2"}),
        ]

        assert find_message_index_by_context_message_id(messages, "msg-2") == 1
        messages[1].content = "tool-new"
        assert find_message_index_by_context_message_id(messages, "msg-2") == 1

    def test_session_memory_manager_truncates_to_completed_api_round(self):
        from openjiuwen.core.context_engine.context.session_memory_manager import (
            SessionMemoryManager,
            SessionMemoryConfig,
        )

        messages = [
            UserMessage(content="u1"),
            AssistantMessage(content="a1"),
            UserMessage(content="u2"),
            AssistantMessage(content="", tool_calls=[create_tool_call("tc-1", "read_file")]),
            ToolMessage(content="tool", tool_call_id="tc-1"),
        ]

        truncated = SessionMemoryManager(SessionMemoryConfig())._truncate_messages_to_completed_api_round(messages)

        assert truncated == messages[:5]

    def test_group_completed_api_rounds_ends_tool_round_at_tool_message(self):
        from openjiuwen.core.context_engine.context.session_memory_manager import group_completed_api_rounds

        messages = [
            UserMessage(content="u1"),
            AssistantMessage(content="", tool_calls=[create_tool_call("tc-1", "read_file")]),
            ToolMessage(content="tool-1", tool_call_id="tc-1"),
            AssistantMessage(content="a1"),
            UserMessage(content="u2"),
            AssistantMessage(content="a2"),
        ]

        rounds = group_completed_api_rounds(messages)

        assert rounds == [(0, 3), (3, 4), (4, 6)]

    def test_full_compact_invalidates_session_memory_anchor(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor
        from openjiuwen.core.context_engine.context.session_memory_manager import get_session_memory_runtime

        class _DummySession:
            def __init__(self):
                self._state = {
                    "__session_memory__": {
                        "last_summarized_message_count": 9,
                        "notes_upto_message_id": "anchor-id",
                    }
                }

            def get_state(self, key: str):
                return self._state.get(key)

            def update_state(self, update):
                self._state.update(update)

        session = _DummySession()
        processor = FullCompactProcessor(FullCompactProcessorConfig())

        processor._invalidate_session_memory_anchor(MagicMock(_session_ref=session))

        runtime = get_session_memory_runtime(session)
        assert runtime["last_summarized_message_count"] == 0
        assert runtime["notes_upto_message_id"] is None

    def test_get_runtime_state_keeps_message_id_anchor(self):
        from openjiuwen.core.context_engine.context.session_memory_manager import (
            SessionMemoryManager,
            SessionMemoryConfig,
        )

        session = MagicMock()
        session.get_session_id.return_value = "s1"
        session.get_state.return_value = {
            "session_id": "s1",
            "last_summarized_message_count": 99,
            "notes_upto_message_id": "anchor-id",
        }

        runtime = SessionMemoryManager(SessionMemoryConfig())._get_runtime_state(session)

        assert runtime["last_summarized_message_count"] == 99
        assert runtime["notes_upto_message_id"] == "anchor-id"
