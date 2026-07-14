# coding: utf-8

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_dump import (
    COMPRESSION_DUMP_DIR_ENV,
)
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    ForkedDialogueCompressor,
    ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import CompressionResult
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


def _context(*, workspace_root: str = "", session_id: str = "session-1"):
    context = MagicMock()
    context.session_id.return_value = session_id
    context.context_id.return_value = "context-1"
    context.token_counter.return_value = None
    context.workspace_dir.return_value = workspace_root
    return context


def _context_window(messages):
    return ContextWindow(system_messages=[], context_messages=messages, tools=[])


def _attach_executor(compressor, summary: str = "compact summary"):
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content=summary)))
    compressor._compression_executor = executor
    return executor


def _compressible_window():
    # Long historical padding collapses to a short summary, so _has_compression_benefit
    # is True and the dump path actually runs.
    return _context_window(
        [
            UserMessage(content="Historical request"),
            AssistantMessage(content="historical padding " * 600),
            UserMessage(content="Current task"),
        ]
    )


@pytest.mark.asyncio
async def test_dump_disabled_by_default_creates_no_files(tmp_path):
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig(compression_dump_dir=str(tmp_path)))
    _attach_executor(compressor)
    context = _context()
    window = _compressible_window()

    await compressor.on_get_context_window(context, window)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_dump_writes_request_and_post_compression_context(tmp_path):
    compressor = ForkedDialogueCompressor(
        ForkedDialogueCompressorConfig(
            enable_compression_dump=True,
            compression_dump_dir=str(tmp_path),
        )
    )
    _attach_executor(compressor)
    context = _context()
    window = _compressible_window()

    await compressor.on_get_context_window(context, window)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))

    assert payload["processor_type"] == "ForkedDialogueCompressor"
    assert payload["session_id"] == "session-1"

    request = payload["compression_request"]
    assert request["prompt"]
    assert request["exclude_recent_messages"] >= 0
    # messages_sent_to_model is exactly what the executor builds for the model call:
    # system_messages + to-be-compressed context + the compression prompt as a user turn.
    sent = request["messages_sent_to_model"]
    assert isinstance(sent, list)
    assert sent[-1]["role"] == "user"
    assert sent[-1]["content"] == request["prompt"]

    after = payload["context_after"]
    assert isinstance(after["messages"], list)
    assert after["total_tokens_after"] > 0

    benefit = payload["benefit"]
    assert benefit["tokens_before"] > benefit["tokens_after"]
    assert benefit["reduction"] > 0
    assert 0.0 < benefit["reduction_ratio"] <= 1.0

    assert payload["compression_response"]["raw_content"] == "compact summary"
    assert "memory_block_dialogue" in after["messages"][0]["content"]


@pytest.mark.asyncio
async def test_dump_expands_session_template_dir(tmp_path):
    compressor = ForkedDialogueCompressor(
        ForkedDialogueCompressorConfig(
            enable_compression_dump=True,
            compression_dump_dir=str(
                tmp_path / "context" / "{session_id}_context" / "debug_artifacts" / "compression"
            ),
        )
    )
    _attach_executor(compressor)
    context = _context(session_id="session/with spaces")
    window = _compressible_window()

    await compressor.on_get_context_window(context, window)

    files = list(
        (tmp_path / "context" / "session-with-spaces_context" / "debug_artifacts" / "compression").glob("*.json")
    )
    assert len(files) == 1


@pytest.mark.asyncio
async def test_dump_uses_env_var_when_config_dir_unset(tmp_path, monkeypatch):
    monkeypatch.setenv(COMPRESSION_DUMP_DIR_ENV, str(tmp_path))
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig(enable_compression_dump=True))
    _attach_executor(compressor)
    context = _context()
    window = _compressible_window()

    await compressor.on_get_context_window(context, window)

    assert any(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_dump_uses_session_workspace_default_dir(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    compressor = ForkedDialogueCompressor(ForkedDialogueCompressorConfig(enable_compression_dump=True))
    _attach_executor(compressor)
    context = _context(workspace_root=str(workspace), session_id="s-7")
    window = _compressible_window()

    await compressor.on_get_context_window(context, window)

    expected_dir = workspace / "context" / "s-7_context" / "compression_logs"
    files = list(expected_dir.glob("*.json"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_dump_failure_does_not_break_compression(tmp_path):
    # Pointing the dump dir at an existing *file* makes os.makedirs raise
    # (path exists but is not a directory); the dump's defensive except must
    # swallow it and compression must still take effect.
    blocking_file = tmp_path / "not_a_dir"
    blocking_file.write_text("block", encoding="utf-8")

    compressor = ForkedDialogueCompressor(
        ForkedDialogueCompressorConfig(
            enable_compression_dump=True,
            compression_dump_dir=str(blocking_file),
        )
    )
    _attach_executor(compressor)
    context = _context()
    window = _compressible_window()

    event, updated_window = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert updated_window.context_messages[0].content  # compression still applied
