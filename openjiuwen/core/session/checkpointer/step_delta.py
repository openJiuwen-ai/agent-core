# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Snapshot and delta algorithms for per-iteration agent step checkpoints.

This module is deliberately pure (no I/O, no session access): the storage
layer in :mod:`step_storage` owns the KV writes and the in-process baseline.

Reuse boundaries with :mod:`openjiuwen.core.session.vcs`:

- ``append`` / ``reset`` come from ``vcs.delta.diff_context`` / ``apply_context``.
- ``replace_range`` is added here only for the step path: compression and
  offload rewrite a middle span in place, and rewriting the whole history for
  that would defeat the point of an incremental checkpoint.
- state uses ``vcs.delta.diff_state`` unless the state dict contains dotted or
  non-str keys; ``diff_state`` joins paths with ``.`` and cannot round-trip
  those, so the full state is carried instead.

Nothing here touches ``VersioningManager`` or the vcs adapters/managers.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from openjiuwen.core.session.state.base import AGENT_STATE_KEY, GLOBAL_STATE_KEY
from openjiuwen.core.session.vcs.codec import decode_context_state, encode_context_state
from openjiuwen.core.session.vcs.constants import CONTEXT_KEY
from openjiuwen.core.session.vcs.delta import apply_context, apply_state, diff_context, diff_state
from openjiuwen.core.session.vcs.models import MessageDelta, StateDelta

STEP_WINDOW_KEY = "__step_context_window__"
"""State key carrying ``{context_id: last_context_window_access_at}``.

``SessionModelContext.save_state`` exposes the window timestamp, but the vcs
context codec only encodes messages and offload tables. The step snapshot keeps
the timestamp aside in the state track so a resumed process restores it.
"""

StepKind = Literal["step", "final", "interrupt"]


@dataclass
class RangeDelta:
    """In-place replacement of ``messages[start:end]`` within one context.

    Attributes:
        context_id: The context this delta applies to.
        start: First index of the replaced span in the replayed list.
        end: Exclusive end index of the replaced span.
        messages: Encoded messages that replace the span.
        offload_messages: Full offload table, only when it also changed. Only
            in-memory fallback entries need to ride along; files on disk are
            addressed by handle.
        kind: Always ``"replace_range"``; present so callers can dispatch on
            the same attribute as ``MessageDelta``.
    """

    context_id: str
    start: int
    end: int
    messages: list[dict] = field(default_factory=list)
    offload_messages: dict | None = None
    kind: Literal["replace_range"] = "replace_range"


ContextDelta = MessageDelta | RangeDelta


@dataclass
class StepRecord:
    """One appended step: everything that changed since the previous record.

    Attributes:
        seq: Zero-based append sequence within ``(session_id, agent_id)``.
        iteration: ReAct iteration that produced this record.
        kind: ``step`` (completed iteration), ``final`` (task finished) or
            ``interrupt`` (waiting for human confirmation).
        query: User query of the run this record belongs to.
        context: Per-context message deltas.
        state: KV state delta (empty when ``state_full`` is set).
        state_full: Whole state dict used when ``state`` cannot be expressed
            as dotted paths.
    """

    seq: int
    iteration: int
    kind: StepKind
    query: str
    context: list[ContextDelta]
    state: StateDelta = field(default_factory=StateDelta)
    state_full: dict | None = None


# --- snapshot <-> session state ---

def build_step_snapshot(full_state: dict) -> dict:
    """Split a ``session.state().get_state()`` result into ``{context, state}``.

    Args:
        full_state: ``{"global_state": {...}, "agent_state": {...}}`` where
            ``global_state["context"]`` holds each context's ``save_state()``.

    Returns:
        ``{"context": {cid: encoded context}, "state": state}``. The state is
        the full state with ``global_state["context"]`` removed and
        ``STEP_WINDOW_KEY`` added at its top level.
    """
    full = deepcopy(full_state) if full_state else {}
    global_state = full.get(GLOBAL_STATE_KEY)
    contexts = {}
    if isinstance(global_state, dict):
        contexts = global_state.pop(CONTEXT_KEY, None) or {}
    encoded_context = {}
    window: dict[str, Any] = {}
    for cid, cstate in contexts.items():
        cstate = cstate or {}
        window[cid] = cstate.get("last_context_window_access_at")
        encoded_context[cid] = encode_context_state(cstate)
    full[STEP_WINDOW_KEY] = window
    return {"context": encoded_context, "state": full}


def restore_full_state(snapshot: dict) -> dict:
    """Rebuild a ``session.state().set_state()`` payload from a step snapshot.

    Inverse of :func:`build_step_snapshot`: the context track is decoded back
    into ``BaseMessage`` lists and the window timestamps are put back on each
    context.
    """
    snapshot = snapshot or {}
    state = deepcopy(snapshot.get("state") or {})
    window = state.pop(STEP_WINDOW_KEY, None) or {}
    contexts = {}
    for cid, cstate in (snapshot.get("context") or {}).items():
        decoded = decode_context_state(cstate or {})
        decoded["last_context_window_access_at"] = window.get(cid)
        contexts[cid] = decoded
    if contexts:
        global_state = state.get(GLOBAL_STATE_KEY)
        if not isinstance(global_state, dict):
            global_state = {}
            state[GLOBAL_STATE_KEY] = global_state
        global_state[CONTEXT_KEY] = contexts
    return state


# --- context track ---

def diff_step_context(old: dict, new: dict) -> list[ContextDelta]:
    """Diff two encoded context maps for the step path.

    ``append`` and first-seen contexts are kept as the vcs produced them; a
    ``reset`` is first retried as one or more ``replace_range`` deltas, and only
    kept when the change cannot be expressed as spans.
    """
    deltas: list[ContextDelta] = []
    for delta in diff_context(old, new):
        old_ctx = old.get(delta.context_id)
        if delta.kind == "append" or old_ctx is None:
            deltas.append(delta)
            continue
        ranges = _diff_ranges(delta.context_id, old_ctx, new.get(delta.context_id) or {})
        if ranges is None:
            deltas.append(delta)
        else:
            deltas.extend(ranges)
    return deltas


def apply_step_context(ctx: dict, deltas: list[ContextDelta]) -> dict:
    """Apply step context deltas onto an encoded context map; returns a copy."""
    result = deepcopy(ctx)
    for delta in deltas:
        if isinstance(delta, RangeDelta):
            entry = result.setdefault(delta.context_id, {"messages": [], "offload_messages": {}})
            entry["messages"][delta.start:delta.end] = deepcopy(delta.messages)
            if delta.offload_messages is not None:
                entry["offload_messages"] = deepcopy(delta.offload_messages)
        else:
            result = apply_context(result, [delta])
    return result


def _diff_ranges(cid: str, old_ctx: dict, new_ctx: dict) -> list[RangeDelta] | None:
    """Express a reset as span replacements, or None when it does not fit."""
    old_msgs, new_msgs = old_ctx.get("messages", []) or [], new_ctx.get("messages", []) or []
    new_offload = new_ctx.get("offload_messages") or {}
    offload_changed = (old_ctx.get("offload_messages") or {}) != new_offload
    candidates = [_prefix_suffix_spans(old_msgs, new_msgs)]
    if len(new_msgs) >= len(old_msgs):
        candidates.append(_elementwise_spans(old_msgs, new_msgs))
    spans = min(candidates, key=lambda s: sum(len(msgs) for _, _, msgs in s))
    if new_msgs and sum(len(msgs) for _, _, msgs in spans) >= len(new_msgs):
        return None
    if not spans:
        if not offload_changed:
            return []
        spans = [(len(old_msgs), len(old_msgs), [])]
    return [
        RangeDelta(cid, start, end, msgs, new_offload if offload_changed and i == 0 else None)
        for i, (start, end, msgs) in enumerate(spans)
    ]


def _prefix_suffix_spans(old_msgs: list, new_msgs: list) -> list[tuple[int, int, list]]:
    """One span covering everything between the common prefix and suffix."""
    limit = min(len(old_msgs), len(new_msgs))
    p = 0
    while p < limit and old_msgs[p] == new_msgs[p]:
        p += 1
    s = 0
    while s < limit - p and old_msgs[-1 - s] == new_msgs[-1 - s]:
        s += 1
    if p == len(old_msgs) == len(new_msgs):
        return []
    return [(p, len(old_msgs) - s, new_msgs[p:len(new_msgs) - s])]


def _elementwise_spans(old_msgs: list, new_msgs: list) -> list[tuple[int, int, list]]:
    """One span per run of differing positions, plus any appended tail."""
    spans, i = [], 0
    while i < len(old_msgs):
        if old_msgs[i] == new_msgs[i]:
            i += 1
            continue
        j = i
        while j < len(old_msgs) and old_msgs[j] != new_msgs[j]:
            j += 1
        spans.append((i, j, new_msgs[i:j]))
        i = j
    if len(new_msgs) > len(old_msgs):
        spans.append((len(old_msgs), len(old_msgs), new_msgs[len(old_msgs):]))
    return spans


# --- state track ---

def diff_step_state(old: dict, new: dict) -> tuple[StateDelta, dict | None]:
    """Diff two step state dicts.

    Returns:
        ``(delta, None)`` normally; ``(empty_delta, full_state)`` when either
        side has dotted or non-str keys, which ``diff_state``'s dotted paths
        cannot round-trip.
    """
    old = old or {}
    new = new or {}
    if _needs_full_state(old) or _needs_full_state(new):
        return StateDelta(), deepcopy(new)
    return diff_state(old, new), None


def apply_step_state(state: dict, delta: StateDelta, state_full: dict | None) -> dict:
    """Apply a step state record onto the replayed state; returns a new dict."""
    if state_full is not None:
        return deepcopy(state_full)
    return apply_state(state, delta)


def _needs_full_state(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if not isinstance(key, str) or "." in key:
            return True
        if _needs_full_state(value):
            return True
    return False


__all__ = [
    "AGENT_STATE_KEY",
    "ContextDelta",
    "GLOBAL_STATE_KEY",
    "RangeDelta",
    "STEP_WINDOW_KEY",
    "StepKind",
    "StepRecord",
    "apply_step_context",
    "apply_step_state",
    "build_step_snapshot",
    "diff_step_context",
    "diff_step_state",
    "restore_full_state",
]
