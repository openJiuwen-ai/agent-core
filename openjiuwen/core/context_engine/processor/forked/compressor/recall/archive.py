"""Filesystem archive for messages replaced by forked context compressors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
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
# CreateDirectory keeps 12 characters under MAX_PATH for an 8.3 name. 247 stays
# on the safe side of the 248-character documented limit. File paths exclude the
# terminating NUL, so the usable file limit is 259.
WINDOWS_DIRECTORY_MAX_CHARS = 247
WINDOWS_FILE_MAX_CHARS = 259
_ID_ATTEMPTS = 8
_TEMP_TOKEN_BYTES = 4
_COLLISION_TOKEN_BYTES = 2
_FALLBACK_ID_BYTES = 8
_ARCHIVE_FILENAMES = ("turns.jsonl", "raw_messages.json", "manifest.json")


class ArchiveError(OSError):
    """Raised when a compression archive cannot be written."""


def directory_matches_memory_id(name: str, memory_id: str) -> bool:
    """True for a pure memory-id directory or a legacy ``memory_id_<slug>`` directory."""
    return name == memory_id or name.startswith(f"{memory_id}_")


def _exceeds_windows_path_budget(path: Path, *, kind: str) -> bool:
    return len(str(path)) > _windows_path_limit(kind)


def _windows_path_limit(kind: str) -> int:
    if kind == "directory":
        return WINDOWS_DIRECTORY_MAX_CHARS
    if kind == "file":
        return WINDOWS_FILE_MAX_CHARS
    raise ValueError(f"unknown path kind: {kind}")


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


def archive_compression_messages(
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
    try:
        return _archive_compression_messages(
            context=context,
            processor_type=processor_type,
            original_messages=original_messages,
            messages_to_compress=messages_to_compress,
            preceding_messages=preceding_messages,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError("compression archive failed") from exc


def _archive_compression_messages(  # pylint: disable=too-many-locals
    *,
    context: ModelContext,
    processor_type: str,
    original_messages: list[BaseMessage],
    messages_to_compress: list[BaseMessage],
    preceding_messages: list[BaseMessage],
    chunk_size_tokens: int,
    chunk_overlap_tokens: int,
) -> CompressionArchive:
    workspace_dir = _context_value(context, "workspace_dir")
    session_id = _context_value(context, "session_id")
    context_id = _context_value(context, "context_id")
    if not workspace_dir:
        raise OSError("compression recall requires a workspace directory")
    if not session_id:
        raise OSError("compression recall requires a session id")

    workspace = Path(workspace_dir)
    recall_root = _recall_root(workspace, session_id)
    _validate_archive_root(workspace, recall_root)
    fallback_query = _latest_real_user_content(preceding_messages)
    turns = _group_turns(messages_to_compress, fallback_query=fallback_query)
    archive_query = _latest_non_empty_query(turns) or fallback_query
    turn_records, chunk_files = _prepare_turn_files(
        turns,
        chunk_size_tokens=chunk_size_tokens,
        chunk_overlap_tokens=chunk_overlap_tokens,
    )
    memory_id = _new_memory_id(recall_root)
    temp_token = _allocate_temp_token(recall_root, memory_id)
    _ensure_paths_fit(
        _layout_entries(
            recall_root,
            memory_id,
            temp_token,
            [filename for filename, _markdown in chunk_files],
        )
    )

    final_path = recall_root / memory_id
    temporary_path = recall_root / f".{memory_id}.{temp_token}.tmp"
    try:
        recall_root.mkdir(parents=True, exist_ok=True)
        _validate_archive_root(workspace, recall_root)
        chunks_dir = temporary_path / "chunks"
        chunks_dir.mkdir(parents=True)
        for filename, markdown in chunk_files:
            (chunks_dir / filename).write_text(markdown, encoding="utf-8")
        _write_json_lines(temporary_path / "turns.jsonl", turn_records)
        _write_json(
            temporary_path / "raw_messages.json",
            [_message_to_json(message) for message in messages_to_compress],
        )
        _write_json(
            temporary_path / "manifest.json",
            {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "memory_id": memory_id,
                "session_id": session_id,
                "context_id": context_id,
                "processor_type": processor_type,
                "archive_query": archive_query,
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
    if not directory_matches_memory_id(resolved_path.name, archive.memory_id):
        raise OSError("compression recall archive directory does not match the memory id")
    shutil.rmtree(resolved_path, ignore_errors=True)


def _prepare_turn_files(
    turns: list[_Turn],
    *,
    chunk_size_tokens: int,
    chunk_overlap_tokens: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    records: list[dict[str, Any]] = []
    files: list[tuple[str, str]] = []
    for turn in turns:
        bodies = _split_text(
            _render_turn(turn.messages),
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        chunk_paths: list[str] = []
        for chunk_index, chunk_body in enumerate(bodies):
            filename = f"{turn.turn_id}_chunk_{chunk_index:03d}.md"
            chunk_paths.append(f"chunks/{filename}")
            files.append((filename, _chunk_markdown(turn.turn_id, chunk_index, chunk_body)))
        records.append(
            {
                "turn_id": turn.turn_id,
                "query": turn.query,
                "answer": turn.answer,
                "chunk_paths": chunk_paths,
            }
        )
    return records, files


def _chunk_markdown(turn_id: str, chunk_index: int, body: str) -> str:
    return (
        "---\n"
        f"turn_id: {turn_id}\n"
        f"chunk_id: {turn_id}_chunk_{chunk_index:03d}\n"
        f"chunk_index: {chunk_index}\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _group_turns(messages: list[BaseMessage], *, fallback_query: str) -> list[_Turn]:
    groups: list[tuple[str, list[BaseMessage]]] = []
    current_messages: list[BaseMessage] = []
    current_query = fallback_query
    for message in messages:
        if _is_real_user_message(message) and current_messages:
            groups.append((current_query, current_messages))
            current_messages = []
            current_query = _content_to_query_text(message.content)
        elif _is_real_user_message(message):
            current_query = _content_to_query_text(message.content)
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
        base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    except Exception:
        return _allocate_hex_id(recall_root, _FALLBACK_ID_BYTES)
    if not _memory_id_taken(recall_root, base):
        return base
    for _ in range(_ID_ATTEMPTS):
        candidate = f"{base}-{secrets.token_hex(_COLLISION_TOKEN_BYTES)}"
        if not _memory_id_taken(recall_root, candidate):
            return candidate
    raise ArchiveError("compression archive could not allocate a memory id")


def _allocate_hex_id(recall_root: Path, nbytes: int) -> str:
    for _ in range(_ID_ATTEMPTS):
        candidate = secrets.token_hex(nbytes)
        if not _memory_id_taken(recall_root, candidate):
            return candidate
    raise ArchiveError("compression archive could not allocate a memory id")


def _allocate_temp_token(recall_root: Path, memory_id: str) -> str:
    for _ in range(_ID_ATTEMPTS):
        token = secrets.token_hex(_TEMP_TOKEN_BYTES)
        if not (recall_root / f".{memory_id}.{token}.tmp").exists():
            return token
    raise ArchiveError("compression archive could not allocate a temporary directory")


def _memory_id_taken(recall_root: Path, memory_id: str) -> bool:
    if not recall_root.is_dir():
        return False
    for path in recall_root.iterdir():
        if not path.is_dir() or path.is_symlink() or path.name.startswith("."):
            continue
        if directory_matches_memory_id(path.name, memory_id):
            return True
    return False


def _layout_entries(
    recall_root: Path,
    memory_id: str,
    temp_token: str,
    chunk_filenames: list[str],
) -> list[tuple[Path, str]]:
    final_dir = recall_root / memory_id
    temporary_dir = recall_root / f".{memory_id}.{temp_token}.tmp"
    entries: list[tuple[Path, str]] = []
    for directory in (final_dir, temporary_dir):
        entries.append((directory, "directory"))
        entries.append((directory / "chunks", "directory"))
        for filename in _ARCHIVE_FILENAMES:
            entries.append((directory / filename, "file"))
        for filename in chunk_filenames:
            entries.append((directory / "chunks" / filename, "file"))
    return entries


def _ensure_paths_fit(entries: list[tuple[Path, str]]) -> None:
    for path, kind in entries:
        if _exceeds_windows_path_budget(path, kind=kind):
            limit = _windows_path_limit(kind)
            raise ArchiveError(
                f"compression archive path exceeds the Windows {kind} limit ({limit}): {path}"
            )


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return safe[:120] or "unknown"


def _latest_real_user_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if _is_real_user_message(message):
            return _content_to_query_text(message.content)
    return ""


def _latest_non_empty_query(turns: list[_Turn]) -> str:
    for turn in reversed(turns):
        if turn.query.strip():
            return turn.query
    return ""


def _is_real_user_message(message: BaseMessage) -> bool:
    if not isinstance(message, UserMessage):
        return False
    content = _content_to_query_text(message.content).lstrip()
    return not content.startswith(INTERNAL_USER_PREFIXES)


def _content_to_query_text(content: Any) -> str:
    """Extract user-visible text from structured content for query labeling.

    User messages may carry multimodal parts (``[{"type": "text", "text": ...}]``);
    flattening those with :func:`_content_to_text` yields a JSON blob, which is
    not a useful recall query. Text-bearing parts are joined; anything else
    yields an empty string.
    """
    if isinstance(content, str) or content is None:
        return _strip_channel_envelope(_content_to_text(content))
    parts = content if isinstance(content, list) else [content]
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str):
            texts.append(part)
            continue
        if isinstance(part, dict):
            for key in ("text", "content"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value)
                    break
    if not texts:
        return ""
    return _strip_channel_envelope(_content_to_text("\n".join(texts)))


def _strip_channel_envelope(text: str) -> str:
    """Unwrap a ``lead-in line + JSON envelope`` channel wrapper for query labeling.

    Channels may deliver user text as ``你收到一条消息：\\n{"content": "...", ...}``.
    Indexed as-is, the envelope metadata (source, timezone, timestamp) dilutes
    BM25 turn matching, while the real query lives in the envelope's ``content``
    field. Unwrap only when the payload is a JSON object with a non-empty string
    ``content``; a message whose own first line starts with ``{`` is treated as
    genuine user text.
    """
    stripped = text.strip()
    head, sep, payload = stripped.partition("\n")
    payload = payload.strip()
    if not sep or head.lstrip().startswith("{") or not payload.startswith("{"):
        return text
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError:
        return text
    if isinstance(envelope, dict):
        inner = envelope.get("content")
        if isinstance(inner, str) and inner.strip():
            return inner
    return text


def _content_to_text(content: Any) -> str:
    # This text is indexed and later re-rendered into model context; raw
    # base64 would poison the index and balloon recalled chunks.
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
