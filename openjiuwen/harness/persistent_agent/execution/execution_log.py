# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution log snapshot (PA-17): read one earlier Execution's saved events once and keep every usable one.

Each returned item is checked on its own. One that does not validate, belongs to another invocation, or
shares its local_seq with a different event is skipped and named in the header by a fixed category; the
other events stay exactly as saved. ``record_lines`` renders the snapshot losslessly as JSON lines, the
header first as line 0;
``preview`` renders the copy a continuation injects, where model usage and reply deltas are only counted
and long strings may be cut, every cut listed by its JSON path. Nothing here interprets the events:
whether a write's result is known is the Agent's call under the continuation rules (PA-18).
"""

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from ..schema.build_context import PersistentBuildContext
from ..schema.events import AgentEvent, EventType, ReplyDeltaPayload
from ..schema.protocol import Identifier

# The tool a continuation reads full records with (execution/record_tool.py).
RECORD_TOOL = "read_execution_record"
# Version of this rendering; the events keep their own schema_version.
ENCODING = "pa17-jsonl/1"
# AgentEvent.schema_version of the events this encoding renders.
EVENT_SCHEMA_VERSION = 1
# Longest string the injected preview keeps of one field; the snapshot and the record tool keep all of it.
PREVIEW_MAX_CHARS = 2000
# Frequent events the preview only counts; the record tool returns them.
COUNTED_ONLY = (EventType.MODEL_COMPLETED, EventType.REPLY_DELTA)
_IDENTITY = ("namespace_id", "agent_id", "session_id", "request_id")
# The same in every event of one log, so stated once in its header.
_HOISTED = (*_IDENTITY, "execution_id", "schema_version")
_ENDINGS = (EventType.EXECUTION_PAUSED, EventType.EXECUTION_FINAL)
_SKIP_KEYS = {"invalid_event": "indexes", "foreign_event": "indexes", "conflicting_events": "local_seqs"}
# Positions listed per skip category; the count is always exact.
_LISTED = 20
_EXECUTION_ID = TypeAdapter(Identifier)


@dataclass(frozen=True)
class LogSnapshot:
    """One read of one Execution's log: its usable events in local_seq order and what was left out.

    ``unusable`` says why nothing can be used (read_failed, not_a_sequence, empty, no_usable_event) and is
    None once one event is usable. ``skipped`` holds (category, position) pairs: the index in the returned
    sequence for an item that did not validate or belongs elsewhere, whose own local_seq is not trusted,
    and the local_seq of every event in a conflicting group. ``missing`` are the local_seq ranges no
    owned event has, computed before anything is left out of a rendering.
    """

    execution_id: str
    events: tuple[AgentEvent, ...] = ()
    unusable: str | None = None
    skipped: tuple[tuple[str, int], ...] = ()
    duplicates: int = 0
    missing: tuple[tuple[int, int], ...] = ()


async def read_snapshot(context: PersistentBuildContext, execution_id: str) -> LogSnapshot:
    """Read ``execution_id``'s saved events in this Request once and keep every usable one.

    Invalid arguments and cancellation propagate. A failed read, a return value that is not a sequence or
    a log without one usable event gives an unusable snapshot; that is a missing record, not proof that
    nothing ran.
    """
    if not isinstance(context, PersistentBuildContext):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="PersistentBuildContext required")
    try:
        _EXECUTION_ID.validate_python(execution_id, strict=True)
    except ValidationError:
        raise build_error(
            StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="execution_id must be an identifier"
        ) from None
    execution = context.execution
    try:
        returned = await context.ports.commit.read_execution_log(execution, execution_id)
    except Exception as exc:  # noqa: BLE001 - a failed read is a missing record; cancellation still propagates
        _diagnose(execution, execution_id, f"read failed ({type(exc).__name__})", broken=False)
        return LogSnapshot(execution_id, unusable="read_failed")
    # The port declares a tuple; a list is accepted as a tolerance, not as part of the contract.
    if not isinstance(returned, (tuple, list)):
        _diagnose(execution, execution_id, "log is not a sequence of events", broken=True)
        return LogSnapshot(execution_id, unusable="not_a_sequence")
    if not returned:
        _diagnose(execution, execution_id, "log is empty", broken=False)
        return LogSnapshot(execution_id, unusable="empty")
    snapshot = _snapshot(returned, execution, execution_id)
    if snapshot.skipped:
        counts = Counter(category for category, _ in snapshot.skipped)
        skipped = ", ".join(f"{category}={count}" for category, count in sorted(counts.items()))
        _diagnose(execution, execution_id, f"events skipped ({skipped})", broken=True)
    return snapshot


def _snapshot(returned, execution, execution_id: str) -> LogSnapshot:
    skipped: list[tuple[str, int]] = []
    by_seq: dict[int, list[AgentEvent]] = {}
    for index, item in enumerate(returned):
        try:
            event = AgentEvent.model_validate(item)
        except (ValidationError, TypeError, ValueError):
            skipped.append(("invalid_event", index))
            continue
        if _foreign(event, execution, execution_id):
            skipped.append(("foreign_event", index))
            continue
        by_seq.setdefault(event.local_seq, []).append(event)
    events: list[AgentEvent] = []
    duplicates = 0
    for seq in sorted(by_seq):
        group = by_seq[seq]
        # event_id hashes the invocation and local_seq, so one comparison per local_seq covers both.
        if len({event.proposal_digest() for event in group}) > 1:
            # Which of them was saved for real cannot be told; none is used.
            skipped.extend(("conflicting_events", seq) for _ in group)
            continue
        events.append(group[0])
        duplicates += len(group) - 1
    return LogSnapshot(
        execution_id,
        events=tuple(events),
        unusable=None if events else "no_usable_event",
        skipped=tuple(skipped),
        duplicates=duplicates,
        missing=_gaps(sorted(by_seq)),
    )


def _foreign(event: AgentEvent, execution, execution_id: str) -> bool:
    return event.execution_id != execution_id or any(
        getattr(event, name) != getattr(execution, name) for name in _IDENTITY
    )


def _gaps(seqs: list[int]) -> tuple[tuple[int, int], ...]:
    spans, expected = [], 1
    for seq in seqs:
        if seq > expected:
            spans.append((expected, seq - 1))
        expected = seq + 1
    return tuple(spans)


def _diagnose(execution, execution_id: str, category: str, *, broken: bool) -> None:
    """Category and locating identity only: no event content, exception text or validation detail.

    A log that breaks the port contract is an error; a failed read or an empty log is a warning.
    """
    log = logger.error if broken else logger.warning
    log(
        "[PersistentAgent] execution log not fully usable: session=%s request=%s execution=%s reason=%s",
        execution.session_id,
        execution.request_id,
        execution_id,
        category,
    )


def header(snapshot: LogSnapshot) -> dict[str, Any]:
    """The full record's first line: identity, encoding and what the log lacks, in a fixed shape."""
    return _header(snapshot, is_preview=False)


def _header(snapshot: LogSnapshot, *, is_preview: bool) -> dict[str, Any]:
    head: dict[str, Any] = {
        "kind": "execution_log",
        "encoding": ENCODING,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "preview": is_preview,
        "execution_id": snapshot.execution_id,
        "usable": snapshot.unusable is None,
    }
    if snapshot.unusable is not None:
        head["unusable_reason"] = snapshot.unusable
    if snapshot.events:
        first = snapshot.events[0]
        head.update({name: getattr(first, name) for name in _IDENTITY})
        head["source_ref"] = first.source_ref
        head["source_mismatch"] = any(event.source_ref != first.source_ref for event in snapshot.events)
    head["events"] = len(snapshot.events)
    head["missing_seq_ranges"] = [list(span) for span in snapshot.missing]
    head["skipped"] = _skipped(snapshot.skipped)
    head["duplicates_removed"] = snapshot.duplicates
    head["ending_seqs"] = [event.local_seq for event in snapshot.events if event.event_type in _ENDINGS]
    return head


def _skipped(skipped: tuple[tuple[str, int], ...]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[int]] = {}
    for category, position in skipped:
        grouped.setdefault(category, []).append(position)
    out = {}
    for category, positions in grouped.items():
        listed = list(dict.fromkeys(positions))
        out[category] = {"count": len(positions), _SKIP_KEYS[category]: listed[:_LISTED]}
    return out


def record_lines(snapshot: LogSnapshot) -> tuple[tuple[int, str], ...]:
    """The full record: the header as line 0, then every event as (local_seq, line); events start at 1."""
    return ((0, _dumps(header(snapshot))), *event_lines(snapshot))


def event_lines(snapshot: LogSnapshot) -> tuple[tuple[int, str], ...]:
    """Every usable event as (local_seq, one JSON line), in order and with nothing left out."""
    source, referenced = _source(snapshot), _referenced(snapshot.events)
    return tuple((event.local_seq, _dumps(_event(event, source, referenced))) for event in snapshot.events)


def preview(snapshot: LogSnapshot) -> str:
    """The copy a continuation injects: header, then the events, with counted events and cut strings listed.

    Every event except model usage and reply deltas is there; a string longer than PREVIEW_MAX_CHARS keeps
    its start only, and ``truncated`` names its event and JSON path. The snapshot itself is not changed.
    """
    source, referenced = _source(snapshot), _referenced(snapshot.events)
    lines: list[str] = []
    truncated: list[dict[str, Any]] = []
    counted: Counter[str] = Counter()
    delta_chars = 0
    for event in snapshot.events:
        if event.event_type in COUNTED_ONLY:
            counted[str(event.event_type)] += 1
            if isinstance(event.payload, ReplyDeltaPayload):
                delta_chars += len(event.payload.text)
            continue
        cut = _cut(_event(event, source, referenced), "", event.local_seq, truncated)
        lines.append(_dumps(cut))
    head = _header(snapshot, is_preview=True)
    head["counted_only"] = {
        str(EventType.MODEL_COMPLETED): {"events": counted[str(EventType.MODEL_COMPLETED)]},
        str(EventType.REPLY_DELTA): {"events": counted[str(EventType.REPLY_DELTA)], "chars": delta_chars},
    }
    head["truncated"] = truncated
    return "\n".join([_dumps(head), *lines])


def _source(snapshot: LogSnapshot) -> str | None:
    return snapshot.events[0].source_ref if snapshot.events else None


def _referenced(events: tuple[AgentEvent, ...]) -> set[str]:
    """Event IDs other events name; only those keep their event_id, which the Agent cannot recompute."""
    found: set[str] = set()
    for event in events:
        found.update(event.correlation_refs)
        decision = getattr(event.payload, "decision_event_id", None)
        if decision:
            found.add(decision)
    return found


def _event(event: AgentEvent, source: str | None, referenced: set[str]) -> dict[str, Any]:
    """The saved event without what its header already states; null and empty values are left out."""
    data = _pruned(event.model_dump(mode="json"))
    for name in _HOISTED:
        data.pop(name, None)
    if data.get("source_ref") == source:
        data.pop("source_ref")
    if data.get("event_id") not in referenced:
        data.pop("event_id", None)
    return data


def _pruned(value: Any) -> Any:
    """Drop null and empty collections only; 0, false and every other value stay."""
    if isinstance(value, dict):
        kept = {key: _pruned(item) for key, item in value.items() if item is not None}
        return {key: item for key, item in kept.items() if item not in ([], {})}
    if isinstance(value, list):
        return [_pruned(item) for item in value]
    return value


def _cut(value: Any, path: str, seq: int, truncated: list[dict[str, Any]]) -> Any:
    """A copy whose long strings keep PREVIEW_MAX_CHARS characters, each cut listed by JSON Pointer."""
    if isinstance(value, dict):
        return {key: _cut(item, f"{path}/{_escape(key)}", seq, truncated) for key, item in value.items()}
    if isinstance(value, list):
        return [_cut(item, f"{path}/{index}", seq, truncated) for index, item in enumerate(value)]
    if isinstance(value, str) and len(value) > PREVIEW_MAX_CHARS:
        truncated.append(
            {"local_seq": seq, "path": path, "original_chars": len(value), "included_chars": PREVIEW_MAX_CHARS}
        )
        return value[:PREVIEW_MAX_CHARS]
    return value


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
