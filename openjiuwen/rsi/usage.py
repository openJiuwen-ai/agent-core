# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run-scoped model usage observation and a durable, content-free call ledger."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

import aiofiles

from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.core.runner.runner import Runner
from openjiuwen.rsi.events import EventUsage, OnEvent, emit
from openjiuwen.rsi.schema import RsiModelCall, RsiUsage, RsiUsageTokens

_observer: ContextVar[ModelUsageObserver | None] = ContextVar("rsi_usage_observer", default=None)
_node: ContextVar[str | None] = ContextVar("rsi_usage_node", default=None)
_stage: ContextVar[str | None] = ContextVar("rsi_usage_stage", default=None)


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _counter(value: Any, *keys: str) -> int | None:
    for key in keys:
        raw = _field(value, key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            return raw
    return None


def usage_tokens(usage: Any) -> RsiUsageTokens:
    """Normalize core/OpenAI counters without estimating absent provider data.

    Input includes cache hits; cache hits are a subset, not extra input tokens.
    Reasoning tokens, when provided, are already included in output tokens.
    """
    if callable(getattr(usage, "model_dump", None)):
        usage = usage.model_dump(exclude_unset=True)
    # Legacy cache_tokens can mean cache writes; do not label it as a hit.
    cache = _counter(usage, "cache_read_tokens", "prompt_cache_hit_tokens", "cache_hit")
    if cache is None:
        cache = _counter(_field(usage, "prompt_tokens_details"), "cached_tokens")
    return RsiUsageTokens(
        input=_counter(usage, "input_tokens", "prompt_tokens", "input"),
        output=_counter(usage, "output_tokens", "completion_tokens", "output"),
        cache_hit=cache,
    )


def usage_snapshot(raw: Any) -> RsiUsage | None:
    """Load the shared cumulative projection from persisted state."""
    if not isinstance(raw, Mapping):
        return None
    return RsiUsage(usage_tokens(raw.get("tokens")), None, int(raw.get("call_count", 0)))


def set_usage_node(node_ref: str) -> None:
    """Select the current iteration node inside the active run only."""
    if _observer.get() is not None:
        _node.set(node_ref)


def model_usage_stage(stage_ref: str):
    """Attribute calls made by an async engine operation to its public stage."""

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            token = _stage.set(stage_ref)
            try:
                return await function(*args, **kwargs)
            finally:
                _stage.reset(token)

        return wrapped

    return decorate


async def record_model_usage(
    *,
    model: str,
    call_id: str,
    usage: Any,
    status: str = "succeeded",
    duration_ms: int | None = None,
    stage_ref: str | None = None,
) -> None:
    """Adapter hook for calls outside core ``Model`` (including subprocesses).

    Invoke in the parent run with the external call's stable id and provider
    usage. Never pass aggregate trial totals as if they were a single call.
    Re-importing the same call on resume is idempotent. No active run is a no-op.
    """
    observer = _observer.get()
    if observer is not None:
        await observer.record(model, call_id, usage, status, duration_ms, _node.get(), stage_ref or _stage.get())


class ModelUsageObserver:
    """Observe only callbacks inherited from this run's async context.

    The runner callback registry is global; ContextVar filtering prevents two
    simultaneous RSI tasks from charging one another. No callbacks transform
    model input/output. Journal replay restores totals without re-emitting deltas.
    """

    def __init__(self, on_event: OnEvent | None) -> None:
        self.on_event = on_event
        self.state: dict[str, Any] | None = None
        self.path: Path | None = None
        self.seen: set[str] = set()
        self.pending: dict[str, tuple[str, float, str | None, str | None]] = {}
        self.lock = asyncio.Lock()
        self.delivery_error: Exception | None = None
        self.sequence = 0
        self.totals = RsiUsage(RsiUsageTokens(0, 0, 0), None, 0)

    async def bind(self, state: dict[str, Any], output_dir: Path) -> None:
        """Attach only after the controller validated the resumed run identity."""
        self.path = output_dir / "model_calls.jsonl"
        if self.path.is_file():
            async with aiofiles.open(self.path, encoding="utf-8") as stream:
                async for line in stream:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record["task_id"] != str(state.get("task_id", "")):
                        raise ValueError("model usage ledger task_id does not match this run")
                    self.sequence = max(self.sequence, int(record["event_id"]))
                    if record["call_id"] not in self.seen:
                        self.seen.add(record["call_id"])
                        self._accumulate(usage_tokens(record["model_call"]["tokens"]))
        self.state = state
        self._update_state()

    def _accumulate(self, tokens: RsiUsageTokens) -> None:
        previous = asdict(self.totals.tokens)
        summed = {
            key: previous[key] + value if previous[key] is not None and value is not None else None
            for key, value in asdict(tokens).items()
        }
        self.totals = RsiUsage(RsiUsageTokens(**summed), None, self.totals.call_count + 1)

    def _update_state(self) -> None:
        if self.state is not None:
            self.state["usage"] = asdict(self.totals) if self.totals.call_count else None
            self.state["model_calls_path"] = str(self.path)

    def check_delivery(self) -> None:
        """Surface observation failures outside the model's retry/error handler."""
        if self.delivery_error is not None:
            raise RuntimeError(
                "RSI model usage delivery failed; persisted calls can be reconciled"
            ) from self.delivery_error

    async def record(  # pylint: disable=huawei-too-many-arguments
        self,
        model: str,
        call_id: str,
        usage: Any,
        status: str,
        duration_ms: int | None,
        node_ref: str | None,
        stage_ref: str | None,
    ) -> None:
        if self.state is None or self.path is None:
            return
        if not model or not call_id or status not in {"succeeded", "failed", "incomplete"}:
            raise ValueError("model usage requires model, stable call_id and valid status")
        async with self.lock:
            if call_id in self.seen:
                return
            event = EventUsage(
                event_id=self.sequence + 1,
                task_id=str(self.state.get("task_id", "")),
                ts=datetime.now(UTC).isoformat(),
                call_id=call_id,
                model_call=RsiModelCall(model, 1, usage_tokens(usage), status, duration_ms),
                node_ref=node_ref,
                stage_ref=stage_ref,
            )
            async with aiofiles.open(self.path, "a", encoding="utf-8", newline="\n") as stream:
                await stream.write(json.dumps(asdict(event), ensure_ascii=True) + "\n")
            self.sequence = event.event_id
            self.seen.add(call_id)
            self._accumulate(event.model_call.tokens)
            self._update_state()
            # Service-side work in the sink is not an engine model call; it
            # must not recursively re-enter this observer while its lock is held.
            token = _observer.set(None)
            try:
                await emit(self.on_event, event)
            except Exception as exc:
                self.delivery_error = exc
            finally:
                _observer.reset(token)

    async def _input(self, **kwargs) -> None:
        if _observer.get() is not self:
            return
        call_id = get_current_llm_call_id()
        model = kwargs.get("model") or _field(kwargs.get("model_config"), "model_name") or "unknown"
        if call_id:
            self.pending[call_id] = (model, time.monotonic(), _node.get(), _stage.get())

    async def _complete(self, *, model_name=None, usage=None, error=None, **_kwargs) -> None:
        if _observer.get() is not self:
            return
        call_id = get_current_llm_call_id()
        if not call_id or call_id in self.seen:
            return
        pending = self.pending.pop(call_id, None)
        model, started, node, stage = pending or (model_name or "unknown", time.monotonic(), _node.get(), _stage.get())
        try:
            await self.record(
                model_name or model,
                call_id,
                usage,
                "failed" if error is not None else "succeeded",
                max(0, round((time.monotonic() - started) * 1000)) if pending else None,
                node,
                stage,
            )
        except Exception as exc:
            self.delivery_error = exc

    async def _invoke_output(self, *, result=None, **_kwargs) -> None:
        # Custom clients may omit LLM_OUTPUT; the Model wrapper still supplies
        # an invocation result. Native clients' duplicate callback is ignored.
        await self._complete(usage=_field(result, "usage_metadata"))

    async def finish_pending(self) -> None:
        """Count interrupted/unreported requests without inventing success/tokens."""
        for call_id, (model, started, node, stage) in list(self.pending.items()):
            await self.record(
                model, call_id, None, "incomplete", max(0, round((time.monotonic() - started) * 1000)), node, stage
            )
            self.pending.pop(call_id, None)

    @asynccontextmanager
    async def observe(self):
        """Install scoped subscriptions, removing them even on cancellation."""
        framework = Runner.callback_framework
        bindings = (
            (LLMCallEvents.LLM_INVOKE_INPUT, self._input),
            (LLMCallEvents.LLM_STREAM_INPUT, self._input),
            (LLMCallEvents.LLM_OUTPUT, self._complete),
            (LLMCallEvents.LLM_INVOKE_OUTPUT, self._invoke_output),
            (LLMCallEvents.LLM_CALL_ERROR, self._complete),
        )
        token = _observer.set(self)
        node_token = _node.set("h0")
        try:
            for event, callback in bindings:
                framework.register_sync(event, callback)
            yield self
            self.check_delivery()
        finally:
            for event, callback in bindings:
                framework.unregister_sync(event, callback)
            _node.reset(node_token)
            _observer.reset(token)


async def bind_model_usage(state: dict[str, Any], output_dir: Path) -> None:
    """Bind the active observer to the controller's validated state."""
    observer = _observer.get()
    if observer is not None:
        await observer.bind(state, output_dir)
