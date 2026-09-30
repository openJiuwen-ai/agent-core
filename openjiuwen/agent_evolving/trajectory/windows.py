# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Replay the context windows a trajectory's v2 events state.

Every model request commits the window it was sent as a
``context.window.commit`` event. Only an epoch baseline states the whole
window; every later commit states its change against the window before it,
and a message keeps one ``message_id`` for as long as it stays in context.
Replaying that chain answers what a model actually read, and whether two
requests read the same message, by identity rather than by comparing text.

The replay follows the web trajectory viewer's reducer so the two readers
agree on the same records: events are ordered per execution subject by
sequence epoch and subject sequence, deltas apply operation by operation,
and the issue codes carry the viewer's names. The vectors under
``tests/unit_tests/agent_evolving/trajectory/fixtures/v2`` pin that contract.

Everything here is a pure function of the spans it is given.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeGuard

from openjiuwen.agent_evolving.trajectory.spans import (
    Span,
    attributes_from_map,
    iter_spans,
    normalize_otlp,
    span_attributes,
    span_sort_key,
    trim_trajectory,
)
from openjiuwen.extensions.observability import semconv

CONTEXT_WINDOW_COMMIT = "context.window.commit"
COMPACTION_COMPLETED = "compaction.completed"

# Baselines state a complete window and no base. ``trim_baseline`` is the
# evolution window's own: it restates the window a retained commit applies
# onto, after the spans that built that window were trimmed away.
EPOCH_BASELINE = "epoch_baseline"
TRIM_BASELINE = "trim_baseline"
_BASELINE_REASONS = MappingProxyType({EPOCH_BASELINE: "runtime_epoch_start", TRIM_BASELINE: "trimmed_window"})
_DELTA_OPERATIONS = frozenset({"insert", "remove", "move", "replace"})
_MESSAGE_ORIGINS = frozenset({"external_user", "harness_internal"})


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    """One v2 trajectory event read from its span."""

    span: Span
    event_id: str
    event_kind: str
    subject_id: str
    sequence_epoch: str
    sequence: int
    recorded_at: int
    payload: Mapping[str, Any]

    @property
    def span_id(self) -> str:
        return str(self.span.get("spanId") or "")

    @property
    def parent_span_id(self) -> str:
        return str(self.span.get("parentSpanId") or "")


@dataclass(frozen=True)
class WindowReplay:
    """The windows a trajectory's commit chains rebuild to.

    Attributes:
        windows: Per subject, every rebuilt window by ``window_id``.
        by_inference: The window each model request read, keyed by the span id
            of the inference span its commit hangs off. Compaction commits are
            not here: the window a compaction produces was read by no request
            of its own.
        issues: Chain defects, in replay order.
    """

    windows: Mapping[str, Mapping[str, tuple[dict[str, Any], ...]]] = field(default_factory=dict)
    by_inference: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    issues: tuple[Mapping[str, object], ...] = ()


def _issue(code: str, message: str, event: TrajectoryEvent | None = None) -> Mapping[str, object]:
    data: dict[str, object] = {"code": code, "message": message}
    if event is not None:
        data.update(
            {
                "subject_id": event.subject_id,
                "event_id": event.event_id,
                "sequence": event.sequence,
            }
        )
    return MappingProxyType(data)


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _non_empty_str(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _has_text(value: Any) -> TypeGuard[str]:
    """Whether a value is a string with non-whitespace content."""
    return isinstance(value, str) and bool(value.strip())


def _declared_conflict(declared: Any, stated: Any) -> bool:
    """Whether a compaction declares a window id that differs from the stated one."""
    return isinstance(declared, str) and declared != stated


def read_trajectory_event(span: Mapping[str, Any]) -> TrajectoryEvent | None:
    """Return the v2 event a span states, or None for any other span.

    A span is an event when it states ``openjiuwen.trajectory.event_kind``. An
    event whose envelope is incomplete is still not readable and returns None.
    """

    attributes = span_attributes(span)
    event_kind = attributes.get(semconv.OJ_TRAJECTORY_EVENT_KIND)
    if not isinstance(event_kind, str) or not event_kind:
        return None
    event_id = attributes.get(semconv.OJ_TRAJECTORY_EVENT_ID)
    subject_id = attributes.get(semconv.OJ_TRAJECTORY_SUBJECT_ID)
    sequence_epoch = attributes.get(semconv.OJ_TRAJECTORY_SEQUENCE_EPOCH)
    sequence = _positive_int(attributes.get(semconv.OJ_TRAJECTORY_SUBJECT_SEQUENCE))
    recorded_at = _positive_int(attributes.get(semconv.OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO))
    raw_payload = attributes.get(semconv.OJ_TRAJECTORY_PAYLOAD)
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except ValueError:
        payload = None
    if not _non_empty_str(event_id) or not _non_empty_str(subject_id) or not _non_empty_str(sequence_epoch):
        return None
    if sequence is None or recorded_at is None or not isinstance(payload, Mapping):
        return None
    return TrajectoryEvent(
        span=dict(span),
        event_id=event_id,
        event_kind=event_kind,
        subject_id=subject_id,
        sequence_epoch=sequence_epoch,
        sequence=sequence,
        recorded_at=recorded_at,
        payload=MappingProxyType(dict(payload)),
    )


def iter_trajectory_events(value: Any) -> list[TrajectoryEvent]:
    """Return every readable v2 event of a trajectory or span iterable.

    An event is either a span of its own (window commits, compactions) or a
    span event recorded on a short-lived owner span (``ask_user``); the latter
    reads with its owner's attributes under its own, as the viewer reads it.
    """

    spans = iter_spans(value) if not _is_span_iterable(value) else value
    events: list[TrajectoryEvent] = []
    for span in spans:
        event = read_trajectory_event(span)
        if event is not None:
            events.append(event)
            continue
        for span_event in span.get("events") or ():
            if not isinstance(span_event, Mapping):
                continue
            logged = read_trajectory_event(
                {
                    **span,
                    "name": span_event.get("name"),
                    "attributes": attributes_from_map(
                        {**span_attributes(span), **span_attributes(span_event)}
                    ),
                    "events": [],
                }
            )
            if logged is not None:
                events.append(logged)
    return events


def _is_span_iterable(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _ordered_subject_events(events: Sequence[TrajectoryEvent]) -> list[TrajectoryEvent]:
    """Order one subject's events: epochs by first record time, then sequence."""

    by_epoch: dict[str, list[TrajectoryEvent]] = {}
    for event in events:
        by_epoch.setdefault(event.sequence_epoch, []).append(event)
    epochs = []
    for epoch, epoch_events in by_epoch.items():
        ordered = sorted(epoch_events, key=lambda item: (item.sequence, item.recorded_at, item.event_id))
        epochs.append((min(item.recorded_at for item in ordered), epoch, ordered))
    epochs.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, ordered in epochs for event in ordered]


def _context_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    message_id = value.get("message_id")
    role = value.get("role")
    source_kind = value.get("source_kind")
    if not _has_text(message_id) or not _has_text(role) or value.get("origin") not in _MESSAGE_ORIGINS:
        return None
    if source_kind is not None and not _has_text(source_kind):
        return None
    return deepcopy(dict(value))


def _context_delta(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("op") not in _DELTA_OPERATIONS:
        return None
    message_id = value.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        return None
    result: dict[str, Any] = {"op": value["op"], "message_id": message_id}
    for key in ("index", "from_index"):
        index = value.get(key)
        if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
            result[key] = index
    if "message" in value:
        message = _context_message(value["message"])
        if message is None:
            return None
        result["message"] = message
    if result["op"] in {"insert", "replace"} and "message" not in result:
        return None
    return result


def _commit_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate a window commit payload; None when it cannot be replayed."""

    window_id = payload.get("window_id")
    base_window_id = payload.get("base_window_id")
    delta = payload.get("delta")
    if not _has_text(window_id) or payload.get("complete") is not True or not isinstance(delta, list):
        return None
    if base_window_id is not None and not isinstance(base_window_id, str):
        return None
    transition_kind = payload.get("transition_kind")
    baseline = transition_kind in _BASELINE_REASONS
    messages = payload.get("messages")
    if baseline and not isinstance(messages, list):
        return None
    if messages is not None and not isinstance(messages, list):
        return None
    for key in (
        "request_purpose",
        "baseline_reason",
        "correlation_kind",
        "transition_kind",
        "caused_by_operation_id",
        "output_window_id",
    ):
        if payload.get(key) is not None and not isinstance(payload.get(key), str):
            return None
    input_window_id = payload.get("input_window_id")
    if input_window_id is not None and not isinstance(input_window_id, str):
        return None
    parsed_messages = None if messages is None else [_context_message(item) for item in messages]
    parsed_delta = [_context_delta(item) for item in delta]
    if (parsed_messages is not None and any(item is None for item in parsed_messages)) or any(
        item is None for item in parsed_delta
    ):
        return None
    if parsed_messages is not None:
        ids = [item["message_id"] for item in parsed_messages]  # type: ignore[index]
        if len(set(ids)) != len(ids):
            return None
    # A baseline states its own reason; any other commit states none.
    if payload.get("baseline_reason") != _BASELINE_REASONS.get(transition_kind):
        return None
    if baseline and (base_window_id is not None or delta):
        return None
    result = dict(payload)
    result["delta"] = parsed_delta
    if parsed_messages is not None:
        result["messages"] = parsed_messages
    return result


def apply_context_delta(
    base: Sequence[Mapping[str, Any]],
    operations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Apply one commit's delta onto the window it is based on.

    Operations apply in order, each against the window the previous one left:
    an insert places a new message at its index, a remove drops a message
    wherever it now is, a replace swaps content in place, and a move lifts a
    message out and reinserts it at its index.

    Returns:
        The rebuilt window, or None when an operation does not fit the window.
    """

    messages = [dict(message) for message in base]
    for operation in operations:
        message_id = operation.get("message_id")
        current = next((i for i, message in enumerate(messages) if message.get("message_id") == message_id), -1)
        op = operation.get("op")
        index = operation.get("index")
        if op == "insert":
            message = operation.get("message")
            if message is None or current != -1:
                return None
            if index is None or index > len(messages):
                return None
            messages.insert(index, deepcopy(dict(message)))
            continue
        if current == -1:
            return None
        if op == "remove":
            messages.pop(current)
            continue
        if op == "replace":
            message = operation.get("message")
            if message is None:
                return None
            messages[current] = deepcopy(dict(message))
            continue
        if index is None or index >= len(messages):
            return None
        moved = messages.pop(current)
        messages.insert(index, moved)
    return messages


def _same_window(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> bool:
    def canonical(value: Sequence[Mapping[str, Any]]) -> str:
        return json.dumps(list(value), ensure_ascii=False, sort_keys=True, default=str)

    return canonical(left) == canonical(right)


def _matches_checkpoint(
    rebuilt: Sequence[Mapping[str, Any]] | None,
    stated: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Whether a replayed delta fits and agrees with the window its commit states, if any."""
    return rebuilt is not None and (stated is None or _same_window(rebuilt, stated))


def _is_compaction_commit(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("transition_kind") == "compaction" or payload.get("correlation_kind") == "compaction"
    ) and isinstance(payload.get("model_requests"), list)


def _compaction_correlation_issue(
    event: TrajectoryEvent,
    payload: Mapping[str, Any],
    compactions: Mapping[str, TrajectoryEvent | None],
) -> str | None:
    """Explain why a commit's stated compaction correlation cannot hold."""

    explicit = any(
        key in payload
        for key in ("caused_by_operation_id", "input_window_id", "output_window_id")
    ) or "compaction" in (payload.get("transition_kind"), payload.get("correlation_kind"))
    if not explicit:
        return None
    operation_id = str(payload.get("caused_by_operation_id") or "").strip()
    input_window_id = payload.get("input_window_id")
    output_window_id = str(payload.get("output_window_id") or "").strip()
    compaction_kind = "compaction" in (payload.get("transition_kind"), payload.get("correlation_kind"))
    missing_ids = not operation_id or not output_window_id
    blank_input = input_window_id is not None and not str(input_window_id).strip()
    if not compaction_kind or missing_ids or blank_input:
        return "Explicit compaction correlation is incomplete."
    if input_window_id != payload.get("base_window_id") or output_window_id != payload.get("window_id"):
        return "Explicit compaction correlation conflicts with the context window transition."
    if operation_id not in compactions:
        return f"Compaction operation {operation_id} is not available for this output window."
    compaction = compactions[operation_id]
    if compaction is None:
        return f"Compaction operation {operation_id} is ambiguous."
    if compaction.sequence >= event.sequence:
        return f"Compaction operation {operation_id} does not precede its output window."
    declared_input = compaction.payload.get("input_window_id")
    declared_output = compaction.payload.get("output_window_id")
    input_conflict = input_window_id is not None and _declared_conflict(declared_input, input_window_id)
    if input_conflict or _declared_conflict(declared_output, output_window_id):
        return f"Compaction operation {operation_id} conflicts with its declared input/output windows."
    return None


def _replay_subject(
    events: Sequence[TrajectoryEvent],
    windows: dict[str, tuple[dict[str, Any], ...]],
    by_inference: dict[str, tuple[dict[str, Any], ...]],
    issues: list[Mapping[str, object]],
) -> None:
    seen: dict[tuple[str, int], TrajectoryEvent] = {}
    expected_by_epoch: dict[str, int] = {}
    blocked_epochs: set[str] = set()
    compactions: dict[str, TrajectoryEvent | None] = {}
    for event in _ordered_subject_events(events):
        position = (event.sequence_epoch, event.sequence)
        if position in seen:
            if seen[position].event_id != event.event_id:
                issues.append(
                    _issue(
                        "v2.sequence_conflict",
                        "Two different events use the same sequence within one epoch; the first was kept.",
                        event,
                    )
                )
            continue
        seen[position] = event
        is_commit = event.event_kind == CONTEXT_WINDOW_COMMIT
        expected = expected_by_epoch.get(event.sequence_epoch)
        if event.sequence_epoch in blocked_epochs:
            if not is_commit:
                continue
            blocked_epochs.discard(event.sequence_epoch)
            expected = event.sequence
        if expected is not None and event.sequence > expected:
            issues.append(
                _issue(
                    "v2.sequence_gap",
                    f"Expected subject sequence {expected} but received {event.sequence}.",
                    event,
                )
            )
            if not is_commit:
                blocked_epochs.add(event.sequence_epoch)
                continue
        expected_by_epoch[event.sequence_epoch] = event.sequence + 1

        if event.event_kind == COMPACTION_COMPLETED:
            operation_id = str(event.payload.get("operation_id") or "").strip()
            if operation_id:
                compactions[operation_id] = None if operation_id in compactions else event
            continue
        if not is_commit:
            continue
        restated = event.payload.get("transition_kind") == TRIM_BASELINE
        if not restated and not _is_compaction_commit(event.payload) and not event.parent_span_id:
            # A request's commit hangs off the inference that sent the window;
            # a compaction's names its requests in its payload instead.
            issues.append(
                _issue(
                    "v2.missing_physical_request",
                    "Context commit is missing its physical inference parent.",
                    event,
                )
            )
            continue

        payload = _commit_payload(event.payload)
        if payload is None:
            issues.append(_issue("v2.invalid_context_commit", "Invalid context.window.commit payload.", event))
            continue
        base_window_id = payload.get("base_window_id")
        base = () if base_window_id is None else windows.get(base_window_id)
        if base_window_id is not None and base is None:
            issues.append(
                _issue(
                    "v2.missing_base_window",
                    f"Base context window {base_window_id} is not available.",
                    event,
                )
            )
        baseline = payload.get("transition_kind") in _BASELINE_REASONS
        stated = payload.get("messages")
        rebuilt = None if base is None or baseline else apply_context_delta(base, payload["delta"])
        if base is not None and not baseline and not _matches_checkpoint(rebuilt, stated):
            issues.append(
                _issue(
                    "v2.delta_checkpoint_mismatch",
                    "Context delta does not reconstruct its complete checkpoint.",
                    event,
                )
            )
            continue
        window = stated if stated is not None else rebuilt
        if window is None:
            # No complete window stated and the base the delta applies onto was
            # never read: the chain is broken at this commit.
            continue
        frozen_window = tuple(window)
        windows[payload["window_id"]] = frozen_window
        correlation = _compaction_correlation_issue(event, payload, compactions)
        if correlation is not None:
            issues.append(_issue("v2.invalid_compaction_correlation", correlation, event))
        if not restated and not _is_compaction_commit(payload) and event.parent_span_id:
            by_inference[event.parent_span_id] = frozen_window


def replay_windows(value: Any) -> WindowReplay:
    """Replay every subject's commit chain in a trajectory.

    Args:
        value: A ``Trajectory``, an OTLP mapping, or a list of spans.

    Returns:
        The rebuilt windows, the window each model request read, and the
        chain defects found along the way.
    """

    by_subject: dict[str, list[TrajectoryEvent]] = {}
    for event in iter_trajectory_events(value):
        by_subject.setdefault(event.subject_id, []).append(event)
    windows: dict[str, dict[str, tuple[dict[str, Any], ...]]] = {}
    by_inference: dict[str, tuple[dict[str, Any], ...]] = {}
    issues: list[Mapping[str, object]] = []
    for subject_id, subject_events in sorted(by_subject.items(), key=lambda item: item[0]):
        subject_windows = windows.setdefault(subject_id, {})
        _replay_subject(subject_events, subject_windows, by_inference, issues)
    return WindowReplay(windows=windows, by_inference=by_inference, issues=tuple(issues))


def window_for_inference(replay: WindowReplay, span: Mapping[str, Any]) -> tuple[dict[str, Any], ...] | None:
    """Return the window one inference span was sent, or None if none was committed."""

    span_id = str(span.get("spanId") or "")
    return replay.by_inference.get(span_id) if span_id else None


def _synthetic_span_id(window_id: str, subject_id: str) -> str:
    return hashlib.sha256(f"trim_baseline\0{subject_id}\0{window_id}".encode()).hexdigest()[:16]


def _trim_baseline_span(
    first: TrajectoryEvent,
    base_window_id: str,
    window: Sequence[Mapping[str, Any]],
    sequence: int,
) -> Span:
    """Restate the window a retained commit applies onto as a baseline event."""

    attributes = span_attributes(first.span)
    payload = {
        "window_id": base_window_id,
        "base_window_id": None,
        "complete": True,
        "delta": [],
        "messages": [deepcopy(dict(message)) for message in window],
        "transition_kind": TRIM_BASELINE,
        "baseline_reason": _BASELINE_REASONS[TRIM_BASELINE],
    }
    attributes.update(
        {
            semconv.OJ_TRAJECTORY_EVENT_ID: f"trim:{first.subject_id}:{base_window_id}",
            semconv.OJ_TRAJECTORY_EVENT_KIND: CONTEXT_WINDOW_COMMIT,
            semconv.OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
            semconv.OJ_TRAJECTORY_PAYLOAD: json.dumps(payload, ensure_ascii=False, default=str),
        }
    )
    start = max(0, int(first.span.get("startTimeUnixNano") or 0) - 1)
    span: Span = {
        "traceId": first.span.get("traceId"),
        "spanId": _synthetic_span_id(base_window_id, first.subject_id),
        "name": CONTEXT_WINDOW_COMMIT,
        "kind": first.span.get("kind", "SPAN_KIND_INTERNAL"),
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start),
        "attributes": attributes_from_map(attributes),
        "status": {"code": "STATUS_CODE_OK"},
    }
    return span


def rebase_window_chain(history: Any, retained: Any) -> Any:
    """Give every commit chain in ``retained`` a replayable head.

    ``retained`` is a subset of ``history`` -- a trimmed evolution window, say.
    When the first commit a subject keeps is a delta whose base window was
    built by spans that did not survive, the base is restated as a
    ``trim_baseline`` commit placed just before it, taken from replaying
    ``history``. Nothing else changes.

    Returns:
        A new ``Trajectory`` (or ``retained`` itself when no chain needed a
        head).
    """

    from openjiuwen.agent_evolving.trajectory.model import Trajectory

    retained_events = iter_trajectory_events(retained)
    if not any(event.event_kind == CONTEXT_WINDOW_COMMIT for event in retained_events):
        return retained
    history_replay: WindowReplay | None = None
    additions: list[Span] = []
    by_subject: dict[str, list[TrajectoryEvent]] = {}
    for event in retained_events:
        by_subject.setdefault(event.subject_id, []).append(event)
    for subject_id, events in by_subject.items():
        ordered = _ordered_subject_events(events)
        first_commit = next((event for event in ordered if event.event_kind == CONTEXT_WINDOW_COMMIT), None)
        if first_commit is None:
            continue
        base_window_id = first_commit.payload.get("base_window_id")
        if not isinstance(base_window_id, str) or first_commit.payload.get("transition_kind") in _BASELINE_REASONS:
            continue
        if history_replay is None:
            history_replay = replay_windows(history)
        window = history_replay.windows.get(subject_id, {}).get(base_window_id)
        if window is None:
            continue
        epoch_sequences = [
            event.sequence for event in ordered if event.sequence_epoch == first_commit.sequence_epoch
        ]
        additions.append(_trim_baseline_span(first_commit, base_window_id, window, min(epoch_sequences) - 1))
    if not additions:
        return retained
    payload = normalize_otlp(retained.to_otlp() if hasattr(retained, "to_otlp") else retained)
    resource_spans = payload.get("resourceSpans") or []
    if not resource_spans:
        return retained
    scopes = resource_spans[0].setdefault("scopeSpans", [])
    if not scopes:
        scopes.append({"scope": {}, "spans": []})
    scopes[0]["spans"] = sorted([*additions, *scopes[0].get("spans", [])], key=span_sort_key)
    return Trajectory.from_otlp(payload)


def is_trajectory_event_span(span: Mapping[str, Any]) -> bool:
    """Whether a span is a v2 trajectory event rather than work."""

    attributes = span_attributes(span)
    return (
        attributes.get(semconv.OJ_TRAJECTORY_RECORD_KIND) == "event"
        or semconv.OJ_TRAJECTORY_EVENT_KIND in attributes
    )


def trim_trajectory_window(value: Any, max_spans: int | None) -> Any:
    """Bound a trajectory to its newest ``max_spans`` spans of work.

    Events do not spend the budget: they are kept alongside the work they
    describe, and every commit chain the trim cut into is given a restated
    baseline, so the bounded window still replays every request it keeps.
    """

    trimmed = trim_trajectory(value, max_spans, uncounted=is_trajectory_event_span)
    return rebase_window_chain(value, trimmed)


__all__ = [
    "COMPACTION_COMPLETED",
    "CONTEXT_WINDOW_COMMIT",
    "EPOCH_BASELINE",
    "TRIM_BASELINE",
    "TrajectoryEvent",
    "WindowReplay",
    "apply_context_delta",
    "is_trajectory_event_span",
    "iter_trajectory_events",
    "read_trajectory_event",
    "rebase_window_chain",
    "replay_windows",
    "trim_trajectory_window",
    "window_for_inference",
]
