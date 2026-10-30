# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Invocation-local accounting only; never controls browser execution or budgets."""

from __future__ import annotations

import copy
import math
import time
import uuid
from typing import Any

from .browser_logging import browser_agent_log_warning

_STATE_KEY = "__browser_model_usage__"
_COUNTERS = (
    "calls", "input_tokens", "output_tokens", "total_tokens", "elapsed_ms",
    "usage_reported_calls", "usage_unknown_calls", "failed_calls", "cancelled_calls", "pending_calls",
)


def new_model_usage() -> dict[str, Any]:
    return {
        "run_id": uuid.uuid4().hex,
        "policy_windows": 0,
        "llm": dict.fromkeys(_COUNTERS, 0),
        "jev": dict.fromkeys(_COUNTERS, 0),
        "active": {},
    }


def load_model_usage(session: Any) -> dict[str, Any] | None:
    getter = getattr(session, "get_state", None)
    if not callable(getter):
        return None
    try:
        state = getter(_STATE_KEY)
        if isinstance(state, dict) and state.get("run_id") and "active" in state:
            return copy.deepcopy(state)
    except Exception:
        browser_agent_log_warning("[BROWSER_MODEL_USAGE] unable to read counters")
    return None


def store_model_usage(session: Any, state: dict[str, Any]) -> None:
    updater = getattr(session, "update_state", None)
    if callable(updater):
        try:
            # Session recursively merges dictionaries; replace this snapshot so
            # removed call handles cannot survive completion or invocation reset.
            # Both updates are synchronous, with no interleaving await.
            updater({_STATE_KEY: None})
            updater({_STATE_KEY: state})
        except Exception:
            browser_agent_log_warning("[BROWSER_MODEL_USAGE] unable to save counters")


def mark_policy_window(session: Any) -> None:
    state = load_model_usage(session) or new_model_usage()
    state["policy_windows"] += 1
    store_model_usage(session, state)


def start_model_call(session: Any, source: str) -> tuple[str, str]:
    state = load_model_usage(session) or new_model_usage()
    call_id = uuid.uuid4().hex
    state["active"][call_id] = {"source": source, "started_at": time.monotonic()}
    store_model_usage(session, state)
    return state["run_id"], call_id


def _token(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return value if type(value) is int and value >= 0 else None


def add_model_usage(
    state: dict[str, Any], source: str, usage: Any, elapsed_ms: float, *, status: str = "succeeded"
) -> None:
    counters = state[source]
    counters["calls"] += 1
    input_tokens = _token(usage, "input_tokens")
    output_tokens = _token(usage, "output_tokens")
    total_tokens = _token(usage, "total_tokens")
    counters["input_tokens"] += input_tokens or 0
    counters["output_tokens"] += output_tokens or 0
    # Some clients omit total or leave its schema default at zero.
    counters["total_tokens"] += max(total_tokens or 0, (input_tokens or 0) + (output_tokens or 0))
    known = input_tokens is not None and output_tokens is not None and status == "succeeded"
    counters["usage_reported_calls" if known else "usage_unknown_calls"] += 1
    if status in {"failed", "cancelled", "pending"}:
        counters[status + "_calls"] += 1
    if isinstance(elapsed_ms, (int, float)) and math.isfinite(elapsed_ms):
        counters["elapsed_ms"] += max(0, elapsed_ms)


def finish_model_call(
    session: Any,
    handle: tuple[str, str],
    usage: Any,
    elapsed_ms: float,
    *,
    status: str = "succeeded",
) -> None:
    state = load_model_usage(session)
    if state is None or state["run_id"] != handle[0]:
        return
    active = state["active"].pop(handle[1], None)
    if active is None:
        return  # Exactly once, including close/exception paths.
    add_model_usage(state, active["source"], usage, elapsed_ms, status=status)
    store_model_usage(session, state)


def summarize_model_usage(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(state)
    for active in snapshot["active"].values():
        add_model_usage(
            snapshot, active["source"], None,
            (time.monotonic() - active["started_at"]) * 1000, status="pending",
        )
    groups = {source: snapshot[source] for source in ("llm", "jev")}
    groups["total"] = {key: sum(group[key] for group in groups.values()) for key in _COUNTERS}
    for group in groups.values():
        group["elapsed_ms"] = round(group["elapsed_ms"], 3)
        group["token_usage_complete"] = group["usage_unknown_calls"] == 0
    return {"scope": "invocation", "invocation_id": state["run_id"],
            "calls_scope": "client_invocations", **groups}
