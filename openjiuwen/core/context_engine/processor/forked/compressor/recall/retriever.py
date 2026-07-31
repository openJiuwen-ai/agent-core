"""Two-stage session-isolated BM25 retrieval for compressed context archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import RECALL_DIR_NAME
from openjiuwen.core.context_engine.processor.forked.compressor.recall.bm25 import normalize_markdown, rank_bm25

TURN_TOP_K = 2
CHUNK_TOP_K = 2
TURN_SCORE_MIN_RATIO = 0.3
MAX_CONTENT_TOKENS_ENV = "OPENJIUWEN_RECALL_MAX_CONTENT_TOKENS"
DEFAULT_MAX_CONTENT_TOKENS = 8_000
_MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class CompressionRecallError(ValueError):
    """Raised when a recall archive cannot be safely read."""


def recall_compressed_context(  # pylint: disable=too-many-locals
    *,
    workspace_dir: str,
    session_id: str,
    memory_id: str,
    query: str,
) -> dict[str, Any]:
    """Recall budget-capped chunks from one archive inside the current session."""
    if not workspace_dir:
        raise CompressionRecallError("compression recall requires a workspace directory")
    if not session_id:
        raise CompressionRecallError("compression recall requires a session")
    if not memory_id or not _MEMORY_ID_RE.fullmatch(memory_id):
        raise CompressionRecallError("memory_id is invalid")
    if not query or not query.strip():
        raise CompressionRecallError("query is required")

    session_digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    session_dir = f"{_safe_filename_part(session_id)}-{session_digest}_context"
    session_root = Path(workspace_dir) / "context" / session_dir / RECALL_DIR_NAME
    _validate_session_root(Path(workspace_dir), session_root)
    resolved_session_root = session_root.resolve()
    if not resolved_session_root.is_dir():
        raise CompressionRecallError("compression recall archive was not found in the current session")

    candidates = [path for path in session_root.glob(f"{memory_id}_*") if path.is_dir() and not path.is_symlink()]
    if len(candidates) != 1:
        raise CompressionRecallError("compression recall archive was not found in the current session")
    archive_path = _resolve_inside(candidates[0], resolved_session_root, kind="archive")

    manifest = _read_json_object(_resolve_inside(archive_path / "manifest.json", archive_path, kind="manifest"))
    if str(manifest.get("session_id") or "") != session_id:
        raise CompressionRecallError("compression recall archive does not belong to the current session")
    if str(manifest.get("memory_id") or "") != memory_id:
        raise CompressionRecallError("compression recall archive id does not match")

    turns_path = _resolve_inside(archive_path / "turns.jsonl", archive_path, kind="turn index")
    turns = _read_json_lines(turns_path)
    turn_documents = [f"{turn.get('query', '')}\n{turn.get('answer', '')}" for turn in turns]
    turn_scores = rank_bm25(query, turn_documents)
    ranked_turns = sorted(
        ((score, index, turns[index]) for index, score in enumerate(turn_scores) if score > 0),
        key=lambda item: (-item[0], item[1]),
    )
    if not ranked_turns:
        return {
            "memory_id": memory_id,
            "archive_path": str(archive_path),
            "matched_turn": None,
            "matched_turns": [],
            "chunks": [],
            "truncated": False,
            "returned_tokens": 0,
        }

    top_turn_score = ranked_turns[0][0]
    matched_turns = [
        (score, turn) for score, _, turn in ranked_turns[:TURN_TOP_K] if score >= TURN_SCORE_MIN_RATIO * top_turn_score
    ]

    candidates = _collect_chunk_candidates(archive_path, matched_turns, query)
    candidates.sort(key=lambda chunk: (-chunk["score"], chunk["chunk_id"]))
    budget = _max_content_tokens()
    chunks: list[dict[str, Any]] = []
    returned_tokens = 0
    truncated = False
    for candidate in candidates:
        chunk_tokens = _estimate_tokens(candidate["content"])
        if returned_tokens + chunk_tokens > budget:
            truncated = True
            continue
        returned_tokens += chunk_tokens
        chunks.append(candidate)

    top_turn_score_value, top_turn = matched_turns[0]
    return {
        "memory_id": memory_id,
        "archive_path": str(archive_path),
        "matched_turn": {
            "turn_id": top_turn.get("turn_id"),
            "query": top_turn.get("query", ""),
            "answer": top_turn.get("answer", ""),
            "score": top_turn_score_value,
        },
        "matched_turns": [
            {
                "turn_id": turn.get("turn_id"),
                "query": turn.get("query", ""),
                "answer": turn.get("answer", ""),
                "score": score,
            }
            for score, turn in matched_turns
        ],
        "chunks": chunks,
        "truncated": truncated,
        "returned_tokens": returned_tokens,
    }


def _collect_chunk_candidates(
    archive_path: Path,
    matched_turns: list[tuple[float, dict[str, Any]]],
    query: str,
) -> list[dict[str, Any]]:
    """Collect up to CHUNK_TOP_K positively scored chunks from each matched turn."""
    candidates: list[dict[str, Any]] = []
    for _, turn in matched_turns:
        chunk_documents: list[str] = []
        chunk_records: list[dict[str, Any]] = []
        for relative_path in turn.get("chunk_paths") or []:
            chunk_path = _resolve_relative_file(archive_path, relative_path)
            try:
                markdown = chunk_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise CompressionRecallError("compression recall chunk could not be read") from exc
            chunk_documents.append(normalize_markdown(markdown))
            chunk_records.append({"path": str(chunk_path), "content": markdown})

        chunk_scores = rank_bm25(query, chunk_documents)
        ranked_chunks = sorted(
            ((score, index, chunk_records[index]) for index, score in enumerate(chunk_scores) if score > 0),
            key=lambda item: (-item[0], item[1]),
        )[:CHUNK_TOP_K]
        for score, _, record in ranked_chunks:
            candidates.append(
                {
                    "chunk_id": Path(record["path"]).stem,
                    "turn_id": turn.get("turn_id"),
                    "score": score,
                    "path": record["path"],
                    "content": record["content"],
                }
            )
    return candidates


def _max_content_tokens() -> int:
    raw = os.getenv(MAX_CONTENT_TOKENS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_CONTENT_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONTENT_TOKENS
    return value if value > 0 else DEFAULT_MAX_CONTENT_TOKENS


def _estimate_tokens(text: str) -> int:
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text, disallowed_special=()))
    except Exception:
        return len(text) // 3


def _resolve_relative_file(archive_path: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise CompressionRecallError("compression recall chunk path is invalid")
    path = _resolve_inside(archive_path / relative_path, archive_path, kind="chunk")
    if not path.is_file():
        raise CompressionRecallError("compression recall chunk is invalid")
    return path


def _validate_session_root(workspace_dir: Path, session_root: Path) -> None:
    resolved_workspace = workspace_dir.resolve()
    try:
        relative_parts = session_root.relative_to(workspace_dir).parts
    except ValueError as exc:
        raise CompressionRecallError("compression recall session path is invalid") from exc
    current = workspace_dir
    for part in relative_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise CompressionRecallError("compression recall session path is invalid")
    if session_root.exists():
        try:
            session_root.resolve().relative_to(resolved_workspace)
        except ValueError as exc:
            raise CompressionRecallError("compression recall session path is invalid") from exc


def _resolve_inside(path: Path, root: Path, *, kind: str) -> Path:
    if path.is_symlink():
        raise CompressionRecallError(f"compression recall {kind} symlink is not allowed")
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise CompressionRecallError(f"compression recall {kind} path escapes the current session") from exc
    if not resolved_path.exists():
        raise CompressionRecallError(f"compression recall {kind} was not found")
    return resolved_path


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompressionRecallError("compression recall manifest is invalid") from exc
    if not isinstance(value, dict):
        raise CompressionRecallError("compression recall manifest is invalid")
    return value


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise CompressionRecallError("compression recall turn index is invalid")
                records.append(value)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompressionRecallError("compression recall turn index is invalid") from exc
    return records


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return safe[:120] or "unknown"
