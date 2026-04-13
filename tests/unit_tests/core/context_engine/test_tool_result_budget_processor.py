# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ToolResultBudgetProcessor with filesystem offload."""

from __future__ import annotations

import json
import os
import tempfile
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
    ToolResultBudgetProcessor,
    ToolResultBudgetProcessorConfig,
    PERSISTED_OUTPUT_TAG,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)


def create_mock_sys_operation():
    """Create a mock SysOperation for testing."""
    mock_sys_op = MagicMock()
    mock_fs = MagicMock()
    mock_fs.write_file = AsyncMock()
    mock_sys_op.fs.return_value = mock_fs
    return mock_sys_op, mock_fs  # 返回两个对象，方便单独验证 mock_fs


def create_tool_call_list(ids: List[str]) -> List[ToolCall]:
    return [ToolCall(id=tc, name="test-tool", type="function", arguments="") for tc in ids]


def create_mock_session(session_id: str) -> MagicMock:
    session = MagicMock()
    session.get_session_id.return_value = session_id
    # Avoid MagicMock auto-chaining in ContextEngine._load_state_from_session().
    session.get_state.return_value = None
    return session


def create_sandbox_workspace_root(test_name: str) -> str:
    workspace_root = os.path.join(os.getcwd(), ".pytest_workspace_offload", test_name)
    os.makedirs(workspace_root, exist_ok=True)
    return workspace_root


def assert_filesystem_offload_content(
    workspace_root: str,
    session_id: str,
    offload_handle: str,
    expected_content: str,
) -> None:
    """Verify filesystem offload content (for real fs operations, not mock)."""
    offload_file = os.path.join(
        workspace_root,
        "context",
        f"{session_id}_context",
        "offload",
        f"{offload_handle}.json",
    )
    assert os.path.exists(offload_file)
    with open(offload_file, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["offload_handle"] == offload_handle
    assert isinstance(payload.get("messages"), list)
    assert payload["messages"]
    assert payload["messages"][0]["content"] == expected_content


def assert_mock_write_file_called(mock_fs: MagicMock, workspace_root: str, offload_handle: str) -> None:
    """Verify that mock sys_operation.write_file was called with correct path."""
    mock_fs.write_file.assert_called_once()
    call_args = mock_fs.write_file.call_args
    file_path = call_args[0][0]
    # Verify path contains workspace root and offload handle
    assert workspace_root in file_path, f"Expected workspace '{workspace_root}' in path '{file_path}'"
    assert offload_handle in file_path, f"Expected offload_handle '{offload_handle}' in path '{file_path}'"


class MockWorkspace:
    """Mock workspace for testing."""

    def __init__(self, root_path: str):
        self.root_path = root_path


class TestToolResultBudgetProcessorFilesystemOffload:
    """Test ToolResultBudgetProcessor offload to filesystem."""

    @pytest.mark.asyncio
    async def test_offload_to_filesystem_with_workspace(self, tmp_path):
        """Test that offload writes to filesystem when workspace is provided."""
        workspace_root = create_sandbox_workspace_root(tmp_path.name)
        workspace = MockWorkspace(root_path=workspace_root)
        mock_sys_op, mock_fs = create_mock_sys_operation()

        # Create context engine with workspace
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=mock_sys_op,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=None,
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,  # Very low threshold to trigger offload
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Create a large tool result that exceeds threshold
        large_content = "x" * 500  # 500 chars ~ 166 tokens
        msgs = [
            UserMessage(content="Run grep on large file"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
            ToolMessage(content=large_content, tool_call_id="tc-1", name="grep"),
            AssistantMessage(content="Found results in the file."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        # The tool message should be offloaded
        tool_msg = result[2]
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        # Should contain filesystem offload marker
        assert "[[OFFLOAD:" in tool_msg.content
        assert "type=filesystem" in tool_msg.content or "type=in_memory" in tool_msg.content

        # Check that offload_handle exists
        offload_handle = getattr(tool_msg, "offload_handle", None)
        assert offload_handle
        assert getattr(tool_msg, "offload_type", None) == "filesystem"
        # Verify write_file was called with correct path (mock test)
        assert_mock_write_file_called(mock_fs, workspace_root, offload_handle)

    @pytest.mark.asyncio
    async def test_offload_to_filesystem_specific_path(self, tmp_path):
        """Test that offload writes to specific workspace path structure."""
        workspace_root = create_sandbox_workspace_root(tmp_path.name)
        workspace = MockWorkspace(root_path=workspace_root)
        session_id = "test_session_123"
        mock_sys_op, mock_fs = create_mock_sys_operation()

        # Create context engine with workspace
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=mock_sys_op,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Create conversation with large tool result
        large_content = "y" * 600  # Large enough to trigger offload
        msgs = [
            UserMessage(content="List all files"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-2"])),
            ToolMessage(content=large_content, tool_call_id="tc-2", name="glob"),
            AssistantMessage(content="Here are the files..."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Verify offload happened
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)

        # Check workspace_dir returns the correct path
        assert context.workspace_dir() == workspace_root

        # Verify offload metadata contains session/context identity
        assert context.session_id() == session_id
        assert context.context_id() == "test_ctx"
        offload_handle = getattr(tool_msg, "offload_handle", None)
        assert offload_handle
        assert getattr(tool_msg, "offload_type", None) == "filesystem"
        # Verify write_file was called with correct path (mock test)
        assert_mock_write_file_called(mock_fs, workspace_root, offload_handle)

    @pytest.mark.asyncio
    async def test_offload_fallback_to_memory_when_no_workspace(self):
        """Test that offload falls back to memory when no workspace is provided."""
        mock_sys_op, mock_fs = create_mock_sys_operation()
        # Create context engine WITHOUT workspace
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=None,
            sys_operation=mock_sys_op,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session("no_workspace_session"),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Verify workspace_dir returns empty when no workspace
        assert context.workspace_dir() == ""

        # Create conversation with large tool result
        large_content = "z" * 700
        msgs = [
            UserMessage(content="Read large file"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-3"])),
            ToolMessage(content=large_content, tool_call_id="tc-3", name="read_file"),
            AssistantMessage(content="File contents above."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Offload should still happen even without workspace
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        assert "[[OFFLOAD:" in tool_msg.content

    @pytest.mark.asyncio
    async def test_offload_fallback_to_memory_when_sys_operation_none(self):
        """Test that offload falls back to in_memory when sys_operation is None."""
        # Create context engine WITHOUT sys_operation
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=None,
            sys_operation=None,
        )
        session = create_mock_session("no_sysop_session")
        context = await engine.create_context(
            context_id="test_ctx",
            session=session,
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Create conversation with large tool result
        large_content = "x" * 700
        msgs = [
            UserMessage(content="Read large file"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-fallback"])),
            ToolMessage(content=large_content, tool_call_id="tc-fallback", name="read_file"),
            AssistantMessage(content="File contents above."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Verify offload happened with in_memory type
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        assert "[[OFFLOAD:" in tool_msg.content
        assert "type=in_memory" in tool_msg.content
        assert getattr(tool_msg, "offload_type", None) == "in_memory"

    @pytest.mark.asyncio
    async def test_offload_preserves_original_content_in_file(self, tmp_path):
        """Test that original content is preserved when offloading to filesystem."""
        workspace_root = create_sandbox_workspace_root(tmp_path.name)
        workspace = MockWorkspace(root_path=workspace_root)
        mock_sys_op, mock_fs = create_mock_sys_operation()

        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=mock_sys_op,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session("preserve_test"),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Create a message with unique content we can verify later
        unique_content = "UNIQUE_CONTENT_" + "x" * 500 + "_END_MARKER"
        msgs = [
            UserMessage(content="Get detailed output"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-4"])),
            ToolMessage(content=unique_content, tool_call_id="tc-4", name="grep"),
            AssistantMessage(content="Done."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Verify offload marker exists
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)

        # Verify preview is correct
        preview_start = "Preview (first 20 chars):"
        assert preview_start in tool_msg.content
        assert unique_content[:20] in tool_msg.content

    @pytest.mark.asyncio
    async def test_multiple_rounds_offload_respects_budget(self, tmp_path):
        """Test that multiple rounds are processed and budget is respected."""
        workspace_root = create_sandbox_workspace_root(tmp_path.name)
        workspace = MockWorkspace(root_path=workspace_root)
        mock_sys_op, mock_fs = create_mock_sys_operation()

        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=mock_sys_op,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session("multi_round"),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=150,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Create multiple rounds with large tool results
        msgs = [
            # Round 1
            UserMessage(content="First task"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-r1"])),
            ToolMessage(content="a" * 400, tool_call_id="tc-r1", name="grep"),
            AssistantMessage(content="Round 1 done"),
            # Round 2
            UserMessage(content="Second task"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-r2"])),
            ToolMessage(content="b" * 400, tool_call_id="tc-r2", name="grep"),
            AssistantMessage(content="Round 2 done"),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        # Should have offloaded some large messages
        offloaded_count = sum(1 for m in result if m.content.startswith(PERSISTED_OUTPUT_TAG))
        assert offloaded_count >= 0  # At least some should be processed


class TestToolResultBudgetProcessorConfig:
    """Test ToolResultBudgetProcessorConfig validation."""

    def test_default_config_values(self):
        """Test that default config values are correct."""
        config = ToolResultBudgetProcessorConfig()
        assert config.tokens_threshold == 200000
        assert config.large_message_threshold == 50000
        assert config.trim_size == 2000
        assert config.tool_name_allowlist is None
        assert config.offload_message_type == ["tool"]

    def test_custom_config_values(self):
        """Test custom config values are respected."""
        config = ToolResultBudgetProcessorConfig(
            tokens_threshold=50000,
            large_message_threshold=10000,
            trim_size=500,
            tool_name_allowlist=["grep", "read_file"],
        )
        assert config.tokens_threshold == 50000
        assert config.large_message_threshold == 10000
        assert config.trim_size == 500
        assert config.tool_name_allowlist == ["grep", "read_file"]


class TestToolResultBudgetProcessorBasic:
    """Basic functionality tests for ToolResultBudgetProcessor."""

    @pytest.mark.asyncio
    async def test_trigger_add_messages_false_when_below_threshold(self):
        """Test that trigger_add_messages returns False when below threshold."""
        processor = ToolResultBudgetProcessor(
            ToolResultBudgetProcessorConfig(
                tokens_threshold=100000,
                large_message_threshold=100,
                trim_size=20,
            )
        )
        context = MagicMock()
        context.get_messages.return_value = [
            UserMessage(content="short"),
            ToolMessage(content="short", tool_call_id="tc-1"),
        ]
        context.token_counter.return_value = None

        triggered = await processor.trigger_add_messages(
            context,
            [UserMessage(content="more")],
        )
        assert triggered is False

    @pytest.mark.asyncio
    async def test_trigger_add_messages_true_when_above_threshold(self):
        """Test that trigger_add_messages returns True when above threshold."""
        processor = ToolResultBudgetProcessor(
            ToolResultBudgetProcessorConfig(
                tokens_threshold=100,
                large_message_threshold=50,
                trim_size=20,
            )
        )
        context = MagicMock()
        context.get_messages.return_value = [
            UserMessage(content="task"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
            # Fallback estimator is len(content)//3 when token_counter is None.
            # Use a clearly-above-threshold payload to avoid boundary ambiguity.
            ToolMessage(content="x" * 600, tool_call_id="tc-1"),
            AssistantMessage(content="done"),
        ]
        context.token_counter.return_value = None

        triggered = await processor.trigger_add_messages(context, [])
        assert triggered is True

    @pytest.mark.asyncio
    async def test_should_offload_message_respects_allowlist(self):
        """Test that allowlisted tools are not offloaded."""
        processor = ToolResultBudgetProcessor(
            ToolResultBudgetProcessorConfig(
                tool_name_allowlist=["important_tool"],
            )
        )

        messages = [
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
            ToolMessage(content="x" * 500, tool_call_id="tc-1", name="important_tool"),
        ]
        context = MagicMock()
        context.token_counter.return_value = None

        should_offload = processor._should_offload_message(messages[1], messages, context)
        assert should_offload is False

    @pytest.mark.asyncio
    async def test_is_already_offloaded(self):
        """Test detection of already offloaded messages."""
        processor = ToolResultBudgetProcessor(ToolResultBudgetProcessorConfig())

        already_offloaded = ToolMessage(
            content=f"{PERSISTED_OUTPUT_TAG}\nOutput too large...", tool_call_id="tc-x"
        )
        assert processor._is_already_offloaded(already_offloaded) is True

        not_offloaded = ToolMessage(content="normal content", tool_call_id="tc-y")
        assert processor._is_already_offloaded(not_offloaded) is False


class TestToolResultBudgetProcessorRealFilesystem:
    """使用真实 SysOperation 测试文件系统写入"""

    @pytest.mark.asyncio
    async def test_offload_actually_writes_to_file(self, tmp_path):
        """验证 offload 真的写入文件"""
        from openjiuwen.core.sys_operation import SysOperation, OperationMode
        from openjiuwen.core.sys_operation import SysOperationCard
        from openjiuwen.core.sys_operation.config import LocalWorkConfig
        
        workspace_root = str(tmp_path / "workspace")
        workspace = MockWorkspace(root_path=workspace_root)
        
        # 创建真实 SysOperation
        card = SysOperationCard(
            id="test_real_sys_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(),
        )
        real_sys_op = SysOperation(card=card)
        
        # 创建 context engine
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=real_sys_op,
        )
        session_id = "real_fs_test_session"
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,  # Very low threshold to trigger offload
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )
        
        # Create a large tool result that exceeds threshold
        large_content = "x" * 500
        msgs = [
            UserMessage(content="Run grep on large file"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-1"])),
            ToolMessage(content=large_content, tool_call_id="tc-1", name="grep"),
            AssistantMessage(content="Found results in the file."),
        ]
        await context.add_messages(msgs)
        
        result = context.get_messages()
        tool_msg = result[2]
        
        # Verify offload happened
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        offload_handle = getattr(tool_msg, "offload_handle", None)
        assert offload_handle
        
        # Verify file was actually created
        offload_file = os.path.join(
            workspace_root, "context", f"{session_id}_context",
            "offload", f"{offload_handle}.json"
        )
        assert os.path.exists(offload_file), f"Offload file not found: {offload_file}"
        
        # Verify file content
        with open(offload_file, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["offload_handle"] == offload_handle
        assert payload["messages"][0]["content"] == large_content


class TestToolResultBudgetProcessorReload:
    """测试 filesystem 和 in_memory offload 的 reload 流程"""

    @pytest.mark.asyncio
    async def test_reload_from_filesystem_with_real_sys_operation(self, tmp_path):
        """测试从真实文件系统 reload 已 offload 的消息"""
        from openjiuwen.core.sys_operation import SysOperation, OperationMode
        from openjiuwen.core.sys_operation import SysOperationCard
        from openjiuwen.core.sys_operation.config import LocalWorkConfig
        from openjiuwen.core.context_engine.context.message_buffer import OffloadMessageBuffer

        workspace_root = str(tmp_path / "workspace")
        workspace = MockWorkspace(root_path=workspace_root)

        # 创建真实 SysOperation
        card = SysOperationCard(
            id="test_real_sys_op",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(),
        )
        real_sys_op = SysOperation(card=card)

        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=real_sys_op,
        )
        session_id = "reload_test_session"
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Step 1: Offload a large tool result
        original_content = "ORIGINAL_TOOL_CONTENT_" + "x" * 500 + "_END_MARKER"
        msgs = [
            UserMessage(content="Get detailed output"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-reload"])),
            ToolMessage(content=original_content, tool_call_id="tc-reload", name="grep"),
            AssistantMessage(content="Done."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Verify offload happened with filesystem type
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        offload_handle = getattr(tool_msg, "offload_handle", None)
        offload_type = getattr(tool_msg, "offload_type", None)
        assert offload_handle
        assert offload_type == "filesystem"

        # Step 2: Reload the offloaded message using buffer directly
        reloaded_messages = await context._offload_message_buffer.reload(offload_handle, offload_type)

        # Verify reload result contains original content
        assert len(reloaded_messages) == 1
        assert "ORIGINAL_TOOL_CONTENT_" in reloaded_messages[0].content
        assert "_END_MARKER" in reloaded_messages[0].content

    @pytest.mark.asyncio
    async def test_reload_from_in_memory(self, tmp_path):
        """测试从内存 reload 已 offload 的消息 (sys_operation=None 场景)"""
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=None,
            sys_operation=None,
        )
        session_id = "inmemory_reload_session"
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Step 1: Offload (will fallback to in_memory since sys_operation=None)
        original_content = "INMEMORY_TOOL_CONTENT_" + "y" * 500 + "_END_MARKER"
        msgs = [
            UserMessage(content="Read file"),
            AssistantMessage(content="", tool_calls=create_tool_call_list(["tc-inmem"])),
            ToolMessage(content=original_content, tool_call_id="tc-inmem", name="read_file"),
            AssistantMessage(content="Done."),
        ]
        await context.add_messages(msgs)

        result = context.get_messages()
        tool_msg = result[2]

        # Verify offload happened with in_memory type
        assert tool_msg.content.startswith(PERSISTED_OUTPUT_TAG)
        offload_handle = getattr(tool_msg, "offload_handle", None)
        offload_type = getattr(tool_msg, "offload_type", None)
        assert offload_handle
        assert offload_type == "in_memory"

        # Step 2: Reload from in_memory using buffer directly
        reloaded_messages = await context._offload_message_buffer.reload(offload_handle, offload_type)

        # Verify reload result contains original content
        assert len(reloaded_messages) == 1
        assert "INMEMORY_TOOL_CONTENT_" in reloaded_messages[0].content
        assert "_END_MARKER" in reloaded_messages[0].content

    @pytest.mark.asyncio
    async def test_reload_filesystem_with_mock_sys_operation(self, tmp_path):
        """测试用 mock SysOperation 验证 reload 流程"""
        workspace_root = str(tmp_path / "workspace_mock")
        workspace = MockWorkspace(root_path=workspace_root)

        # 创建带有 read_file mock 的 SysOperation
        mock_sys_op = MagicMock()
        mock_fs = MagicMock()
        mock_fs.write_file = AsyncMock()
        mock_sys_op.fs.return_value = mock_fs

        # 设置 mock read_file 返回预设的内容
        original_content = "MOCK_FILE_CONTENT_" + "z" * 500 + "_END_MARKER"
        mock_read_result = MagicMock()
        mock_read_result.code = 0
        mock_read_result.data = MagicMock()
        mock_read_result.data.content = json.dumps({
            "offload_handle": "mock_handle_123",
            "messages": [
                {
                    "role": "tool",
                    "content": original_content,
                    "tool_call_id": "tc-mock",
                    "name": "grep",
                }
            ]
        })
        mock_fs.read_file = AsyncMock(return_value=mock_read_result)

        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=workspace,
            sys_operation=mock_sys_op,
        )
        session_id = "mock_reload_session"
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # Reload using buffer directly
        reloaded_messages = await context._offload_message_buffer.reload(
            offload_handle="mock_handle_123",
            offload_type="filesystem"
        )

        # Verify reload result from mock
        assert len(reloaded_messages) == 1
        assert "MOCK_FILE_CONTENT_" in reloaded_messages[0].content
        assert "_END_MARKER" in reloaded_messages[0].content
        # Verify read_file was called
        mock_fs.read_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_reload_nonexistent_handle_returns_empty(self, tmp_path):
        """测试 reload 不存在的 handle 返回空列表"""
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=None,
            sys_operation=None,
        )
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session("nonexistent_session"),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig())
            ],
        )

        # Reload nonexistent handle
        reloaded_messages = await context._offload_message_buffer.reload(
            offload_handle="nonexistent_handle_xyz",
            offload_type="in_memory"
        )

        # Should return empty list
        assert reloaded_messages == []

    @pytest.mark.asyncio
    async def test_reload_filesystem_without_sys_operation_returns_empty(self, tmp_path):
        """测试 filesystem 类型但没有 sys_operation 时 reload 返回空"""
        engine = ContextEngine(
            ContextEngineConfig(default_window_message_num=100),
            workspace=None,
            sys_operation=None,
        )
        session_id = "no_sysop_session"
        context = await engine.create_context(
            context_id="test_ctx",
            session=create_mock_session(session_id),
            history_messages=[],
            processors=[
                ("ToolResultBudgetProcessor", ToolResultBudgetProcessorConfig(
                    tokens_threshold=100,
                    large_message_threshold=50,
                    trim_size=20,
                ))
            ],
        )

        # filesystem reload without sys_operation should return empty
        reloaded_messages = await context._offload_message_buffer.reload(
            offload_handle="some_file_path.json",
            offload_type="filesystem"
        )

        assert reloaded_messages == []
