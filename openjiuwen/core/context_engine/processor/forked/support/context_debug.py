# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared debug-record writer for the forked context processors.

When ``ContextEngineConfig.enable_context_debug`` (or a processor-local
``enable_compression_dump`` / ``enable_debug_dump``) is on, the forked
processors call :func:`write_debug_record` to persist a single JSONL record
describing one stage of the compression/offload pipeline — a threshold
check, a span split, a compression retry, a before/after diff, etc.  The
ReAct agent uses the same writer for the final outbound model payload.

All filesystem errors are swallowed so tracing never breaks the context
pipeline.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext

CONTEXT_DEBUG_DIR_ENV = "OPENJIUWEN_CONTEXT_DEBUG_DIR"


def write_debug_record(
    context: ModelContext,
    *,
    processor_type: str,
    event: str,
    enabled: bool,
    dump_dir: str | None,
    **payload: Any,
) -> str | None:
    """Append one JSONL debug record to the processor's debug log file.

    Returns the written file path, or ``None`` when ``enabled`` is False or
    writing fails. ``enabled`` and ``dump_dir`` are passed explicitly so the
    caller owns the toggle (the unified ``enable_context_debug`` flag or a
    processor-local one) without this helper having to read config.
    """
    if not enabled:
        return None
    log_dir = _resolve_debug_dir(context, dump_dir)
    if not log_dir:
        return None
    record: dict[str, Any] = {
        "timestamp": time.time(),
        "event": event,
        "processor": processor_type,
        "session_id": _safe_context_value(context, "session_id", "unknown_session"),
        "context_id": _safe_context_value(context, "context_id", "unknown_context"),
    }
    record.update(payload)
    log_path = os.path.join(log_dir, _debug_log_file_name(processor_type))
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, default=str)
            handle.write("\n")
    except Exception as exc:  # pragma: no cover - tracing must not break the pipeline
        logger.warning("[%s] failed to write context debug record: %s", processor_type, exc, exc_info=True)
        return None
    return log_path


def write_llm_request_record(
    context: ModelContext,
    *,
    enabled: bool,
    dump_dir: str | None,
    model: str | None,
    provider: str | None,
    request_id: str | None,
    sequence: int | None,
    messages: Any,
    tools: Any,
    context_window_tokens: int | None,
    system_message_count: int | None = None,
    context_message_count: int | None = None,
    statistic: Any = None,
    usage_report: Any = None,
) -> str | None:
    """Persist the exact message/tool payload about to be sent to an LLM.

    This is intentionally opt-in because the payload can contain user data,
    tool results, and system instructions.  The record is kept in the same
    context-debug directory as the processor traces, but uses a stable
    ``llm_request.jsonl`` filename so it is easy to inspect independently.
    """
    message_items = _as_debug_sequence(messages)
    tool_items = _as_debug_sequence(tools)
    return write_debug_record(
        context,
        processor_type="llm_request",
        event="pre_call",
        enabled=enabled,
        dump_dir=dump_dir,
        request_id=request_id,
        sequence=sequence,
        model=model,
        provider=provider,
        context_window_tokens=context_window_tokens,
        message_count=len(message_items),
        system_message_count=system_message_count,
        context_message_count=context_message_count,
        tool_count=len(tool_items),
        messages=_json_safe_debug_value(message_items),
        tools=_json_safe_debug_value(tool_items),
        statistic=_json_safe_debug_value(statistic),
        usage_report=_json_safe_debug_value(usage_report),
    )


def _as_debug_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _json_safe_debug_value(value: Any) -> Any:
    """Convert pydantic/custom objects while preserving structured payloads."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe_debug_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_debug_value(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe_debug_value(model_dump(mode="json"))
        except Exception:
            try:
                return _json_safe_debug_value(model_dump())
            except Exception as exc:
                logger.debug(
                    "context debug model_dump() failed for %s: %s",
                    type(value).__name__,
                    exc,
                )
    return str(value)


def _resolve_debug_dir(context: ModelContext, dump_dir: str | None) -> str:
    """Resolution order: explicit dump_dir → env var → workspace default."""
    if dump_dir:
        return os.path.abspath(_expand_template(dump_dir, context))
    env_dir = os.getenv(CONTEXT_DEBUG_DIR_ENV)
    if env_dir:
        return os.path.abspath(_expand_template(env_dir, context))
    workspace_dir = _workspace_dir(context)
    if workspace_dir:
        session_id = _safe_context_value(context, "session_id", "unknown_session")
        return os.path.join(workspace_dir, "context", f"{session_id}_context", "context_debug")
    return os.path.abspath(os.path.join("context", "context_debug"))


def _debug_log_file_name(processor_type: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", processor_type).strip("-") or "processor"
    return f"{safe}.jsonl"


def _expand_template(path: str, context: ModelContext) -> str:
    if "{session_id}" not in path and "{context_id}" not in path:
        return path
    return path.format(
        session_id=_safe_filename_part(_safe_context_value(context, "session_id", "unknown_session")),
        context_id=_safe_filename_part(_safe_context_value(context, "context_id", "unknown_context")),
    )


def _workspace_dir(context: ModelContext) -> str:
    method = getattr(context, "workspace_dir", None)
    if not callable(method):
        return ""
    try:
        return os.path.abspath(str(method() or ""))
    except Exception:
        return ""


def _safe_context_value(context: ModelContext, method_name: str, fallback: str) -> str:
    method = getattr(context, method_name, None)
    if not callable(method):
        return fallback
    try:
        value = method()
    except Exception:
        return fallback
    return str(value or fallback)


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or "unknown"
