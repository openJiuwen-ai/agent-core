# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Self-contained PTL (prompt-too-long) fallback capability.

This module owns provider-limit parsing, dialogue-round truncation, archiving,
and the Phase A/B orchestration. :class:`PtlFallbackConfig` lives in
``schema/config.py`` so configuration stays in the schema layer.

It is intentionally *uninstallable*: remove this ``recovery`` package only.
``PtlFallbackConfig`` remains in ``schema/config.py`` with a ``None`` default,
so the engine keeps its legacy path without importing this package. To remove
the configuration surface as well, delete the class/field in
``schema/config.py`` and the export in ``context_engine/__init__.py``. The
package only imports lower-level modules (message schema, tool schema,
logging, and the neutral dialogue-round helper), so deleting it cannot break
the context engine's import graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.dialogue_rounds import find_all_dialogue_round
from openjiuwen.core.context_engine.schema.config import (
    DEFAULT_OVERFLOW_TRUNCATE_MARKER,
    PtlFallbackConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the module import-light
    from openjiuwen.core.context_engine.base import ModelContext
    from openjiuwen.core.context_engine.context_engine import ContextEngine
    from openjiuwen.core.session.agent import Session


TRUNCATE_MARKER_METADATA_KEY = "overflow_truncated_marker"
DEFAULT_CONTEXT_LIMIT_FALLBACK = 200_000
_CHARS_PER_TOKEN_FALLBACK = 4
_MIN_PLAUSIBLE_CONTEXT_LIMIT = 1024


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
    limit_source: str = ""
    resolved_window_tokens: int = 0


@dataclass(frozen=True)
class RecoveryOutcome:
    """Result of one two-phase recovery call.

    ``trace`` contains only the phases attempted by *this* call; the caller
    merges it into its own attempt trace. ``recover`` never mutates the
    caller's ``attempted_recoveries`` list.
    """

    recovered: bool
    trace: Tuple[str, ...] = ()


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


class _TokenEstimator:
    """Thin wrapper that never lets a counter failure abort the fallback."""

    def __init__(self, counter: Any):
        self._counter = counter

    @classmethod
    def from_context(cls, context: "ModelContext") -> Optional["_TokenEstimator"]:
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

    def tools(self, tools: Optional[Sequence[Any]]) -> int:
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
    context: "ModelContext",
    *,
    error_texts: Sequence[str] = (),
    attempt: int = 1,
    target_ratio: float = 0.8,
    keep_last_rounds: int = 1,
    archive_dropped_rounds: bool = True,
    marker_template: str = DEFAULT_OVERFLOW_TRUNCATE_MARKER,
    messages: Optional[Sequence[BaseMessage]] = None,
    tools: Optional[Sequence[Any]] = None,
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
        If the resolved limit is not above the fixed system/tool overhead, the
        context is left unchanged: truncating history could not make it fit.
        ``limit_source`` records whether the limit came from the provider
        error, the context window, or the built-in fallback.
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

    limit_tokens, limit_source = _resolve_limit(context, error_texts)
    target_tokens = _resolve_target(limit_tokens, target_ratio, attempt)
    resolved_window_tokens = _resolved_window_tokens(context)

    # The provider's limit is authoritative, but a value at or below the fixed
    # system/tool overhead can never fit: truncating history would only lose
    # data and still fail. Preserve the original error instead.
    fixed_overhead = overhead + estimator.messages(prefix)
    if limit_tokens <= fixed_overhead:
        logger.warning(
            "PTL fallback refuses to truncate: limit=%s (%s) is not above the fixed "
            "system/tool overhead=%s; original error preserved",
            limit_tokens,
            limit_source,
            fixed_overhead,
        )
        return OverflowTruncationResult(
            changed=False,
            limit_tokens=limit_tokens,
            target_tokens=target_tokens,
            tokens_before=tokens_before,
            limit_source=limit_source,
            resolved_window_tokens=resolved_window_tokens,
        )

    if resolved_window_tokens and limit_source == "provider":
        if limit_tokens * 2 < resolved_window_tokens:
            logger.warning(
                "PTL provider limit %s is far below the resolved context window %s; the provider limit may be wrong",
                limit_tokens,
                resolved_window_tokens,
            )
        elif limit_tokens > resolved_window_tokens * 2:
            logger.warning(
                "PTL provider limit %s is far above the resolved context window %s; truncation may not be enough",
                limit_tokens,
                resolved_window_tokens,
            )

    dropped: List[BaseMessage] = []
    dropped_rounds = 0
    dropped_tool_exchanges = 0
    retained = body
    # Where the marker goes in the retained text: the position of the first
    # dropped message, so the model sees the gap where the history was cut.
    marker_index = 0

    rounds = _dialogue_rounds(body)
    protected_from = max(0, len(rounds) - max(1, keep_last_rounds))
    droppable_ends = [end for index, (_, end) in enumerate(rounds) if index < protected_from and end is not None]
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
    while (
        _estimate(estimator, prefix, retained, marker_template, dropped_rounds, _dropped_token_hint(dropped), overhead)
        > target_tokens
    ):
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
            limit_source=limit_source,
            resolved_window_tokens=resolved_window_tokens,
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
            limit_source=limit_source,
            resolved_window_tokens=resolved_window_tokens,
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
        limit_source=limit_source,
        resolved_window_tokens=resolved_window_tokens,
    )


async def recover(
    engine: "ContextEngine",
    *,
    compress: Callable[..., Awaitable[bool]],
    ptl_config: PtlFallbackConfig,
    context_id: str,
    session: Optional["Session"],
    context: Optional["ModelContext"],
    exception: Exception,
    error_texts: Sequence[str],
    messages: Optional[Sequence[BaseMessage]],
    tools: Optional[Sequence[Any]],
    attempt: int,
    attempted_recoveries: Optional[List[str]],
    compression_kwargs: Dict[str, Any],
) -> RecoveryOutcome:
    """Run the two-phase PTL recovery for one failed model call.

    ``engine`` supplies persistence and pool lookup; ``compress`` is the
    engine's Phase A entry point, injected by the caller so this module does
    not reach into private engine methods. Keeping the module free of a hard
    import of the engine is what makes it removable.

    The returned :class:`RecoveryOutcome` carries only the phases attempted
    by this call. The input ``attempted_recoveries`` list is read, never
    mutated; the caller decides how to merge the trace.
    """
    tried = tuple(attempted_recoveries) if attempted_recoveries is not None else ()
    new_trace: List[str] = []
    phase_a_done = "phase_a_compress" in tried

    if ptl_config.phase_a_compress and not phase_a_done:
        new_trace.append("phase_a_compress")
        compressed = await compress(
            context_id=context_id,
            session=session,
            timeout_s=ptl_config.phase_a_timeout_s,
            swallow_errors=True,
            kwargs=compression_kwargs,
        )
        if compressed:
            return RecoveryOutcome(True, tuple(new_trace))

    if not ptl_config.enable_pure_code_truncate:
        return RecoveryOutcome(False, tuple(new_trace))

    truncated = await _recover_with_pure_code_truncation(
        engine,
        context_id=context_id,
        session=session,
        context=context,
        error_texts=error_texts,
        messages=messages,
        tools=tools,
        attempt=attempt,
        config=ptl_config,
    )
    if truncated:
        new_trace.append("phase_b_truncate")
    return RecoveryOutcome(truncated, tuple(new_trace))


async def _recover_with_pure_code_truncation(
    engine: "ContextEngine",
    *,
    context_id: str,
    session: Optional["Session"],
    context: Optional["ModelContext"],
    error_texts: Sequence[str],
    messages: Optional[Sequence[BaseMessage]],
    tools: Optional[Sequence[Any]],
    attempt: int,
    config: PtlFallbackConfig,
) -> bool:
    """Phase B: drop the oldest complete dialogue turns without a model."""
    target_context = context
    if target_context is None:
        resolved_session_id = session.get_session_id() if session else "default_session_id"
        target_context = engine.get_context(context_id=context_id, session_id=resolved_session_id)
    if target_context is None:
        logger.warning("model context recovery cannot find context %s", context_id)
        return False

    try:
        result = truncate_overflowed_context(
            target_context,
            error_texts=error_texts,
            attempt=attempt,
            target_ratio=config.truncate_target_ratio,
            keep_last_rounds=config.keep_last_rounds,
            archive_dropped_rounds=config.archive_dropped_rounds,
            marker_template=config.marker_template,
            messages=messages,
            tools=tools,
        )
    except Exception:  # noqa: BLE001 - keep the original model error visible
        logger.warning("model context recovery truncation failed", exc_info=True)
        return False

    if not result.changed:
        logger.info(
            "model context recovery found nothing to truncate, limit=%s source=%s target=%s estimated=%s",
            result.limit_tokens,
            result.limit_source,
            result.target_tokens,
            result.tokens_before,
        )
        return False

    await _persist_truncation(engine, session, target_context)

    logger.info(
        "model context recovery truncated context: rounds=%s messages=%s "
        "tool_exchanges=%s tokens=%s->%s limit=%s source=%s target=%s "
        "resolved_window=%s archive=%s",
        result.dropped_rounds,
        result.dropped_messages,
        result.dropped_tool_exchanges,
        result.tokens_before,
        result.tokens_after,
        result.limit_tokens,
        result.limit_source,
        result.target_tokens,
        result.resolved_window_tokens,
        result.archive_memory_id,
    )
    return True


async def _persist_truncation(engine: "ContextEngine", session: Optional["Session"], context: "ModelContext") -> None:
    """Persist the truncated context, tolerating storage failures."""
    effective_session = session or _context_session_ref(context)
    if effective_session is None:
        return

    if not _is_context_pooled(engine, effective_session, context):
        # Saving an unpooled context would rewrite the session's context state
        # with an empty payload, so persistence is skipped.
        logger.info(
            "model context recovery truncated context %s in memory; "
            "it is not owned by this engine, skipping persistence",
            context.context_id(),
        )
        return

    # The model retry can proceed from the in-memory context even when
    # persistence fails, so a storage problem must not undo the rescue.
    try:
        await engine.save_contexts(effective_session, context_ids=[context.context_id()])
        await effective_session.commit()
    except Exception:  # noqa: BLE001 - in-memory rescue still valid
        logger.warning(
            "model context recovery truncated in memory but failed to persist",
            exc_info=True,
        )


def _context_session_ref(context: "ModelContext") -> Optional["Session"]:
    getter = getattr(context, "get_session_ref", None)
    return getter() if callable(getter) else None


def _is_context_pooled(engine: "ContextEngine", session: "Session", context: "ModelContext") -> bool:
    """Return whether ``context`` is the engine-owned instance for ``session``."""
    try:
        session_id = session.get_session_id()
    except Exception:  # noqa: BLE001 - unknown session cannot own a pooled context
        return False
    return engine.get_context(context_id=context.context_id(), session_id=session_id) is context


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
        max(len(str(getattr(message, "content", "") or "")), 0) // _CHARS_PER_TOKEN_FALLBACK for message in dropped
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
        raw_rounds = find_all_dialogue_round(list(messages))
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


def _resolve_limit(context: "ModelContext", error_texts: Sequence[str]) -> Tuple[int, str]:
    parsed = parse_context_limit(error_texts)
    if parsed is not None:
        return parsed, "provider"
    window = _resolved_window_tokens(context)
    if window:
        return window, "context"
    return DEFAULT_CONTEXT_LIMIT_FALLBACK, "default"


def _resolved_window_tokens(context: "ModelContext") -> int:
    resolver = getattr(context, "context_window_tokens", None)
    if callable(resolver):
        try:
            value = int(resolver())
            if value > 0:
                return value
        except Exception:  # noqa: BLE001 - diagnostics must not block truncation
            pass
    return 0


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
        window = text[max(0, match.start() - 32) : match.end() + 32]
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

        expected = {str(getattr(tool_call, "id", "") or "") for tool_call in tool_calls if getattr(tool_call, "id", "")}
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
    context: "ModelContext",
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
    "DEFAULT_OVERFLOW_TRUNCATE_MARKER",
    "OverflowTruncationResult",
    "PtlFallbackConfig",
    "RecoveryOutcome",
    "TRUNCATE_MARKER_METADATA_KEY",
    "parse_context_limit",
    "recover",
    "truncate_overflowed_context",
]
