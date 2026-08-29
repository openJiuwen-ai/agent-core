# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-process Code Graph telemetry for A/B analysis.

Events stay in memory until ``snapshot_code_graph_metrics``. Eval scripts dump
JSON after each instance; production can ignore the collector.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_events: list[dict[str, Any]] = []
_INDEX_KINDS = {
    "index_build",
    "index_cache_hit",
    "index_memory",
    "index_refresh",
    "index_checkpoint",
}


def reset_code_graph_metrics() -> None:
    with _lock:
        _events.clear()


def record_code_graph_event(kind: str, duration_ms: float, **fields: Any) -> None:
    event: dict[str, Any] = {
        "kind": kind,
        "duration_ms": round(float(duration_ms), 3),
        "ts": time.time(),
    }
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    with _lock:
        _events.append(event)


def snapshot_code_graph_metrics() -> dict[str, Any]:
    with _lock:
        events = [dict(item) for item in _events]
    index_events = [item for item in events if item.get("kind") in _INDEX_KINDS]
    query_events = [item for item in events if str(item.get("kind") or "").startswith("query_")]
    build_ms = sum(item["duration_ms"] for item in index_events if item.get("kind") == "index_build")
    cache_ms = sum(
        item["duration_ms"] for item in index_events if item.get("kind") in {"index_cache_hit", "index_memory"}
    )
    refresh_ms = sum(item["duration_ms"] for item in index_events if item.get("kind") == "index_refresh")
    query_ms = sum(item["duration_ms"] for item in query_events)
    return {
        "events": events,
        "totals": {
            "index_build_ms": round(build_ms, 3),
            "index_cache_hit_ms": round(cache_ms, 3),
            "index_refresh_ms": round(refresh_ms, 3),
            "index_cache_hits": sum(
                1 for item in index_events if item.get("kind") in {"index_cache_hit", "index_memory"}
            ),
            "index_builds": sum(1 for item in index_events if item.get("kind") == "index_build"),
            "index_refreshes": sum(1 for item in index_events if item.get("kind") == "index_refresh"),
            "index_checkpoints": sum(1 for item in events if item.get("kind") == "index_checkpoint"),
            "full_rebuilds": sum(1 for item in events if item.get("reason") == "full"),
            "incremental_refreshes": sum(1 for item in events if item.get("reason") == "incremental"),
            "query_ms": round(query_ms, 3),
            "query_count": len(query_events),
            "query_waits": sum(1 for item in events if item.get("kind") == "query_wait"),
            "search_code_count": sum(1 for item in query_events if item.get("kind") == "query_search_code"),
            "search_text_count": sum(1 for item in query_events if item.get("kind") == "query_search_text"),
            "list_symbols_count": sum(1 for item in query_events if item.get("kind") == "query_list_symbols"),
            "expand_related_count": sum(1 for item in query_events if item.get("kind") == "query_expand_related"),
        },
        "last_index": index_events[-1] if index_events else None,
    }
