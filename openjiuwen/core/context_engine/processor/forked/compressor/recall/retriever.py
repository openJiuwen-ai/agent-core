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


def recall_compressed_context(
    *,
    workspace_dir: str,
    session_id: str,
    memory_id: str | None = None,
    query: str,
) -> dict[str, Any]:
    """Recall budget-capped chunks from compression archives of the current session.

    With ``memory_id`` the search is confined to that one archive (existing
    behavior). Without it, every archive of the session is searched and the
    results are merged with per-archive score normalization — raw BM25 scores
    are corpus-dependent and not comparable across archives.
    """
    if not workspace_dir:
        raise CompressionRecallError("compression recall requires a workspace directory")
    if not session_id:
        raise CompressionRecallError("compression recall requires a session")
    memory_id = (memory_id or "").strip()
    if memory_id and not _MEMORY_ID_RE.fullmatch(memory_id):
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

    archives = _list_session_archives(resolved_session_root, session_id)
    if memory_id:
        result = _recall_single_archive(
            resolved_session_root, archives, memory_id=memory_id, session_id=session_id, query=query
        )
    else:
        if not archives:
            raise CompressionRecallError("compression recall archive was not found in the current session")
        result = _recall_all_archives(archives, query=query)
    result["recall_root"] = str(resolved_session_root)
    result["archives_in_session"] = [
        {
            "memory_id": item["memory_id"],
            "created_at": item["created_at"],
            "turn_count": item["turn_count"],
        }
        for item in archives
    ]
    return result


def _list_session_archives(session_root: Path, session_id: str) -> list[dict[str, Any]]:
    """List valid archives of the session, oldest first (names start with a timestamp)."""
    archives: list[dict[str, Any]] = []
    for path in sorted(session_root.iterdir()):
        if not path.is_dir() or path.is_symlink() or path.name.startswith("."):
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = _read_json_object(manifest_path)
        except CompressionRecallError:
            continue
        memory_id = str(manifest.get("memory_id") or "")
        if not memory_id or not path.name.startswith(f"{memory_id}_"):
            continue
        if str(manifest.get("session_id") or "") != session_id:
            continue
        archives.append(
            {
                "memory_id": memory_id,
                "path": path,
                "created_at": str(manifest.get("created_at") or ""),
                "turn_count": int(manifest.get("turn_count") or 0),
            }
        )
    return archives


def _recall_single_archive(
    session_root: Path,
    archives: list[dict[str, Any]],
    *,
    memory_id: str,
    session_id: str,
    query: str,
) -> dict[str, Any]:
    matches = [item for item in archives if item["memory_id"] == memory_id]
    if len(matches) != 1:
        _raise_for_rejected_candidate(session_root, memory_id, session_id=session_id)
        raise CompressionRecallError("compression recall archive was not found in the current session")
    archive_path = _resolve_inside(matches[0]["path"], session_root, kind="archive")
    result = _search_archive(archive_path, memory_id=memory_id, query=query)
    chunks, truncated, returned_tokens = _apply_budget(result["chunks"])
    result["chunks"] = chunks
    result["truncated"] = truncated
    result["returned_tokens"] = returned_tokens
    return result


def _raise_for_rejected_candidate(session_root: Path, memory_id: str, *, session_id: str) -> None:
    """Report why an explicitly requested archive was rejected, when it exists.

    ``_list_session_archives`` silently excludes foreign or malformed archives;
    an explicit ``memory_id`` lookup keeps the specific errors so callers can
    tell "no such archive" apart from "not your session's archive".
    """
    candidates = [path for path in session_root.glob(f"{memory_id}_*") if path.is_dir() and not path.is_symlink()]
    if len(candidates) != 1:
        return
    try:
        manifest = _read_json_object(candidates[0] / "manifest.json")
    except CompressionRecallError:
        return
    if str(manifest.get("session_id") or "") != session_id:
        raise CompressionRecallError("compression recall archive does not belong to the current session")
    if str(manifest.get("memory_id") or "") != memory_id:
        raise CompressionRecallError("compression recall archive id does not match")


def _normalized_scored(items: list[dict[str, Any]], *, keep_raw: bool) -> list[dict[str, Any]]:
    """Normalize scores to the archive's own top item (raw BM25 scores are not
    comparable across archives, so each archive's top scores 1.0)."""
    top_score = max((entry["score"] for entry in items), default=0.0)
    for entry in items:
        if keep_raw:
            entry["raw_score"] = entry["score"]
        if top_score > 0:
            entry["score"] = entry["score"] / top_score
    return items


def _recall_all_archives(archives: list[dict[str, Any]], *, query: str) -> dict[str, Any]:
    merged_chunks: list[dict[str, Any]] = []
    merged_turns: list[dict[str, Any]] = []
    for item in archives:
        sub = _search_archive(item["path"], memory_id=item["memory_id"], query=query)
        merged_chunks.extend(_normalized_scored(sub["chunks"], keep_raw=True))
        merged_turns.extend(_normalized_scored(sub["matched_turns"], keep_raw=False))
    # Primary key is the per-archive normalized score; ties (each archive's top
    # chunk normalizes to 1.0) fall back to the raw score, which still ranks a
    # strongly matching archive above an incidentally matching one.
    merged_chunks.sort(key=lambda chunk: (-chunk["score"], -chunk["raw_score"], chunk["chunk_id"]))
    merged_turns.sort(key=lambda turn: (-turn["score"], turn["memory_id"], turn["turn_id"]))

    chunks, truncated, returned_tokens = _apply_budget(merged_chunks)
    matched_turn = merged_turns[0] if merged_turns else None
    archive_path = ""
    if chunks:
        archive_path = chunks[0]["archive_path"]
    elif matched_turn:
        archive_path = str(
            next((item["path"] for item in archives if item["memory_id"] == matched_turn["memory_id"]), "")
        )
    return {
        "memory_id": "",
        "archive_path": archive_path,
        "matched_turn": matched_turn,
        "matched_turns": merged_turns,
        "chunks": chunks,
        "truncated": truncated,
        "returned_tokens": returned_tokens,
    }


def _search_archive(archive_path: Path, *, memory_id: str, query: str) -> dict[str, Any]:
    """Two-stage BM25 search inside one archive; chunks are not budget-capped."""
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
        }

    top_turn_score = ranked_turns[0][0]
    matched_turns = [
        (score, turn) for score, _, turn in ranked_turns[:TURN_TOP_K] if score >= TURN_SCORE_MIN_RATIO * top_turn_score
    ]

    candidates = _collect_chunk_candidates(archive_path, matched_turns, query)
    for candidate in candidates:
        candidate["memory_id"] = memory_id
        candidate["archive_path"] = str(archive_path)
    candidates.sort(key=lambda chunk: (-chunk["score"], chunk["chunk_id"]))

    return {
        "memory_id": memory_id,
        "archive_path": str(archive_path),
        "matched_turn": {
            "memory_id": memory_id,
            "turn_id": matched_turns[0][1].get("turn_id"),
            "query": matched_turns[0][1].get("query", ""),
            "answer": matched_turns[0][1].get("answer", ""),
            "score": matched_turns[0][0],
        },
        "matched_turns": [
            {
                "memory_id": memory_id,
                "turn_id": turn.get("turn_id"),
                "query": turn.get("query", ""),
                "answer": turn.get("answer", ""),
                "score": score,
            }
            for score, turn in matched_turns
        ],
        "chunks": candidates,
    }


def _apply_budget(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, int]:
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
    return chunks, truncated, returned_tokens


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
