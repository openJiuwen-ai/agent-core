# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail for detecting model anomalies: stream stalls/loops and tool-call loops."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.runner.callback.errors import AbortError
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

_STREAM_CHUNK_INSPECTORS_KEY = "_stream_chunk_inspectors"
_MODEL_ANOMALY_STATE_KEY = "_model_anomaly_detection_state"
_REPEAT_ERROR_MARKER = "LLM repeated stream output detected"
_STREAM_TIMEOUT_MARKERS = (
    "LLM stream timeout",
    "stream frame timeout",
)
# Whole-call retry backoff (seconds) before the 1st/2nd/... retry attempt.
# The last value is reused if there are more retries than entries.
_DEFAULT_BACKOFF_SECONDS = (0.5, 1.0, 2.0)

_TOOL_LOOP_WARNING_TEMPLATE_CN = """\
检测到连续多轮调用了相同的工具集，且每个工具的入参和执行结果也相同。
工具调用命令如下：
---
{tool_calls}
---
工具执行结果如下：
---
{tool_results}
---
**请跳出重复工具调用**
"""

_TOOL_LOOP_WARNING_TEMPLATE_EN = """\
Detected consecutive turns calling the same tool set with identical arguments and results.
Tool calls:
---
{tool_calls}
---
Tool results:
---
{tool_results}
---
**Please break out of the repeated tool calls**
"""

_TOOL_LOOP_BAILOUT_MESSAGE = (
    "Detected that the model fell into repeated tool calls; "
    "this task has been automatically aborted."
)

_TOOL_LOOP_WARNING_TEMPLATES = {
    "cn": _TOOL_LOOP_WARNING_TEMPLATE_CN,
    "en": _TOOL_LOOP_WARNING_TEMPLATE_EN,
}


class ToolLoopCompactConfig(BaseModel):
    """Detect and compact consecutive identical tool-call/args/result rounds."""

    enabled: bool = Field(
        default=False,
        description="Enable consecutive tool-loop compaction. Off by default.",
    )
    consecutive_threshold: int = Field(
        default=4,
        ge=2,
        description=(
            "Trigger compaction when this many consecutive completed tool rounds "
            "share the same tool name set, arguments, and return values "
            "(order-independent)."
        ),
    )
    bailout_threshold: int = Field(
        default=3,
        ge=0,
        description=(
            "Raise a bail-out error when about to trigger this many loop "
            "compactions within a single agent invoke. With the default 3, "
            "at most 2 compactions occur and the 3rd hit aborts. "
            "Set to 0 to disable bail-out."
        ),
    )


@dataclass(frozen=True)
class _ToolRound:
    start: int
    end: int
    # Sorted (tool_name, canonical_args, result_content) triples.
    fingerprint: Tuple[Tuple[str, str, str], ...]


class ModelAnomalyDetectionRail(DeepAgentRail):
    """Detect and handle selected model anomalies.

    Handles three failure modes:
      - repeated output suffixes in reasoning/content streams
      - stream frame timeout errors raised by ``Model.stream``
      - consecutive identical tool-call/args/result loops (compact or bail out)
    """

    priority = 70

    def __init__(
        self,
        *,
        max_retries: int = 2,
        repeat_min_pattern_chars: int = 2,
        repeat_max_pattern_chars: int = 64,
        repeat_min_count: int = 6,
        repeat_min_total_chars: int = 160,
        repeat_window_chars: int = 1024,
        single_char_repeat_count: int = 100,
        backoff_seconds: Optional[List[float]] = None,
        tool_loop_compact: ToolLoopCompactConfig | Dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        backoff = list(backoff_seconds) if backoff_seconds is not None else list(_DEFAULT_BACKOFF_SECONDS)
        if any(delay < 0 for delay in backoff):
            raise ValueError("backoff_seconds entries must be >= 0")
        if repeat_min_pattern_chars < 1:
            raise ValueError("repeat_min_pattern_chars must be >= 1")
        if repeat_max_pattern_chars < repeat_min_pattern_chars:
            raise ValueError("repeat_max_pattern_chars must be >= repeat_min_pattern_chars")
        if repeat_min_count < 2:
            raise ValueError("repeat_min_count must be >= 2")
        if repeat_min_total_chars < 1:
            raise ValueError("repeat_min_total_chars must be >= 1")
        if single_char_repeat_count < 2:
            raise ValueError("single_char_repeat_count must be >= 2")
        min_window = max(
            repeat_max_pattern_chars * repeat_min_count,
            repeat_min_total_chars,
            single_char_repeat_count,
        )
        if repeat_window_chars < min_window:
            raise ValueError(
                "repeat_window_chars is too small for the configured repetition thresholds"
            )

        self.max_retries = max_retries
        self.repeat_min_pattern_chars = repeat_min_pattern_chars
        self.repeat_max_pattern_chars = repeat_max_pattern_chars
        self.repeat_min_count = repeat_min_count
        self.repeat_min_total_chars = repeat_min_total_chars
        self.repeat_window_chars = repeat_window_chars
        self.single_char_repeat_count = single_char_repeat_count
        self.backoff_seconds = backoff
        self.repeat_retry_count = 0
        self.stream_timeout_retry_count = 0

        self._tool_loop_compact = self._normalize_tool_loop_compact_config(tool_loop_compact)
        # Per-session, per-invoke counters. Keyed by session id because the same
        # rail instance may be shared across concurrent sessions.
        self._tool_loop_compact_counts: Dict[str, int] = {}

    @staticmethod
    def _normalize_tool_loop_compact_config(
        config: ToolLoopCompactConfig | Dict[str, Any] | None,
    ) -> ToolLoopCompactConfig:
        if config is None:
            return ToolLoopCompactConfig()
        if isinstance(config, ToolLoopCompactConfig):
            return config
        if isinstance(config, dict):
            return ToolLoopCompactConfig(**config)
        raise TypeError(
            "tool_loop_compact must be ToolLoopCompactConfig, dict, or None, "
            f"got {type(config)!r}"
        )

    def backoff_delay(self, retry_index: int) -> float:
        """Return the sleep (seconds) before the retry at ``retry_index`` (0-based).

        Uses the configured backoff schedule, clamped to its last entry.
        """
        if not self.backoff_seconds:
            return 0.0
        return self.backoff_seconds[min(retry_index, len(self.backoff_seconds) - 1)]

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset per-invoke anomaly counters."""
        self.repeat_retry_count = 0
        self.stream_timeout_retry_count = 0
        self._reset_tool_loop_compact_counter(ctx)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._reset_tool_loop_compact_counter(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Compact tool loops (if needed) and install a stream chunk inspector."""
        self._maybe_compact_or_bailout_tool_loop(ctx)
        ctx.extra[_MODEL_ANOMALY_STATE_KEY] = {
            "reasoning_content": "",
            "content": "",
        }
        inspectors = ctx.extra.get(_STREAM_CHUNK_INSPECTORS_KEY)
        if not isinstance(inspectors, list):
            inspectors = []
        inspectors = [
            inspector
            for inspector in inspectors
            if getattr(inspector, "__self__", None) is not self
        ]
        inspectors.append(self.inspect_stream_chunk)
        ctx.extra[_STREAM_CHUNK_INSPECTORS_KEY] = inspectors

    async def inspect_stream_chunk(self, ctx: AgentCallbackContext, chunk: Any) -> None:
        """Inspect stream chunks for repeated suffixes."""
        state = ctx.extra.setdefault(_MODEL_ANOMALY_STATE_KEY, {})
        for field_name in ("reasoning_content", "content"):
            text = getattr(chunk, field_name, None)
            if not text:
                continue
            self._append_and_check(state, field_name, str(text))

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Retry selected model-call failures."""
        if self._is_repeat_exception(ctx.exception):
            self._request_retry_or_reset(ctx, "repeat")
            return

        if self._is_stream_timeout_exception(ctx.exception):
            self._request_retry_or_reset(ctx, "stream_timeout")

    def _append_and_check(self, state: Dict[str, str], field_name: str, text: str) -> None:
        tail = (state.get(field_name, "") + text)[-self.repeat_window_chars:]
        state[field_name] = tail
        detected = self._detect_repeated_suffix(tail)
        if detected is None:
            return

        repeated_unit, repeat_count = detected
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg=(
                f"{_REPEAT_ERROR_MARKER}: field={field_name}, "
                f"unit={self._format_unit(repeated_unit)!r}, repeat_count={repeat_count}"
            ),
        )

    def _detect_repeated_suffix(self, text: str) -> Optional[Tuple[str, int]]:
        single_char_match = self._detect_single_char_suffix(text)
        if single_char_match is not None:
            return single_char_match

        max_unit_len = min(self.repeat_max_pattern_chars, len(text) // self.repeat_min_count)
        for unit_len in range(self.repeat_min_pattern_chars, max_unit_len + 1):
            unit = text[-unit_len:]
            if not unit.strip() or self._is_single_char_pattern(unit):
                continue

            required_count = max(
                self.repeat_min_count,
                (self.repeat_min_total_chars + unit_len - 1) // unit_len,
            )
            repeat_count = 1
            pos = len(text) - unit_len * 2
            while pos >= 0 and text[pos:pos + unit_len] == unit:
                repeat_count += 1
                pos -= unit_len

            if repeat_count >= required_count:
                return unit, repeat_count

        return None

    def _detect_single_char_suffix(self, text: str) -> Optional[Tuple[str, int]]:
        if not text:
            return None
        last_char = text[-1]
        if not last_char.strip():
            return None

        repeat_count = 1
        for index in range(len(text) - 2, -1, -1):
            if text[index] != last_char:
                break
            repeat_count += 1
            if repeat_count >= self.single_char_repeat_count:
                return last_char, repeat_count
        return None

    @staticmethod
    def _is_single_char_pattern(unit: str) -> bool:
        return len(set(unit)) == 1

    @staticmethod
    def _format_unit(unit: str, limit: int = 80) -> str:
        formatted = unit.replace("\r", "\\r").replace("\n", "\\n")
        if len(formatted) <= limit:
            return formatted
        return formatted[:limit] + "..."

    def _request_retry_or_reset(self, ctx: AgentCallbackContext, reason: str) -> None:
        if reason == "repeat":
            counter_attr = "repeat_retry_count"
            label = "repeated stream output"
        elif reason == "stream_timeout":
            counter_attr = "stream_timeout_retry_count"
            label = "stream frame timeout"
        else:
            return

        current = getattr(self, counter_attr)
        if current < self.max_retries:
            delay = self.backoff_delay(current)
            setattr(self, counter_attr, current + 1)
            logger.warning(
                "[ModelAnomalyDetectionRail] retrying model call after %s "
                "(%d/%d) after %.2fs backoff",
                label,
                current + 1,
                self.max_retries,
                delay,
            )
            ctx.request_retry(delay_seconds=delay)
        else:
            setattr(self, counter_attr, 0)

    @staticmethod
    def _is_repeat_exception(exc: Optional[BaseException]) -> bool:
        return exc is not None and _REPEAT_ERROR_MARKER in str(exc)

    @staticmethod
    def _is_stream_timeout_exception(exc: Optional[BaseException]) -> bool:
        if exc is None:
            return False
        message = str(exc)
        return any(marker in message for marker in _STREAM_TIMEOUT_MARKERS)

    @staticmethod
    def _tool_loop_compact_session_key(ctx: AgentCallbackContext) -> str:
        session = ctx.session
        if session is None:
            return ""
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id):
            try:
                session_id = get_session_id()
            except Exception:
                session_id = None
            if session_id:
                return str(session_id)
        session_id = getattr(session, "session_id", None)
        if session_id:
            return str(session_id)
        return f"session@{id(session)}"

    def _get_tool_loop_compact_count(self, ctx: AgentCallbackContext) -> int:
        return self._tool_loop_compact_counts.get(
            self._tool_loop_compact_session_key(ctx), 0
        )

    def _set_tool_loop_compact_count(
        self, ctx: AgentCallbackContext, count: int
    ) -> None:
        key = self._tool_loop_compact_session_key(ctx)
        if count <= 0:
            self._tool_loop_compact_counts.pop(key, None)
        else:
            self._tool_loop_compact_counts[key] = count

    def _reset_tool_loop_compact_counter(self, ctx: AgentCallbackContext) -> None:
        self._set_tool_loop_compact_count(ctx, 0)

    def _maybe_compact_or_bailout_tool_loop(self, ctx: AgentCallbackContext) -> None:
        """Compact identical tool loops, or abort on the N-th trigger.

        Runs in ``before_model_call`` so tool results for the latest round are
        already committed to context. Matching is order-independent over
        (tool name, arguments, return value).

        Raising ``AbortError`` (with a ``build_error`` cause) is required because
        plain exceptions raised inside a rail callback are swallowed by the
        callback framework; ``AbortError`` re-raises its cause across the
        ``trigger`` boundary so the underlying ``build_error`` propagates.
        """
        config = self._tool_loop_compact
        if not config.enabled:
            return

        context = ctx.context
        if context is None:
            return

        messages = list(context.get_messages())
        compact_range = _find_tool_loop_compact_range(
            messages,
            threshold=config.consecutive_threshold,
        )
        if compact_range is None:
            return

        fold_start, fold_end, rounds = compact_range
        next_count = self._get_tool_loop_compact_count(ctx) + 1
        language = self._resolve_tool_loop_warning_language(ctx)
        if config.bailout_threshold > 0 and next_count >= config.bailout_threshold:
            self._reset_tool_loop_compact_counter(ctx)
            bailout_msg = _TOOL_LOOP_BAILOUT_MESSAGE
            logger.warning(
                "[ModelAnomalyDetectionRail] tool-loop bailout: "
                "next_compaction=%d >= threshold=%d; %s",
                next_count,
                config.bailout_threshold,
                bailout_msg,
            )
            raise AbortError(
                bailout_msg,
                cause=build_error(
                    StatusCode.CONTEXT_EXECUTION_ERROR,
                    error_msg=bailout_msg,
                ),
            )

        latest = rounds[-1]
        tool_calls_text, tool_results_text = _collect_call_and_result_sets(
            messages[latest.start:latest.end]
        )
        warning = UserMessage(
            content=_build_tool_loop_warning_content(
                language=language,
                tool_calls=tool_calls_text,
                tool_results=tool_results_text,
            )
        )
        compacted = messages[:fold_start] + [warning] + messages[fold_end:]
        context.set_messages(compacted)
        self._set_tool_loop_compact_count(ctx, next_count)
        logger.info(
            "[ModelAnomalyDetectionRail] compacted consecutive identical tool "
            "loops: before=%d after=%d compact_count=%d",
            len(messages),
            len(compacted),
            next_count,
        )

    @staticmethod
    def _resolve_tool_loop_warning_language(ctx: AgentCallbackContext) -> str:
        agent = ctx.agent
        builder = getattr(agent, "system_prompt_builder", None) if agent is not None else None
        language = getattr(builder, "language", None) if builder is not None else None
        return _normalize_language(language)


def _find_tool_loop_compact_range(
    messages: Sequence[BaseMessage],
    *,
    threshold: int,
) -> Optional[Tuple[int, int, List[_ToolRound]]]:
    if threshold < 2:
        return None
    rounds = _collect_tool_rounds(messages)
    if len(rounds) < threshold:
        return None

    eligible: List[_ToolRound] = []
    for round_info in reversed(rounds):
        if not eligible:
            eligible.append(round_info)
            continue
        if eligible[-1].fingerprint != round_info.fingerprint:
            break
        eligible.append(round_info)

    eligible.reverse()
    if len(eligible) < threshold:
        return None
    return eligible[0].start, eligible[-1].end, eligible


def _collect_tool_rounds(messages: Sequence[BaseMessage]) -> List[_ToolRound]:
    rounds: List[_ToolRound] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        if not isinstance(message, AssistantMessage):
            index += 1
            continue

        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            index += 1
            continue

        pending_ids = {
            str(getattr(tool_call, "id", "") or "")
            for tool_call in tool_calls
            if getattr(tool_call, "id", None)
        }
        if not pending_ids:
            index += 1
            continue

        call_by_id: Dict[str, Tuple[str, str]] = {}
        for tool_call in tool_calls:
            tool_call_id = str(getattr(tool_call, "id", "") or "")
            name = str(getattr(tool_call, "name", "") or "").strip()
            if not tool_call_id or not name:
                continue
            call_by_id[tool_call_id] = (
                name,
                _canonical_arguments_text(getattr(tool_call, "arguments", None)),
            )

        if not call_by_id:
            index += 1
            continue

        result_by_id: Dict[str, str] = {}
        cursor = index + 1
        while cursor < total and pending_ids:
            next_message = messages[cursor]
            if not isinstance(next_message, ToolMessage):
                break
            tool_call_id = str(getattr(next_message, "tool_call_id", "") or "")
            if tool_call_id in pending_ids:
                content = next_message.content
                if not isinstance(content, str):
                    content = "" if content is None else str(content)
                result_by_id[tool_call_id] = content
                pending_ids.discard(tool_call_id)
                cursor += 1
                continue
            break

        if pending_ids:
            index += 1
            continue

        fingerprint = tuple(
            sorted(
                (
                    call_by_id[tool_call_id][0],
                    call_by_id[tool_call_id][1],
                    result_by_id.get(tool_call_id, ""),
                )
                for tool_call_id in call_by_id
            )
        )
        rounds.append(_ToolRound(start=index, end=cursor, fingerprint=fingerprint))
        index = cursor

    return rounds


def _collect_call_and_result_sets(
    folded_messages: Sequence[BaseMessage],
) -> Tuple[str, str]:
    """Serialize latest-round tool calls/results as structured plain-text JSON."""
    call_items: List[Dict[str, Any]] = []
    result_items: List[Dict[str, Any]] = []
    tool_name_by_id: Dict[str, str] = {}

    for message in folded_messages:
        if isinstance(message, AssistantMessage):
            for tool_call in getattr(message, "tool_calls", None) or []:
                name = str(getattr(tool_call, "name", "") or "").strip() or "(unknown)"
                tool_call_id = str(getattr(tool_call, "id", "") or "")
                if tool_call_id:
                    tool_name_by_id[tool_call_id] = name
                call_items.append({
                    "name": name,
                    "arguments": _parse_tool_arguments(getattr(tool_call, "arguments", None)),
                })
        elif isinstance(message, ToolMessage):
            content = message.content
            if not isinstance(content, str) or not content.strip():
                continue
            tool_call_id = str(getattr(message, "tool_call_id", "") or "")
            result_items.append({
                "name": tool_name_by_id.get(tool_call_id, "(unknown)"),
                "content": content.strip(),
            })

    return _dumps_json_list(call_items), _dumps_json_list(result_items)


def _build_tool_loop_warning_content(
    *,
    language: str,
    tool_calls: str,
    tool_results: str,
) -> str:
    template = _TOOL_LOOP_WARNING_TEMPLATES.get(language, _TOOL_LOOP_WARNING_TEMPLATE_CN)
    return template.format(
        tool_calls=tool_calls,
        tool_results=tool_results,
    ).strip()


def _parse_tool_arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _canonical_arguments_text(raw: Any) -> str:
    parsed = _parse_tool_arguments(raw)
    try:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(parsed)


def _dumps_json_list(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "[]"
    return json.dumps(items, ensure_ascii=False, indent=2)


def _normalize_language(language: str | None) -> str:
    normalized = (language or "cn").strip().lower()
    if normalized.startswith("zh") or normalized == "cn":
        return "cn"
    if normalized == "en":
        return "en"
    return "cn"


__all__ = [
    "ModelAnomalyDetectionRail",
    "ToolLoopCompactConfig",
]
