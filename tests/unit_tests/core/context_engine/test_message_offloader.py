# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from unittest.mock import MagicMock
from typing import List

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloaderConfig,
    OMIT_STRING,
)
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    UserMessage,
    AssistantMessage,
    ToolMessage,
    ToolCall,
)
from openjiuwen.core.session.agent import Session
from tests.unit_tests.core.context_engine._stream_state_helpers import (
    assert_context_state_pair,
    capture_context_compression_states,
)


def create_tool_call_list(ids: List[str]) -> List[ToolCall]:
    return [ToolCall(id=tc, name="test-tool", type="function", arguments="") for tc in ids]


async def create_context_with_offloader(
    offloader_config: MessageOffloaderConfig,
    history_messages=None,
    token_counter=None,
    session=None,
):
    """Create context with MessageOffloader via ContextEngine.create_context."""
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        session,
        history_messages=history_messages or [],
        processors=[("MessageOffloader", offloader_config)],
        token_counter=token_counter,
    )


class TestMessageOffloader:
    """MessageOffloader unit tests: all cases pass MessageOffloader via create_context."""

    # ---------- Config validation: invalid config causes create_context to fail ----------
    @pytest.mark.asyncio
    async def test_invalid_config_trim_size_ge_large_message_threshold_raises(self):
        config = MessageOffloaderConfig(
            trim_size=500,
            large_message_threshold=500,
        )
        with pytest.raises(BaseError):
            await create_context_with_offloader(config)

    @pytest.mark.asyncio
    async def test_invalid_config_trim_size_gt_large_message_threshold_raises(self):
        config = MessageOffloaderConfig(
            trim_size=600,
            large_message_threshold=500,
        )
        with pytest.raises(BaseError):
            await create_context_with_offloader(config)

    @pytest.mark.asyncio
    async def test_invalid_config_messages_to_keep_ge_threshold_raises(self):
        config = MessageOffloaderConfig(
            messages_to_keep=20,
            messages_threshold=20,
        )
        with pytest.raises(BaseError):
            await create_context_with_offloader(config)

    @pytest.mark.asyncio
    async def test_invalid_config_messages_to_keep_gt_threshold_raises(self):
        config = MessageOffloaderConfig(
            messages_to_keep=25,
            messages_threshold=20,
        )
        with pytest.raises(BaseError):
            await create_context_with_offloader(config)

    @pytest.mark.asyncio
    async def test_valid_config_creates_context_successfully(self):
        config = MessageOffloaderConfig(
            messages_to_keep=10,
            messages_threshold=20,
            large_message_threshold=500,
            trim_size=100,
        )
        ctx = await create_context_with_offloader(config)
        assert ctx is not None
        assert len(ctx) == 0

    # ---------- messages_to_keep: no offload when below threshold ----------
    @pytest.mark.asyncio
    async def test_below_messages_to_keep_no_offload(self):
        config = MessageOffloaderConfig(
            messages_threshold=20,
            messages_to_keep=10,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [UserMessage(content="a")] * 5
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        assert len(result) == 5
        assert not any(isinstance(m, OffloadMixin) for m in result)

    # ---------- messages_threshold: offload triggered when exceeded ----------
    @pytest.mark.asyncio
    async def test_above_messages_threshold_triggers_offload(self):
        config = MessageOffloaderConfig(
            messages_threshold=4,
            tokens_threshold=100000,
            large_message_threshold=30,
            trim_size=10,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [
            UserMessage(content="u1"),
            ToolMessage(content="x" * 100, tool_call_id="tc-1"),
            UserMessage(content="u2"),
            UserMessage(content="u3"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        assert len(result) == 4
        offloaded = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded) == 0

        await ctx.add_messages(UserMessage(content="u4"))
        result = ctx.get_messages()
        assert len(result) == 5
        offloaded = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded) == 1
        reloaded = await ctx.reloader_tool().invoke(
            dict(offload_handle=offloaded[0].offload_handle, offload_type="in_memory")
        )
        assert "x" * 100 in reloaded

    @pytest.mark.asyncio
    async def test_streams_state_when_offload_processor_triggers(self):
        session = Session(session_id="message-offloader-stream-session")
        config = MessageOffloaderConfig(
            messages_threshold=1,
            tokens_threshold=100000,
            large_message_threshold=30,
            trim_size=10,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config, session=session)

        _, states = await capture_context_compression_states(
            session,
            lambda: ctx.add_messages([
                ToolMessage(content="x" * 100, tool_call_id="tc-stream"),
                UserMessage(content="trigger"),
            ]),
        )

        assert_context_state_pair(states, processor_type="MessageOffloader")
        assert "modified 1 messages" in states[1].summary

    # ---------- tokens_threshold: offload triggered when token count exceeded ----------
    @pytest.mark.asyncio
    async def test_above_tokens_threshold_triggers_offload(self):
        mock_counter = MagicMock()
        mock_counter.count_messages = MagicMock(return_value=200)
        config = MessageOffloaderConfig(
            messages_threshold=100,
            tokens_threshold=50,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config, token_counter=mock_counter)
        msgs = [
            UserMessage(content="u"),
            ToolMessage(content="x" * 20, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        offloaded = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded) >= 1
        assert offloaded[0].content[:5] + OMIT_STRING in offloaded[0].content

    # ---------- offload_message_type: only offload configured roles ----------
    @pytest.mark.asyncio
    async def test_offload_only_configured_roles(self):
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=20,
            trim_size=8,
            offload_message_type=["user", "assistant"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [
            UserMessage(content="U" * 50),
            AssistantMessage(content="A" * 50),
            ToolMessage(content="T" * 50, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        assert isinstance(result[0], OffloadMixin)
        assert isinstance(result[1], OffloadMixin)
        assert isinstance(result[2], ToolMessage)
        assert result[2].content == "T" * 50

    # ---------- large_message_threshold: short messages are not offloaded ----------
    @pytest.mark.asyncio
    async def test_short_messages_not_offloaded(self):
        config = MessageOffloaderConfig(
            messages_threshold=3,
            large_message_threshold=100,
            trim_size=10,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [
            ToolMessage(content="short", tool_call_id="tc-1"),
            UserMessage(content="u"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        assert result[0].content == "short"
        assert not isinstance(result[0], OffloadMixin)

    # ---------- messages_to_keep: preserves most recent N messages ----------
    @pytest.mark.asyncio
    async def test_messages_to_keep_preserves_recent(self):
        config = MessageOffloaderConfig(
            messages_threshold=10,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=3,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        tools = [ToolMessage(content="x" * 50, tool_call_id=f"tc-{i}") for i in range(5)]
        await ctx.add_messages(tools)
        result = ctx.get_messages()
        assert len(result) == 5
        offloaded = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded) <= 2

    # ---------- keep_last_round: preserves final assistant of last round ----------
    @pytest.mark.asyncio
    async def test_keep_last_round_preserves_final_assistant(self):
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=True,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [
            UserMessage(content="u1"),
            AssistantMessage(content="a1", tool_calls=create_tool_call_list(["tc-1"])),
            ToolMessage(content="x" * 50, tool_call_id="tc-1"),
            AssistantMessage(content="a2-final"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        final_assistant = next(m for m in result if m.content == "a2-final")
        assert not isinstance(final_assistant, OffloadMixin)

    # ---------- trim_size: content is trimmed after offload ----------
    @pytest.mark.asyncio
    async def test_offload_trims_content(self):
        config = MessageOffloaderConfig(
            messages_threshold=1,
            large_message_threshold=30,
            trim_size=10,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        long_content = "a" * 200
        msgs = [
            UserMessage(content="u"),
            ToolMessage(content=long_content, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        offload_msg = result[1]
        assert isinstance(offload_msg, OffloadMixin)
        assert offload_msg.content.startswith("a" * 10)
        assert "[[OFFLOAD:" in offload_msg.content
        reloaded = await ctx.reloader_tool().invoke(
            dict(offload_handle=offload_msg.offload_handle, offload_type="in_memory")
        )
        assert long_content in reloaded

    # ---------- tool_call_id is preserved ----------
    @pytest.mark.asyncio
    async def test_offload_preserves_tool_call_id(self):
        config = MessageOffloaderConfig(
            messages_threshold=1,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
        )
        ctx = await create_context_with_offloader(config)
        full_content = "Very long tool response: " + "x" * 100
        msgs = [
            UserMessage(content="u"),
            ToolMessage(content=full_content, tool_call_id="critical-tc-123"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        offload_msg = result[1]
        assert offload_msg.tool_call_id == "critical-tc-123"
        reloaded = await ctx.reloader_tool().invoke(
            dict(offload_handle=offload_msg.offload_handle, offload_type="in_memory")
        )
        assert "Very long tool response" in reloaded

    # ========== Complex end-to-end functional tests ==========

    @pytest.mark.asyncio
    async def test_full_flow_add_messages_triggers_offload(self):
        """Full flow: add_messages triggers offload, content can be reloaded."""
        config = MessageOffloaderConfig(
            messages_threshold=4,
            tokens_threshold=100000,
            large_message_threshold=40,
            trim_size=15,
            offload_message_type=["tool", "user"],
            messages_to_keep=2,
            keep_last_round=True,
        )
        ctx = await create_context_with_offloader(config)
        msgs = [
            UserMessage(content="u1"),
            AssistantMessage(content="a1", tool_calls=create_tool_call_list(["tc-1"])),
            ToolMessage(content="T" * 80, tool_call_id="tc-1"),
            AssistantMessage(content="a2"),
            UserMessage(content="U" * 80),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        assert len(result) == 5
        offloaded = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded) >= 1
        for m in offloaded:
            reloaded = await ctx.reloader_tool().invoke(
                dict(offload_handle=m.offload_handle, offload_type="in_memory")
            )
            assert len(reloaded) > 0

    @pytest.mark.asyncio
    async def test_multi_round_dialogue_offload_old_keep_recent(self):
        """Multi-round dialogue: old tools offloaded, last round final assistant preserved."""
        config = MessageOffloaderConfig(
            messages_threshold=10,
            large_message_threshold=30,
            trim_size=10,
            offload_message_type=["tool"],
            messages_to_keep=8,
            keep_last_round=True,
        )
        ctx = await create_context_with_offloader(config)
        rounds = []
        for r in range(3):
            rounds.append([
                UserMessage(content=f"user-round-{r}"),
                AssistantMessage(content=f"ai-{r}", tool_calls=create_tool_call_list([f"tc-{r}"])),
                ToolMessage(content="LONG_TOOL_RESPONSE " * 5, tool_call_id=f"tc-{r}"),
                AssistantMessage(content=f"ai-final-{r}"),
            ])
        all_msgs = [m for r in rounds for m in r]
        await ctx.add_messages(all_msgs)
        result = ctx.get_messages()
        assert len(result) == 12
        last_final = next(m for m in result if m.content == "ai-final-2")
        assert not isinstance(last_final, OffloadMixin)
        offloaded_tools = [m for m in result if isinstance(m, OffloadMixin)]
        assert len(offloaded_tools) >= 1
        reloaded = await ctx.reloader_tool().invoke(
            dict(offload_handle=offloaded_tools[0].offload_handle, offload_type="in_memory")
        )
        assert "LONG_TOOL_RESPONSE" in reloaded

    # ========== protected_tool_names: tool protection tests ==========

    @pytest.mark.asyncio
    async def test_protected_tool_by_name_not_offloaded(self):
        """Tool messages from protected tool names are never offloaded."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["reload_original_context_messages"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200
        msgs = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-reload",
                    name="reload_original_context_messages",
                    type="function",
                    arguments="{}"
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-reload"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert not isinstance(tool_msg, OffloadMixin)
        assert tool_msg.content == long_content

    @pytest.mark.asyncio
    async def test_unprotected_tool_is_offloaded(self):
        """Tool messages from unprotected tools are offloaded when large."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["reload_original_context_messages"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200
        msgs = [
            UserMessage(content="u"),
            AssistantMessage(content="a", tool_calls=create_tool_call_list(["tc-other"])),
            ToolMessage(content=long_content, tool_call_id="tc-other"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert isinstance(tool_msg, OffloadMixin)
        assert tool_msg.content.startswith("X" * 5)

    @pytest.mark.asyncio
    async def test_protected_tool_with_pattern_matches(self):
        """Tool messages are protected when tool_name:pattern format matches args."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["view_file:*.md"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200
        msgs = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-1",
                    name="view_file",
                    type="function",
                    arguments='{"path": "README.md"}'
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert not isinstance(tool_msg, OffloadMixin)
        assert tool_msg.content == long_content

    @pytest.mark.asyncio
    async def test_protected_tool_with_pattern_not_matches(self):
        """Tool messages are offloaded when tool_name matches but pattern does not."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["read_file:*USER.md"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200
        msgs = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-data",
                    name="read_file",
                    type="function",
                    arguments='{"path": "data.txt"}'
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-data"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert isinstance(tool_msg, OffloadMixin)

    @pytest.mark.asyncio
    async def test_protected_tool_with_wildcard_pattern(self):
        """fnmatch wildcard patterns (* and ?) work for argument matching."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["read:path/to/*.py"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200

        # Matching path should be protected
        msgs_match = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-1",
                    name="read",
                    type="function",
                    arguments='{"path": "path/to/main.py"}'
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs_match)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert not isinstance(tool_msg, OffloadMixin)

    @pytest.mark.asyncio
    async def test_protected_tool_with_question_mark_pattern(self):
        """Question mark (?) wildcard matches single character."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["read:file?.txt"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200

        # "file1.txt" matches "file?.txt"
        msgs_match = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-1",
                    name="read",
                    type="function",
                    arguments='{"path": "file1.txt"}'
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs_match)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert not isinstance(tool_msg, OffloadMixin)

        # "file12.txt" does NOT match "file?.txt"
        msgs_no_match = [
            UserMessage(content="u2"),
            AssistantMessage(
                content="a2",
                tool_calls=[ToolCall(
                    id="tc-2",
                    name="read",
                    type="function",
                    arguments='{"path": "file12.txt"}'
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-2"),
        ]
        await ctx.add_messages(msgs_no_match)
        result = ctx.get_messages()
        tool_msg = result[5]
        assert isinstance(tool_msg, OffloadMixin)

    @pytest.mark.asyncio
    async def test_multiple_protected_patterns(self):
        """Multiple protected patterns can be configured together."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=[
                "reload_original_context_messages",
                "view_file:*.md",
                "read:*.py",
            ],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200

        # Test reload_original_context_messages is protected (exact match)
        msgs_reload = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[ToolCall(
                    id="tc-reload",
                    name="reload_original_context_messages",
                    type="function",
                    arguments="{}"
                )]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-reload"),
        ]
        await ctx.add_messages(msgs_reload)
        result = ctx.get_messages()
        assert not isinstance(result[2], OffloadMixin)

    @pytest.mark.asyncio
    async def test_dict_tool_call_format(self):
        """Tool calls as dict format are handled correctly for pattern matching."""
        config = MessageOffloaderConfig(
            messages_threshold=2,
            large_message_threshold=10,
            trim_size=5,
            offload_message_type=["tool"],
            messages_to_keep=None,
            keep_last_round=False,
            protected_tool_names=["read_file:*.json"],
        )
        ctx = await create_context_with_offloader(config)
        long_content = "X" * 200

        # dict format tool_call
        msgs = [
            UserMessage(content="u"),
            AssistantMessage(
                content="a",
                tool_calls=[{
                    "id": "tc-1",
                    "name": "read_file",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "config.json"}'
                    }
                }]
            ),
            ToolMessage(content=long_content, tool_call_id="tc-1"),
        ]
        await ctx.add_messages(msgs)
        result = ctx.get_messages()
        tool_msg = result[2]
        assert not isinstance(tool_msg, OffloadMixin)
        assert tool_msg.content == long_content
