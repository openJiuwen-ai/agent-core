from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine import CompressionRecallConfig, ContextEngine, ContextEngineConfig
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.processor.forked.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.base import PrefixCompactSpan
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.recall import archive as archive_module
from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import (
    CompressionArchive,
    _new_memory_id,
    _recall_root,
    archive_compression_messages,
    delete_compression_archive,
)
from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import normalize_markdown
from openjiuwen.core.context_engine.processor.forked.compressor.recall.retriever import (
    CompressionRecallError,
    recall_compressed_context,
)
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionResult,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage


def _context(
    tmp_path: Path,
    session_id: str = "session-1",
    recall_config: CompressionRecallConfig | None = None,
):
    context = MagicMock()
    context.workspace_dir.return_value = str(tmp_path)
    context.session_id.return_value = session_id
    context.context_id.return_value = "context-1"
    context.token_counter.return_value = None
    context.compression_recall_config.return_value = recall_config or CompressionRecallConfig()
    return context


def _archive(tmp_path: Path, messages, *, session_id: str = "session-1", preceding=None, **kwargs):
    return archive_compression_messages(
        context=_context(tmp_path, session_id),
        processor_type="DialogueCompressor",
        original_messages=list(messages),
        messages_to_compress=list(messages),
        preceding_messages=list(preceding or []),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_context_exposes_engine_wide_compression_recall_config():
    recall_config = CompressionRecallConfig(
        enabled=True,
        chunk_size_tokens=1200,
        chunk_overlap_tokens=120,
    )
    engine = ContextEngine(
        ContextEngineConfig(compression_recall_config=recall_config),
    )

    context = await engine.create_context()

    assert context.compression_recall_config() == recall_config
    assert context.compression_recall_config() is not recall_config


def test_archive_writes_turn_index_raw_messages_and_readable_chunks(tmp_path):
    messages = [
        UserMessage(content="How should database retries work?"),
        AssistantMessage(content="Use exponential backoff for database timeout errors."),
        UserMessage(content="How should cache eviction work?"),
        AssistantMessage(content="Evict least recently used cache entries."),
    ]

    archive = _archive(tmp_path, messages)
    archive_path = Path(archive.path)

    manifest = json.loads((archive_path / "manifest.json").read_text(encoding="utf-8"))
    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    raw_messages = json.loads((archive_path / "raw_messages.json").read_text(encoding="utf-8"))

    assert manifest["session_id"] == "session-1"
    assert manifest["turn_count"] == 2
    assert turns[0]["query"] == "How should database retries work?"
    assert turns[0]["answer"].startswith("Use exponential backoff")
    assert len(turns[0]["chunk_paths"]) == 1
    chunk = (archive_path / turns[0]["chunk_paths"][0]).read_text(encoding="utf-8")
    assert "## User" in chunk
    assert "## Assistant" in chunk
    assert raw_messages[0]["content"] == "How should database retries work?"


def test_archive_extracts_query_text_from_structured_user_content(tmp_path):
    messages = [
        UserMessage(content=[{"type": "text", "text": "How should database retries work?"}]),
        AssistantMessage(content="Use exponential backoff for database timeout errors."),
        UserMessage(content=[{"type": "text", "text": "如何配置缓存淘汰策略？"}]),
        AssistantMessage(content="Evict least recently used cache entries."),
    ]

    archive = _archive(tmp_path, messages)
    archive_path = Path(archive.path)

    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((archive_path / "manifest.json").read_text(encoding="utf-8"))
    assert turns[0]["query"] == "How should database retries work?"
    assert turns[1]["query"] == "如何配置缓存淘汰策略？"
    assert archive_path.name == archive.memory_id
    assert manifest["schema_version"] == 1
    assert manifest["archive_query"] == "如何配置缓存淘汰策略？"


def test_archive_structured_content_without_text_keeps_empty_query_in_turns(tmp_path):
    messages = [
        AssistantMessage(content="Working on it."),
    ]

    archive = _archive(
        tmp_path,
        messages,
        preceding=[UserMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}])],
    )

    archive_path = Path(archive.path)
    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert archive_path.name == archive.memory_id
    assert turns[0]["query"] == ""


def test_archive_unwraps_channel_envelope_for_query(tmp_path):
    messages = [
        UserMessage(content='你收到一条消息：\n{"content": "帮我查一下昨天的报错", "source": "wecom"}'),
        AssistantMessage(content="查看日志发现是超时。"),
    ]

    archive = _archive(tmp_path, messages)
    archive_path = Path(archive.path)

    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((archive_path / "manifest.json").read_text(encoding="utf-8"))
    assert turns[0]["query"] == "帮我查一下昨天的报错"
    assert archive_path.name == archive.memory_id
    assert manifest["archive_query"] == "帮我查一下昨天的报错"


def test_archive_keeps_json_first_line_as_genuine_user_text(tmp_path):
    messages = [
        UserMessage(content='{"content": "这不是信封"}'),
        AssistantMessage(content="收到。"),
    ]

    archive = _archive(tmp_path, messages)

    turns = [json.loads(line) for line in (Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert turns[0]["query"] == '{"content": "这不是信封"}'


def test_archive_ignores_session_memory_block_when_picking_query(tmp_path):
    messages = [
        UserMessage(content="real user question"),
        AssistantMessage(content="first answer"),
        UserMessage(content="<memory_block_session>\nnotes\n</memory_block_session>"),
        AssistantMessage(content="second answer"),
    ]

    archive = _archive(tmp_path, messages)

    turns = [json.loads(line) for line in (Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(turns) == 1
    assert turns[0]["query"] == "real user question"


def test_current_round_style_archive_uses_preceding_user_as_turn_query(tmp_path):
    messages = [
        AssistantMessage(content="Investigating the failing request."),
        ToolMessage(content="database timeout in worker", tool_call_id="call-1"),
    ]

    archive = _archive(
        tmp_path,
        messages,
        preceding=[UserMessage(content="Fix the database timeout")],
    )
    turn = json.loads((Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert turn["query"] == "Fix the database timeout"
    chunk = (Path(archive.path) / turn["chunk_paths"][0]).read_text(encoding="utf-8")
    assert "Fix the database timeout" not in chunk
    assert "database timeout in worker" in chunk


def test_archive_chunk_filenames_use_turn_chunk_ids(tmp_path):
    messages = [
        UserMessage(content="How should database retries work?"),
        AssistantMessage(content="Use exponential backoff for database timeout errors."),
    ]

    archive = _archive(tmp_path, messages, chunk_size_tokens=12, chunk_overlap_tokens=2)
    archive_path = Path(archive.path)
    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]

    chunk_names = [Path(path).name for path in turns[0]["chunk_paths"]]
    assert chunk_names
    assert all(re.fullmatch(r"turn_000_chunk_\d{3}\.md", name) for name in chunk_names)
    for name in chunk_names:
        assert (archive_path / "chunks" / name).is_file()

    # 新文件名下召回链路不受影响
    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="database retries backoff",
    )
    assert result["chunks"]


def test_recall_selects_one_turn_and_at_most_two_chunks(tmp_path):
    messages = [
        UserMessage(content="database timeout"),
        AssistantMessage(content="database timeout retry backoff " * 30),
        UserMessage(content="cache eviction"),
        AssistantMessage(content="least recently used cache policy"),
    ]
    archive = _archive(tmp_path, messages, chunk_size_tokens=12, chunk_overlap_tokens=2)

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="database timeout retry",
    )

    assert result["matched_turn"]["turn_id"] == "turn_000"
    assert 1 <= len(result["chunks"]) <= 2
    assert all("database" in chunk["content"] for chunk in result["chunks"])
    turn = json.loads((Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(turn["chunk_paths"]) > 2


def test_markdown_normalization_removes_formatting_but_keeps_code_and_paths():
    markdown = """---
turn_id: turn_000
---

## Tool Result
- Failure in `/repo/app.py`
```python
def __init__():
    pass
raise RuntimeError("database timeout")
```
"""

    normalized = normalize_markdown(markdown)

    assert "turn_id" not in normalized
    assert "##" not in normalized
    assert "```" not in normalized
    assert "/repo/app.py" in normalized
    assert "__init__" in normalized
    assert 'RuntimeError("database timeout")' in normalized


def test_recall_is_strictly_isolated_between_colliding_session_names(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.processor.forked.compressor.recall.archive._new_memory_id",
        lambda _: "same-memory-id",
    )
    first = _archive(
        tmp_path,
        [UserMessage(content="first secret"), AssistantMessage(content="alpha private answer")],
        session_id="tenant/a",
    )
    second = _archive(
        tmp_path,
        [UserMessage(content="second secret"), AssistantMessage(content="beta private answer")],
        session_id="tenant-a",
    )

    first_result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="tenant/a",
        memory_id="same-memory-id",
        query="alpha private",
    )
    second_result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="tenant-a",
        memory_id="same-memory-id",
        query="beta private",
    )

    assert first.path != second.path
    assert "alpha private answer" in first_result["chunks"][0]["content"]
    assert "beta private answer" in second_result["chunks"][0]["content"]
    assert "beta private answer" not in first_result["chunks"][0]["content"]


def test_recall_across_archives_finds_content_in_older_archive(tmp_path):
    older = _archive(
        tmp_path,
        [
            UserMessage(content="database timeout retry policy"),
            AssistantMessage(content="use exponential backoff for database retries"),
        ],
    )
    newer = _archive(
        tmp_path,
        [
            UserMessage(content="cache eviction strategy"),
            AssistantMessage(content="evict least recently used cache entries"),
        ],
    )

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        query="database backoff retry",
    )

    assert result["chunks"]
    assert result["chunks"][0]["memory_id"] == older.memory_id
    assert 0 < result["chunks"][0]["score"] <= 1
    assert all("raw_score" in chunk for chunk in result["chunks"])
    archive_ids = [item["memory_id"] for item in result["archives_in_session"]]
    assert archive_ids == sorted(archive_ids)
    assert set(archive_ids) == {older.memory_id, newer.memory_id}
    assert result["archives_in_session"][0]["turn_count"] == 1
    assert Path(result["recall_root"]).name == "compression_recall"
    assert result["matched_turn"]["memory_id"] == older.memory_id


def test_recall_across_archives_miss_still_reports_archives(tmp_path):
    _archive(
        tmp_path,
        [UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
    )

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        query="completely-unrelated-zebra",
    )

    assert result["chunks"] == []
    assert result["matched_turn"] is None
    assert len(result["archives_in_session"]) == 1
    assert result["recall_root"]


def test_recall_rejects_manifest_from_another_session(tmp_path):
    archive = _archive(
        tmp_path,
        [UserMessage(content="query"), AssistantMessage(content="matching answer")],
    )
    manifest_path = Path(archive.path) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["session_id"] = "another-session"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CompressionRecallError, match="does not belong"):
        recall_compressed_context(
            workspace_dir=str(tmp_path),
            session_id="session-1",
            memory_id=archive.memory_id,
            query="matching",
        )


def test_recall_returns_archive_path_when_bm25_has_no_match(tmp_path):
    archive = _archive(
        tmp_path,
        [UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
    )

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="completely-unrelated-zebra",
    )

    assert result["matched_turn"] is None
    assert result["matched_turns"] == []
    assert result["chunks"] == []
    assert result["truncated"] is False
    assert result["returned_tokens"] == 0
    assert result["archive_path"] == archive.path


def test_recall_rejects_chunk_symlink_escape(tmp_path):
    archive = _archive(
        tmp_path,
        [UserMessage(content="database"), AssistantMessage(content="retry database")],
    )
    archive_path = Path(archive.path)
    turn = json.loads((archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])
    chunk_path = archive_path / turn["chunk_paths"][0]
    outside = tmp_path / "outside.md"
    outside.write_text("database secret outside the session archive", encoding="utf-8")
    chunk_path.unlink()
    chunk_path.symlink_to(outside)

    with pytest.raises(CompressionRecallError, match="symlink|escapes"):
        recall_compressed_context(
            workspace_dir=str(tmp_path),
            session_id="session-1",
            memory_id=archive.memory_id,
            query="database",
        )


@pytest.mark.asyncio
async def test_compressor_adds_marker_only_after_archive_succeeds(tmp_path):
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="compact database state")))
    compressor._compression_executor = executor
    context = _context(tmp_path, recall_config=CompressionRecallConfig(enabled=True))
    messages = [
        UserMessage(content="Historical database task"),
        AssistantMessage(content="database work " * 600),
        UserMessage(content="Current task"),
    ]
    window = ContextWindow(system_messages=[], context_messages=messages, tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert "[[COMPRESSION_RECALL: id=" in updated.context_messages[0].content
    memory_id = updated.context_messages[0].content.split("[[COMPRESSION_RECALL: id=", 1)[1].split("]]", 1)[0]
    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=memory_id,
        query="database work",
    )
    assert result["chunks"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compressor", "messages"),
    [
        (
            DialogueCompressor(DialogueCompressorConfig()),
            [
                UserMessage(content="Historical dialogue"),
                AssistantMessage(content="dialogue history " * 600),
                UserMessage(content="Current request"),
            ],
        ),
        (
            CurrentRoundCompressor(CurrentRoundCompressorConfig()),
            [
                UserMessage(content="Current execution"),
                AssistantMessage(content="execution trace " * 600),
            ],
        ),
        (
            RoundLevelCompressor(RoundLevelCompressorConfig(keep_recent_messages=1)),
            [
                UserMessage(content="Historical round"),
                AssistantMessage(content="round history " * 600),
                UserMessage(content="Current request"),
            ],
        ),
    ],
)
async def test_all_three_forked_compressors_archive_and_emit_marker(tmp_path, compressor, messages):
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="compact state")))
    compressor._compression_executor = executor
    context = _context(tmp_path, recall_config=CompressionRecallConfig(enabled=True))
    window = ContextWindow(system_messages=[], context_messages=messages, tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert any("[[COMPRESSION_RECALL: id=" in str(message.content) for message in updated.context_messages)


def test_empty_turn_writes_no_chunk_files_but_keeps_turn_record(tmp_path):
    archive = _archive(
        tmp_path,
        [AssistantMessage(content="")],
        preceding=[UserMessage(content="fallback query")],
    )
    archive_path = Path(archive.path)

    turns = [json.loads(line) for line in (archive_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(turns) == 1
    assert turns[0]["query"] == "fallback query"
    assert turns[0]["chunk_paths"] == []
    assert list((archive_path / "chunks").iterdir()) == []

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="fallback query",
    )
    assert result["matched_turn"]["turn_id"] == "turn_000"
    assert result["chunks"] == []


def test_recall_returns_chunks_across_multiple_matched_turns(tmp_path):
    messages = [
        UserMessage(content="tell me about zebra stripes"),
        AssistantMessage(content="zebras have distinctive stripes"),
        UserMessage(content="how to fix database timeout"),
        AssistantMessage(content="database timeout needs retry backoff"),
        UserMessage(content="how to speed up database queries"),
        AssistantMessage(content="database queries need a covering index"),
    ]
    archive = _archive(tmp_path, messages)

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="database",
    )

    assert {turn["turn_id"] for turn in result["matched_turns"]} == {"turn_001", "turn_002"}
    assert result["matched_turn"]["turn_id"] in {"turn_001", "turn_002"}
    assert {chunk["turn_id"] for chunk in result["chunks"]} == {"turn_001", "turn_002"}
    assert result["truncated"] is False
    assert result["returned_tokens"] > 0


def test_recall_drops_chunks_beyond_content_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_RECALL_MAX_CONTENT_TOKENS", "30")
    messages = [
        UserMessage(content="database timeout"),
        AssistantMessage(content="database timeout retry backoff " * 30),
    ]
    archive = _archive(tmp_path, messages, chunk_size_tokens=12, chunk_overlap_tokens=2)
    turn = json.loads((Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="database timeout retry",
    )

    assert result["truncated"] is True
    assert result["returned_tokens"] <= 30
    assert len(result["chunks"]) < len(turn["chunk_paths"])


@pytest.mark.parametrize("invalid_value", ["not-a-number", "0", "-5"])
def test_recall_budget_env_falls_back_to_default_on_invalid_value(tmp_path, monkeypatch, invalid_value):
    monkeypatch.setenv("OPENJIUWEN_RECALL_MAX_CONTENT_TOKENS", invalid_value)
    archive = _archive(
        tmp_path,
        [UserMessage(content="database"), AssistantMessage(content="retry database with backoff")],
    )

    result = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id="session-1",
        memory_id=archive.memory_id,
        query="database retry",
    )

    assert result["chunks"]
    assert result["truncated"] is False


def test_archive_splits_turn_exceeding_default_chunk_size(tmp_path):
    messages = [
        UserMessage(content="database"),
        AssistantMessage(content="database retry backoff " * 2000),
    ]

    archive = _archive(tmp_path, messages)

    turn = json.loads((Path(archive.path) / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(turn["chunk_paths"]) >= 2


def test_recall_chunk_config_rejects_overlap_not_smaller_than_size():
    with pytest.raises(ValueError, match="chunk_overlap_tokens"):
        CompressionRecallConfig(chunk_size_tokens=1000, chunk_overlap_tokens=1000)


def test_delete_compression_archive_removes_only_matching_archive_dir(tmp_path):
    archive = _archive(
        tmp_path,
        [UserMessage(content="database"), AssistantMessage(content="retry database")],
    )

    delete_compression_archive(archive)
    assert not Path(archive.path).exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(OSError, match="does not match"):
        delete_compression_archive(CompressionArchive(memory_id=archive.memory_id, path=str(outside)))
    assert outside.exists()


@pytest.mark.asyncio
async def test_compressor_rolls_back_archive_when_benefit_check_fails(tmp_path):
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="bloated summary " * 2000)))
    compressor._compression_executor = executor
    context = _context(tmp_path, recall_config=CompressionRecallConfig(enabled=True))
    messages = [
        UserMessage(content="Historical task"),
        AssistantMessage(content="tiny result"),
        UserMessage(content="Current task"),
    ]
    window = ContextWindow(system_messages=[], context_messages=list(messages), tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is None
    assert updated.context_messages == messages
    assert all("COMPRESSION_RECALL" not in str(message.content) for message in updated.context_messages)
    context.set_messages.assert_not_called()
    leftover_archives = list(tmp_path.glob("context/*_context/compression_recall/*"))
    assert leftover_archives == []


@pytest.mark.asyncio
async def test_compressor_without_recall_compresses_without_archive(tmp_path):
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="compact state")))
    compressor._compression_executor = executor
    context = _context(tmp_path)
    messages = [
        UserMessage(content="Historical request"),
        AssistantMessage(content="historical work " * 600),
        UserMessage(content="Current task"),
    ]
    window = ContextWindow(system_messages=[], context_messages=messages, tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is not None
    assert all("COMPRESSION_RECALL" not in str(message.content) for message in updated.context_messages)
    assert list(tmp_path.glob("context/*_context/compression_recall")) == []


@pytest.mark.asyncio
async def test_compressor_passes_recall_chunk_config_to_archive(tmp_path):
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="compact state")))
    compressor._compression_executor = executor
    context = _context(
        tmp_path,
        recall_config=CompressionRecallConfig(
            enabled=True,
            chunk_size_tokens=1000,
            chunk_overlap_tokens=100,
        ),
    )
    messages = [
        UserMessage(content="Historical database task"),
        AssistantMessage(content="database work " * 600),
        UserMessage(content="Current task"),
    ]
    window = ContextWindow(system_messages=[], context_messages=messages, tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is not None
    archive_dirs = list(tmp_path.glob("context/*_context/compression_recall/*"))
    assert len(archive_dirs) == 1
    turn = json.loads((archive_dirs[0] / "turns.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert len(turn["chunk_paths"]) >= 2


def _workspace_for_recall_root_length(length: int, session_id: str = "s") -> Path:
    parent = Path(tempfile.gettempdir()) / "ocw-recall-budget"
    parent.mkdir(parents=True, exist_ok=True)
    probe = _recall_root(parent / "x", session_id)
    tail = str(probe)[len(str(parent / "x")) :]
    name_length = length - len(str(parent)) - 1 - len(tail)
    if name_length < 1:
        raise RuntimeError(f"cannot build a recall root of length {length}")
    workspace = parent / ("p" * name_length)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    root = _recall_root(workspace, session_id)
    if len(str(root)) != length:
        raise RuntimeError(f"recall root length {len(str(root))} != {length}")
    return workspace


def _path_of_length(length: int) -> Path:
    return Path("x" * length)


def test_path_budget_rejects_directory_over_247_and_file_over_259():
    exceeds = archive_module._exceeds_windows_path_budget
    assert exceeds(_path_of_length(247), kind="directory") is False
    assert exceeds(_path_of_length(259), kind="file") is False
    assert exceeds(_path_of_length(248), kind="directory") is True
    assert exceeds(_path_of_length(260), kind="file") is True


def test_archive_paths_fit_accident_root(monkeypatch):
    session_id = "s"
    workspace = _workspace_for_recall_root_length(181, session_id)
    recall_root = _recall_root(workspace, session_id)
    ids = (
        "20260102T030405123456Z",
        "20260102T030405123456Z-ab12",
        "0123456789abcdef",
    )
    assert [len(memory_id) for memory_id in ids] == [22, 27, 16]
    captured: dict[str, dict[str, object]] = {}
    real_replace = os.replace

    def _capture_replace(src, dst):
        temporary = Path(src)
        chunk_files = sorted((temporary / "chunks").glob("*.md"))
        captured[Path(dst).name] = {
            "chunks_length": len(str(temporary / "chunks")),
            "chunk_file_length": len(str(chunk_files[0])),
            "paths": [
                temporary,
                temporary / "chunks",
                temporary / "turns.jsonl",
                temporary / "raw_messages.json",
                temporary / "manifest.json",
                *chunk_files,
            ],
        }
        return real_replace(src, dst)

    monkeypatch.setattr(archive_module.os, "replace", _capture_replace)
    for memory_id in ids:
        monkeypatch.setattr(archive_module, "_new_memory_id", lambda _root, memory_id=memory_id: memory_id)
        archive = _archive(
            workspace,
            [UserMessage(content="database retries"), AssistantMessage(content="use backoff")],
            session_id=session_id,
        )
        archive_path = Path(archive.path)
        record = captured[memory_id]
        assert archive_path.name == memory_id
        for path in record["paths"]:
            kind = "file" if path.suffix in {".json", ".jsonl", ".md"} else "directory"
            assert archive_module._exceeds_windows_path_budget(path, kind=kind) is False
        for path in (archive_path, archive_path / "chunks"):
            assert archive_module._exceeds_windows_path_budget(path, kind="directory") is False
        for path in archive_path.rglob("*.md"):
            assert archive_module._exceeds_windows_path_budget(path, kind="file") is False
        if len(memory_id) == 27:
            assert record["chunks_length"] == 230
            assert record["chunk_file_length"] == 252
    assert [path.name for path in recall_root.iterdir() if path.name.startswith(".")] == []


def test_archive_preflight_rejects_without_creating_directories(monkeypatch):
    session_id = "s"
    workspace = _workspace_for_recall_root_length(189, session_id)
    memory_id = "20260102T030405123456Z-ab12"
    monkeypatch.setattr(archive_module, "_new_memory_id", lambda _root: memory_id)

    with pytest.raises(archive_module.ArchiveError):
        _archive(
            workspace,
            [UserMessage(content="database retries"), AssistantMessage(content="use backoff")],
            session_id=session_id,
        )

    assert list(workspace.rglob("compression_recall")) == []


def test_new_memory_id_collision_is_timestamp_plus_4_hex(tmp_path, monkeypatch):
    fixed = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(archive_module, "datetime", _FrozenDateTime)
    timestamp = fixed.strftime("%Y%m%dT%H%M%S%fZ")
    recall_root = tmp_path / "compression_recall"
    (recall_root / f"{timestamp}_legacy").mkdir(parents=True)

    memory_id = _new_memory_id(recall_root)

    assert len(memory_id) == 27
    assert re.fullmatch(rf"{timestamp}-[0-9a-f]{{4}}", memory_id)
    assert not re.fullmatch(r"[0-9a-f]{32}", memory_id)


def test_new_memory_id_strftime_failure_is_16_hex(tmp_path, monkeypatch):
    class _BrokenDateTime:
        @staticmethod
        def now(tz=None):
            raise RuntimeError("clock unavailable")

    monkeypatch.setattr(archive_module, "datetime", _BrokenDateTime)
    memory_id = _new_memory_id(tmp_path / "compression_recall")

    assert re.fullmatch(r"[0-9a-f]{16}", memory_id)


def _plant_archive(recall_root: Path, memory_id: str, directory_name: str, session_id: str) -> Path:
    directory = recall_root / directory_name
    (directory / "chunks").mkdir(parents=True)
    (directory / "chunks" / "turn_000_chunk_000.md").write_text("database timeout retry backoff\n", encoding="utf-8")
    (directory / "turns.jsonl").write_text(
        json.dumps(
            {
                "turn_id": "turn_000",
                "query": "database timeout",
                "answer": "retry backoff",
                "chunk_paths": ["chunks/turn_000_chunk_000.md"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "memory_id": memory_id,
                "session_id": session_id,
                "created_at": "2026-01-02T03:04:05+00:00",
                "turn_count": 1,
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_old_slug_directory_still_lists_and_deletes(tmp_path):
    session_id = "session-1"
    recall_root = _recall_root(tmp_path, session_id)
    old_id = "20260102T030405123456Z"
    kept_id = "20260102T030405654321Z"
    old_dir = _plant_archive(recall_root, old_id, f"{old_id}_legacy", session_id)
    kept_dir = _plant_archive(recall_root, kept_id, kept_id, session_id)

    found = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id=session_id,
        memory_id=old_id,
        query="database timeout retry",
    )
    listed = {item["memory_id"] for item in found["archives_in_session"]}
    assert listed == {old_id, kept_id}
    assert found["chunks"]

    delete_compression_archive(CompressionArchive(memory_id=old_id, path=str(old_dir)))

    assert not old_dir.exists()
    assert kept_dir.is_dir()
    kept = recall_compressed_context(
        workspace_dir=str(tmp_path),
        session_id=session_id,
        memory_id=kept_id,
        query="database timeout retry",
    )
    assert {item["memory_id"] for item in kept["archives_in_session"]} == {kept_id}
    assert kept["chunks"]


@pytest.mark.asyncio
async def test_prefix_compact_skips_archive_when_nothing_to_compress(tmp_path):
    compressor = DialogueCompressor(DialogueCompressorConfig())
    context = _context(tmp_path, recall_config=CompressionRecallConfig(enabled=True))
    span = PrefixCompactSpan(preserved_prefix=[], messages_to_compress=[], protected_tail=[])

    archive = await compressor._archive_compressed_messages(
        context=context,
        original_messages=[],
        span=span,
    )

    assert archive is None
    assert list(tmp_path.rglob("compression_recall")) == []


@pytest.mark.asyncio
async def test_prefix_compact_keeps_messages_when_recall_archive_fails(tmp_path):
    blocking_workspace = tmp_path / "workspace-file"
    blocking_workspace.write_text("not a directory", encoding="utf-8")
    compressor = DialogueCompressor(DialogueCompressorConfig())
    executor = MagicMock()
    executor.invoke = AsyncMock(return_value=CompressionResult(AssistantMessage(content="compact state")))
    compressor._compression_executor = executor
    context = _context(blocking_workspace, recall_config=CompressionRecallConfig(enabled=True))
    messages = [
        UserMessage(content="Historical request"),
        AssistantMessage(content="historical work " * 600),
        UserMessage(content="Current task"),
    ]
    window = ContextWindow(system_messages=[], context_messages=messages, tools=[])

    event, updated = await compressor.on_get_context_window(context, window)

    assert event is None
    assert updated.context_messages == messages
    context.set_messages.assert_not_called()
    assert all("COMPRESSION_RECALL" not in str(message.content) for message in updated.context_messages)
