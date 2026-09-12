# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Translate Claude Agent SDK messages into protocol v1 observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from openjiuwen.harness_protocol import (
    ContentBlock,
    DiagnosticEvent,
    DiagnosticLevel,
    ItemEventKind,
    ItemLifecycleEvent,
    JsonValue,
    MessageRole,
    MonetaryAmount,
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
    TurnUsage,
    UsageUpdatedEvent,
    UsageUpdateMode,
    freeze_json_object,
    freeze_json_value,
)
from openjiuwen.harness_providers.base import PendingTurn, TurnTiming, interrupted_result
from openjiuwen.harness_providers.claudecode.failure_classifier import (
    classify_assistant_error,
    classify_result_message,
    merge_pending_error,
)
from openjiuwen.harness_providers.jsonsafe import to_json_object, to_json_safe

PROVIDER_NAME = "claude-code"
_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class MappedClaudeEvent:
    """One normalized payload plus envelope-only provider correlation."""

    payload: Any
    item_id: str | None = None


class ClaudeTurnAccumulator:
    """State needed to normalize the messages of one Claude turn."""

    def __init__(self, *, turn_id: str, sdk: Any) -> None:
        self.turn_id = turn_id
        self._sdk = sdk
        self.messages: list[TurnMessage] = []
        self.last_text_output = ""
        self.pending_error: TurnError | None = None
        self.emitted_output = False
        self._message_index = 0
        self._stream_message_open = False
        self._tool_names: dict[str, str] = {}
        self._tool_parents: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Message mapping
    # ------------------------------------------------------------------

    def consume(self, message: Any) -> list[MappedClaudeEvent]:
        """Map one SDK message; returns the events to emit in order."""

        sdk = self._sdk
        if isinstance(message, sdk.StreamEvent):
            return self._map_stream_event(message)
        if isinstance(message, sdk.AssistantMessage):
            return self._map_assistant(message)
        if isinstance(message, sdk.UserMessage):
            return self._map_user(message)
        if isinstance(message, sdk.ResultMessage):
            return self._map_result_observations(message)
        if isinstance(message, sdk.SystemMessage):
            subtype = str(getattr(message, "subtype", "") or "system")
            return [self._provider_event(f"system/{subtype}", to_json_object(getattr(message, "data", {})))]
        return [self._provider_event(type(message).__name__, to_json_object(message))]

    def _map_stream_event(self, message: Any) -> list[MappedClaudeEvent]:
        if getattr(message, "parent_tool_use_id", None):
            # Sub-agent partial output is not this agent's answer stream.
            return []
        event = getattr(message, "event", None)
        if not isinstance(event, Mapping):
            return []
        event_type = event.get("type")
        if event_type == "message_start":
            self._message_index += 1
            self._stream_message_open = True
            return []
        if event_type == "message_stop":
            self._stream_message_open = False
            return []
        if event_type != "content_block_delta":
            return []
        delta = event.get("delta")
        if not isinstance(delta, Mapping):
            return []
        index = event.get("index")
        content_index = index if isinstance(index, int) and index >= 0 else 0
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text")
            channel = OutputChannel.ANSWER
        elif delta_type == "thinking_delta":
            text = delta.get("thinking")
            channel = OutputChannel.REASONING
        else:
            return []
        if not isinstance(text, str) or not text:
            return []
        self.emitted_output = True
        return [
            MappedClaudeEvent(
                OutputEvent(
                    output_id=self._output_id(self._message_index, content_index, channel),
                    kind=OutputKind.TEXT,
                    content=text,
                    operation=OutputOperation.DELTA,
                    channel=channel,
                    content_index=content_index,
                )
            )
        ]

    def _map_assistant(self, message: Any) -> list[MappedClaudeEvent]:
        sdk = self._sdk
        if self._stream_message_open:
            # The final message closes the message its stream events opened.
            self._stream_message_open = False
        else:
            self._message_index += 1
        message_index = self._message_index
        parent_tool_use_id = getattr(message, "parent_tool_use_id", None)
        content = getattr(message, "content", None)
        blocks: list[ContentBlock] = []
        mapped: list[MappedClaudeEvent] = []
        answer_parts: list[str] = []
        raw_blocks = content if isinstance(content, list) else []
        for index, block in enumerate(raw_blocks):
            if isinstance(block, sdk.TextBlock):
                text = block.text or ""
                blocks.append(ContentBlock(block_id=f"claude-block:{message_index}:{index}", kind="text", content=text))
                answer_parts.append(text)
                if text and parent_tool_use_id is None:
                    mapped.append(self._final_output(message_index, index, OutputChannel.ANSWER, text))
            elif isinstance(block, sdk.ThinkingBlock):
                thinking = block.thinking or ""
                blocks.append(
                    ContentBlock(block_id=f"claude-block:{message_index}:{index}", kind="reasoning", content=thinking)
                )
                if thinking and parent_tool_use_id is None:
                    mapped.append(self._final_output(message_index, index, OutputChannel.REASONING, thinking))
            elif isinstance(block, sdk.ToolUseBlock):
                call_id = block.id or f"claude-tool:{message_index}:{index}"
                self._tool_names[call_id] = block.name
                self._tool_parents[call_id] = parent_tool_use_id
                arguments = to_json_safe(block.input)
                blocks.append(
                    ContentBlock(
                        block_id=f"claude-block:{message_index}:{index}",
                        kind="tool_call",
                        content={"name": block.name, "arguments": arguments},
                        data={"call_id": call_id},
                    )
                )
                item_data: dict[str, Any] = {"name": block.name, "arguments": arguments}
                if parent_tool_use_id:
                    item_data["parent_tool_use_id"] = parent_tool_use_id
                mapped.append(
                    MappedClaudeEvent(
                        ItemLifecycleEvent(
                            kind=ItemEventKind.STARTED,
                            item_type="tool",
                            data=freeze_json_object(item_data),
                        ),
                        item_id=call_id,
                    )
                )
            else:
                blocks.append(
                    ContentBlock(
                        block_id=f"claude-block:{message_index}:{index}",
                        kind=type(block).__name__,
                        content=freeze_json_value(to_json_safe(block)),
                    )
                )
        if parent_tool_use_id is None and answer_parts:
            self.last_text_output = "".join(answer_parts)
        message_data: dict[str, Any] = {"model": getattr(message, "model", None)}
        if parent_tool_use_id:
            message_data["parent_tool_use_id"] = parent_tool_use_id
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason:
            message_data["stop_reason"] = stop_reason
        usage = getattr(message, "usage", None)
        if isinstance(usage, Mapping):
            message_data["usage"] = to_json_safe(usage)
        message_id = getattr(message, "message_id", None) or f"claude-message:{message_index}"
        self.messages.append(
            TurnMessage(
                message_id=message_id,
                role=MessageRole.ASSISTANT,
                content=tuple(blocks),
                data=freeze_json_object(to_json_object(message_data)),
            )
        )
        error = getattr(message, "error", None)
        if error:
            self.pending_error = classify_assistant_error(error, message)
            mapped.append(
                MappedClaudeEvent(
                    DiagnosticEvent(
                        level=DiagnosticLevel.ERROR,
                        message=f"Claude assistant message reported {self.pending_error.category}",
                        data={"category": self.pending_error.category, "error": str(error)},
                    )
                )
            )
        return mapped

    def _map_user(self, message: Any) -> list[MappedClaudeEvent]:
        sdk = self._sdk
        content = getattr(message, "content", None)
        mapped: list[MappedClaudeEvent] = []
        blocks: list[ContentBlock] = []
        raw_blocks = content if isinstance(content, list) else []
        for index, block in enumerate(raw_blocks):
            if not isinstance(block, sdk.ToolResultBlock):
                continue
            call_id = block.tool_use_id
            result = _normalize_tool_result(block.content)
            blocks.append(
                ContentBlock(
                    block_id=f"claude-result:{call_id}:{index}",
                    kind="tool_result",
                    content=freeze_json_value(to_json_safe(result)),
                    data={"call_id": call_id},
                )
            )
            mapped.append(self._tool_completed(call_id, result, is_error=bool(getattr(block, "is_error", False))))
        tool_use_result = getattr(message, "tool_use_result", None)
        parent_id = getattr(message, "parent_tool_use_id", None)
        if not mapped and tool_use_result is not None and parent_id:
            result = _normalize_tool_result(tool_use_result)
            mapped.append(self._tool_completed(parent_id, result, is_error=False))
        if blocks:
            self.messages.append(
                TurnMessage(
                    message_id=getattr(message, "uuid", None) or f"claude-tool-message:{len(self.messages)}",
                    role=MessageRole.TOOL,
                    content=tuple(blocks),
                )
            )
        return mapped

    def _tool_completed(self, call_id: str, result: Any, *, is_error: bool) -> MappedClaudeEvent:
        data: dict[str, Any] = {
            "tool_name": self._tool_names.get(call_id, "unknown"),
            "result": to_json_safe(result),
            "is_error": is_error,
        }
        parent = self._tool_parents.get(call_id)
        if parent:
            data["parent_tool_use_id"] = parent
        return MappedClaudeEvent(
            ItemLifecycleEvent(kind=ItemEventKind.COMPLETED, item_type="tool", data=freeze_json_object(data)),
            item_id=call_id,
        )

    @staticmethod
    def _map_result_observations(message: Any) -> list[MappedClaudeEvent]:
        usage = _turn_usage(getattr(message, "usage", None))
        if usage is None:
            return []
        return [MappedClaudeEvent(UsageUpdatedEvent(usage=usage, mode=UsageUpdateMode.CUMULATIVE))]

    # ------------------------------------------------------------------
    # Terminal result
    # ------------------------------------------------------------------

    def build_terminal_result(
        self,
        result_message: Any,
        *,
        turn: PendingTurn,
        timing: TurnTiming,
    ) -> tuple[TurnEventKind, TurnResult]:
        """Build the external terminal result from a Claude ``ResultMessage``."""

        usage = _turn_usage(getattr(result_message, "usage", None))
        final_output = getattr(result_message, "result", None)
        if not isinstance(final_output, str) or not final_output:
            final_output = self.last_text_output
        if turn.abort_requested:
            return TurnEventKind.ABORTED, interrupted_result(
                turn,
                provider_name=PROVIDER_NAME,
                timing=timing,
                messages=tuple(self.messages),
                final_output=final_output,
                usage=usage,
            )
        cost = _monetary(getattr(result_message, "total_cost_usd", None))
        provider_data: dict[str, Any] = {
            "subtype": getattr(result_message, "subtype", None),
            "num_turns": getattr(result_message, "num_turns", None),
            "duration_api_ms": getattr(result_message, "duration_api_ms", None),
        }
        stop_reason = getattr(result_message, "stop_reason", None)
        common: dict[str, Any] = {
            "messages": tuple(self.messages),
            "final_output": final_output,
            "structured_output": freeze_json_value(to_json_safe(getattr(result_message, "structured_output", None))),
            "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
            "usage": usage,
            "cost": cost,
            "started_at": timing.started_at,
            "completed_at": timing.completed_at(),
            "duration_ms": timing.duration_ms(),
            "provider_data": freeze_json_object(to_json_object(provider_data)),
        }
        if getattr(result_message, "is_error", False):
            error = merge_pending_error(self.pending_error, classify_result_message(result_message))
            if not getattr(result_message, "errors", None) and self.last_text_output:
                # The CLI reports API failures as synthetic assistant text and an
                # error result without ``errors``; keep that text as the reason.
                error = TurnError(
                    message=self.last_text_output,
                    code=error.code,
                    category=error.category,
                    retryable=error.retryable,
                    provider_data=error.provider_data,
                )
            return TurnEventKind.FAILED, TurnResult(status=TurnStatus.FAILED, error=error, **common)
        return TurnEventKind.FINISHED, TurnResult(status=TurnStatus.COMPLETED, **common)

    def build_failed_result(self, error: TurnError, *, timing: TurnTiming) -> TurnResult:
        """Build a FAILED result for an SDK exception raised mid-turn."""

        return TurnResult(
            status=TurnStatus.FAILED,
            messages=tuple(self.messages),
            final_output=self.last_text_output,
            error=merge_pending_error(self.pending_error, error),
            started_at=timing.started_at,
            completed_at=timing.completed_at(),
            duration_ms=timing.duration_ms(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _final_output(self, message_index: int, index: int, channel: OutputChannel, text: str) -> MappedClaudeEvent:
        self.emitted_output = True
        return MappedClaudeEvent(
            OutputEvent(
                output_id=self._output_id(message_index, index, channel),
                kind=OutputKind.TEXT,
                content=text,
                operation=OutputOperation.FINAL,
                channel=channel,
                content_index=index,
            )
        )

    @staticmethod
    def _output_id(message_index: int, content_index: int, channel: OutputChannel) -> str:
        return f"claude-output:{message_index}:{content_index}:{channel.value}"

    @staticmethod
    def _provider_event(event_type: str, payload: Mapping[str, Any]) -> MappedClaudeEvent:
        return MappedClaudeEvent(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type=event_type,
                schema_version=_SCHEMA_VERSION,
                payload=freeze_json_object(payload),
            )
        )


def _normalize_tool_result(value: Any) -> Any:
    """Collapse SDK text content blocks into the native string result shape."""
    if not isinstance(value, list):
        return value
    text_parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("type") != "text":
            return value
        text = item.get("text")
        if not isinstance(text, str):
            return value
        text_parts.append(text)
    return "\n".join(text_parts)


def _non_negative(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _turn_usage(usage: Any) -> TurnUsage | None:
    if not isinstance(usage, Mapping):
        return None
    input_tokens = _non_negative(usage.get("input_tokens"))
    output_tokens = _non_negative(usage.get("output_tokens"))
    cache_read = _non_negative(usage.get("cache_read_input_tokens"))
    cache_write = _non_negative(usage.get("cache_creation_input_tokens"))
    if input_tokens is None and output_tokens is None and cache_read is None:
        return None
    total = sum(value or 0 for value in (input_tokens, output_tokens, cache_read, cache_write))
    provider_data: dict[str, JsonValue] = {}
    if cache_write is not None:
        provider_data["cache_creation_input_tokens"] = cache_write
    return TurnUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cache_read,
        total_tokens=total,
        provider_data=provider_data,
    )


def _monetary(total_cost_usd: Any) -> MonetaryAmount | None:
    if isinstance(total_cost_usd, bool) or not isinstance(total_cost_usd, (int, float)):
        return None
    micros = int(round(float(total_cost_usd) * 1_000_000))
    if micros < 0:
        return None
    return MonetaryAmount(micros=micros, currency="USD")


__all__ = ["ClaudeTurnAccumulator", "MappedClaudeEvent", "PROVIDER_NAME"]
