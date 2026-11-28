# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect bounded model usage for one PersonalContext fetch run."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any, Iterator
from weakref import WeakSet

from openjiuwen.core.context_engine.context.compression_scope import current_context_compression_operation_id
from openjiuwen.core.context_engine.token.string_length_counter import StringLengthCounter
from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.enums import HookType
from openjiuwen.core.runner.callback.events import LLMCallEvents


_Attempt = dict[str, Any]
RunUsage = dict[str, _Attempt]
_usage_scope: ContextVar[tuple[RunUsage, str] | None] = ContextVar("pcs_run_usage", default=None)
_registered_frameworks: WeakSet = WeakSet()
_registration_lock = Lock()
_MAX_ATTEMPTS = 4096
_MAX_GROUPS = 32
_OVERFLOW = "overflow"
_ESTIMATE_LIMIT = 16_384
_counter = StringLengthCounter()


def _attempt(phase: str, provider: str, model: str) -> _Attempt:
    return _Attempt(
        phase=phase, provider=provider, model=model, input_tokens=None, output_tokens=None,
        total_tokens=None, input_estimate=0, output_estimate=0, completed=False,
        retries=0, dispatches=0, route_attempt=0, response_model="",
    )


@contextmanager
def run_usage_scope(meter: RunUsage | None, phase: str = "pipeline") -> Iterator[None]:
    """Bind usage to the current producer or one queue consumer task."""
    token = _usage_scope.set((meter, phase) if meter is not None else None)
    try:
        yield
    finally:
        _usage_scope.reset(token)


@contextmanager
def model_usage_phase(phase: str) -> Iterator[None]:
    """Observe only requests owned by the current PCS run and phase."""
    scope = _usage_scope.get()
    if scope is None:
        yield
        return
    meter, _ = scope
    try:
        _register_observers()
    except Exception:
        meter.setdefault(_OVERFLOW, _attempt(phase, "", ""))
    with run_usage_scope(meter, phase):
        yield


def note_unobserved_model_call(phase: str, provider: str, model: str) -> None:
    """Indicate that a model entry point hides per-request usage, such as embeddings."""
    scope = _usage_scope.get()
    if scope is not None:
        meter, _ = scope
        key = f"unobserved:{phase}:{provider}:{model[:96]}"
        if key in meter:
            meter[key]["retries"] += 1
        else:
            meter[key] = _attempt(phase, provider[:96], model[:96])


def _count(value: object, *, minimum: int = 0) -> int | None:
    if type(value) is int and minimum <= value <= 1_000_000_000:
        return value
    return None


def _record_usage(attempt: _Attempt, usage: Any) -> None:
    if usage is None:
        return
    # UsageMetadata adapters may explicitly fill absent numbers with zero.
    # Raw mappings can distinguish a reported zero by field presence.
    raw = isinstance(usage, Mapping)
    read = usage.get if raw else lambda key: getattr(usage, key, None)
    input_tokens = _count(read("input_tokens"), minimum=0 if raw else 1)
    output_tokens = _count(read("output_tokens"), minimum=0 if raw else 1)
    total_tokens = _count(read("total_tokens"), minimum=0 if raw else 1)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return
    if input_tokens is not None:
        attempt["input_tokens"] = input_tokens
    if output_tokens is not None:
        attempt["output_tokens"] = output_tokens
    # Adapters synthesize totals from whichever sides a stream frame contains.
    # Recompute those totals from the latest input and output across frames.
    if not raw and total_tokens == (input_tokens or 0) + (output_tokens or 0):
        attempt["total_tokens"] = None
        total_tokens = None
    if total_tokens is not None:
        attempt["total_tokens"] = total_tokens
    model = read("model_name")
    if isinstance(model, str) and model and not attempt["response_model"]:
        attempt["model"] = model[:96]


def _simple_input_estimate(messages: Any, tools: Any) -> int:
    if tools:
        return 0
    if isinstance(messages, str):
        return _counter.count(messages) if len(messages) <= _ESTIMATE_LIMIT else 0
    if not isinstance(messages, (list, tuple)) or len(messages) > 16:
        return 0
    lengths = []
    for message in messages:
        content = getattr(message, "content", None)
        if not isinstance(content, str) or getattr(message, "tool_calls", None):
            return 0
        lengths.append(len(content))
    if not lengths or sum(lengths) > _ESTIMATE_LIMIT:
        return 0
    return _counter.count_messages(messages)


def _observe(event: str, data: dict[str, Any], meter: RunUsage, phase: str) -> None:
    result = data.get("result")
    if event == LLMCallEvents.LLM_STREAM_OUTPUT and getattr(result, "usage_metadata", None) is None:
        return
    call_id = get_current_llm_call_id()
    if not call_id:
        meter.setdefault(_OVERFLOW, _attempt(phase, "", ""))
        return
    entry = meter.get(call_id)
    if entry is None:
        if len(meter) >= _MAX_ATTEMPTS:
            meter.setdefault(_OVERFLOW, _attempt(phase, "", ""))
            return
        if current_context_compression_operation_id():
            phase = "compression"
        provider = data.get("model_provider") or getattr(data.get("model_client_config"), "client_provider", "")
        model = data.get("model") or data.get("model_name") or getattr(data.get("model_config"), "model_name", "")
        entry = meter[call_id] = _attempt(phase, str(provider or "")[:96], str(model or "")[:96])
    if event == LLMCallEvents.LLM_ROUTE_ATTEMPT:
        route = data["route_metadata"]
        route_attempt = entry["route_attempt"]
        if route_attempt:
            if len(meter) >= _MAX_ATTEMPTS:
                meter.setdefault(_OVERFLOW, _attempt(phase, "", ""))
                return
            meter[f"{call_id}:{route_attempt}"] = entry
            next_entry = _attempt(entry["phase"], entry["provider"], entry["model"])
            next_entry["input_estimate"] = entry["input_estimate"]
            meter[call_id] = entry = next_entry
        entry["route_attempt"] = route_attempt + 1
        entry["provider"] = str(route.get("provider") or entry["provider"])[:96]
        entry["model"] = str(route.get("model_name") or route.get("routed_model") or entry["model"])[:96]
    elif event == LLMCallEvents.LLM_INPUT:
        entry["dispatches"] += 1
    elif event in _input_events:
        entry["input_estimate"] = _simple_input_estimate(data.get("messages"), data.get("tools"))
    elif event == LLMCallEvents.LLM_RESPONSE_STARTED:
        entry["retries"] = max(entry["retries"], _count(data.get("retry_count")) or 0)
    else:
        _record_usage(entry, data.get("usage") or getattr(result, "usage_metadata", None))
        model = getattr(result, "response_model", None)
        if isinstance(model, str) and model:
            entry["response_model"] = entry["model"] = model[:96]
        if event in _completion_events:
            entry["completed"] = True
            text = getattr(result, "content", None)
            if not entry["output_tokens"] and isinstance(text, str) and 0 < len(text) <= _ESTIMATE_LIMIT:
                if not getattr(result, "tool_calls", None) and not getattr(result, "reasoning_content", None):
                    entry["output_estimate"] = _counter.count(text)


_input_events = (LLMCallEvents.LLM_INVOKE_INPUT, LLMCallEvents.LLM_STREAM_INPUT)
_completion_events = (LLMCallEvents.LLM_INVOKE_OUTPUT, LLMCallEvents.LLM_STREAM_COMPLETED)


def _observer(event: str):
    def observe(*_args: Any, **data: Any) -> None:
        scope = _usage_scope.get()
        if scope is None:
            return
        meter, phase = scope
        try:
            _observe(event, data, meter, phase)
        except Exception:
            meter.setdefault(_OVERFLOW, _attempt(phase, "", ""))

    return observe


_OBSERVED_EVENTS = (
    *_input_events, *_completion_events, LLMCallEvents.LLM_INPUT, LLMCallEvents.LLM_OUTPUT,
    LLMCallEvents.LLM_STREAM_OUTPUT, LLMCallEvents.LLM_RESPONSE_STARTED, LLMCallEvents.LLM_ROUTE_ATTEMPT,
)
_observers = {event: _observer(event) for event in _OBSERVED_EVENTS}


def _register_observers() -> None:
    framework = Runner.callback_framework
    if framework in _registered_frameworks:
        return
    if not _registration_lock.acquire(blocking=False):
        raise RuntimeError("PCS usage hook registration is busy")
    try:
        if framework not in _registered_frameworks:
            for event, observer in _observers.items():
                # Hooks avoid running global callback filters or rate limits
                # an extra time for unrelated model requests.
                framework.add_hook(event, HookType.BEFORE, observer)
            _registered_frameworks.add(framework)
    finally:
        _registration_lock.release()


def summarize_run_usage(meter: RunUsage) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return a public summary and bounded per-phase, per-model totals."""
    if not meter:
        return {"total_tokens": 0, "has_estimate": False, "incomplete": False}, []
    actual = estimated = unknown = 0
    any_value = False
    detail: dict[tuple[str, str, str], list[int]] = {}
    for entry in meter.values():
        input_tokens = entry["input_tokens"]
        output_tokens = entry["output_tokens"]
        explicit = entry["total_tokens"]
        known = explicit if explicit is not None else (input_tokens or 0) + (output_tokens or 0)
        estimate = 0
        if explicit is None and entry["completed"]:
            if input_tokens is None:
                estimate += entry["input_estimate"]
            if output_tokens is None:
                estimate += entry["output_estimate"]
        complete = explicit is not None or (
            (input_tokens is not None or entry["completed"] and entry["input_estimate"] > 0)
            and (output_tokens is not None or entry["completed"] and entry["output_estimate"] > 0)
        )
        retries = max(entry["retries"], entry["dispatches"] - 1)
        missing = retries + int(not entry["completed"] or not complete)
        actual += known
        estimated += estimate
        unknown += missing
        any_value |= any(value is not None for value in (explicit, input_tokens, output_tokens)) or estimate > 0
        key = (entry["phase"], entry["provider"], entry["model"])
        if key not in detail and len(detail) >= _MAX_GROUPS - 1:
            key = ("other", "", "")
        row = detail.setdefault(key, [0, 0, 0])
        row[0] += known
        row[1] += estimate
        row[2] += missing
    return (
        {"total_tokens": actual + estimated if any_value else None,
         "has_estimate": estimated > 0, "incomplete": unknown > 0},
        [{"phase": phase, "provider": provider, "model": model,
          "actual_tokens": row[0], "estimated_tokens": row[1], "incomplete_attempts": row[2]}
         for (phase, provider, model), row in detail.items()],
    )
