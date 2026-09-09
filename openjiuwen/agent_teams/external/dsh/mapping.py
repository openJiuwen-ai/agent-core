# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate DeepSeek Harness notifications into protocol v4 observations."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from openjiuwen.agent_teams.external.protocol import (
    ContentBlock,
    ItemEventKind,
    ItemLifecycleEvent,
    JsonObject,
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
)

_PROVIDER = "deepseek-harness"
_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class MappedDshEvent:
    """One normalized payload plus envelope-only provider correlation."""

    payload: Any
    item_id: str | None = None
    session_id: str | None = None


class DshTurnAccumulator:
    """State needed to normalize one externally driven DSH activity interval."""

    def __init__(self, *, turn_id: str, root_session_id: str) -> None:
        self.turn_id = turn_id
        self.root_session_id = root_session_id
        self.messages: list[TurnMessage] = []
        self._message_ids: set[str] = set()
        self._finalized_output_ids: set[str] = set()
        self._usage_by_step: dict[tuple[int, int], TurnUsage] = {}
        self._tool_names: dict[str, str] = {}
        self.last_turn_reason: JsonObject | None = None
        self.native_turn_end_count = 0
        self.last_text_output = ""

    def consume(self, notification: object) -> list[MappedDshEvent]:
        """Map one SDK notification without retaining its mutable containers."""

        method = _notification_method(notification)
        payload = _notification_payload(notification)
        if method in {"subagent.started", "subagent.finished"}:
            return [self._map_subagent(method, payload)]
        if method != "session.event":
            return [self._provider_event(method or "unknown-notification", payload)]

        session_id = _string(payload.get("sessionId")) or self.root_session_id
        raw_event = _mapping(payload.get("event"))
        if session_id != self.root_session_id:
            if raw_event.get("type") == "turn/end":
                raw_event = _redact_turn_end_event(raw_event)
            return [self._provider_event("child.session.event", {"event": raw_event}, session_id=session_id)]
        return self._map_root_session_event(raw_event)

    def build_terminal_result(
        self,
        run_result: object,
        *,
        started_at: float,
        started_monotonic: float,
    ) -> tuple[TurnEventKind, TurnResult]:
        """Build the single external terminal result at whole-agent idle."""

        completed_at = time.time()
        duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        finish_reason = getattr(run_result, "finish_reason", None)
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)
        final_response = getattr(run_result, "final_response", None)
        if not isinstance(final_response, str):
            final_response = self.last_text_output
        provider_data: dict[str, object] = {
            "native_turn_end_count": self.native_turn_end_count,
        }
        if finish_reason is not None:
            provider_data["native_finish_reason"] = finish_reason

        common = {
            "messages": tuple(self.messages),
            "final_output": final_response,
            "stop_reason": finish_reason,
            "usage": self.total_usage,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "provider_data": provider_data,
        }
        if finish_reason is None:
            return TurnEventKind.FAILED, TurnResult(
                status=TurnStatus.FAILED,
                error=TurnError(
                    message="DeepSeek Harness became idle without ending a native turn",
                    code="DSH_MISSING_TURN_END",
                    category="protocol",
                ),
                **common,
            )
        if finish_reason in {"completed", "max-tokens"}:
            return TurnEventKind.FINISHED, TurnResult(status=TurnStatus.COMPLETED, **common)
        if finish_reason == "blocked":
            return TurnEventKind.ABORTED, TurnResult(
                status=TurnStatus.INTERRUPTED,
                termination=TurnTermination(
                    kind=TurnTerminationKind.POLICY,
                    message="DeepSeek Harness blocked the turn",
                ),
                **common,
            )
        if finish_reason in {"aborted", "interrupted"}:
            termination_kind = self._termination_kind(finish_reason)
            return TurnEventKind.ABORTED, TurnResult(
                status=TurnStatus.INTERRUPTED,
                termination=TurnTermination(
                    kind=termination_kind,
                    message="DeepSeek Harness interrupted the turn",
                    provider_data=_safe_reason_data(self.last_turn_reason),
                ),
                **common,
            )
        if finish_reason == "error":
            return TurnEventKind.FAILED, TurnResult(
                status=TurnStatus.FAILED,
                error=TurnError(
                    message="DeepSeek Harness reported a failed turn",
                    code=_reason_error_code(self.last_turn_reason),
                    category="provider",
                    provider_data=_safe_reason_data(self.last_turn_reason),
                ),
                **common,
            )
        return TurnEventKind.FAILED, TurnResult(
            status=TurnStatus.FAILED,
            error=TurnError(
                message="DeepSeek Harness returned an unknown turn ending",
                code="DSH_UNKNOWN_FINISH_REASON",
                category="protocol",
                provider_data={"finish_reason": finish_reason},
            ),
            **common,
        )

    def build_failed_result(
        self,
        exc: BaseException,
        *,
        started_at: float,
        started_monotonic: float,
    ) -> TurnResult:
        """Normalize an SDK failure without copying possibly sensitive stderr."""

        completed_at = time.time()
        return TurnResult(
            status=TurnStatus.FAILED,
            messages=tuple(self.messages),
            final_output=self.last_text_output,
            error=TurnError(
                message="DeepSeek Harness SDK turn failed",
                code=type(exc).__name__,
                category="provider_sdk",
            ),
            usage=self.total_usage,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
            provider_data={"exception_type": type(exc).__name__},
        )

    def build_stopped_result(
        self,
        *,
        started_at: float,
        started_monotonic: float,
    ) -> TurnResult:
        """Build an interrupted result when cycle shutdown stops the activity."""

        completed_at = time.time()
        return TurnResult(
            status=TurnStatus.INTERRUPTED,
            messages=tuple(self.messages),
            final_output=self.last_text_output,
            termination=TurnTermination(
                kind=TurnTerminationKind.HARNESS_STOP,
                message="DeepSeek Harness stopped before the turn completed",
            ),
            usage=self.total_usage,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
        )

    @property
    def total_usage(self) -> TurnUsage | None:
        """Return cumulative usage across DSH model-call iterations."""

        if not self._usage_by_step:
            return None
        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        reasoning_output_tokens = 0
        cache_write_tokens = 0
        for usage in self._usage_by_step.values():
            input_tokens += usage.input_tokens or 0
            output_tokens += usage.output_tokens or 0
            cached_input_tokens += usage.cached_input_tokens or 0
            reasoning_output_tokens += usage.reasoning_output_tokens or 0
            cache_write_tokens += _int(usage.provider_data.get("cache_write_tokens")) or 0
        return TurnUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            total_tokens=input_tokens + output_tokens + cached_input_tokens + cache_write_tokens,
            provider_data={"cache_write_tokens": cache_write_tokens},
        )

    def _map_root_session_event(self, event: Mapping[str, object]) -> list[MappedDshEvent]:
        event_type = _string(event.get("type")) or "unknown"
        data = _mapping(event.get("data"))
        if event_type == "assistant/chunk":
            return self._map_assistant_chunk(data, event)
        if event_type == "assistant/message":
            return self._map_assistant_message(data, event)
        if event_type == "tool/call":
            return [self._map_tool_call(data)]
        if event_type == "tool/result":
            return [self._map_tool_result(data)]
        if event_type in {"step/start", "step/end"}:
            return [self._map_iteration(event_type, data)]
        if event_type == "turn/end":
            reason = _mapping(data.get("reason"))
            self.last_turn_reason = reason
            self.native_turn_end_count += 1
            return [self._provider_event(event_type, {"event": _redact_turn_end_event(event)})]
        return [self._provider_event(event_type, {"event": event})]

    def _map_assistant_chunk(
        self,
        data: Mapping[str, object],
        raw_event: Mapping[str, object],
    ) -> list[MappedDshEvent]:
        chunk = _mapping(data.get("chunk"))
        chunk_type = _string(chunk.get("type")) or "unknown"
        native_turn = _int(data.get("turn")) or 0
        native_step = _int(data.get("step")) or 0
        content_index = _int(chunk.get("index")) or 0

        if chunk_type in {"text-delta", "reasoning-delta"}:
            text = _string(chunk.get("text"))
            if text is None:
                return []
            channel = OutputChannel.ANSWER if chunk_type == "text-delta" else OutputChannel.REASONING
            output_id = _output_id(native_turn, native_step, content_index, channel)
            return [
                MappedDshEvent(
                    OutputEvent(
                        output_id=output_id,
                        kind=OutputKind.TEXT,
                        content=text,
                        operation=OutputOperation.DELTA,
                        channel=channel,
                        content_index=content_index,
                    )
                )
            ]

        if chunk_type == "block-end":
            block = _mapping(chunk.get("block"))
            block_type = _string(block.get("type"))
            if block_type in {"text", "reasoning"}:
                channel = OutputChannel.ANSWER if block_type == "text" else OutputChannel.REASONING
                output_id = _output_id(native_turn, native_step, content_index, channel)
                self._finalized_output_ids.add(output_id)
                text = _string(block.get("text")) or ""
                if channel is OutputChannel.ANSWER:
                    self.last_text_output = text
                return [
                    MappedDshEvent(
                        OutputEvent(
                            output_id=output_id,
                            kind=OutputKind.TEXT,
                            content=text,
                            operation=OutputOperation.FINAL,
                            channel=channel,
                            content_index=content_index,
                        )
                    )
                ]

        if chunk_type == "usage":
            return self._update_usage(native_turn, native_step, _mapping(chunk.get("usage")))
        return [self._provider_event("assistant/chunk", {"event": raw_event})]

    def _map_assistant_message(
        self,
        data: Mapping[str, object],
        raw_event: Mapping[str, object],
    ) -> list[MappedDshEvent]:
        native_turn = _int(data.get("turn")) or 0
        native_step = _int(data.get("step")) or 0
        message = _mapping(data.get("message")) or data
        raw_content = message.get("content")
        content = raw_content if isinstance(raw_content, (list, tuple)) else ()
        message_id = _string(message.get("id")) or f"dsh-message:{native_turn}:{native_step}:{raw_event.get('seq', 0)}"
        blocks: list[ContentBlock] = []
        mapped: list[MappedDshEvent] = []
        answer_parts: list[str] = []

        for index, raw_block in enumerate(content):
            block = _mapping(raw_block)
            block_type = _string(block.get("type")) or "unknown"
            block_id = _string(block.get("id")) or f"{message_id}:block:{index}"
            blocks.append(_content_block(block_id, block_type, block))
            if block_type not in {"text", "reasoning"}:
                continue
            channel = OutputChannel.ANSWER if block_type == "text" else OutputChannel.REASONING
            output_id = _output_id(native_turn, native_step, index, channel)
            text = _string(block.get("text")) or ""
            if channel is OutputChannel.ANSWER:
                answer_parts.append(text)
            if output_id not in self._finalized_output_ids:
                self._finalized_output_ids.add(output_id)
                mapped.append(
                    MappedDshEvent(
                        OutputEvent(
                            output_id=output_id,
                            kind=OutputKind.TEXT,
                            content=text,
                            operation=OutputOperation.FINAL,
                            channel=channel,
                            content_index=index,
                        )
                    )
                )

        # Match the SDK's final_response projection: the latest assistant
        # message owns the value and all of its text blocks are concatenated.
        self.last_text_output = "".join(answer_parts)

        if message_id not in self._message_ids:
            self._message_ids.add(message_id)
            source = _mapping(message.get("source"))
            message_data: dict[str, object] = {"native_turn": native_turn, "native_step": native_step}
            if source:
                message_data["source"] = source
            self.messages.append(
                TurnMessage(
                    message_id=message_id,
                    role=MessageRole.ASSISTANT,
                    content=tuple(blocks),
                    data=message_data,
                )
            )
        usage = _mapping(data.get("usage"))
        if usage:
            mapped.extend(self._update_usage(native_turn, native_step, usage))
        return mapped

    def _map_tool_call(self, data: Mapping[str, object]) -> MappedDshEvent:
        call_id = _string(data.get("callId")) or "unknown-tool-call"
        tool_name = _string(data.get("name")) or "unknown"
        self._tool_names[call_id] = tool_name
        return MappedDshEvent(
            ItemLifecycleEvent(
                kind=ItemEventKind.STARTED,
                item_type="tool",
                data={
                    "name": tool_name,
                    "arguments": data.get("arguments"),
                    "provider_turn": _int(data.get("turn")),
                    "provider_step": _int(data.get("step")),
                },
            ),
            item_id=call_id,
        )

    def _map_tool_result(self, data: Mapping[str, object]) -> MappedDshEvent:
        message = _mapping(data.get("message"))
        source = _mapping(message.get("source"))
        call_id = _string(source.get("callId")) or _string(data.get("callId")) or "unknown-tool-call"
        content = message.get("content") if message else data.get("content")
        item_data: dict[str, object] = {
            "tool_name": self._tool_names.get(call_id, "unknown"),
            "result": content,
            "provider_turn": _int(data.get("turn")),
            "provider_step": _int(data.get("step")),
        }
        if data.get("error") is not None:
            item_data["error"] = data.get("error")
        if data.get("meta") is not None:
            item_data["meta"] = data.get("meta")
        return MappedDshEvent(
            ItemLifecycleEvent(kind=ItemEventKind.COMPLETED, item_type="tool", data=item_data),
            item_id=call_id,
        )

    @staticmethod
    def _map_iteration(event_type: str, data: Mapping[str, object]) -> MappedDshEvent:
        native_turn = _int(data.get("turn")) or 0
        native_step = _int(data.get("step")) or 0
        kind = ItemEventKind.STARTED if event_type == "step/start" else ItemEventKind.COMPLETED
        return MappedDshEvent(
            ItemLifecycleEvent(
                kind=kind,
                item_type="iteration",
                data={"provider_turn": native_turn, "provider_step": native_step},
            ),
            item_id=f"dsh-iteration:{native_turn}:{native_step}",
        )

    @staticmethod
    def _map_subagent(method: str, payload: Mapping[str, object]) -> MappedDshEvent:
        child_id = _string(payload.get("childSessionId")) or _string(payload.get("sessionId")) or "unknown-subagent"
        kind = ItemEventKind.STARTED if method == "subagent.started" else ItemEventKind.COMPLETED
        return MappedDshEvent(
            ItemLifecycleEvent(kind=kind, item_type="subagent", data=payload),
            item_id=child_id,
            session_id=child_id,
        )

    def _update_usage(
        self,
        native_turn: int,
        native_step: int,
        raw_usage: Mapping[str, object],
    ) -> list[MappedDshEvent]:
        usage = _turn_usage(raw_usage)
        key = (native_turn, native_step)
        if self._usage_by_step.get(key) == usage:
            return []
        self._usage_by_step[key] = usage
        total = self.total_usage
        return [MappedDshEvent(UsageUpdatedEvent(total))] if total is not None else []

    @staticmethod
    def _provider_event(
        event_type: str,
        payload: Mapping[str, object],
        *,
        session_id: str | None = None,
    ) -> MappedDshEvent:
        return MappedDshEvent(
            ProviderEvent(
                provider=_PROVIDER,
                event_type=event_type,
                schema_version=_SCHEMA_VERSION,
                payload=payload,
            ),
            session_id=session_id,
        )

    def _termination_kind(self, finish_reason: str) -> TurnTerminationKind:
        if finish_reason == "interrupted":
            return TurnTerminationKind.PROVIDER
        reason = _mapping(self.last_turn_reason or {}).get("reason")
        cause = _mapping(reason)
        cause_kind = _string(cause.get("kind"))
        if cause_kind == "user":
            return TurnTerminationKind.USER_ABORT
        if cause_kind == "disposed":
            return TurnTerminationKind.HARNESS_STOP
        if cause_kind == "hook":
            return TurnTerminationKind.POLICY
        return TurnTerminationKind.PROVIDER


def build_queued_stop_result() -> TurnResult:
    """Return the terminal result for an accepted turn stopped before execution."""

    now = time.time()
    return TurnResult(
        status=TurnStatus.INTERRUPTED,
        termination=TurnTermination(
            kind=TurnTerminationKind.HARNESS_STOP,
            message="DeepSeek Harness stopped before the queued turn started",
        ),
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )


def _notification_method(notification: object) -> str:
    if isinstance(notification, Mapping):
        return _string(notification.get("method")) or ""
    return _string(getattr(notification, "method", None)) or ""


def _notification_payload(notification: object) -> Mapping[str, object]:
    if isinstance(notification, Mapping):
        return _mapping(notification.get("payload") or notification.get("params"))
    return _mapping(getattr(notification, "payload", None))


def _provider_payload(value: Mapping[str, object] | None) -> JsonObject:
    return dict(value or {})


def _safe_reason_data(reason: Mapping[str, object] | None) -> JsonObject:
    """Keep useful non-message failure fields without copying provider diagnostics."""

    if not reason:
        return {}
    result: dict[str, object] = {}
    kind = _string(reason.get("kind"))
    if kind:
        result["kind"] = kind
    cause = _mapping(reason.get("reason"))
    if cause:
        result["cause"] = {key: value for key, value in cause.items() if key != "message"}
    error = _mapping(reason.get("error"))
    if error:
        result["error"] = {
            key: value for key, value in error.items() if key in {"code", "status", "requestId", "retryable"}
        }
    return _provider_payload(result)


def _redact_turn_end_event(event: Mapping[str, object]) -> JsonObject:
    """Preserve the DSH event shape without exposing provider error text."""

    safe_event = dict(event)
    data = dict(_mapping(event.get("data")))
    reason = _mapping(data.get("reason"))
    safe_reason: dict[str, object] = {}
    kind = _string(reason.get("kind"))
    if kind:
        safe_reason["kind"] = kind
    cause = _mapping(reason.get("reason"))
    if cause:
        safe_reason["reason"] = {key: value for key, value in cause.items() if key != "message"}
    error = _mapping(reason.get("error"))
    if error:
        safe_reason["error"] = {
            key: value for key, value in error.items() if key in {"code", "status", "requestId", "retryable"}
        }
    data["reason"] = safe_reason
    safe_event["data"] = data
    return _provider_payload(safe_event)


def _reason_error_code(reason: Mapping[str, object] | None) -> str | None:
    error = _mapping((reason or {}).get("error"))
    code = error.get("code")
    return str(code) if isinstance(code, (str, int)) else None


def _turn_usage(raw: Mapping[str, object]) -> TurnUsage:
    input_tokens = _non_negative_int(raw.get("inputTokens"))
    output_tokens = _non_negative_int(raw.get("outputTokens"))
    cache_read_tokens = _non_negative_int(raw.get("cacheReadTokens"))
    cache_write_tokens = _non_negative_int(raw.get("cacheWriteTokens"))
    reasoning_tokens = _non_negative_int(raw.get("reasoningTokens"))
    return TurnUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cache_read_tokens,
        reasoning_output_tokens=reasoning_tokens,
        total_tokens=sum(value or 0 for value in (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)),
        provider_data={"cache_write_tokens": cache_write_tokens or 0},
    )


def _content_block(block_id: str, block_type: str, block: Mapping[str, object]) -> ContentBlock:
    if block_type in {"text", "reasoning"}:
        return ContentBlock(block_id=block_id, kind=block_type, content=_string(block.get("text")) or "")
    if block_type == "tool-call":
        return ContentBlock(
            block_id=block_id,
            kind="tool_call",
            content={
                "name": _string(block.get("name")) or "unknown",
                "arguments": block.get("arguments"),
            },
            data={"call_id": _string(block.get("id")) or block_id},
        )
    return ContentBlock(block_id=block_id, kind=block_type, content=dict(block))


def _output_id(native_turn: int, native_step: int, content_index: int, channel: OutputChannel) -> str:
    return f"dsh-output:{native_turn}:{native_step}:{content_index}:{channel.value}"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _non_negative_int(value: object) -> int | None:
    number = _int(value)
    return number if number is not None and number >= 0 else None


__all__ = ["DshTurnAccumulator", "MappedDshEvent", "build_queued_stop_result"]
