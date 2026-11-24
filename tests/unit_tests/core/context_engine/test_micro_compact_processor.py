# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging
from typing import List

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.compressor.micro_compact_processor import (
    MicroCompactProcessor,
    MicroCompactProcessorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.session.agent import Session
from tests.unit_tests.core.context_engine._stream_state_helpers import (
    assert_context_state_pair,
    capture_context_compression_states,
)


def create_tool_call_list(ids: List[str], names: List[str]) -> List[ToolCall]:
    return [
        ToolCall(id=tool_call_id, name=tool_name, type="function", arguments="")
        for tool_call_id, tool_name in zip(ids, names)
    ]


async def create_context_with_micro_compact(
    config: MicroCompactProcessorConfig,
    history_messages=None,
    session=None,
):
    engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
    return await engine.create_context(
        "test_ctx",
        session,
        history_messages=history_messages or [],
        processors=[("MicroCompactProcessor", config)],
    )


class _FakeSummaryResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeSummaryModel:
    def __init__(self, content: str):
        self._content = content

    async def invoke(self, messages=None, tools=None):
        return _FakeSummaryResponse(self._content)


class _RaisingSummaryModel:
    async def invoke(self, messages=None, tools=None):
        raise RuntimeError("summary generation failed")


class TestMicroCompactProcessor:
    @pytest.mark.asyncio
    async def test_trigger_add_messages_clears_old_tool_messages(self):
        """Test that MicroCompactProcessor clears old tool messages when threshold exceeded."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file1", "read_file2"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        # Add 3 read_file1 + 3 read_file2 in a single batch (exceeds limit of 2)
        batch1 = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-1", tool_call_id="tc-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-2"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-2", tool_call_id="tc-2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-3"], ["read_file1"]),
            ),
            ToolMessage(content="file-content-3", tool_call_id="tc-3"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-4"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-4", tool_call_id="tc-4"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-5"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-5", tool_call_id="tc-5"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-6"], ["read_file2"]),
            ),
            ToolMessage(content="file-content-6", tool_call_id="tc-6"),
        ]
        await ctx.add_messages(batch1)

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # With trigger=1 and keep=1, compaction keeps only the newest tool result per tool name.
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "file-content-3",
            config.cleared_marker,
            config.cleared_marker,
            "file-content-6",
        ]

    @pytest.mark.asyncio
    async def test_streams_state_when_micro_compact_processor_triggers(self):
        session = Session(session_id="micro-compact-stream-session")
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config, session=session)

        _, states = await capture_context_compression_states(
            session,
            lambda: ctx.add_messages([
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"], ["read_file"])),
                ToolMessage(content="old-content", tool_call_id="tc-1"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-2"], ["read_file"])),
                ToolMessage(content="new-content", tool_call_id="tc-2"),
                AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-3"], ["read_file"])),
                ToolMessage(content="newest-content", tool_call_id="tc-3"),
            ]),
        )

        assert_context_state_pair(states, processor_type="MicroCompactProcessor")
        assert "modified" in states[1].summary

    @pytest.mark.asyncio
    async def test_keep_recent_per_tool_is_applied_per_tool_name(self):
        """Test that keep_recent_per_tool is applied per tool name."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file", "grep"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        # Add 3 read_file + 3 grep messages in a single batch
        batch = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r1"], ["read_file"]),
            ),
            ToolMessage(content="read-1", tool_call_id="r1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r2"], ["read_file"]),
            ),
            ToolMessage(content="read-2", tool_call_id="r2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["r3"], ["read_file"]),
            ),
            ToolMessage(content="read-3", tool_call_id="r3"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g1"], ["grep"]),
            ),
            ToolMessage(content="grep-1", tool_call_id="g1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g2"], ["grep"]),
            ),
            ToolMessage(content="grep-2", tool_call_id="g2"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["g3"], ["grep"]),
            ),
            ToolMessage(content="grep-3", tool_call_id="g3"),
        ]
        await ctx.add_messages(batch)
        await ctx.add_messages([UserMessage(content="trigger")])

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # With trigger=1 and keep=1, compaction clears all but the newest tool result for each tool.
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "read-3",
            config.cleared_marker,
            config.cleared_marker,
            "grep-3",
        ]

    @pytest.mark.asyncio
    async def test_keep_recent_per_tool_is_applied_independently_across_tools(self):
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file", "grep", "glob"],
            keep_recent_per_tool=2,
        )
        ctx = await create_context_with_micro_compact(config)
        messages = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["read-1"], ["read_file"]),
            ),
            ToolMessage(content="read-old", tool_call_id="read-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["grep-1"], ["grep"]),
            ),
            ToolMessage(content="grep-old", tool_call_id="grep-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["glob-1"], ["glob"]),
            ),
            ToolMessage(content="glob-newer", tool_call_id="glob-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["read-2"], ["read_file"]),
            ),
            ToolMessage(content="read-newest", tool_call_id="read-2"),
        ]
        await ctx.add_messages(messages)

        window = await ctx.get_context_window()
        tool_messages = [msg for msg in window.context_messages if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            "read-old",
            "grep-old",
            "glob-newer",
            "read-newest",
        ]

    @pytest.mark.asyncio
    async def test_cleared_messages_are_not_reprocessed(self):
        """Test that already cleared messages are not reprocessed."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
        )
        history = [
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-1"], ["read_file"]),
            ),
            ToolMessage(content=config.cleared_marker, tool_call_id="tc-1"),
            AssistantMessage(
                content="",
                tool_calls=create_tool_call_list(["tc-2"], ["read_file"]),
            ),
            ToolMessage(content="fresh-content", tool_call_id="tc-2"),
        ]
        ctx = await create_context_with_micro_compact(config, history_messages=history)

        agent_messages = ctx.get_messages()
        tool_messages = [msg for msg in agent_messages if isinstance(msg, ToolMessage)]
        # Cleared marker should remain, fresh content should remain
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            "fresh-content",
        ]

    @pytest.mark.asyncio
    async def test_semantic_critical_tool_is_not_marker_cleared(self):
        """Semantic-critical tools are protected from marker clearing."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["ontology_context"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-1"], ["ontology_context"])),
            ToolMessage(content="ontology-1", tool_call_id="oc-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-2"], ["ontology_context"])),
            ToolMessage(content="ontology-2", tool_call_id="oc-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-3"], ["ontology_context"])),
            ToolMessage(content="ontology-3", tool_call_id="oc-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            "ontology-1",
            "ontology-2",
            "ontology-3",
        ]

    @pytest.mark.asyncio
    async def test_summary_mode_replaces_stale_results_with_per_tool_summary(self):
        """Summary mode replaces stale results with a per-tool LLM digest."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["ontology_context"],
            keep_recent_per_tool=1,
            compact_strategy="summary",
        )
        ctx = await create_context_with_micro_compact(config)
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        processor._model = _FakeSummaryModel("ontology digest")

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-1"], ["ontology_context"])),
            ToolMessage(content="ontology-1", tool_call_id="oc-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-2"], ["ontology_context"])),
            ToolMessage(content="ontology-2", tool_call_id="oc-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-3"], ["ontology_context"])),
            ToolMessage(content="ontology-3", tool_call_id="oc-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        # The digest is written once (on the most recent cleared message); the rest
        # of the stale messages use the short placeholder to keep context small.
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.summary_prefix + "ontology digest",
            "ontology-3",
        ]

    @pytest.mark.asyncio
    async def test_summary_mode_llm_failure_falls_back_to_marker_for_fetchable_tool(self):
        """LLM failure degrades fetchable tools back to the marker placeholder."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
            compact_strategy="summary",
        )
        ctx = await create_context_with_micro_compact(config)
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        processor._model = _RaisingSummaryModel()

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-1"], ["read_file"])),
            ToolMessage(content="read-1", tool_call_id="r-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-2"], ["read_file"])),
            ToolMessage(content="read-2", tool_call_id="r-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-3"], ["read_file"])),
            ToolMessage(content="read-3", tool_call_id="r-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "read-3",
        ]

    @pytest.mark.asyncio
    async def test_summary_mode_llm_failure_keeps_semantic_critical_tool_verbatim(self):
        """LLM failure never degrades semantic-critical results to the marker."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["ontology_context"],
            keep_recent_per_tool=1,
            compact_strategy="summary",
        )
        ctx = await create_context_with_micro_compact(config)
        processor = ctx._processors[0]  # type: ignore[attr-defined]
        processor._model = _RaisingSummaryModel()

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-1"], ["ontology_context"])),
            ToolMessage(content="ontology-1", tool_call_id="oc-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-2"], ["ontology_context"])),
            ToolMessage(content="ontology-2", tool_call_id="oc-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-3"], ["ontology_context"])),
            ToolMessage(content="ontology-3", tool_call_id="oc-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            "ontology-1",
            "ontology-2",
            "ontology-3",
        ]

    @pytest.mark.asyncio
    async def test_multimodal_tool_result_is_not_compacted(self):
        """List content (multimodal tool results) never breaks or participates in compaction."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
        )
        ctx = await create_context_with_micro_compact(config)

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["m-1"], ["read_file"])),
            ToolMessage(content=["screenshot: a.png", {"size": 123}], tool_call_id="m-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["m-2"], ["read_file"])),
            ToolMessage(content=["screenshot: b.png", {"size": 456}], tool_call_id="m-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["m-3"], ["read_file"])),
            ToolMessage(content=["screenshot: c.png", {"size": 789}], tool_call_id="m-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            ["screenshot: a.png", {"size": 123}],
            ["screenshot: b.png", {"size": 456}],
            ["screenshot: c.png", {"size": 789}],
        ]

    @pytest.mark.asyncio
    async def test_summary_mode_without_model_falls_back_to_marker_for_fetchable_tool(self):
        """Without a summarizer model, fetchable tools still fall back to the marker."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["read_file"],
            keep_recent_per_tool=1,
            compact_strategy="summary",
        )
        ctx = await create_context_with_micro_compact(config)

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-1"], ["read_file"])),
            ToolMessage(content="read-1", tool_call_id="r-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-2"], ["read_file"])),
            ToolMessage(content="read-2", tool_call_id="r-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["r-3"], ["read_file"])),
            ToolMessage(content="read-3", tool_call_id="r-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            config.cleared_marker,
            config.cleared_marker,
            "read-3",
        ]

    @pytest.mark.asyncio
    async def test_summary_mode_without_model_keeps_semantic_critical_tool_verbatim(self):
        """Without a summarizer model, semantic-critical results are never marker-cleared."""
        config = MicroCompactProcessorConfig(
            trigger_threshold=1,
            compactable_tool_names=["ontology_context"],
            keep_recent_per_tool=1,
            compact_strategy="summary",
        )
        ctx = await create_context_with_micro_compact(config)

        await ctx.add_messages([
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-1"], ["ontology_context"])),
            ToolMessage(content="ontology-1", tool_call_id="oc-1"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-2"], ["ontology_context"])),
            ToolMessage(content="ontology-2", tool_call_id="oc-2"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["oc-3"], ["ontology_context"])),
            ToolMessage(content="ontology-3", tool_call_id="oc-3"),
        ])

        tool_messages = [msg for msg in ctx.get_messages() if isinstance(msg, ToolMessage)]
        assert [msg.content for msg in tool_messages] == [
            "ontology-1",
            "ontology-2",
            "ontology-3",
        ]

    @pytest.mark.asyncio
    async def test_marker_mode_warns_when_semantic_critical_tool_registered(self, caplog):
        with caplog.at_level(logging.WARNING):
            config = MicroCompactProcessorConfig(
                trigger_threshold=1,
                compactable_tool_names=["ontology_context"],
                keep_recent_per_tool=1,
            )
            await create_context_with_micro_compact(config)
        assert "semantic-critical" in caplog.text

    def test_rebind_model_updates_summarizer(self):
        config = MicroCompactProcessorConfig()
        processor = MicroCompactProcessor(config)
        assert processor._model is None
        assert processor.rebind_model(model=_FakeSummaryModel("digest")) is True
        assert processor._model is not None
