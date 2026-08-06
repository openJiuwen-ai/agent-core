# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Filesystem archive for messages replaced by forked context compressors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.content_sanitize import sanitize_content_for_text
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
    INTERNAL_USER_PREFIXES,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage

ARCHIVE_SCHEMA_VERSION = 1
DEFAULT_CHUNK_SIZE_TOKENS = 3_000
DEFAULT_CHUNK_OVERLAP_TOKENS = 300
RECALL_DIR_NAME = "compression_recall"


@dataclass(frozen=True)
class CompressionArchive:
    """Location of one persisted compression archive."""

    memory_id: str
    path: str


@dataclass
class _Turn:
    turn_id: str
    query: str
    answer: str
    messages: list[BaseMessage]


def archive_compression_messages(  # pylint: disable=too-many-locals
    *,
    context: ModelContext,
    processor_type: str,
    original_messages: list[BaseMessage],
    messages_to_compress: list[BaseMessage],
    preceding_messages: list[BaseMessage],
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
) -> CompressionArchive:
    """Atomically archive the exact messages replaced by one compression."""
    if chunk_overlap_tokens >= chunk_size_tokens:
        raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
    workspace_dir = _context_value(context, "workspace_dir")
    session_id = _context_value(context, "session_id")
    context_id = _context_value(context, "context_id")
    if not workspace_dir:
        raise OSError("compression recall requires a workspace directory")
    if not session_id:
        raise OSError("compression recall requires a session id")

    recall_root = _recall_root(Path(workspace_dir), session_id)
    _validate_archive_root(Path(workspace_dir), recall_root)
    recall_root.mkdir(parents=True, exist_ok=True)
    _validate_archive_root(Path(workspace_dir), recall_root)
    memory_id = _new_memory_id(recall_root)
    fallback_query = _latest_real_user_content(preceding_messages)
    turns = _group_turns(messages_to_compress, fallback_query=fallback_query)
    archive_query = _latest_non_empty_query(turns) or fallback_query
    directory_name = f"{memory_id}_{_query_slug(archive_query)}"
    final_path = recall_root / directory_name
    temporary_path = recall_root / f".{directory_name}.{uuid.uuid4().hex}.tmp"

    try:
        chunks_dir = temporary_path / "chunks"
        chunks_dir.mkdir(parents=True)
        turn_records = _write_turns(
            turns,
            chunks_dir,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        _write_json_lines(temporary_path / "turns.jsonl", turn_records)
        _write_json(
            temporary_path / "raw_messages.json", [_message_to_json(message) for message in messages_to_compress]
        )
        _write_json(
            temporary_path / "manifest.json",
            {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "memory_id": memory_id,
                "session_id": session_id,
                "context_id": context_id,
                "processor_type": processor_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_message_count": len(messages_to_compress),
                "original_context_message_count": len(original_messages),
                "turn_count": len(turn_records),
                "files": {
                    "turns": "turns.jsonl",
                    "raw_messages": "raw_messages.json",
                    "chunks": "chunks",
                },
            },
        )
        os.replace(temporary_path, final_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise
    return CompressionArchive(memory_id=memory_id, path=str(final_path))


def delete_compression_archive(archive: CompressionArchive) -> None:
    """Best-effort removal of one archive directory, used to roll back a failed compression."""
    path = Path(archive.path)
    resolved_root = path.parent.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise OSError("compression recall archive path escapes the recall root") from exc
    if not resolved_path.name.startswith(f"{archive.memory_id}_"):
        raise OSError("compression recall archive directory does not match the memory id")
    shutil.rmtree(resolved_path, ignore_errors=True)


def _write_turns(
    turns: list[_Turn],
    chunks_dir: Path,
    *,
    chunk_size_tokens: int,
    chunk_overlap_tokens: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for turn in turns:
        body = _render_turn(turn.messages)
        bodies = _split_text(body, chunk_size_tokens=chunk_size_tokens, chunk_overlap_tokens=chunk_overlap_tokens)
        chunk_paths: list[str] = []
        for chunk_index, chunk_body in enumerate(bodies):
            chunk_id = f"{turn.turn_id}_chunk_{chunk_index:03d}"
            relative_path = f"chunks/{chunk_id}.md"
            chunk_paths.append(relative_path)
            markdown = (
                "---\n"
                f"turn_id: {turn.turn_id}\n"
                f"chunk_id: {chunk_id}\n"
                f"chunk_index: {chunk_index}\n"
                "---\n\n"
                f"{chunk_body.rstrip()}\n"
            )
            (chunks_dir / f"{chunk_id}.md").write_text(markdown, encoding="utf-8")
        records.append(
            {
                "turn_id": turn.turn_id,
                "query": turn.query,
                "answer": turn.answer,
                "chunk_paths": chunk_paths,
            }
        )
    return records


def _group_turns(messages: list[BaseMessage], *, fallback_query: str) -> list[_Turn]:
    groups: list[tuple[str, list[BaseMessage]]] = []
    current_messages: list[BaseMessage] = []
    current_query = fallback_query
    for message in messages:
        if _is_real_user_message(message) and current_messages:
            groups.append((current_query, current_messages))
            current_messages = []
            current_query = _content_to_text(message.content)
        elif _is_real_user_message(message):
            current_query = _content_to_text(message.content)
        current_messages.append(message)
    if current_messages:
        groups.append((current_query, current_messages))

    turns: list[_Turn] = []
    for index, (query, turn_messages) in enumerate(groups):
        answer_parts = [
            _content_to_text(message.content)
            for message in turn_messages
            if isinstance(message, AssistantMessage) and _content_to_text(message.content).strip()
        ]
        turns.append(
            _Turn(
                turn_id=f"turn_{index:03d}",
                query=query,
                answer="\n".join(answer_parts),
                messages=turn_messages,
            )
        )
    return turns


def _render_turn(messages: list[BaseMessage]) -> str:
    sections: list[str] = []
    for message in messages:
        content = _content_to_text(message.content)
        if isinstance(message, UserMessage):
            sections.append(f"## User\n{content}")
            continue
        if isinstance(message, AssistantMessage):
            reasoning = _content_to_text(getattr(message, "reasoning_content", ""))
            if reasoning:
                sections.append(f"## Assistant Reasoning\n{reasoning}")
            if content:
                sections.append(f"## Assistant\n{content}")
            for tool_call in getattr(message, "tool_calls", None) or []:
                tool_name = getattr(tool_call, "name", "") or "unknown"
                arguments = getattr(tool_call, "arguments", "")
                sections.append(f"## Tool Call: {tool_name}\n{_content_to_text(arguments)}")
            continue
        if isinstance(message, ToolMessage):
            tool_call_id = getattr(message, "tool_call_id", "")
            sections.append(f"## Tool Result: {tool_call_id}\n{content}")
            continue
        sections.append(f"## {str(getattr(message, 'role', 'message')).title()}\n{content}")
    return "\n\n".join(sections).strip()


def _split_text(
    text: str,
    *,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    if not text:
        return []
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        token_ids = encoding.encode(text, disallowed_special=())
        if len(token_ids) <= chunk_size_tokens:
            return [text]
        step = chunk_size_tokens - chunk_overlap_tokens
        chunks: list[str] = []
        start = 0
        while start < len(token_ids):
            end = start + chunk_size_tokens
            chunks.append(encoding.decode(token_ids[start:end]))
            if end >= len(token_ids):
                break
            start += step
        return chunks
    except Exception:
        char_size = chunk_size_tokens * 3
        char_overlap = chunk_overlap_tokens * 3
        if len(text) <= char_size:
            return [text]
        step = char_size - char_overlap
        chunks = []
        start = 0
        while start < len(text):
            end = start + char_size
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += step
        return chunks


def _recall_root(workspace_dir: Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    session_dir = f"{_safe_filename_part(session_id)}-{digest}_context"
    return workspace_dir / "context" / session_dir / RECALL_DIR_NAME


def _validate_archive_root(workspace_dir: Path, recall_root: Path) -> None:
    resolved_workspace = workspace_dir.resolve()
    try:
        relative_parts = recall_root.relative_to(workspace_dir).parts
    except ValueError as exc:
        raise OSError("compression recall path escapes the workspace") from exc
    current = workspace_dir
    for part in relative_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise OSError("compression recall path contains a symbolic link")
    if recall_root.exists():
        try:
            recall_root.resolve().relative_to(resolved_workspace)
        except ValueError as exc:
            raise OSError("compression recall path escapes the workspace") from exc


def _new_memory_id(recall_root: Path) -> str:
    try:
        candidate = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    except Exception:
        return uuid.uuid4().hex
    if any(recall_root.glob(f"{candidate}_*")):
        return uuid.uuid4().hex
    return candidate


def _query_slug(query: str) -> str:
    compact = re.sub(r"\s+", "_", query.strip())[:10]
    safe = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", compact).strip("-._")
    return safe or "no-query"


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return safe[:120] or "unknown"


def _latest_real_user_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if _is_real_user_message(message):
            return _content_to_text(message.content)
    return ""


def _latest_non_empty_query(turns: list[_Turn]) -> str:
    for turn in reversed(turns):
        if turn.query.strip():
            return turn.query
    return ""


def _is_real_user_message(message: BaseMessage) -> bool:
    if not isinstance(message, UserMessage):
        return False
    content = _content_to_text(message.content).lstrip()
    return not content.startswith(INTERNAL_USER_PREFIXES)


def _content_to_text(content: Any) -> str:
    # Sanitized: this text is BM25-indexed and re-rendered into context on
    # recall — raw base64 would poison the index and balloon retrieved chunks.
    if content is None:
        return ""
    return sanitize_content_for_text(content)


def _message_to_json(message: BaseMessage) -> dict[str, Any]:
    try:
        return message.model_dump(mode="json", exclude_none=True)
    except TypeError:
        return message.model_dump(exclude_none=True)


def _context_value(context: ModelContext, method_name: str) -> str:
    method = getattr(context, method_name, None)
    if not callable(method):
        return ""
    value = method()
    return str(value or "")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json_lines(path: Path, values: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values)
    path.write_text(content, encoding="utf-8")
