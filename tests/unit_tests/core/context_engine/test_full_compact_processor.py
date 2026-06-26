# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from tempfile import TemporaryDirectory

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo


def _full_compact_from_context(ctx) -> "FullCompactProcessor":
    from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
        FullCompactProcessor,
    )

    processor = ctx.get_processor_by_type(FullCompactProcessor.processor_type())
    assert processor is not None
    return processor


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
        processor = _full_compact_from_context(ctx)
        triggered = await processor.trigger_add_messages(
            ctx,
            [AssistantMessage(content="new assistant " + ("payload " * 20))],
        )
        assert triggered is True

    @pytest.mark.asyncio
    async def test_on_add_messages_skips_whole_window_llm_on_add_path(self):
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
        with patch.object(
            processor,
            "_build_replacement_messages",
            new_callable=AsyncMock,
        ) as mock_build_replacement:
            event, remaining = await processor.on_add_messages(
                ctx,
                [UserMessage(content="new message")],
            )

        assert event is None
        assert len(remaining) == 1
        mock_build_replacement.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_get_context_window_runs_l2_when_over_threshold(self):
        from openjiuwen.core.context_engine.base import ContextWindow
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=20,
                compression_call_max_tokens=2000,
                messages_to_keep=1,
            )
        )
        ctx = await create_context_with_full_compact(
            processor.config,
            history_messages=[UserMessage(content="history " * 50)],
        )
        window = ContextWindow(
            system_messages=[],
            context_messages=ctx.get_messages(),
            tools=[],
        )
        with patch.object(
            processor,
            "_fallback_whole_window_compact",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_fallback:
            await processor.on_get_context_window(ctx, window)

        mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_get_context_window_debounces_duplicate_l2_same_signature(self):
        from openjiuwen.core.context_engine.base import ContextWindow
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=20,
                compression_call_max_tokens=2000,
                messages_to_keep=1,
            )
        )
        ctx = await create_context_with_full_compact(
            processor.config,
            history_messages=[UserMessage(content="history " * 50)],
        )
        window = ContextWindow(
            system_messages=[],
            context_messages=ctx.get_messages(),
            tools=[],
        )
        with patch.object(
            processor,
            "_fallback_whole_window_compact",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_fallback:
            await processor.on_get_context_window(ctx, window)
            await processor.on_get_context_window(ctx, window)

        assert mock_fallback.await_count == 1

    @pytest.mark.asyncio
    async def test_build_session_memory_messages_uses_committed_notes_while_extraction_in_progress(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        active_messages = [
            UserMessage(content="old-a", metadata={"context_message_id": "msg-1"}),
            AssistantMessage(content="old-b", metadata={"context_message_id": "msg-2"}),
            UserMessage(content="keep-1", metadata={"context_message_id": "msg-3"}),
            AssistantMessage(content="keep-2", metadata={"context_message_id": "msg-4"}),
        ]
        runtime = {
            "is_extracting": True,
            "notes_upto_message_id": "msg-2",
            "memory_path": "unused",
        }

        with (
            patch.object(processor, "_load_session_memory_runtime", return_value=runtime),
            patch.object(processor, "_load_session_memory_text", return_value="committed notes") as mock_load_text,
            patch.object(processor, "build_reinjected_state_messages", return_value=[]),
        ):
            candidate_messages, session_memory_message = await processor._build_session_memory_messages(
                context=MagicMock(),
                prefix=[],
                active_messages=active_messages,
                has_compaction_boundary=False,
            )

        assert candidate_messages is not None
        assert session_memory_message is not None
        mock_load_text.assert_called_once_with(ANY, runtime)
        assert "committed notes" in session_memory_message.content
        assert candidate_messages[2:] == active_messages[2:]

    @pytest.mark.asyncio
    async def test_build_session_memory_messages_returns_none_when_committed_notes_unavailable(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        runtime = {
            "is_extracting": True,
            "notes_upto_message_id": "msg-2",
            "memory_path": "unused",
        }
        with (
            patch.object(processor, "_load_session_memory_runtime", return_value=runtime),
            patch.object(processor, "_load_session_memory_text", return_value=""),
        ):
            candidate_messages, session_memory_message = await processor._build_session_memory_messages(
                context=MagicMock(),
                prefix=[],
                active_messages=[
                    UserMessage(content="keep-1", metadata={"context_message_id": "msg-3"}),
                    AssistantMessage(content="keep-2", metadata={"context_message_id": "msg-4"}),
                ],
                has_compaction_boundary=False,
            )

        assert candidate_messages is None
        assert session_memory_message is None

    @pytest.mark.asyncio
    async def test_build_session_memory_messages_reads_runtime_updated_by_session_memory_manager(self):
        from openjiuwen.core.context_engine.base import ContextWindow
        from openjiuwen.core.context_engine.context.session_memory_manager import (
            SessionMemoryConfig,
            SessionMemoryManager,
            get_session_memory_runtime,
            update_session_memory_runtime,
        )
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        class _DummySession:
            def __init__(self):
                self._state = {}

            def get_state(self, key: str):
                return self._state.get(key)

            def update_state(self, update):
                self._state.update(update)

        processor = FullCompactProcessor(FullCompactProcessorConfig())
        manager = SessionMemoryManager(SessionMemoryConfig())
        active_messages = [
            UserMessage(content="old-a", metadata={"context_message_id": "msg-1"}),
            AssistantMessage(content="old-b", metadata={"context_message_id": "msg-2"}),
            UserMessage(content="keep-1", metadata={"context_message_id": "msg-3"}),
            AssistantMessage(content="keep-2", metadata={"context_message_id": "msg-4"}),
        ]

        with TemporaryDirectory() as temp_dir:
            session = _DummySession()
            session_id = "test-session"
            context = MagicMock(_session_ref=session)
            model_context = MagicMock()
            model_context.token_counter.return_value = None
            ctx = MagicMock(session=session, context=model_context)
            session.get_session_id = MagicMock(return_value=session_id)
            workspace = MagicMock(root_path=temp_dir)
            context_window = ContextWindow(system_messages=[], context_messages=active_messages, tools=[])
            notes_path = manager._get_session_memory_path(workspace, session_id)
            pending_notes_path = manager._get_pending_session_memory_path(notes_path)
            update_session_memory_runtime(
                session,
                {
                    "memory_path": str(notes_path),
                    "pending_memory_path": str(pending_notes_path),
                },
            )

            async def _fake_invoke(*, context_messages, notes_path, current_notes, is_incremental=False, trigger_tokens=0, full_scan_tokens=0):
                _ = context_messages, current_notes, is_incremental, trigger_tokens, full_scan_tokens
                notes_path.write_text("updated notes", encoding="utf-8")

            with patch.object(
                manager._update_agent,
                "invoke",
                new_callable=AsyncMock,
                side_effect=_fake_invoke,
            ):
                await manager._update_background(
                    ctx,
                    workspace,
                    context_window,
                )

            runtime = get_session_memory_runtime(session)
            assert runtime["notes_upto_message_id"] == "msg-4"
            assert runtime["last_summarized_message_count"] == 4
            assert runtime["is_extracting"] is False

            with patch.object(processor, "build_reinjected_state_messages", return_value=[]):
                candidate_messages, session_memory_message = await processor._build_session_memory_messages(
                    context=context,
                    prefix=[],
                    active_messages=active_messages,
                    has_compaction_boundary=False,
                )

        assert candidate_messages is not None
        assert session_memory_message is not None
        assert candidate_messages[2:] == []
        assert session_memory_message.content.startswith(processor.config.session_memory_intro)
        assert "updated notes" in session_memory_message.content

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


class TestFullCompactContextWindowAccounting:
    def _processor(self, **kwargs):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        return FullCompactProcessor(FullCompactProcessorConfig(**kwargs))

    def test_case1_trigger_counts_system_and_tools(self):
        processor = self._processor(trigger_total_tokens=50)
        ctx = MagicMock()
        ctx.get_messages.return_value = [UserMessage(content="hello")]
        ctx.token_counter.return_value = MagicMock(
            count_messages=lambda messages: sum(len(getattr(m, "content", "") or "") for m in messages),
            count_tools=lambda tools: sum(len(t.name) * 10 for t in tools),
        )

        system = [SystemMessage(content="x" * 40)]
        tools = [
            ToolInfo(
                name="search",
                description="search tool",
                parameters={"type": "object", "properties": {}},
            )
        ]
        without_tools = processor._count_context_window_tokens([], ctx.get_messages(), [], ctx)
        with_tools = processor._count_context_window_tokens(system, ctx.get_messages(), tools, ctx)
        assert with_tools > without_tools

        larger_system = processor._count_context_window_tokens(
            [SystemMessage(content="x" * 400)],
            ctx.get_messages(),
            tools,
            ctx,
        )
        assert larger_system > with_tools

    def test_case2_baseline_never_lowers_estimate(self):
        processor = self._processor(trigger_total_tokens=10_000)
        ctx = MagicMock()
        messages = [
            UserMessage(content="u1"),
            AssistantMessage(
                content="a1",
                usage_metadata=UsageMetadata(total_tokens=5000),
            ),
            UserMessage(content="u2" * 100),
        ]
        ctx.token_counter.return_value = MagicMock(
            count_messages=lambda msgs: 100 * len(msgs),
            count_tools=lambda tools: 0,
        )

        full_only = processor._count_context_window_tokens([], messages, [], ctx, use_baseline=False)
        with_baseline = processor._count_context_window_tokens([], messages, [], ctx, use_baseline=True)
        assert with_baseline >= full_only

        no_usage = [
            UserMessage(content="only"),
            AssistantMessage(content="no usage"),
        ]
        assert processor._count_context_window_tokens(
            [], no_usage, [], ctx, use_baseline=True
        ) == processor._count_context_window_tokens(
            [], no_usage, [], ctx, use_baseline=False
        )

        high_baseline_messages = [
            AssistantMessage(content="anchor", usage_metadata=UsageMetadata(total_tokens=9000)),
            UserMessage(content="tail"),
        ]
        via_baseline = processor._count_context_window_tokens(
            [], high_baseline_messages, [], ctx, use_baseline=True
        )
        full_small = processor._count_context_window_tokens(
            [], high_baseline_messages, [], ctx, use_baseline=False
        )
        assert via_baseline > full_small

    @pytest.mark.asyncio
    async def test_case3_replacement_rejected_when_system_tools_push_over_threshold(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=30,
                session_memory_enabled=False,
            )
        )
        ctx = await create_context_with_full_compact(processor.config)
        system_messages = [SystemMessage(content="s" * 200)]
        tools = [
            ToolInfo(
                name="heavy",
                description="d" * 200,
                parameters={"type": "object", "properties": {}},
            )
        ]
        all_messages = [UserMessage(content="payload " * 20)]
        with (
            patch.object(
                processor,
                "_generate_summary",
                new_callable=AsyncMock,
                return_value="Summary:\nshort",
            ),
            patch.object(processor, "build_reinjected_state_messages", return_value=[]),
        ):
            result = await processor._build_replacement_messages(
                ctx,
                all_messages,
                system_messages,
                tools,
            )
        assert result[1] is None

    @pytest.mark.asyncio
    async def test_case4_adaptive_chain_picks_first_fitting_attempt(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=500,
                messages_to_keep=2,
                session_memory_enabled=False,
            )
        )
        ctx = await create_context_with_full_compact(
            processor.config,
            history_messages=[UserMessage(content="history " * 5)],
        )
        calls: list[int] = []

        original_select = processor._select_messages_to_keep

        def _tracking_select(messages, context=None, *, keep_recent=None):
            calls.append(keep_recent if keep_recent is not None else processor._messages_to_keep)
            return original_select(messages, context, keep_recent=keep_recent)

        active = [UserMessage(content="u"), AssistantMessage(content="a")]
        with (
            patch.object(
                processor,
                "_generate_summary",
                new_callable=AsyncMock,
                return_value="Summary:\n" + ("x" * 50),
            ),
            patch.object(processor, "build_reinjected_state_messages", return_value=[]),
            patch.object(processor, "_select_messages_to_keep", side_effect=_tracking_select),
        ):
            result = await processor._try_full_compact_adaptive_chain(
                context=ctx,
                prefix=[],
                active_messages=active,
                system_messages=[],
                tools=[],
                threshold=500,
            )
        assert result is not None
        assert calls[0] == 2


class TestFullCompactQAArtifactManager:
    def test_qa_artifact_manager_binds_model_from_processor_config(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )
        from openjiuwen.core.context_engine.qa_artifact import QAArtifactConfig
        from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig

        model_config = ModelRequestConfig(model="glm-test")
        model_client = ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://test-base",
            verify_ssl=False,
        )
        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                qa_artifact=QAArtifactConfig(enabled=True),
                model=model_config,
                model_client=model_client,
            )
        )

        mgr = processor.qa_artifact_manager
        assert mgr is not None
        update_agent = mgr._overview._sm._update_agent
        assert update_agent._config.model is not None
        assert update_agent._config.model.model_name == "glm-test"
        assert update_agent._config.model_client is model_client
        assert mgr._catalog._model is not None

    @pytest.mark.asyncio
    async def test_context_exposes_qa_artifact_manager_via_public_api(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )
        from openjiuwen.core.context_engine.qa_artifact import QAArtifactConfig

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                qa_artifact=QAArtifactConfig(enabled=True),
            )
        )
        ctx = await create_context_with_full_compact(processor.config)
        bound_processor = _full_compact_from_context(ctx)
        mgr = ctx.get_qa_artifact_manager()
        assert mgr is bound_processor.qa_artifact_manager

    def test_qa_artifact_manager_without_model_stays_unbound(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )
        from openjiuwen.core.context_engine.qa_artifact import QAArtifactConfig

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(qa_artifact=QAArtifactConfig(enabled=True))
        )
        mgr = processor.qa_artifact_manager
        assert mgr is not None
        assert mgr._overview._sm._update_agent._config.model is None
        assert mgr._catalog._model is None


class TestFullCompactHardWindowValidation:
    @pytest.mark.asyncio
    async def test_fallback_uses_actual_tokens_not_stale_usage_baseline(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=100,
                compression_call_max_tokens=200,
                messages_to_keep=1,
            )
        )
        ctx = await create_context_with_full_compact(processor.config)
        compacted = [
            SystemMessage(content="compact summary"),
            AssistantMessage(
                content="tail",
                usage_metadata=UsageMetadata(
                    input_tokens=150,
                    output_tokens=10,
                    total_tokens=160,
                ),
            ),
        ]

        with patch.object(
            processor,
            "_build_replacement_messages",
            new=AsyncMock(return_value=(None, compacted, None)),
        ):
            ok = await processor._fallback_whole_window_compact(
                ctx,
                all_messages=ctx.get_messages(),
                system_messages=[],
                tools=[],
            )

        assert ok is True
        assert processor.consume_deferred_overflow_recovery() is False

    @pytest.mark.asyncio
    async def test_fallback_defers_when_still_over_hard_window_after_reduction(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=50,
                compression_call_max_tokens=80,
                messages_to_keep=1,
            )
        )
        ctx = await create_context_with_full_compact(processor.config)
        compacted = [
            UserMessage(content="chunk-a " * 15),
            UserMessage(content="chunk-b " * 15),
            UserMessage(content="chunk-c " * 15),
        ]

        with patch.object(
            processor,
            "_build_replacement_messages",
            new=AsyncMock(return_value=(None, compacted, None)),
        ):
            ok = await processor._fallback_whole_window_compact(
                ctx,
                all_messages=ctx.get_messages(),
                system_messages=[],
                tools=[],
            )

        assert ok is True
        assert processor.consume_deferred_overflow_recovery() is True
        assert processor.is_force_compact_pending() is True

    @pytest.mark.asyncio
    async def test_fallback_defers_even_for_single_oversized_message(self):
        from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
            FullCompactProcessor,
        )

        processor = FullCompactProcessor(
            FullCompactProcessorConfig(
                trigger_total_tokens=20,
                compression_call_max_tokens=30,
                messages_to_keep=0,
            )
        )
        ctx = await create_context_with_full_compact(processor.config)
        giant = [UserMessage(content="x" * 5000)]

        with patch.object(
            processor,
            "_build_replacement_messages",
            new=AsyncMock(return_value=(None, giant, None)),
        ):
            ok = await processor._fallback_whole_window_compact(
                ctx,
                all_messages=ctx.get_messages(),
                system_messages=[],
                tools=[],
            )

        assert ok is True
        assert processor.consume_deferred_overflow_recovery() is True
        assert processor.is_force_compact_pending() is True
