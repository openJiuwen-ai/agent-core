# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ToolResultWindowProcessor sliding-window offload."""

from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
    PERSISTED_OUTPUT_TAG,
)
from openjiuwen.core.context_engine.processor.offloader.tool_result_window_processor import (
    ToolResultWindowProcessorConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.session.agent import Session
from tests.unit_tests.core.context_engine._stream_state_helpers import (
    assert_context_state_pair,
    capture_context_compression_states,
)


def create_mock_sys_operation():
    mock_sys_op = MagicMock()
    mock_fs = MagicMock()
    mock_fs.write_file = AsyncMock()
    mock_sys_op.fs.return_value = mock_fs
    return mock_sys_op, mock_fs


class MockWorkspace:
    def __init__(self, root_path: str):
        self.root_path = root_path


def tool_round(call_id: str, tool_name: str, content: str) -> List:
    """One assistant tool call + the matching tool result."""
    return [
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id=call_id, name=tool_name, type="function", arguments="")],
        ),
        ToolMessage(content=content, tool_call_id=call_id, name=tool_name),
    ]


async def build_context(workspace_root: str, config: ToolResultWindowProcessorConfig):
    workspace = MockWorkspace(root_path=workspace_root)
    mock_sys_op, mock_fs = create_mock_sys_operation()
    engine = ContextEngine(
        ContextEngineConfig(default_window_message_num=100),
        workspace=workspace,
        sys_operation=mock_sys_op,
    )
    context = await engine.create_context(
        context_id="test_ctx",
        session=None,
        history_messages=[],
        processors=[("ToolResultWindowProcessor", config)],
    )
    return context, mock_fs


class TestToolResultWindowProcessor:
    @pytest.mark.asyncio
    async def test_keeps_last_k_and_offloads_older(self, tmp_path):
        config = ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=2, trim_size=10)
        context, _ = await build_context(str(tmp_path), config)

        msgs: List = [UserMessage(content="search please")]
        for i in range(4):
            msgs += tool_round(f"tc-{i}", "grep", f"result-{i}-" + "x" * 100)
        await context.add_messages(msgs)

        result = context.get_messages()
        grep_results = [m for m in result if isinstance(m, ToolMessage) and m.name == "grep"]
        assert len(grep_results) == 4
        # Oldest two are offloaded; newest two keep full content.
        assert isinstance(grep_results[0], OffloadToolMessage)
        assert isinstance(grep_results[1], OffloadToolMessage)
        assert grep_results[0].content.startswith(PERSISTED_OUTPUT_TAG)
        assert not isinstance(grep_results[2], OffloadToolMessage)
        assert not isinstance(grep_results[3], OffloadToolMessage)
        assert grep_results[2].content.startswith("result-2-")
        assert grep_results[3].content.startswith("result-3-")

    @pytest.mark.asyncio
    async def test_only_listed_tools_are_windowed(self, tmp_path):
        config = ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=1)
        context, _ = await build_context(str(tmp_path), config)

        msgs: List = [UserMessage(content="mixed")]
        msgs += tool_round("g0", "grep", "grep-old-" + "x" * 100)
        msgs += tool_round("r0", "read_file", "read-" + "x" * 100)
        msgs += tool_round("g1", "grep", "grep-new-" + "x" * 100)
        await context.add_messages(msgs)

        result = context.get_messages()
        by_name = {}
        for m in result:
            if isinstance(m, ToolMessage):
                by_name.setdefault(m.name, []).append(m)

        # read_file is not in tool_names -> untouched.
        assert len(by_name["read_file"]) == 1
        assert not isinstance(by_name["read_file"][0], OffloadToolMessage)
        # grep window (k=1): oldest offloaded, newest kept.
        assert isinstance(by_name["grep"][0], OffloadToolMessage)
        assert not isinstance(by_name["grep"][1], OffloadToolMessage)

    @pytest.mark.asyncio
    async def test_matches_tool_name_by_suffix(self, tmp_path):
        # A bare suffix matches a registry-prefixed tool name.
        config = ToolResultWindowProcessorConfig(tool_names=["browser_snapshot"], keep_last_k=1)
        context, _ = await build_context(str(tmp_path), config)

        mcp_name = "mcp_playwright-official_browser_snapshot"
        msgs: List = [UserMessage(content="browse")]
        msgs += tool_round("s0", mcp_name, "snap-old-" + "x" * 100)
        msgs += tool_round("s1", mcp_name, "snap-new-" + "x" * 100)
        await context.add_messages(msgs)

        result = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
        assert isinstance(result[0], OffloadToolMessage)  # oldest offloaded via suffix match
        assert not isinstance(result[1], OffloadToolMessage)
        assert result[1].content.startswith("snap-new-")

    @pytest.mark.asyncio
    async def test_no_offload_when_within_window(self, tmp_path):
        config = ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=3)
        context, mock_fs = await build_context(str(tmp_path), config)

        msgs: List = [UserMessage(content="search")]
        for i in range(2):
            msgs += tool_round(f"tc-{i}", "grep", f"result-{i}-" + "x" * 100)
        await context.add_messages(msgs)

        result = context.get_messages()
        grep_results = [m for m in result if isinstance(m, ToolMessage)]
        assert all(not isinstance(m, OffloadToolMessage) for m in grep_results)
        mock_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_add_offloads_one_per_new_result(self, tmp_path):
        config = ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=2)
        context, _ = await build_context(str(tmp_path), config)

        await context.add_messages([UserMessage(content="start")])
        # First two: within window, nothing offloaded.
        await context.add_messages(tool_round("tc-0", "grep", "r0-" + "x" * 100))
        await context.add_messages(tool_round("tc-1", "grep", "r1-" + "x" * 100))
        offloaded = [m for m in context.get_messages() if isinstance(m, OffloadToolMessage)]
        assert len(offloaded) == 0

        # Third result pushes the oldest (r0) out of the window.
        await context.add_messages(tool_round("tc-2", "grep", "r2-" + "x" * 100))
        result = context.get_messages()
        grep_results = [m for m in result if isinstance(m, ToolMessage)]
        assert isinstance(grep_results[0], OffloadToolMessage)
        assert not isinstance(grep_results[1], OffloadToolMessage)
        assert not isinstance(grep_results[2], OffloadToolMessage)

    @pytest.mark.asyncio
    async def test_second_round_skips_already_offloaded(self, tmp_path):
        # An already-offloaded result stays in the window count but must not be
        # re-offloaded on the next round (only the newly-outside one is).
        config = ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=2)
        context, mock_fs = await build_context(str(tmp_path), config)

        await context.add_messages([UserMessage(content="start")])
        for i in range(3):
            await context.add_messages(tool_round(f"tc-{i}", "grep", f"r{i}-" + "x" * 100))
        # Round 1 offloaded r0.
        offloaded_r0 = context.get_messages()[2]
        assert isinstance(offloaded_r0, OffloadToolMessage)
        r0_content = offloaded_r0.content
        assert mock_fs.write_file.call_count == 1

        # Round 2: r3 arrives -> r1 offloaded, r0 skipped (not re-processed).
        await context.add_messages(tool_round("tc-3", "grep", "r3-" + "x" * 100))
        grep_results = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
        assert [isinstance(m, OffloadToolMessage) for m in grep_results] == [True, True, False, False]
        assert grep_results[0].content == r0_content  # r0 untouched
        assert mock_fs.write_file.call_count == 2  # only r1 written this round

    @pytest.mark.asyncio
    async def test_empty_tool_names_is_noop(self, tmp_path):
        config = ToolResultWindowProcessorConfig(tool_names=[], keep_last_k=1)
        context, mock_fs = await build_context(str(tmp_path), config)

        msgs: List = [UserMessage(content="search")]
        for i in range(4):
            msgs += tool_round(f"tc-{i}", "grep", f"result-{i}-" + "x" * 100)
        await context.add_messages(msgs)

        result = context.get_messages()
        assert all(not isinstance(m, OffloadToolMessage) for m in result)
        mock_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_streams_state_when_processor_triggers(self):
        session = Session(session_id="tool-result-window-stream-session")
        engine = ContextEngine(ContextEngineConfig(default_window_message_num=100))
        context = await engine.create_context(
            context_id="test_ctx",
            session=session,
            history_messages=[],
            processors=[
                (
                    "ToolResultWindowProcessor",
                    ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=1),
                )
            ],
        )

        _, states = await capture_context_compression_states(
            session,
            lambda: context.add_messages(
                [
                    UserMessage(content="Run grep"),
                    *tool_round("tc-0", "grep", "r0-" + "x" * 100),
                    *tool_round("tc-1", "grep", "r1-" + "x" * 100),
                    AssistantMessage(content="done"),
                ]
            ),
        )

        assert_context_state_pair(states, processor_type="ToolResultWindowProcessor")
        # k=1 with two grep results -> the oldest one is offloaded.
        assert "modified 1 messages" in states[1].summary


class TestToolResultWindowProcessorConfig:
    def test_default_config_values(self):
        config = ToolResultWindowProcessorConfig()
        assert config.tool_names == []
        assert config.keep_last_k == 3
        assert config.trim_size == 3000
        assert config.offload_file_prefix == "ToolResultWindowProcessor"

    def test_non_positive_window_and_trim_are_rejected(self):
        # keep_last_k / trim_size declare gt=0; guard that constraint.
        with pytest.raises(ValidationError):
            ToolResultWindowProcessorConfig(tool_names=["grep"], keep_last_k=0)
        with pytest.raises(ValidationError):
            ToolResultWindowProcessorConfig(tool_names=["grep"], trim_size=0)
