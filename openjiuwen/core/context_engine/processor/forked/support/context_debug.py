# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared debug-record writer for the forked context processors.

When ``ContextEngineConfig.enable_context_debug`` (or a processor-local
``enable_compression_dump`` / ``enable_debug_dump``) is on, the forked
processors call :func:`write_debug_record` to persist a single JSONL record
describing one stage of the compression/offload pipeline — a threshold
check, a span split, a compression retry, a before/after diff, etc.

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
