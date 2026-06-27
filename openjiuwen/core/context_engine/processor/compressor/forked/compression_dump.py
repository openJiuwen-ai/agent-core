from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.processor.compressor.forked.base import (
    ForkedPrefixCompactProcessor,
    PrefixCompactSpan,
)
from openjiuwen.core.context_engine.processor.compressor.forked.executor import (
    ForkedCompressionExecutor,
    ForkedCompressionRequest,
)
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm.request_trace import _to_jsonable


FORKED_COMPRESSION_DUMP_DIR_ENV = "OPENJIUWEN_FORKED_COMPRESSION_DUMP_DIR"


def dump_compression_artifact(
    *,
    processor: ForkedPrefixCompactProcessor,
    context: ModelContext,
    context_window: ContextWindow,
    config: Any,
    processor_type: str,
    original_messages: list[BaseMessage],
    span: PrefixCompactSpan,
    prompt: str,
    request: ForkedCompressionRequest,
    response_content: str,
    summary: str,
    new_messages: list[BaseMessage],
    usage: dict[str, Any] | None,
) -> str | None:
    """Persist one forked compression invocation for offline effect analysis.

    Resolution order for the dump directory:
      1. ``config.compression_dump_dir`` (explicit override)
      2. ``OPENJIUWEN_FORKED_COMPRESSION_DUMP_DIR`` env var
      3. ``{workspace_dir}/context/{session_id}_context/forked_compression_logs/``

    Returns the written file path, or ``None`` when dumping is disabled or
    fails. All filesystem errors are swallowed so tracing never breaks the
    compression pipeline.
    """
    if not getattr(config, "enable_compression_dump", False):
        return None

    dump_dir = _resolve_dump_dir(context, config)
    if not dump_dir:
        logger.info("[%s] compression dump skipped: no dump dir resolved", processor_type)
        return None

    context_max = processor._resolve_context_max(context, {})
    tokens_before = processor._count_messages_tokens(original_messages, context)
    tokens_after = processor._count_messages_tokens(new_messages, context)
    messages_sent_to_model = ForkedCompressionExecutor.build_messages(request)

    payload = {
        "processor_type": processor_type,
        "session_id": _safe_context_value(context, "session_id", "unknown_session"),
        "context_id": _safe_context_value(context, "context_id", "unknown_context"),
        "trigger": {
            "context_max": context_max,
            "total_tokens_before": tokens_before,
            "target_tokens": processor._count_messages_tokens(span.messages_to_compress, context),
        },
        "span": {
            "preserved_prefix_count": len(span.preserved_prefix),
            "messages_to_compress_count": len(span.messages_to_compress),
            "protected_tail_count": len(span.protected_tail),
        },
        "compression_request": {
            "prompt": prompt,
            "exclude_recent_messages": request.exclude_recent_messages,
            "system_messages_count": len(list(request.system_messages or [])),
            "tools_count": len(list(request.tools or [])),
            "tools": [_to_jsonable(tool) for tool in (request.tools or [])],
            "messages_sent_to_model": [_to_jsonable(message) for message in messages_sent_to_model],
        },
        "compression_response": {
            "raw_content": response_content,
            "extracted_summary": summary,
            "usage": usage,
        },
        "context_after": {
            "messages": [_to_jsonable(message) for message in new_messages],
            "total_tokens_after": tokens_after,
        },
        "benefit": _build_benefit(tokens_before, tokens_after),
    }

    file_name = _build_file_name(payload["session_id"], processor_type)
    dump_path = os.path.join(dump_dir, file_name)
    payload["timestamp"] = time.time()
    try:
        os.makedirs(dump_dir, exist_ok=True)
        with open(dump_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # pragma: no cover - defensive: tracing must not break compression
        logger.warning("[%s] failed to dump compression artifact: %s", processor_type, exc, exc_info=True)
        return None
    logger.info("[%s] dumped compression artifact path=%s", processor_type, dump_path)
    return dump_path


def _resolve_dump_dir(context: ModelContext, config: Any) -> str:
    configured = getattr(config, "compression_dump_dir", None)
    if configured:
        return str(configured)
    env_dir = os.getenv(FORKED_COMPRESSION_DUMP_DIR_ENV)
    if env_dir:
        return env_dir
    workspace_dir = _workspace_dir(context)
    if not workspace_dir:
        return ""
    session_id = _safe_context_value(context, "session_id", "unknown_session")
    return os.path.join(workspace_dir, "context", f"{session_id}_context", "forked_compression_logs")


def _workspace_dir(context: ModelContext) -> str:
    method = getattr(context, "workspace_dir", None)
    if not callable(method):
        return ""
    try:
        return str(method() or "")
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


def _build_benefit(tokens_before: int, tokens_after: int) -> dict[str, Any]:
    reduction = max(tokens_before - tokens_after, 0)
    ratio = (reduction / tokens_before) if tokens_before > 0 else 0.0
    return {
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "reduction": reduction,
        "reduction_ratio": round(ratio, 4),
    }


def _build_file_name(session_id: str, processor_type: str) -> str:
    # Millisecond timestamp prefix keeps each invocation's file unique and
    # sortable, mirroring RuleCompressionPipeline's dump naming.
    parts = [
        str(int(time.time() * 1000)),
        _safe_filename_part(processor_type),
        _safe_filename_part(session_id),
    ]
    return "_".join(parts) + ".json"


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or "unknown"
