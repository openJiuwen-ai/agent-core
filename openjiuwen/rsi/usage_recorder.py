# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context-local LLM usage ledger utilities."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass
class _UsageLedger:
    path: Path
    run_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)


_CURRENT_LEDGER: ContextVar[_UsageLedger | None] = ContextVar(
    "openjiuwen_llm_usage_ledger",
    default=None,
)
_CURRENT_SCOPE: ContextVar[dict[str, Any]] = ContextVar(
    "openjiuwen_llm_usage_scope",
    default={},
)


@contextmanager
def llm_usage_ledger(path: str | Path, *, run_id: str = "") -> Iterator[None]:
    """Record LLM usage events from the current context into ``path``."""
    ledger_path = Path(path).expanduser().resolve()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = _UsageLedger(path=ledger_path, run_id=str(run_id or uuid.uuid4().hex))
    token = _CURRENT_LEDGER.set(ledger)
    try:
        yield
    finally:
        _CURRENT_LEDGER.reset(token)


def activate_llm_usage_ledger(path: str | Path, *, run_id: str = "") -> Any:
    """Activate a usage ledger and return the context token for callers to reset."""
    ledger_path = Path(path).expanduser().resolve()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = _UsageLedger(path=ledger_path, run_id=str(run_id or uuid.uuid4().hex))
    return _CURRENT_LEDGER.set(ledger)


def reset_llm_usage_ledger(token: Any) -> None:
    """Reset a token returned by :func:`activate_llm_usage_ledger`."""
    _CURRENT_LEDGER.reset(token)


@contextmanager
def llm_usage_scope(
    *,
    stage: str | None = None,
    operation: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Attach stage metadata to LLM usage events in this context."""
    current = dict(_CURRENT_SCOPE.get() or {})
    if stage is not None:
        current["stage"] = str(stage)
    if operation is not None:
        current["operation"] = str(operation)
    if metadata:
        current_metadata = dict(current.get("metadata") or {})
        current_metadata.update(metadata)
        current["metadata"] = current_metadata
    token = _CURRENT_SCOPE.set(current)
    try:
        yield
    finally:
        _CURRENT_SCOPE.reset(token)


def record_llm_usage(usage_metadata: Any, *, metadata: dict[str, Any] | None = None) -> None:
    """Append one LLM usage event if a ledger is active."""
    ledger = _CURRENT_LEDGER.get()
    if ledger is None or usage_metadata is None:
        return

    scope = dict(_CURRENT_SCOPE.get() or {})
    event_metadata = dict(scope.get("metadata") or {})
    if metadata:
        event_metadata.update(metadata)

    input_tokens = _int_field(usage_metadata, "input_tokens")
    output_tokens = _int_field(usage_metadata, "output_tokens")
    total_tokens = _int_field(usage_metadata, "total_tokens") or (input_tokens + output_tokens)
    cache_tokens = _int_field(usage_metadata, "cache_tokens")
    event = {
        "event_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": ledger.run_id,
        "stage": str(scope.get("stage", "")),
        "operation": str(scope.get("operation", "")),
        "model_name": str(_field(usage_metadata, "model_name", "")),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_tokens": cache_tokens,
        "cache_hit_rate": _ratio(cache_tokens, input_tokens),
        "input_cost": _float_field(usage_metadata, "input_cost"),
        "output_cost": _float_field(usage_metadata, "output_cost"),
        "total_cost": _float_field(usage_metadata, "total_cost"),
        "total_latency": _float_field(usage_metadata, "total_latency"),
        "metadata": event_metadata,
    }
    with ledger.lock:
        with ledger.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def summarize_llm_usage_file(
    path: str | Path,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    """Summarize one usage JSONL file by total, stage, operation, and model."""
    usage_path = Path(path).expanduser().resolve()
    summary = {
        "path": str(usage_path),
        "run_id": str(run_id or ""),
        "total": _empty_usage_summary(),
        "by_stage": {},
        "by_operation": {},
        "by_model": {},
        "parse_errors": 0,
    }
    if not usage_path.is_file():
        return summary

    with usage_path.open(encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                summary["parse_errors"] += 1
                continue
            if run_id and str(event.get("run_id", "")) != run_id:
                continue
            _accumulate_usage(summary["total"], event)
            _accumulate_group(summary["by_stage"], str(event.get("stage", "")), event)
            _accumulate_group(
                summary["by_operation"],
                str(event.get("operation", "")),
                event,
            )
            _accumulate_group(
                summary["by_model"],
                str(event.get("model_name", "")),
                event,
            )

    _finalize_usage_summary(summary["total"])
    for group in ("by_stage", "by_operation", "by_model"):
        for aggregate in summary.get(group, {}).values():
            _finalize_usage_summary(aggregate)
    return summary


def _accumulate_group(groups: dict[str, dict[str, Any]], key: str, event: dict[str, Any]) -> None:
    group_key = key or "(unknown)"
    aggregate = groups.setdefault(group_key, _empty_usage_summary())
    _accumulate_usage(aggregate, event)


def _empty_usage_summary() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_tokens": 0,
        "cache_hit_rate": 0.0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
        "total_latency": 0.0,
    }


def _accumulate_usage(aggregate: dict[str, Any], event: dict[str, Any]) -> None:
    aggregate["calls"] += 1
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_tokens",
    ):
        aggregate[key] += int(event.get(key, 0) or 0)
    for key in ("input_cost", "output_cost", "total_cost", "total_latency"):
        aggregate[key] += float(event.get(key, 0.0) or 0.0)


def _finalize_usage_summary(aggregate: dict[str, Any]) -> None:
    aggregate["cache_hit_rate"] = _ratio(
        int(aggregate.get("cache_tokens", 0) or 0),
        int(aggregate.get("input_tokens", 0) or 0),
    )


def _field(usage_metadata: Any, name: str, default: Any = None) -> Any:
    if isinstance(usage_metadata, dict):
        return usage_metadata.get(name, default)
    return getattr(usage_metadata, name, default)


def _int_field(usage_metadata: Any, name: str) -> int:
    try:
        return int(_field(usage_metadata, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _float_field(usage_metadata: Any, name: str) -> float:
    try:
        return float(_field(usage_metadata, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


__all__ = [
    "activate_llm_usage_ledger",
    "llm_usage_ledger",
    "llm_usage_scope",
    "record_llm_usage",
    "reset_llm_usage_ledger",
    "summarize_llm_usage_file",
]
