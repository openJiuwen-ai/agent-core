# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure-code fallback for prompt-too-long (PTL) rejections.

Unlike the LLM-backed compressors this module never calls a model. It drops
whole completed dialogue turns, oldest first, until the estimated payload
fits the target budget. A turn runs from a user message to the assistant
reply without tool calls, so dropping one keeps ``tool_calls`` next to their
``tool`` results and never separates the user's question from its answer.

The public entry point is :func:`truncate_overflowed_context`. It is
deliberately side-effect scoped to one :class:`ModelContext`: callers own
persistence and event reporting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.schema.config import DEFAULT_OVERFLOW_TRUNCATE_MARKER
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.tool import ToolInfo

TRUNCATE_MARKER_METADATA_KEY = "overflow_truncated_marker"
DEFAULT_CONTEXT_LIMIT_FALLBACK = 200_000
_CHARS_PER_TOKEN_FALLBACK = 4
_MIN_PLAUSIBLE_CONTEXT_LIMIT = 1024

# Ordered from most to least specific. Every pattern captures the provider's
# *maximum* input budget, never the rejected request size.
_CONTEXT_LIMIT_PATTERNS = (
    # Anthropic style: "250000 tokens > 200000 maximum".
    re.compile(r"(?i)>\s*(\d[\d,]{3,})\s*(?:tokens?)?\s*(?:max|maximum|limit)"),
    # OpenAI style: "maximum context length is 128000 tokens".
    re.compile(r"(?i)(?:maximum|max)\s+(?:context|input|prompt)[^.\n]{0,40}?(\d[\d,]{3,})"),
    # vLLM style: "the model's context length (16384 tokens)".
    re.compile(r"(?i)(?:context|input|prompt)[^.\n]{0,30}?(?:length|window|limit)[^.\n]{0,20}?(\d[\d,]{3,})"),
    # DashScope style: "Range of input length should be [1, 1000000]".
    re.compile(r"(?i)range\s+of\s+input\s+length[^.\n]{0,20}?(\d[\d,]{3,})"),
)
_NUMBER_RE = re.compile(r"\d[\d,_]*")
_CONTEXT_HINT_RE = re.compile(r"(?i)(context|prompt|input|window|token|length|limit|max|exceed)")
_OUTPUT_HINT_RE = re.compile(r"(?i)(max[_ ]?tokens|output|completion)")


@dataclass(frozen=True)
class OverflowTruncationResult:
    """Outcome of one pure-code truncation attempt."""

    changed: bool
    limit_tokens: int = 0
    target_tokens: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    dropped_rounds: int = 0
    dropped_messages: int = 0
    dropped_tool_exchanges: int = 0
    archive_memory_id: str = ""


class _TokenEstimator:
    """Thin wrapper that never lets a counter failure abort the fallback."""

    def __init__(self, counter: Any):
        self._counter = counter

    @classmethod
    def from_context(cls, context: ModelContext) -> Optional["_TokenEstimator"]:
        get_counter = getattr(context, "token_counter", None)
        counter = get_counter() if callable(get_counter) else get_counter
        if counter is None or not callable(getattr(counter, "count_messages", None)):
            return None
        return cls(counter)

    def messages(self, messages: Sequence[BaseMessage]) -> int:
        if not messages:
            return 0
        try:
            return max(int(self._counter.count_messages(list(messages))), 0)
        except Exception:  # noqa: BLE001 - estimate only; never block truncation
            return sum(
                max(len(str(getattr(message, "content", "") or "")), 0) // _CHARS_PER_TOKEN_FALLBACK
                for message in messages
            )

    def tools(self, tools: Optional[Sequence[ToolInfo]]) -> int:
        if not tools:
            return 0
        count_tools = getattr(self._counter, "count_tools", None)
        if not callable(count_tools):
            return 0
        try:
            return max(int(count_tools(list(tools))), 0)
        except Exception:  # noqa: BLE001 - estimate only; never block truncation
            return 0


def parse_context_limit(error_texts: Iterable[str]) -> Optional[int]:
    """Extract a provider-reported context budget from error text.

    Only values that look like an input/context budget are accepted. Output
    limits such as ``max_tokens`` are ignored so a small generation cap cannot
    collapse the target budget. Returns ``None`` when no value is usable; the
    caller then falls back to the configured context window.
    """
    texts = [text for text in error_texts if isinstance(text, str) and text]
    for text in texts:
        explicit = _explicit_context_limit(text)
        if explicit is not None:
            return explicit

    candidates: List[int] = []
    for text in texts:
        candidates.extend(_generic_context_limits(text))
    return min(candidates) if candidates else None


def truncate_overflowed_context(
    context: ModelContext,
    *,
    error_texts: Sequence[str] = (),
    attempt: int = 1,
    target_ratio: float = 0.8,
    keep_last_rounds: int = 1,
    archive_dropped_rounds: bool = True,
    marker_template: str = DEFAULT_OVERFLOW_TRUNCATE_MARKER,
    messages: Optional[Sequence[BaseMessage]] = None,
    tools: Optional[Sequence[ToolInfo]] = None,
) -> OverflowTruncationResult:
    """Drop the oldest complete dialogue turns until the payload fits.

    The agent passes the exact messages/tools sent to the provider as
    ``messages``/``tools`` when available; their difference from the stored
    context is treated as fixed overhead (system prompt, attached tools).

    Args:
        context: Context to shrink in place.
        error_texts: Flattened provider error text, used to recover the real
            context budget. Falls back to ``context.context_window_tokens()``.
        attempt: 1-based recovery attempt. The target shrinks as
            ``target_ratio ** attempt`` so repeated failures keep progressing.
        target_ratio: Fraction of the budget to target on the first attempt.
        keep_last_rounds: Newest complete rounds that are never dropped.
        archive_dropped_rounds: Archive dropped messages best-effort.
        marker_template: Marker text with ``{rounds}`` and ``{tokens}``.
        messages: Actual payload messages, when available.
        tools: Actual payload tools, when available.

    Returns:
        :class:`OverflowTruncationResult`; ``changed`` is False when nothing
        could be dropped and the caller should preserve the original error.
    """
    estimator = _TokenEstimator.from_context(context)
    if estimator is None:
        logger.warning("PTL fallback skipped: context has no usable token counter")
        return OverflowTruncationResult(changed=False)

    try:
        stored_messages = list(context.get_messages())
    except Exception as exc:  # noqa: BLE001 - fallback must not mask the model error
        logger.warning("PTL fallback skipped: cannot read context messages: %s", exc)
        return OverflowTruncationResult(changed=False)

    # Marker messages from an earlier attempt are replaced, never stacked.
    cleaned = [message for message in stored_messages if not _is_truncate_marker(message)]
    prefix, body = _split_leading_system_messages(cleaned)

    payload_tokens = estimator.messages(messages) if messages is not None else estimator.messages(cleaned)
    overhead = max(payload_tokens - estimator.messages(cleaned), 0) + estimator.tools(tools)
    tokens_before = overhead + estimator.messages(cleaned)

    limit_tokens = _resolve_limit(context, error_texts)
    target_tokens = _resolve_target(limit_tokens, target_ratio, attempt)

    dropped: List[BaseMessage] = []
    dropped_rounds = 0
    dropped_tool_exchanges = 0
    retained = body
    # Where the marker goes in the retained text: the position of the first
    # dropped message, so the model sees the gap where the history was cut.
    marker_index = 0

    rounds = _dialogue_rounds(body)
    protected_from = max(0, len(rounds) - max(1, keep_last_rounds))
    droppable_ends = [
        end
        for index, (_, end) in enumerate(rounds)
        if index < protected_from and end is not None
    ]
    if droppable_ends and tokens_before > target_tokens:
        rounds_to_drop = 1
        while rounds_to_drop <= len(droppable_ends):
            cut = droppable_ends[rounds_to_drop - 1]
            candidate_dropped = body[:cut]
            estimate = _estimate(
                estimator,
                prefix,
                body[cut:],
                marker_template,
                rounds_to_drop,
                _dropped_token_hint(candidate_dropped),
                overhead,
            )
            if estimate <= target_tokens:
                break
            rounds_to_drop += 1
        rounds_to_drop = min(rounds_to_drop, len(droppable_ends))
        cut = droppable_ends[rounds_to_drop - 1]
        dropped.extend(body[:cut])
        retained = body[cut:]
        dropped_rounds = rounds_to_drop

    # Single-round special case: when whole-round dropping cannot fit the
    # budget, remove the oldest complete tool exchange inside the retained
    # text. An exchange is an assistant ``tool_calls`` message plus every
    # matching ``tool`` result, so pairing stays intact.
    while _estimate(
        estimator, prefix, retained, marker_template, dropped_rounds, _dropped_token_hint(dropped), overhead
    ) > target_tokens:
        units = _iter_tool_exchange_units(retained)
        if not units:
            break
        start, end = units[0]
        if not dropped:
            marker_index = start
        dropped.extend(retained[start:end])
        retained = retained[:start] + retained[end:]
        dropped_tool_exchanges += 1

    if not dropped:
        return OverflowTruncationResult(
            changed=False,
            limit_tokens=limit_tokens,
            target_tokens=target_tokens,
            tokens_before=tokens_before,
        )

    marker = _build_marker(marker_template, dropped_rounds or dropped_tool_exchanges, tokens_before - target_tokens)
    marker_index = min(max(marker_index, 0), len(retained))
    new_messages = prefix + retained[:marker_index] + [marker] + retained[marker_index:]
    tokens_after = overhead + estimator.messages(new_messages)

    archive_memory_id = ""
    if archive_dropped_rounds:
        archive_memory_id = _archive_dropped(context, prefix, dropped, stored_messages)

    try:
        context.set_messages(new_messages)
    except Exception as exc:  # noqa: BLE001 - keep the original model error visible
        logger.warning("PTL fallback failed to apply truncated messages: %s", exc)
        return OverflowTruncationResult(
            changed=False,
            limit_tokens=limit_tokens,
            target_tokens=target_tokens,
            tokens_before=tokens_before,
        )

    return OverflowTruncationResult(
        changed=True,
        limit_tokens=limit_tokens,
        target_tokens=target_tokens,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        dropped_rounds=dropped_rounds,
        dropped_messages=len(dropped),
        dropped_tool_exchanges=dropped_tool_exchanges,
        archive_memory_id=archive_memory_id,
    )


def _estimate(
    estimator: _TokenEstimator,
    prefix: Sequence[BaseMessage],
    retained: Sequence[BaseMessage],
    marker_template: str,
    rounds: int,
    dropped_tokens: int,
    overhead: int,
) -> int:
    marker = _build_marker(marker_template, rounds, dropped_tokens)
    return overhead + estimator.messages(list(prefix) + [marker] + list(retained))


def _dropped_token_hint(dropped: Sequence[BaseMessage]) -> int:
    return sum(
        max(len(str(getattr(message, "content", "") or "")), 0) // _CHARS_PER_TOKEN_FALLBACK
        for message in dropped
    )


def _split_leading_system_messages(messages: Sequence[BaseMessage]) -> Tuple[List[BaseMessage], List[BaseMessage]]:
    prefix_end = 0
    for message in messages:
        if not isinstance(message, SystemMessage):
            break
        prefix_end += 1
    return list(messages[:prefix_end]), list(messages[prefix_end:])


def _dialogue_rounds(messages: Sequence[BaseMessage]) -> List[Tuple[int, Optional[int]]]:
    """Return dialogue rounds as ascending ``[start, end)`` ranges.

    A round is a user message through the next assistant message without tool
    calls. An in-flight round has no closing assistant and is returned with
    ``end=None``; it counts towards ``keep_last_rounds`` but is never dropped.
    Using dialogue rounds instead of raw API segments keeps the user's
    question attached to the turn that follows it.
    """
    rounds: List[Tuple[int, Optional[int]]] = []
    try:
        raw_rounds = ContextUtils.find_all_dialogue_round(list(messages))
    except Exception:  # noqa: BLE001 - malformed context must not be truncated blindly
        return rounds

    for raw_round in raw_rounds:
        if not isinstance(raw_round, (list, tuple)) or len(raw_round) < 2:
            continue
        user_index, assistant_index = raw_round[0], raw_round[1]
        if user_index is None:
            continue
        try:
            start = max(int(user_index), 0)
            end = None if assistant_index is None else int(assistant_index) + 1
        except (TypeError, ValueError):
            continue
        if end is not None and (end <= start or end > len(messages)):
            continue
        rounds.append((start, end))

    rounds.sort()
    return rounds


def _is_truncate_marker(message: BaseMessage) -> bool:
    metadata = getattr(message, "metadata", None)
    return isinstance(metadata, dict) and bool(metadata.get(TRUNCATE_MARKER_METADATA_KEY))


def _build_marker(template: str, rounds: int, tokens: int) -> UserMessage:
    try:
        content = template.format(rounds=max(int(rounds), 0), tokens=max(int(tokens), 0))
    except (KeyError, IndexError, ValueError):
        content = DEFAULT_OVERFLOW_TRUNCATE_MARKER.format(rounds=max(int(rounds), 0), tokens=max(int(tokens), 0))
    return UserMessage(content=content, metadata={TRUNCATE_MARKER_METADATA_KEY: True})


def _resolve_limit(context: ModelContext, error_texts: Sequence[str]) -> int:
    parsed = parse_context_limit(error_texts)
    if parsed is not None:
        return parsed
    resolver = getattr(context, "context_window_tokens", None)
    if callable(resolver):
        try:
            value = int(resolver())
            if value > 0:
                return value
        except Exception:  # noqa: BLE001 - resolution must not block truncation
            pass
    return DEFAULT_CONTEXT_LIMIT_FALLBACK


def _resolve_target(limit_tokens: int, target_ratio: float, attempt: int) -> int:
    ratio = target_ratio if target_ratio > 0 else 0.8
    try:
        effective_ratio = ratio ** max(int(attempt), 1)
    except (OverflowError, ValueError):
        effective_ratio = ratio
    return max(int(limit_tokens * effective_ratio), 1)


def _explicit_context_limit(text: str) -> Optional[int]:
    for pattern in _CONTEXT_LIMIT_PATTERNS:
        for match in pattern.finditer(text):
            value = _to_int(match.group(1))
            if value is not None and value >= _MIN_PLAUSIBLE_CONTEXT_LIMIT:
                return value
    return None


def _generic_context_limits(text: str) -> List[int]:
    """Collect plausible budgets near context words; small ones win.

    Provider messages that mention both the rejected size and the maximum
    resolve to the maximum, because it is the smaller of the two.
    """
    values: List[int] = []
    for match in _NUMBER_RE.finditer(text):
        value = _to_int(match.group())
        if value is None or value < _MIN_PLAUSIBLE_CONTEXT_LIMIT:
            continue
        window = text[max(0, match.start() - 32): match.end() + 32]
        if _OUTPUT_HINT_RE.search(window):
            continue
        if not _CONTEXT_HINT_RE.search(window):
            continue
        values.append(value)
    return values


def _to_int(raw: str) -> Optional[int]:
    digits = re.sub(r"[,_]", "", raw).strip()
    if not digits:
        return None
    try:
        return int(float(digits))
    except (OverflowError, ValueError):
        return None


def _iter_tool_exchange_units(messages: Sequence[BaseMessage]) -> List[Tuple[int, int]]:
    """Return ``[start, end)`` ranges of complete tool exchanges, oldest first.

    A unit is kept only when every ``tool_call`` id is answered by the
    contiguous block of tool messages that follows it. Partial exchanges are
    skipped because dropping them would break the provider's pairing rules.
    """
    units: List[Tuple[int, int]] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        tool_calls = getattr(message, "tool_calls", None) if isinstance(message, AssistantMessage) else None
        if not tool_calls:
            index += 1
            continue

        expected = {
            str(getattr(tool_call, "id", "") or "")
            for tool_call in tool_calls
            if getattr(tool_call, "id", "")
        }
        if not expected:
            index += 1
            continue

        cursor = index + 1
        answered: set[str] = set()
        last_result = index
        while cursor < total and isinstance(messages[cursor], ToolMessage):
            tool_call_id = str(getattr(messages[cursor], "tool_call_id", "") or "")
            if tool_call_id in expected:
                answered.add(tool_call_id)
            last_result = cursor
            cursor += 1

        if expected <= answered:
            units.append((index, last_result + 1))
            index = last_result + 1
        else:
            index += 1
    return units


def _archive_dropped(
    context: ModelContext,
    prefix: Sequence[BaseMessage],
    dropped: Sequence[BaseMessage],
    original_messages: Sequence[BaseMessage],
) -> str:
    """Archive dropped messages without ever blocking the truncation."""
    if not dropped:
        return ""
    try:
        # Imported lazily: the archive module pulls the forked compressor
        # subtree, which is heavy and unrelated to a pure-code fallback.
        from openjiuwen.core.context_engine.processor.forked.compressor.recall.archive import (
            archive_compression_messages,
        )

        archive = archive_compression_messages(
            context=context,
            processor_type="PTLFallbackTruncate",
            original_messages=list(original_messages),
            messages_to_compress=list(dropped),
            preceding_messages=list(prefix),
        )
        return str(archive.memory_id or "")
    except Exception as exc:  # noqa: BLE001 - archive is best-effort by design
        logger.warning("PTL fallback archive failed; continuing without it: %s", exc)
        return ""


__all__ = [
    "OverflowTruncationResult",
    "TRUNCATE_MARKER_METADATA_KEY",
    "parse_context_limit",
    "truncate_overflowed_context",
]
