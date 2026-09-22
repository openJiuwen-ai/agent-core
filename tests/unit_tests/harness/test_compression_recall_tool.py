from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.context_engine import CompressionRecallConfig, ContextEngineConfig
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import (
    archive_compression_messages,
)
from openjiuwen.core.context_engine.processor.forked.compressor.session_memory_compressor import (
    SessionMemoryCompressorConfig,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.model import init_model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperationCard
from openjiuwen.harness import Workspace
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts.sections.compression_recall import build_compression_recall_section
from openjiuwen.harness.rails.context_engineer.context_processor_rail import ContextProcessorRail
from openjiuwen.harness.tools.compression_recall import CompressionRecallTool


def _context(tmp_path, session_id: str = "session-1"):
    context = MagicMock()
    context.workspace_dir.return_value = str(tmp_path)
    context.session_id.return_value = session_id
    context.context_id.return_value = "context-1"
    return context


def _make_agent(tmp_path, *, recall_enabled: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sysop_card = SysOperationCard(
        id=f"compression_recall_sysop_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
    sys_operation = Runner.resource_mgr.get_sys_operation(sysop_card.id)
    model = init_model(
        provider="OpenAI",
        model_name="dummy-model",
        api_key="dummy-key",
        api_base="https://example.com/v1",
        verify_ssl=False,
    )
    return create_deep_agent(
        model=model,
        card=AgentCard(name="compression-recall-test", description="test"),
        system_prompt="You are a test assistant.",
        max_iterations=3,
        enable_task_loop=False,
        workspace=Workspace(root_path=str(tmp_path)),
        sys_operation=sys_operation,
        context_engine_config=ContextEngineConfig(
            compression_recall_config=CompressionRecallConfig(enabled=recall_enabled),
        ),
    )


@pytest.mark.asyncio
async def test_tool_requires_runtime_session_and_recalls_current_session(tmp_path):
    messages = [
        UserMessage(content="database timeout"),
        AssistantMessage(content="retry the database operation with backoff"),
    ]
    archive = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=messages,
        messages_to_compress=messages,
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), agent_id="agent-1")

    missing_session = await tool.invoke({"memory_id": archive.memory_id, "query": "database retry"})
    assert missing_session.success is False

    session = MagicMock()
    session.get_session_id.return_value = "session-1"
    result = await tool.invoke(
        {"memory_id": archive.memory_id, "query": "database retry"},
        session=session,
    )

    assert result.success is True
    assert result.data["matched_turn"]["query"] == "database timeout"
    assert result.data["chunks"]


@pytest.mark.asyncio
async def test_rail_registers_tool_only_when_forked_compressor_recall_is_enabled(tmp_path):
    enabled_agent = _make_agent(tmp_path / "enabled", recall_enabled=True)
    enabled_rail = ContextProcessorRail(
        preset=False,
        processors=[("DialogueCompressor", DialogueCompressorConfig())],
    )
    await enabled_agent.register_rail(enabled_rail)
    await enabled_agent.ensure_initialized()

    enabled_card = enabled_agent.ability_manager.get("recall_compressed_context")
    assert isinstance(enabled_card, ToolCard)
    assert enabled_rail._recall_enabled is True

    disabled_agent = _make_agent(tmp_path / "disabled")
    disabled_rail = ContextProcessorRail(
        preset=False,
        processors=[("DialogueCompressor", DialogueCompressorConfig())],
    )
    await disabled_agent.register_rail(disabled_rail)
    await disabled_agent.ensure_initialized()

    assert disabled_agent.ability_manager.get("recall_compressed_context") is None
    assert disabled_rail._recall_enabled is False


def test_recall_tool_result_is_added_to_existing_offloader_protection():
    offloader_config = MagicMock()
    offloader_config.protected_tool_names = ["read_file"]

    ContextProcessorRail._protect_compression_recall_tool_results([("MessageSummaryOffloader", offloader_config)])

    assert offloader_config.protected_tool_names == ["read_file", "recall_compressed_context"]


@pytest.mark.asyncio
async def test_tool_renders_compact_content_for_model(tmp_path):
    messages = [
        UserMessage(content="database timeout"),
        AssistantMessage(content="retry the database operation with backoff"),
    ]
    archive = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=messages,
        messages_to_compress=messages,
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), agent_id="agent-1")
    session = MagicMock()
    session.get_session_id.return_value = "session-1"

    result = await tool.invoke(
        {"memory_id": archive.memory_id, "query": "database retry"},
        session=session,
    )

    assert result.success is True
    content = result.data["content"]
    assert archive.memory_id in content
    assert archive.path in content
    assert str(Path(archive.path).parent) in content
    assert "retry the database operation with backoff" in content
    # 结构化结果仍完整保留在 data 中
    assert result.data["chunks"]
    assert result.data["matched_turn"]["query"] == "database timeout"


@pytest.mark.asyncio
async def test_tool_renders_hint_as_content_on_miss(tmp_path):
    archive = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=[UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
        messages_to_compress=[UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), agent_id="agent-1")
    session = MagicMock()
    session.get_session_id.return_value = "session-1"

    result = await tool.invoke(
        {"memory_id": archive.memory_id, "query": "completely-unrelated-zebra"},
        session=session,
    )

    assert result.success is True
    assert result.data["content"] == result.data["hint"]


@pytest.mark.asyncio
async def test_tool_searches_across_archives_when_memory_id_omitted(tmp_path):
    older = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=[
            UserMessage(content="database timeout retry policy"),
            AssistantMessage(content="use exponential backoff for database retries"),
        ],
        messages_to_compress=[
            UserMessage(content="database timeout retry policy"),
            AssistantMessage(content="use exponential backoff for database retries"),
        ],
        preceding_messages=[],
    )
    newer = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=[
            UserMessage(content="cache eviction strategy"),
            AssistantMessage(content="evict least recently used cache entries"),
        ],
        messages_to_compress=[
            UserMessage(content="cache eviction strategy"),
            AssistantMessage(content="evict least recently used cache entries"),
        ],
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), agent_id="agent-1")
    session = MagicMock()
    session.get_session_id.return_value = "session-1"

    result = await tool.invoke({"query": "database backoff retry"}, session=session)

    assert result.success is True
    assert result.data["chunks"]
    assert result.data["chunks"][0]["memory_id"] == older.memory_id
    assert {item["memory_id"] for item in result.data["archives_in_session"]} == {
        older.memory_id,
        newer.memory_id,
    }
    assert result.data["recall_root"] == str(Path(older.path).parent)
    content = result.data["content"]
    assert "2" in content  # 本 session 共有 2 个压缩归档
    assert older.memory_id in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected_keywords"),
    [
        ("cn", ["同义词", "另一种语言", "标识符", "turns.jsonl", "chunks/"]),
        ("en", ["synonyms", "another language", "identifiers", "turns.jsonl", "chunks/"]),
    ],
)
async def test_tool_returns_retry_hint_with_archive_path_on_miss(tmp_path, language, expected_keywords):
    archive = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=[UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
        messages_to_compress=[UserMessage(content="database"), AssistantMessage(content="retry with backoff")],
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), language=language, agent_id="agent-1")
    session = MagicMock()
    session.get_session_id.return_value = "session-1"

    result = await tool.invoke(
        {"memory_id": archive.memory_id, "query": "completely-unrelated-zebra"},
        session=session,
    )

    assert result.success is True
    assert result.data["chunks"] == []
    assert result.data["archive_path"] == archive.path
    hint = result.data["hint"]
    # hint 指向归档根目录（archive.path 的父目录）并列出本 session 的归档
    assert str(Path(archive.path).parent) in hint
    assert archive.memory_id in hint
    for keyword in expected_keywords:
        assert keyword in hint


@pytest.mark.asyncio
async def test_rail_registers_recall_tool_for_session_memory_compressor(tmp_path):
    agent = _make_agent(tmp_path / "session-memory", recall_enabled=True)
    rail = ContextProcessorRail(
        preset=False,
        processors=[("SessionMemoryCompressor", SessionMemoryCompressorConfig(enabled=True))],
    )
    await agent.register_rail(rail)
    await agent.ensure_initialized()

    assert isinstance(agent.ability_manager.get("recall_compressed_context"), ToolCard)


@pytest.mark.asyncio
async def test_tool_omits_hint_when_chunks_match(tmp_path):
    messages = [
        UserMessage(content="database timeout"),
        AssistantMessage(content="retry the database operation with backoff"),
    ]
    archive = archive_compression_messages(
        context=_context(tmp_path),
        processor_type="DialogueCompressor",
        original_messages=messages,
        messages_to_compress=messages,
        preceding_messages=[],
    )
    tool = CompressionRecallTool(str(tmp_path), agent_id="agent-1")
    session = MagicMock()
    session.get_session_id.return_value = "session-1"

    result = await tool.invoke(
        {"memory_id": archive.memory_id, "query": "database retry"},
        session=session,
    )

    assert result.success is True
    assert result.data["chunks"]
    assert "hint" not in result.data


@pytest.mark.parametrize(
    ("language", "expected_keywords"),
    [
        ("cn", ["已被压缩", "重试", "同义词", "archive_path"]),
        ("en", ["compressed", "retry", "synonyms", "archive_path"]),
    ],
)
def test_prompt_section_explains_marker_and_guides_retry(language, expected_keywords):
    section = build_compression_recall_section(language)

    content = section.content[language]
    for keyword in expected_keywords:
        assert keyword in content
