# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate Codex SDK notifications into protocol v1 observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from openjiuwen.harness_protocol import (
    ContentBlock,
    DiagnosticEvent,
    DiagnosticLevel,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    ProviderEvent,
    TurnError,
    TurnEventKind,
    TurnMessage,
    TurnResult,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    TurnUsage,
    UsageUpdatedEvent,
    UsageUpdateMode,
    freeze_json_object,
    freeze_json_value,
)
from openjiuwen.harness_providers.base import PendingTurn, TurnTiming, interrupted_result
from openjiuwen.harness_providers.codex.failure_classifier import (
    classify_error_notification,
    classify_turn_error,
    merge_pending_error,
)
from openjiuwen.harness_providers.jsonsafe import to_json_object, to_json_safe

PROVIDER_NAME = "codex"
_SCHEMA_VERSION = "1"
TOOL_ITEM_TYPES = frozenset({"commandExecution", "dynamicToolCall", "fileChange", "mcpToolCall"})
_REASONING_METHODS = frozenset({"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"})
# Raw model-response items are large and only useful to observability
# bridges, which read them through the provider-private observer.
_SILENT_METHODS = frozenset({"rawResponseItem/completed", "rawResponse/completed"})


@dataclass(frozen=True, slots=True)
class MappedCodexEvent:
    """One normalized payload plus envelope-only provider correlation."""

    payload: Any
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetryingNotice:
    """A Codex ``will_retry`` error observed while the turn keeps running."""

    error: TurnError


class CodexTurnAccumulator:
    """State needed to normalize the notifications of one Codex turn."""

    def __init__(self, *, turn_id: str) -> None:
        self.turn_id = turn_id
        self.messages: list[TurnMessage] = []
        self.last_text_output = ""
        self.pending_error: TurnError | None = None
        self.completed_turn: Any = None
        self.emitted_output = False
        self.notifications_seen = 0
        self._usage_events: list[TurnUsage] = []
        self._tool_names: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Notification mapping
    # ------------------------------------------------------------------

    def consume(self, notification: Any) -> tuple[list[MappedCodexEvent], RetryingNotice | None]:
        """Map one SDK notification; returns events plus an optional retry notice."""

        self.notifications_seen += 1
        method = str(getattr(notification, "method", "") or "")
        payload = getattr(notification, "payload", None)
        if method == "item/agentMessage/delta":
            return self._delta(payload, OutputChannel.ANSWER), None
        if method in _REASONING_METHODS:
            return self._delta(payload, OutputChannel.REASONING), None
        if method == "item/started":
            return self._item_started(payload), None
        if method == "item/completed":
            return self._item_completed(payload), None
        if method == "thread/tokenUsage/updated":
            return self._usage(payload), None
        if method == "error":
            return self._error(payload)
        if method == "turn/completed":
            self.completed_turn = getattr(payload, "turn", None)
            return [], None
        if method in _SILENT_METHODS:
            return [], None
        return [self._provider_event(method or "unknown-notification", to_json_object(payload))], None

    def _delta(self, payload: Any, channel: OutputChannel) -> list[MappedCodexEvent]:
        delta = getattr(payload, "delta", None)
        if not isinstance(delta, str) or not delta:
            return []
        item_id = str(getattr(payload, "item_id", "") or "unknown-item")
        self.emitted_output = True
        return [
            MappedCodexEvent(
                OutputEvent(
                    output_id=f"codex-output:{item_id}:{channel.value}",
                    kind=OutputKind.TEXT,
                    content=delta,
                    operation=OutputOperation.DELTA,
                    channel=channel,
                ),
                item_id=item_id,
            )
        ]

    def _item_started(self, payload: Any) -> list[MappedCodexEvent]:
        item = _thread_item(payload)
        item_type = _item_type(item)
        item_id = str(getattr(item, "id", "") or "unknown-item")
        if item_type not in TOOL_ITEM_TYPES:
            return []
        tool_name = _tool_name(item)
        self._tool_names[item_id] = tool_name
        data: dict[str, Any] = {"name": tool_name, "arguments": _tool_args(item), "item_type": item_type}
        if item_type == "mcpToolCall":
            data["server"] = str(getattr(item, "server", "") or "")
        return [
            MappedCodexEvent(
                ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data=freeze_json_object(data)),
                item_id=item_id,
            )
        ]

    def _item_completed(self, payload: Any) -> list[MappedCodexEvent]:
        item = _thread_item(payload)
        item_type = _item_type(item)
        item_id = str(getattr(item, "id", "") or "unknown-item")
        if item_type in TOOL_ITEM_TYPES:
            status = _enum_value(getattr(item, "status", None))
            error = getattr(item, "error", None)
            data: dict[str, Any] = {
                "tool_name": self._tool_names.get(item_id) or _tool_name(item),
                "result": _tool_result(item),
                "item_type": item_type,
                "status": status,
            }
            if error is not None:
                data["error"] = to_json_safe(error)
            elif status in {"failed", "declined"}:
                data["error"] = {"status": status}
            self.messages.append(
                TurnMessage(
                    message_id=f"codex-tool:{item_id}",
                    role=MessageRole.TOOL,
                    content=(
                        ContentBlock(
                            block_id=f"codex-block:{item_id}",
                            kind="tool_result",
                            content=freeze_json_value(data["result"]),
                            data={"call_id": item_id},
                        ),
                    ),
                )
            )
            return [
                MappedCodexEvent(
                    ItemLifecycleEvent(kind=ItemEventKind.COMPLETED, item_type="tool", data=freeze_json_object(data)),
                    item_id=item_id,
                )
            ]
        if item_type == "agentMessage":
            text = str(getattr(item, "text", "") or "")
            self.last_text_output = text
            self.messages.append(
                TurnMessage(
                    message_id=f"codex-message:{item_id}",
                    role=MessageRole.ASSISTANT,
                    content=(ContentBlock(block_id=f"codex-block:{item_id}", kind="text", content=text),),
                )
            )
            if not text:
                return []
            self.emitted_output = True
            return [
                MappedCodexEvent(
                    OutputEvent(
                        output_id=f"codex-output:{item_id}:{OutputChannel.ANSWER.value}",
                        kind=OutputKind.TEXT,
                        content=text,
                        operation=OutputOperation.FINAL,
                        channel=OutputChannel.ANSWER,
                    ),
                    item_id=item_id,
                )
            ]
        if item_type == "reasoning":
            summary = getattr(item, "summary", None)
            parts = [str(part) for part in summary] if isinstance(summary, list) else []
            text = "\n".join(part for part in parts if part)
            if not text:
                return []
            self.messages.append(
                TurnMessage(
                    message_id=f"codex-reasoning:{item_id}",
                    role=MessageRole.ASSISTANT,
                    content=(ContentBlock(block_id=f"codex-block:{item_id}", kind="reasoning", content=text),),
                )
            )
            return [
                MappedCodexEvent(
                    OutputEvent(
                        output_id=f"codex-output:{item_id}:{OutputChannel.REASONING.value}",
                        kind=OutputKind.TEXT,
                        content=text,
                        operation=OutputOperation.FINAL,
                        channel=OutputChannel.REASONING,
                    ),
                    item_id=item_id,
                )
            ]
        return [
            MappedCodexEvent(
                ItemLifecycleEvent(
                    kind=ItemEventKind.COMPLETED,
                    item_type=item_type or "unknown",
                    data=freeze_json_object(to_json_object(item)),
                ),
                item_id=item_id,
            )
        ]

    def _usage(self, payload: Any) -> list[MappedCodexEvent]:
        token_usage = getattr(payload, "token_usage", None)
        last = getattr(token_usage, "last", None)
        if last is None:
            return []
        usage = TurnUsage(
            input_tokens=_non_negative(getattr(last, "input_tokens", None)),
            output_tokens=_non_negative(getattr(last, "output_tokens", None)),
            cached_input_tokens=_non_negative(getattr(last, "cached_input_tokens", None)),
            reasoning_output_tokens=_non_negative(getattr(last, "reasoning_output_tokens", None)),
            total_tokens=_non_negative(getattr(last, "total_tokens", None)),
            provider_data={
                "thread_total_tokens": _non_negative(getattr(getattr(token_usage, "total", None), "total_tokens", None))
            },
        )
        self._usage_events.append(usage)
        return [MappedCodexEvent(UsageUpdatedEvent(usage=usage, mode=UsageUpdateMode.DELTA))]

    def _error(self, payload: Any) -> tuple[list[MappedCodexEvent], RetryingNotice | None]:
        error, will_retry = classify_error_notification(payload)
        if will_retry:
            return [
                MappedCodexEvent(
                    DiagnosticEvent(
                        level=DiagnosticLevel.WARNING,
                        message=f"Codex is retrying after {error.category}",
                        data={"kind": "retrying", "category": error.category, "code": error.code},
                    )
                )
            ], RetryingNotice(error=error)
        self.pending_error = error
        return [
            MappedCodexEvent(
                DiagnosticEvent(
                    level=DiagnosticLevel.ERROR,
                    message=f"Codex reported {error.category}",
                    data={"category": error.category, "code": error.code},
                )
            )
        ], None

    # ------------------------------------------------------------------
    # Terminal result
    # ------------------------------------------------------------------

    @property
    def total_usage(self) -> TurnUsage | None:
        """Return cumulative usage across the turn's model calls."""

        if not self._usage_events:
            return None
        totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "reasoning_output_tokens": 0}
        total_tokens = 0
        for usage in self._usage_events:
            totals["input_tokens"] += usage.input_tokens or 0
            totals["output_tokens"] += usage.output_tokens or 0
            totals["cached_input_tokens"] += usage.cached_input_tokens or 0
            totals["reasoning_output_tokens"] += usage.reasoning_output_tokens or 0
            total_tokens += usage.total_tokens or 0
        return TurnUsage(total_tokens=total_tokens, **totals)

    def build_terminal_result(self, *, turn: PendingTurn, timing: TurnTiming) -> tuple[TurnEventKind, TurnResult]:
        """Build the external terminal result from the observed ``turn/completed``."""

        completed = self.completed_turn
        status = _enum_value(getattr(completed, "status", None)) if completed is not None else None
        common: dict[str, Any] = {
            "messages": tuple(self.messages),
            "final_output": self.last_text_output,
            "usage": self.total_usage,
            "started_at": timing.started_at,
            "completed_at": timing.completed_at(),
            "duration_ms": timing.duration_ms(),
            "provider_data": freeze_json_object(
                {"native_turn_id": getattr(completed, "id", None), "native_status": status}
            ),
        }
        if turn.abort_requested:
            return TurnEventKind.ABORTED, interrupted_result(
                turn,
                provider_name=PROVIDER_NAME,
                timing=timing,
                messages=tuple(self.messages),
                final_output=self.last_text_output,
                usage=self.total_usage,
            )
        if status == "completed":
            return TurnEventKind.FINISHED, TurnResult(status=TurnStatus.COMPLETED, stop_reason="completed", **common)
        if status == "interrupted":
            return TurnEventKind.ABORTED, TurnResult(
                status=TurnStatus.INTERRUPTED,
                termination=TurnTermination(kind=TurnTerminationKind.PROVIDER, message="Codex interrupted the turn"),
                **common,
            )
        if status == "failed":
            turn_error = getattr(completed, "error", None)
            terminal = classify_turn_error(turn_error) if turn_error is not None else None
            return TurnEventKind.FAILED, TurnResult(
                status=TurnStatus.FAILED,
                error=merge_pending_error(self.pending_error, terminal),
                **common,
            )
        return TurnEventKind.FAILED, TurnResult(
            status=TurnStatus.FAILED,
            error=merge_pending_error(
                self.pending_error,
                TurnError(
                    message="Codex ended the turn stream without a terminal status",
                    code="CODEX_MISSING_TURN_COMPLETED",
                    category="sdk_error",
                ),
            ),
            **common,
        )

    def build_failed_result(self, error: TurnError, *, timing: TurnTiming) -> TurnResult:
        """Build a FAILED result for an SDK exception raised mid-turn."""

        return TurnResult(
            status=TurnStatus.FAILED,
            messages=tuple(self.messages),
            final_output=self.last_text_output,
            error=merge_pending_error(self.pending_error, error),
            usage=self.total_usage,
            started_at=timing.started_at,
            completed_at=timing.completed_at(),
            duration_ms=timing.duration_ms(),
        )

    @staticmethod
    def _provider_event(event_type: str, payload: dict[str, Any]) -> MappedCodexEvent:
        return MappedCodexEvent(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type=event_type,
                schema_version=_SCHEMA_VERSION,
                payload=freeze_json_object(payload),
            )
        )


def _thread_item(payload: Any) -> Any:
    """Unwrap the SDK's ``ThreadItem`` root model."""
    item = getattr(payload, "item", None)
    return getattr(item, "root", item)


def _item_type(item: Any) -> str:
    return str(_enum_value(getattr(item, "type", "")) or "")


def _tool_name(item: Any) -> str:
    item_type = _item_type(item)
    if item_type == "mcpToolCall":
        return f"{getattr(item, 'server', '')}.{getattr(item, 'tool', '')}".strip(".")
    if item_type == "dynamicToolCall":
        return str(getattr(item, "tool", ""))
    if item_type == "commandExecution":
        return "shell"
    if item_type == "fileChange":
        return "apply_patch"
    return item_type


def _tool_args(item: Any) -> Any:
    item_type = _item_type(item)
    if item_type in {"dynamicToolCall", "mcpToolCall"}:
        return to_json_safe(getattr(item, "arguments", None))
    if item_type == "commandExecution":
        return {"command": to_json_safe(getattr(item, "command", "")), "cwd": to_json_safe(getattr(item, "cwd", ""))}
    if item_type == "fileChange":
        return {"changes": to_json_safe(getattr(item, "changes", []))}
    return {}


def _tool_result(item: Any) -> Any:
    item_type = _item_type(item)
    if item_type == "mcpToolCall":
        result = getattr(item, "result", None)
        if result is None:
            result = getattr(item, "error", None)
        return _normalize_tool_result(result)
    if item_type == "dynamicToolCall":
        return _normalize_tool_result(getattr(item, "content_items", None))
    if item_type == "commandExecution":
        output = getattr(item, "aggregated_output", None)
        if isinstance(output, str) and output:
            return output
        return f"exit_code={getattr(item, 'exit_code', None)}"
    if item_type == "fileChange":
        return {"status": _enum_value(getattr(item, "status", None))}
    return None


def _normalize_tool_result(value: Any) -> Any:
    """Collapse SDK text content lists into one string, keep the rest JSON-safe."""
    jsonable = to_json_safe(value)
    if isinstance(jsonable, list) and jsonable:
        text_parts: list[str] = []
        for item in jsonable:
            if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                return jsonable
            text_parts.append(item["text"])
        return "\n".join(text_parts)
    return jsonable


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _non_negative(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


# Public aliases for observability bridges that read raw thread items.
thread_item = _thread_item
item_type = _item_type
tool_name = _tool_name
tool_args = _tool_args
tool_result = _tool_result
enum_value = _enum_value

__all__ = [
    "CodexTurnAccumulator",
    "MappedCodexEvent",
    "PROVIDER_NAME",
    "RetryingNotice",
    "TOOL_ITEM_TYPES",
    "enum_value",
    "item_type",
    "thread_item",
    "tool_args",
    "tool_name",
    "tool_result",
]
