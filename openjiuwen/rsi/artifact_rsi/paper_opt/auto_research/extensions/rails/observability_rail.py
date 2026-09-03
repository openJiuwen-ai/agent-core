"""OpenJiuwen rail that persists compact, reconstructable subagent traces."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import (
    current_context,
    current_settings,
    digest_text,
    sanitize_for_trace,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    ensure_module_attempt_dir,
    to_project_relative,
    workspace_dir,
)

TRACE_SCHEMA_VERSION = 2
_lock = threading.Lock()


def with_observability(rails: list[Any] | None = None) -> list[Any]:
    """Prepend the observability rail so every DeepAgent records a disk trace."""
    return [ObservabilityRail(), *(rails or [])]


class ObservabilityRail(DeepAgentRail):
    """Append-only JSONL traces; never raises into the agent loop."""

    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self._reset_state()

    def _reset_state(self) -> None:
        self._seq = 0
        self._header_written = False
        self._last_agent: tuple[str, str, str] | None = None
        self._last_messages: list[dict[str, Any]] = []
        self._model_started: dict[int, tuple[float, str]] = {}
        self._tool_started: dict[int, tuple[float, str]] = {}
        self._task_started: dict[int, float] = {}
        self._invoke_started: float | None = None
        self._model_seq = 0
        self._tool_seq = 0
        self._model_calls = 0
        self._tool_calls = 0
        self._token_totals: dict[str, int] = {}
        self._saw_error = False
        self._task_query_emitted = False

    async def before_invoke(self, ctx) -> None:
        self._reset_state()
        self._invoke_started = time.monotonic()
        self._emit("trace_start", ctx, extra=_agent_meta(ctx), header=True)

    async def after_invoke(self, ctx) -> None:
        extra = {
            "status": "error" if self._saw_error else "ok",
            "duration_ms": _elapsed_ms(self._invoke_started),
            "model_calls": self._model_calls,
            "tool_calls": self._tool_calls,
            "usage": dict(self._token_totals),
        }
        self._emit("trace_end", ctx, extra=extra)

    async def before_task_iteration(self, ctx) -> None:
        iteration = _iteration(ctx)
        self._task_started[iteration] = time.monotonic()
        extra: dict[str, Any] = {"iteration": iteration}
        if not self._task_query_emitted:
            extra.update(_task_preview(ctx))
            self._task_query_emitted = True
        else:
            extra["conversation_id"] = _conversation_id(ctx)
        self._emit("task_iteration_start", ctx, extra=extra)

    async def after_task_iteration(self, ctx) -> None:
        iteration = _iteration(ctx)
        extra = {
            "iteration": iteration,
            "duration_ms": _elapsed_ms(self._task_started.pop(iteration, None)),
        }
        self._emit("task_iteration_end", ctx, extra=extra)

    async def after_react_iteration(self, ctx) -> None:
        return

    async def before_model_call(self, ctx) -> None:
        self._model_seq += 1
        call_id = f"m{self._model_seq}"
        self._model_started[id(ctx)] = (time.monotonic(), call_id)
        self._model_calls += 1
        inputs = getattr(ctx, "inputs", None)
        extra = {
            "call_id": call_id,
            "tool_count": _tool_count(inputs),
            **_context_delta(self._last_messages, _normalize_messages(inputs)),
        }
        self._last_messages = _normalize_messages(inputs)
        extra["messages"] = sanitize_for_trace(extra["messages"])
        self._emit("model_call_start", ctx, extra=extra)

    async def after_model_call(self, ctx) -> None:
        started, call_id = self._pop_call(self._model_started, ctx)
        inputs = getattr(ctx, "inputs", None)
        usage = _token_usage(inputs)
        _accumulate_usage(self._token_totals, usage)
        extra = {
            "call_id": call_id,
            "duration_ms": _elapsed_ms(started),
            "response": sanitize_for_trace(_response_preview(inputs)),
            "usage": usage,
        }
        self._emit("model_call_end", ctx, extra=extra)

    async def on_model_exception(self, ctx) -> None:
        self._saw_error = True
        started, call_id = self._pop_call(self._model_started, ctx)
        extra = {
            "call_id": call_id,
            "duration_ms": _elapsed_ms(started),
            "error": _exception_payload(ctx),
        }
        self._emit("model_call_error", ctx, extra=extra)

    async def before_tool_call(self, ctx) -> None:
        self._tool_seq += 1
        call_id = f"t{self._tool_seq}"
        self._tool_started[id(ctx)] = (time.monotonic(), call_id)
        self._tool_calls += 1
        inputs = getattr(ctx, "inputs", None)
        extra = {
            "call_id": call_id,
            "tool_name": _tool_name(inputs),
            "arguments": sanitize_for_trace(_tool_args(inputs)),
        }
        self._emit("tool_call_start", ctx, extra=extra)

    async def after_tool_call(self, ctx) -> None:
        started, call_id = self._pop_call(self._tool_started, ctx)
        inputs = getattr(ctx, "inputs", None)
        extra = {
            "call_id": call_id,
            "duration_ms": _elapsed_ms(started),
            "tool_name": _tool_name(inputs),
            "result": sanitize_for_trace(_tool_result(inputs)),
            "status": "ok",
        }
        self._emit("tool_call_end", ctx, extra=extra)

    async def on_tool_exception(self, ctx) -> None:
        self._saw_error = True
        started, call_id = self._pop_call(self._tool_started, ctx)
        inputs = getattr(ctx, "inputs", None)
        extra = {
            "call_id": call_id,
            "duration_ms": _elapsed_ms(started),
            "tool_name": _tool_name(inputs),
            "error": _exception_payload(ctx),
            "status": "error",
        }
        self._emit("tool_call_error", ctx, extra=extra)

    def _pop_call(
        self, store: dict[int, tuple[float, str]], ctx: Any
    ) -> tuple[float | None, str | None]:
        started, call_id = store.pop(id(ctx), (None, None))
        return started, call_id

    def _emit(
        self,
        event: str,
        ctx: Any,
        *,
        extra: dict[str, Any] | None = None,
        header: bool = False,
    ) -> None:
        if not current_settings().enabled:
            return
        try:
            path = _trace_path()
            if path is None:
                return
            with _lock:
                records = []
                if not self._header_written and not header:
                    records.append(self._header_record(ctx))
                records.append(self._event_record(event, ctx, extra=extra, header=header))
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001, S110 — observability must not break agents
            pass

    def _header_record(self, ctx: Any) -> dict[str, Any]:
        self._header_written = True
        self._seq += 1
        record: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "seq": self._seq,
            "ts": _now(),
            "event": "trace_start",
        }
        ctx_fields = current_context()
        if ctx_fields is not None:
            record.update(ctx_fields.as_dict())
        meta = _agent_meta(ctx)
        record.update(meta)
        self._last_agent = (meta["agent_id"], meta["agent_name"], meta["session_id"])
        return record

    def _event_record(
        self,
        event: str,
        ctx: Any,
        *,
        extra: dict[str, Any] | None,
        header: bool,
    ) -> dict[str, Any]:
        if header:
            self._header_written = True
        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": _now(),
            "event": event,
        }
        if header:
            record["schema_version"] = TRACE_SCHEMA_VERSION
            ctx_fields = current_context()
            if ctx_fields is not None:
                record.update(ctx_fields.as_dict())
        meta = _agent_meta(ctx)
        key = (meta["agent_id"], meta["agent_name"], meta["session_id"])
        if header or key != self._last_agent:
            record.update(meta)
            self._last_agent = key
        if extra:
            record.update(extra)
        return record


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _trace_path() -> Path | None:
    ctx = current_context()
    if ctx is None or not ctx.run_id:
        return None
    if ctx.module and ctx.round_index is not None and ctx.attempt is not None:
        directory = ensure_module_attempt_dir(
            ctx.run_id, ctx.module, ctx.round_index, ctx.attempt
        )
        return directory / "agent_trace.jsonl"
    directory = workspace_dir(ctx.run_id) / "modules" / (ctx.module or "pipeline")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "agent_trace.jsonl"


def _elapsed_ms(started: float | None) -> int | None:
    if started is None:
        return None
    return int((time.monotonic() - started) * 1000)


def _iteration(ctx: Any) -> int:
    inputs = getattr(ctx, "inputs", None)
    value = getattr(inputs, "iteration", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _conversation_id(ctx: Any) -> str:
    inputs = getattr(ctx, "inputs", None)
    return str(getattr(inputs, "conversation_id", None) or "")


def _agent_meta(ctx: Any) -> dict[str, Any]:
    agent = getattr(ctx, "agent", None)
    card = getattr(agent, "card", None)
    session = getattr(ctx, "session", None)
    return {
        "agent_id": getattr(card, "id", None) or getattr(card, "name", None) or "",
        "agent_name": getattr(card, "name", None) or "",
        "session_id": getattr(session, "session_id", None) or "",
    }


def _task_preview(ctx: Any) -> dict[str, Any]:
    inputs = getattr(ctx, "inputs", None)
    return {
        "conversation_id": getattr(inputs, "conversation_id", None) or "",
        "query": sanitize_for_trace(getattr(inputs, "query", None) or ""),
    }


def _normalize_messages(inputs: Any) -> list[dict[str, Any]]:
    messages = getattr(inputs, "messages", None)
    if not messages:
        return []
    out: list[dict[str, Any]] = []
    for message in list(messages):
        if isinstance(message, dict):
            out.append({"role": message.get("role"), "content": message.get("content")})
            continue
        out.append(
            {
                "role": getattr(message, "role", None),
                "content": getattr(message, "content", None),
            }
        )
    return out


def _message_key(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str, sort_keys=True)
    return f"{message.get('role')}\0{content}"


def _context_delta(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Any]:
    prev_keys = [_message_key(item) for item in previous]
    curr_keys = [_message_key(item) for item in current]
    from_index = 0
    for left, right in zip(prev_keys, curr_keys):
        if left != right:
            break
        from_index += 1
    change = "append" if from_index == len(previous) else "replace"
    digest_source = "\n".join(curr_keys)
    return {
        "message_count": len(current),
        "context_digest": digest_text(digest_source),
        "change": change,
        "from_index": from_index,
        "messages": current[from_index:],
    }


def _tool_count(inputs: Any) -> int:
    tools = getattr(inputs, "tools", None)
    try:
        return len(tools) if tools is not None else 0
    except TypeError:
        return 0


def _response_preview(inputs: Any) -> Any:
    response = getattr(inputs, "response", None)
    if response is None:
        return None
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content") or response.get("output")
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls is None and isinstance(response, dict):
        tool_calls = response.get("tool_calls")
    names: list[str] = []
    if tool_calls:
        for item in tool_calls:
            name = getattr(item, "name", None) or getattr(getattr(item, "function", None), "name", None)
            if name is None and isinstance(item, dict):
                name = item.get("name") or (item.get("function") or {}).get("name")
            if name:
                names.append(str(name))
    return {"content": content, "tool_calls": names}


def _token_usage(inputs: Any) -> dict[str, Any]:
    response = getattr(inputs, "response", None)
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")
            if usage.get(key) is not None
        }
    payload = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            payload[key] = value
    return payload


def _accumulate_usage(totals: dict[str, int], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        try:
            totals[key] = totals.get(key, 0) + int(value)
        except (TypeError, ValueError):
            continue


def _tool_name(inputs: Any) -> str:
    return str(getattr(inputs, "tool_name", None) or "")


def _tool_args(inputs: Any) -> Any:
    return getattr(inputs, "tool_args", None)


def _tool_result(inputs: Any) -> Any:
    result = getattr(inputs, "tool_result", None)
    if result is not None:
        return result
    return getattr(inputs, "tool_msg", None)


def _exception_payload(ctx: Any) -> dict[str, Any]:
    exc = getattr(ctx, "exception", None)
    if exc is None:
        return {}
    return {
        "type": type(exc).__name__,
        "message": sanitize_for_trace(str(exc)),
    }


def reconstruct_messages(records: list[dict[str, Any]]) -> list[Any]:
    """Rebuild the latest model context from schema-v2 deltas (tests / debugging)."""
    messages: list[Any] = []
    for record in records:
        if record.get("event") != "model_call_start":
            continue
        incoming = list(record.get("messages") or [])
        from_index = int(record.get("from_index") or 0)
        if record.get("change") == "append":
            messages.extend(incoming)
            continue
        messages = messages[:from_index] + incoming
    return messages


def trace_file_relative(run_id: str, module: str, round_index: int, attempt: int) -> str:
    path = ensure_module_attempt_dir(run_id, module, round_index, attempt) / "agent_trace.jsonl"
    try:
        return to_project_relative(path)
    except ValueError:
        return path.as_posix()


__all__ = [
    "ObservabilityRail",
    "TRACE_SCHEMA_VERSION",
    "reconstruct_messages",
    "trace_file_relative",
    "with_observability",
]
