from __future__ import annotations

import json
import os
import queue
from datetime import datetime, timedelta, timezone
import threading
import re
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage

_LOCK = threading.Lock()
_DEFAULT_DIR = "context_trace"
_WRITE_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=50000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    normalized = raw.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()
    normalized = normalized.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    logger.warning(
        "[ContextTrace] invalid boolean env %s=%r, expected True/False, fallback=%s",
        name,
        raw,
        default,
    )
    return default


def context_trace_enabled() -> bool:
    return _env_flag("OPENJIUWEN_CONTEXT_TRACE_ENABLED", False)


def _trace_root_dir() -> Path:
    raw = os.getenv("OPENJIUWEN_CONTEXT_TRACE_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path.cwd() / _DEFAULT_DIR


def _sanitize_session_id(session_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", session_id) or "unknown"


def _trace_file_path(session_id: str) -> Path:
    root = _trace_root_dir()
    safe_session = _sanitize_session_id(session_id)
    return root / f"context_trace_{safe_session}.jsonl"


def _content_preview_limit() -> int:
    if _env_flag("OPENJIUWEN_CONTEXT_TRACE_FULL_CONTENT", False):
        return -1
    raw = os.getenv("OPENJIUWEN_CONTEXT_TRACE_PREVIEW_CHARS", "").strip()
    if not raw:
        return 300
    try:
        val = int(raw)
    except ValueError:
        return 300
    return max(0, val)


def _serialize_content(value: Any, max_chars: int) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    if max_chars < 0:
        return text
    if max_chars == 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[TRUNCATED]"


def _context_trace_time_beijing() -> str:
    """Human-readable local time for trace rows (China Standard Time)."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S") + " 北京时间"


def snapshot_messages(messages: list[BaseMessage] | None) -> list[dict[str, Any]]:
    if not messages:
        return []
    include_content = _env_flag("OPENJIUWEN_CONTEXT_TRACE_INCLUDE_CONTENT", True)
    preview_limit = _content_preview_limit()
    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        metadata = getattr(msg, "metadata", None)
        entry: dict[str, Any] = {
            "idx": idx,
            "role": getattr(msg, "role", None),
            "class": msg.__class__.__name__,
            "name": getattr(msg, "name", None),
            "tool_call_id": getattr(msg, "tool_call_id", None),
            "context_message_id": metadata.get("context_message_id") if isinstance(metadata, dict) else None,
        }
        if include_content:
            content = getattr(msg, "content", "")
            entry["content_len"] = len(content) if isinstance(content, str) else len(str(content))
            entry["content_preview"] = _serialize_content(content, preview_limit)
        out.append(entry)
    return out


def write_context_trace(event_type: str, payload: dict[str, Any]) -> None:
    if not context_trace_enabled():
        return
    session_id = str(payload.get("session_id") or "unknown")
    row = {
        "time": _context_trace_time_beijing(),
        "event_type": event_type,
        **payload,
    }
    try:
        _ensure_worker_started()
        _WRITE_QUEUE.put_nowait((session_id, row))
    except queue.Full:
        logger.warning(
            "[ContextTrace] write queue full, dropping trace event: event_type=%s session_id=%s",
            event_type,
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "[ContextTrace] failed to enqueue trace event, dropping: event_type=%s session_id=%s error=%s",
            event_type,
            session_id,
            exc,
        )


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                session_id, row = _WRITE_QUEUE.get()
                try:
                    _write_item(session_id, row)
                finally:
                    _WRITE_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="context-trace-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def _write_item(session_id: str, row: dict[str, Any]) -> None:
    try:
        path = _trace_file_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
    except Exception:
        return
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
