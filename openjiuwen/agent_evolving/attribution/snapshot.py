# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory snapshot helpers: node building, range slicing, node-id mapping.

The host re-persists the session's raw trajectory records into a
``trajectory_snapshot.json`` (one entry per execution subject); this module
projects those records into :class:`TrajectoryNode` rows and keeps
``node_ids.json`` mappings stable across snapshot refreshes.

Nothing here imports the host; every function is pure over the inputs.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence
from typing import Any

from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, detect_language, user_request_nodes
from openjiuwen.agent_evolving.trajectory import messages as trajectory_messages
from openjiuwen.agent_evolving.trajectory.messages import MessageField, tool_call_id
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import (
    is_compaction_span,
    normalize_otlp,
    read_llm_exchange,
    read_tool_call,
    span_attributes,
    span_identity,
    span_status,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.agent_evolving.trajectory.windows import (
    CONTEXT_WINDOW_COMMIT,
    iter_trajectory_events,
)
from openjiuwen.extensions.observability import semconv

SNAPSHOT_SCHEMA_VERSION = 1

_NODE_FIELDS: frozenset[MessageField] = frozenset({"content", "name", "tool_calls", "tool_call_id"})


def _subject_trajectory(records: Sequence[Mapping[str, Any]], session_id: str) -> Trajectory:
    payload: dict[str, Any] = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": TRAJECTORY_ID, "value": {"stringValue": f"{session_id}-snapshot"}},
                    ]
                },
                "scopeSpans": [{"spans": list(records)}],
            }
        ]
    }
    return Trajectory.from_otlp(normalize_otlp(payload))


def _time(span: Mapping[str, Any], field: str) -> int:
    try:
        return int(span.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def _message_signature(message: Mapping[str, Any]) -> tuple[str, str, str]:
    """Signature of one window message: (role, content-json, tool-call-ids)."""
    role = str(message.get("role") or "unknown")
    if role == "tool":
        return role, "", str(message.get("tool_call_id") or "")
    content = message.get("content")
    content_json = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str) if content is not None else ""
    call_ids = tuple(
        sorted(str(tool_call_id(call)) for call in message.get("tool_calls") or () if tool_call_id(call) is not None)
    )
    return role, content_json, ",".join(call_ids)


def _commit_window_messages(
    records: Sequence[Mapping[str, Any]],
) -> list[tuple[tuple[str, str, str], Mapping[str, Any], Mapping[str, Any]]]:
    """Return ``(signature, message, span)`` for every committed window message."""
    result: list[tuple[tuple[str, str, str], Mapping[str, Any], Mapping[str, Any]]] = []
    for event in iter_trajectory_events(list(records)):
        if event.event_kind != CONTEXT_WINDOW_COMMIT:
            continue
        messages: list[Mapping[str, Any]] = []
        stated = event.payload.get("messages")
        if isinstance(stated, list):
            messages.extend(stated)
        else:
            for operation in event.payload.get("delta") or ():
                if isinstance(operation, Mapping) and operation.get("op") == "insert":
                    message = operation.get("message")
                    if isinstance(message, Mapping):
                        messages.append(message)
        for message in messages:
            if isinstance(message, Mapping) and message.get("message_id"):
                result.append((_message_signature(message), message, event.span))
    return result


def _tool_source_index(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str | None, str | None, str, int, int, bool]]:
    """Map call_id -> (trace_id, span_id, call_id, start_ns, end_ns, error)."""
    index: dict[str, tuple[str | None, str | None, str, int, int, bool]] = {}
    for span in records:
        if not isinstance(span, Mapping):
            continue
        if span_category(span) != "tool":
            continue
        call = read_tool_call(span)
        call_id = call.get("id")
        if not call_id:
            continue
        identity = span_identity(span)
        trace_id, span_id = identity if identity is not None else (None, None)
        status_code = str(span_status(span).get("code") or "").upper()
        errored = status_code in {"ERROR", "STATUS_CODE_ERROR"} or call.get("error") is not None
        index[str(call_id)] = (
            trace_id,
            span_id,
            str(call_id),
            _time(span, "startTimeUnixNano"),
            _time(span, "endTimeUnixNano"),
            errored,
        )
    return index


def _llm_source_index(
    records: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, tuple[str | None, str | None, int, int]],
    dict[tuple[str, str, str], tuple[str | None, str | None, int, int]],
]:
    """Map llm span outputs to their span identity by call id and by signature."""
    by_call: dict[str, tuple[str | None, str | None, int, int]] = {}
    by_signature: dict[tuple[str, str, str], tuple[str | None, str | None, int, int]] = {}
    for span in records:
        if not isinstance(span, Mapping):
            continue
        if span_category(span) != "llm" or is_compaction_span(span):
            continue
        identity = span_identity(span)
        trace_id, span_id = identity if identity is not None else (None, None)
        start_ns = _time(span, "startTimeUnixNano")
        end_ns = _time(span, "endTimeUnixNano")
        _, completions = read_llm_exchange(span)
        for message in completions:
            for call in message.get("tool_calls") or ():
                call_id = tool_call_id(call)
                if call_id is not None:
                    by_call.setdefault(str(call_id), (trace_id, span_id, start_ns, end_ns))
            signature = _message_signature(message)
            if message.get("role") == "assistant" and signature[1]:
                by_signature.setdefault(signature, (trace_id, span_id, start_ns, end_ns))
    return by_call, by_signature


def _unwrap_span_io(value: Any) -> str | None:
    """Normalize a turn span's input/output attribute into plain text.

    Some producers wrap the payload as a one-key mapping (``{'output': ...}``
    or ``{'input': ...}``); both the Python-literal string form and an
    already-decoded mapping are unwrapped, anything else passes through.
    """
    if isinstance(value, Mapping) and len(value) == 1:
        only_key = next(iter(value))
        if only_key in {"input", "output"} and isinstance(value[only_key], str):
            return value[only_key] or None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, Mapping) and len(parsed) == 1:
            only_key = next(iter(parsed))
            if only_key in {"input", "output"} and isinstance(parsed[only_key], str):
                return parsed[only_key] or None
    return value or None


def _turn_io(
    span: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Return the ``(input, output)`` texts of one ``record.kind=turn`` span."""
    attributes = span_attributes(span)
    if attributes.get(semconv.OJ_TRAJECTORY_RECORD_KIND) != "turn":
        return None, None
    input_text = _unwrap_span_io(attributes.get(semconv.OJ_SPAN_INPUT))
    output_text = _unwrap_span_io(attributes.get(semconv.OJ_SPAN_OUTPUT))
    return input_text, output_text


def _append_turn_fallback_nodes(
    nodes: list[TrajectoryNode],
    records: Sequence[Mapping[str, Any]],
    *,
    subject_id: str,
    display_name: str,
    kind: str,
) -> list[TrajectoryNode]:
    """Append turn-span IO messages the window commit chain never saw.

    A turn whose final reply (or user message) never entered any
    ``context.window.commit`` only survives in its turn span's
    ``openjiuwen.span.input`` / ``openjiuwen.span.output`` attributes; those
    are projected here as user/assistant nodes.  Content that already exists
    among the projected nodes (the normal case, where the commit did land)
    is deduplicated so no message appears twice.
    """
    existing: set[tuple[str, str]] = {(node.role, node.content or "") for node in nodes if node.content is not None}
    fallback: list[TrajectoryNode] = []
    for span in records:
        if not isinstance(span, Mapping):
            continue
        input_text, output_text = _turn_io(span)
        identity = span_identity(span)
        trace_id, span_id = identity if identity is not None else (None, None)
        start_ns = _time(span, "startTimeUnixNano")
        end_ns = _time(span, "endTimeUnixNano")
        # The input message arrived when the turn started; the output reply
        # is produced when the turn ends, so each uses its own edge of the
        # turn span to keep the node chronologically placed.
        for role, text, ts_ns in (
            ("user", input_text, start_ns),
            ("assistant", output_text, end_ns),
        ):
            if not text:
                continue
            if (role, text) in existing:
                continue
            existing.add((role, text))
            fallback.append(
                TrajectoryNode(
                    node_id="",
                    subagent_key=subject_id,
                    display_name=display_name,
                    kind=kind,
                    role=role,
                    content=text,
                    tool_calls=[],
                    trace_id=trace_id,
                    span_id=span_id,
                    start_ts_ns=ts_ns,
                    end_ts_ns=ts_ns,
                    source_kind="turn",
                )
            )
    if not fallback:
        return nodes
    # Stable sort: projected nodes keep their relative order on ties, and a
    # fallback node sorts after commit-derived nodes sharing its timestamp.
    combined = [(node, 0) for node in nodes] + [(node, 1) for node in fallback]
    combined.sort(key=lambda item: (item[0].start_ts_ns, item[0].end_ts_ns, item[1]))
    return [node for node, _ in combined]


def build_subject_nodes(
    records: Sequence[Mapping[str, Any]],
    *,
    subject_id: str,
    display_name: str,
    kind: str,
    session_id: str = "snapshot",
) -> list[TrajectoryNode]:
    """Project one subject's records into ordered trajectory nodes.

    Messages come from the canonical window projection; per-node source ids
    (commit span for message rows, tool span for tool rows, llm span for
    unconfirmed completions) are attached for host lookups and frontend
    highlighting.  Messages that never reached a window commit (a turn's
    final reply) are recovered from the turn spans' ``span.input/output``
    attributes as a fallback.
    """
    trajectory = _subject_trajectory(records, session_id)
    projected, _ = trajectory_messages.project_trajectory_messages(trajectory, fields=_NODE_FIELDS)

    window_entries = _commit_window_messages(records)
    message_sources: dict[tuple[str, str, str], tuple[str, str | None, str | None, int, int]] = {}
    for signature, message, span in window_entries:
        identity = span_identity(span)
        commit_trace_id, commit_span_id = identity if identity is not None else (None, None)
        message_sources.setdefault(
            signature,
            (
                str(message.get("message_id") or ""),
                commit_trace_id,
                commit_span_id,
                _time(span, "startTimeUnixNano"),
                _time(span, "endTimeUnixNano"),
            ),
        )
    tool_index = _tool_source_index(records)
    llm_by_call, llm_by_signature = _llm_source_index(records)

    used_signatures: set[tuple[str, str, str]] = set()
    nodes: list[TrajectoryNode] = []
    for message in projected:
        role = str(message.get("role") or "unknown")
        signature = _message_signature(message)
        message_id: str | None = None
        trace_id: str | None = None
        span_id: str | None = None
        call_id: str | None = None
        start_ns = 0
        end_ns = 0
        source_kind = "msg"
        errored = False
        if role == "tool" and signature[2] and signature[2] in tool_index:
            trace_id, span_id, call_id, start_ns, end_ns, errored = tool_index[signature[2]]
            source_kind = "tool"
        elif signature in message_sources and signature not in used_signatures:
            message_id, trace_id, span_id, start_ns, end_ns = message_sources[signature]
            used_signatures.add(signature)
            source_kind = "msg"
            if role == "assistant" and signature in llm_by_signature:
                # A reply's commit can land much later than the reply itself:
                # the window is committed when the next user turn arrives, so
                # a commit-derived timestamp would push the answer past the
                # time the user selected on the axis.  The llm span records
                # when the reply was actually produced; prefer that window
                # while keeping the commit's identity ids.
                _, _, produced_start, produced_end = llm_by_signature[signature]
                start_ns, end_ns = produced_start, produced_end
        elif role == "assistant":
            source_kind = "llm"
            call_ids = [
                str(tool_call_id(call)) for call in message.get("tool_calls") or () if tool_call_id(call) is not None
            ]
            llm_source = None
            for call_id_candidate in call_ids:
                if call_id_candidate in llm_by_call:
                    llm_source = llm_by_call[call_id_candidate]
                    call_id = call_id_candidate
                    break
            if llm_source is None and signature in llm_by_signature:
                llm_source = llm_by_signature[signature]
            if llm_source is not None:
                trace_id, span_id, start_ns, end_ns = llm_source
        tool_call_id_value = signature[2] if role == "tool" else None
        nodes.append(
            TrajectoryNode(
                node_id="",
                subagent_key=subject_id,
                display_name=display_name,
                kind=kind,
                role=role,
                content=str(message.get("content")) if message.get("content") is not None else None,
                tool_calls=[dict(call) for call in message.get("tool_calls") or ()],
                name=str(message.get("name")) if message.get("name") else None,
                tool_call_id=tool_call_id_value,
                message_id=message_id,
                trace_id=trace_id,
                span_id=span_id,
                call_id=call_id,
                start_ts_ns=start_ns,
                end_ts_ns=end_ns,
                source_kind=source_kind,
                error=errored,
            )
        )
    return _append_turn_fallback_nodes(
        nodes,
        records,
        subject_id=subject_id,
        display_name=display_name,
        kind=kind,
    )


def build_nodes_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, list[TrajectoryNode]]:
    """Build nodes per subject from a ``trajectory_snapshot.json`` payload."""
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    session_id = str(snapshot.get("session_id") or "snapshot")
    result: dict[str, list[TrajectoryNode]] = {}
    for subject in snapshot.get("subjects") or ():
        if not isinstance(subject, Mapping):
            continue
        subject_id = str(subject.get("subject_id") or "")
        records = subject.get("records") or []
        if not subject_id or not isinstance(records, list):
            continue
        result[subject_id] = build_subject_nodes(
            [record for record in records if isinstance(record, Mapping)],
            subject_id=subject_id,
            display_name=str(subject.get("display_name") or subject_id),
            kind=str(subject.get("kind") or "subagent"),
            session_id=session_id,
        )
    return result


class SlicedNodes(dict):
    """The sliced nodes per subject (a plain ``dict`` to every caller), plus ``user_requests`` and ``whole``.

    ``user_requests`` are the user's own messages of the whole session, taken before slicing: the
    task comes from them even when the selected window starts after the request or covers only team
    members (whose inbound rows are dispatches), and they tell which round last delivered the result.
    ``whole`` is every subject's nodes before slicing: deliverables are judged at their final version.
    """

    user_requests: tuple[TrajectoryNode, ...] = ()
    whole: Mapping[str, list[TrajectoryNode]] | None = None


def slice_nodes_by_range(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    ranges: Sequence[tuple[str, int, int]],
) -> dict[str, list[TrajectoryNode]]:
    """Slice nodes to the closed window ``[start_ts, end_ts]``.

    A node joins only when the window covers its full ``[start, end]``
    interval (``start_ts <= node.start_ts_ns and node.end_ts_ns <= end_ts``),
    so a reply that finished after the selected end is excluded even when its
    start falls inside.  A node without a known end (``end_ts_ns == 0``) is
    judged by its start only.

    Each subagent may carry at most one range; subjects without a range drop
    out entirely (proposal §2.3: unselected subjects do not participate).
    The result is a :class:`SlicedNodes` carrying the user's own messages of the whole session,
    read from all subjects before slicing, and the unsliced nodes.
    """
    range_map: dict[str, tuple[int, int]] = {}
    for item in ranges:
        subagent, start_ts, end_ts = item
        range_map[str(subagent)] = (int(start_ts), int(end_ts))
    result = SlicedNodes()
    result.user_requests = tuple(user_request_nodes(nodes_by_subagent))
    result.whole = nodes_by_subagent
    for subagent, nodes in nodes_by_subagent.items():
        if subagent not in range_map:
            continue
        start_ts, end_ts = range_map[subagent]
        result[subagent] = [
            node for node in nodes if start_ts <= node.start_ts_ns and (node.end_ts_ns == 0 or node.end_ts_ns <= end_ts)
        ]
    return result


def node_native_key(node: TrajectoryNode) -> str:
    """Return the composite ``<kind>:<native_id>`` mapping key of a node."""
    if node.source_kind == "msg" and node.message_id:
        return f"msg:{node.message_id}"
    if node.source_kind == "tool" and node.trace_id and node.span_id:
        suffix = f":{node.call_id}" if node.call_id else ""
        return f"tool:{node.trace_id}:{node.span_id}{suffix}"
    if node.source_kind == "llm" and node.trace_id and node.span_id:
        return f"llm:{node.trace_id}:{node.span_id}"
    if node.source_kind == "turn" and node.trace_id and node.span_id:
        return f"turn:{node.trace_id}:{node.span_id}:{node.role}"
    return f"hash:{node.subagent_key}:{node.role}:{node.start_ts_ns}"


def assign_node_ids(
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
    previous_mapping: Mapping[str, Mapping[str, int]] | None = None,
) -> tuple[dict[str, list[TrajectoryNode]], dict[str, dict[str, int]]]:
    """Assign stable ``<subagent_key>:<seq>`` ids reusing previous mappings.

    Nodes already mapped keep their seq; new nodes continue from
    ``max_seq + 1`` per subject so ids stay stable across snapshot refreshes.
    Returns the reassigned nodes and the updated mapping.
    """
    updated: dict[str, dict[str, int]] = {
        subject: dict(entries) for subject, entries in (previous_mapping or {}).items()
    }
    assigned: dict[str, list[TrajectoryNode]] = {}
    for subagent, nodes in nodes_by_subagent.items():
        subject_map = updated.setdefault(subagent, {})
        max_seq = max(subject_map.values(), default=-1)
        used_keys: set[str] = set()
        subject_nodes: list[TrajectoryNode] = []
        for node in nodes:
            key = node_native_key(node)
            if key in subject_map and key not in used_keys:
                seq = subject_map[key]
                used_keys.add(key)
            else:
                max_seq += 1
                seq = max_seq
                subject_map[key] = seq
            subject_nodes.append(node.with_node_id(f"{subagent}:{seq}"))
        assigned[subagent] = subject_nodes
    return assigned, updated


def detect_trajectory_language(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> str:
    """Detect the trajectory language from a bounded sample of node text."""
    sample: list[str] = []
    for nodes in nodes_by_subagent.values():
        for node in nodes:
            if node.content:
                sample.append(node.content[:200])
            if len(sample) >= 40:
                break
        if len(sample) >= 40:
            break
    return detect_language("\n".join(sample))


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "SlicedNodes",
    "assign_node_ids",
    "build_nodes_from_snapshot",
    "build_subject_nodes",
    "detect_trajectory_language",
    "node_native_key",
    "slice_nodes_by_range",
]
