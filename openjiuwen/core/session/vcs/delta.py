# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Diff/apply algorithms for vcs.

Two tracks with different data shapes:

- context: ordered message sequences. Normal growth is pure append, so the
  diff detects "new extends old" and emits only the appended tail; truncation
  / compaction / offload changes emit a full ``reset``.
- state: an unordered kv dict. A recursive diff emits a flat
  ``{nested_path: value}`` set plus a list of removed nested paths.

State apply intentionally does NOT reuse ``session.utils.update_dict``: that
helper treats a None value as a deletion and recurses with
``ignore_delete=False``, which would make a legitimate None value ambiguous.
vcs restores snapshots wholesale, so explicit set/remove helpers are used that
keep None as an ordinary value.
"""
from copy import deepcopy
from typing import Any

from openjiuwen.core.session.vcs.models import MessageDelta, StateDelta


# --- context track ---

def _is_prefix(old: list, new: list) -> bool:
    """Return True if `old` is a (non-strict) prefix of `new`."""
    return len(old) <= len(new) and new[:len(old)] == old


def diff_context(old: dict, new: dict) -> list[MessageDelta]:
    """Diff two encoded context maps into per-context message deltas.

    Args:
        old: Previous ``{cid: {"messages": [dict], "offload_messages": {...}}}``.
        new: Current map of the same shape.

    Returns:
        MessageDelta list: ``append`` (tail only) when messages purely grew and
        offload is unchanged, otherwise ``reset`` (full messages + offload).
    """
    deltas: list[MessageDelta] = []
    for cid, new_ctx in new.items():
        new_msgs = new_ctx.get("messages", [])
        new_offload = new_ctx.get("offload_messages")
        old_ctx = old.get(cid)
        if old_ctx is None:
            deltas.append(
                MessageDelta(context_id=cid, kind="reset", messages=new_msgs, offload_messages=new_offload),
            )
            continue
        old_msgs = old_ctx.get("messages", [])
        offload_changed = old_ctx.get("offload_messages") != new_offload
        if not offload_changed and _is_prefix(old_msgs, new_msgs):
            tail = new_msgs[len(old_msgs):]
            if tail:
                deltas.append(MessageDelta(context_id=cid, kind="append", messages=tail))
        else:
            deltas.append(
                MessageDelta(context_id=cid, kind="reset", messages=new_msgs, offload_messages=new_offload),
            )
    return deltas


def apply_context(ctx: dict, deltas: list[MessageDelta]) -> dict:
    """Apply message deltas onto an encoded context map; returns a new dict."""
    result = deepcopy(ctx)
    for delta in deltas:
        if delta.kind == "reset":
            result[delta.context_id] = {
                "messages": list(delta.messages),
                "offload_messages": delta.offload_messages or {},
            }
        else:
            entry = result.setdefault(delta.context_id, {"messages": [], "offload_messages": {}})
            entry["messages"].extend(delta.messages)
    return result


# --- state track ---

def diff_state(old: dict, new: dict) -> StateDelta:
    """Recursively diff two kv dicts into a flat set map + removed paths."""
    set_flat: dict[str, Any] = {}
    removed: list[str] = []
    _diff_into(old, new, "", set_flat, removed)
    return StateDelta(set=set_flat, removed=removed)


def _diff_into(old: dict, new: dict, prefix: str, set_flat: dict, removed: list) -> None:
    for key, new_value in new.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in old:
            set_flat[path] = new_value
            continue
        old_value = old[key]
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            _diff_into(old_value, new_value, path, set_flat, removed)
        elif old_value != new_value:
            set_flat[path] = new_value
    for key in old:
        if key not in new:
            removed.append(f"{prefix}.{key}" if prefix else key)


def apply_state(state: dict, delta: StateDelta) -> dict:
    """Apply a StateDelta onto a kv dict; returns a new dict. None stays a value."""
    result = deepcopy(state)
    for path, value in delta.set.items():
        _set_path(result, path.split("."), deepcopy(value))
    for path in delta.removed:
        _del_path(result, path.split("."))
    return result


def _set_path(root: dict, keys: list[str], value: Any) -> None:
    node = root
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def _del_path(root: dict, keys: list[str]) -> None:
    node = root
    for key in keys[:-1]:
        node = node.get(key)
        if not isinstance(node, dict):
            return
    node.pop(keys[-1], None)
