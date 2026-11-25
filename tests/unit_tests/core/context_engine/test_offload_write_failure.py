# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid

import pytest

from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.context.message_buffer import OffloadMessageBuffer
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (
    MessageOffloader,
    MessageOffloaderConfig,
)
from openjiuwen.core.context_engine.processor.offloader.tool_result_budget_processor import (
    ToolResultBudgetProcessor,
    ToolResultBudgetProcessorConfig,
)
from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import LocalWorkConfig, SysOperation, SysOperationCard
from openjiuwen.harness.workspace.workspace import Workspace

_CALL = ToolCall(id="call", name="bash", type="function", arguments="{}")
_TOOL_RESULT = "\n".join(f"line {i}: output" for i in range(3000))


def _offload_setup(tmp_path, processor, write_blocked):
    operation = SysOperation(SysOperationCard(
        id=f"offload-write-{uuid.uuid4().hex}",
        work_config=LocalWorkConfig(sandbox_root=[str(tmp_path)]),
    ))
    session = Session(session_id="offload-write")
    if write_blocked:
        # 卸载目录的位置被同名文件占住，write_file 返回失败码而不抛异常
        offload_dir = tmp_path / "context" / f"{session.get_session_id()}_context" / "offload"
        offload_dir.parent.mkdir(parents=True)
        offload_dir.touch()
    context = SessionModelContext(
        "offload-write", session.get_session_id(), ContextEngineConfig(context_window_tokens=8192),
        history_messages=[],
        processors=[processor],
        token_counter=TiktokenCounter(), workspace=Workspace(root_path=str(tmp_path)),
        sys_operation=operation, session_ref=session,
    )
    buffer = OffloadMessageBuffer()
    buffer.set_sys_operation(operation)
    buffer.set_workspace_info(str(tmp_path), session.get_session_id())
    return context, buffer


async def _tool_preview(context):
    window = await context.get_context_window()
    return next(item for item in window.context_messages if getattr(item, "tool_call_id", None) == _CALL.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("write_blocked", [False, True])
async def test_large_tool_result_survives_offload_write_failure(tmp_path, write_blocked):
    processor = MessageSummaryOffloader(MessageSummaryOffloaderConfig())
    context, buffer = _offload_setup(tmp_path, processor, write_blocked)
    await context.add_messages([
        AssistantMessage(content="", tool_calls=[_CALL]),
        ToolMessage(tool_call_id=_CALL.id, content=_TOOL_RESULT),
    ])
    preview = await _tool_preview(context)

    if write_blocked:
        # 原文没写进文件时，不能换成指向不存在文件的占位，也不能截断。
        assert "[[OFFLOAD:" not in preview.content
        assert preview.content == _TOOL_RESULT
        return
    assert "[[OFFLOAD:" in preview.content
    [restored] = await buffer.reload(preview.offload_handle, "filesystem")
    assert restored.content == _TOOL_RESULT


@pytest.mark.asyncio
@pytest.mark.parametrize("write_blocked", [False, True])
async def test_legacy_offloader_keeps_original_in_memory_when_write_fails(tmp_path, write_blocked):
    processor = MessageOffloader(MessageOffloaderConfig(messages_threshold=1, tokens_threshold=10))
    context, buffer = _offload_setup(tmp_path, processor, write_blocked)
    await context.add_messages([
        AssistantMessage(content="", tool_calls=[_CALL]),
        ToolMessage(tool_call_id=_CALL.id, content=_TOOL_RESULT),
    ])
    await context.add_messages([AssistantMessage(content="next")])
    preview = await _tool_preview(context)

    assert len(preview.content) < len(_TOOL_RESULT)
    if write_blocked:
        # 写入失败时退回内存卸载，原文保存在上下文的卸载缓存里，不指向不存在的文件
        assert preview.offload_type == "in_memory"
        [restored] = context.save_state()["offload_messages"][preview.offload_handle]
        assert restored.content == _TOOL_RESULT
        return
    assert preview.offload_type == "filesystem"
    [offload_file] = (tmp_path / "context").rglob("*.json")
    assert offload_file.read_text(encoding="utf-8").startswith("{")
    [restored] = await buffer.reload(preview.offload_handle, "filesystem")
    assert restored.content == _TOOL_RESULT


@pytest.mark.asyncio
@pytest.mark.parametrize("write_blocked", [False, True])
async def test_tool_result_budget_placeholder_has_path_only_when_file_written(tmp_path, write_blocked):
    processor = ToolResultBudgetProcessor(ToolResultBudgetProcessorConfig(
        tokens_threshold=100, large_message_threshold=50, trim_size=20,
    ))
    context, buffer = _offload_setup(tmp_path, processor, write_blocked)
    await context.add_messages([
        UserMessage(content="run"),
        AssistantMessage(content="", tool_calls=[_CALL]),
        ToolMessage(tool_call_id=_CALL.id, content=_TOOL_RESULT),
    ])
    preview = await _tool_preview(context)

    if write_blocked:
        assert preview.offload_type == "in_memory"
        assert "path=" not in preview.content
        [restored] = context.save_state()["offload_messages"][preview.offload_handle]
        assert restored.content == _TOOL_RESULT
        return
    assert preview.offload_type == "filesystem"
    [offload_file] = (tmp_path / "context").rglob("*.json")
    assert f"path={offload_file}" in preview.content
    [restored] = await buffer.reload(preview.offload_handle, "filesystem")
    assert restored.content == _TOOL_RESULT
