# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-scoped active skill body tracker and window-pin helper.

A skill body loaded via ``skill_tool`` is recorded here so that subsequent
``get_context_window`` calls can re-inject the full body even after the
original ``ToolMessage`` slid out of the window or was offloaded. The body
remains active until ``skill_complete`` clears it.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.observability import (
    resolve_context_trace_ids,
    write_context_trace,
)
from openjiuwen.core.foundation.llm import (
    BaseMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

ACTIVE_SKILL_BODIES_STATE_KEY = "active_skill_bodies"
ACTIVE_SKILL_EVICTIONS_STATE_KEY = "active_skill_body_evictions"
ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY = "active_skill_body_eviction_signature"

DEFAULT_MAX_ACTIVE_SKILL_BODIES = 1

# "after_skill_tool" anchors each body pin right after its corresponding stub
# ToolMessage (CC-shaped placement). Falls back to user_prefix when the stub
# is no longer in the window.
ActiveSkillPinTarget = Literal["system", "user_prefix", "after_skill_tool"]

# Per-session RMW lock for active_skill_bodies state.
# Keyed by session_id; bounded by typical session lifetime, fallback to id(session)
# for unidentified sessions (those locks may leak but cost is small).
_session_locks: Dict[str, threading.RLock] = {}
_session_locks_guard = threading.Lock()


def _get_session_lock(session: Any) -> threading.RLock:
    sid = _safe_session_id(session) or f"id:{id(session)}"
    with _session_locks_guard:
        lock = _session_locks.get(sid)
        if lock is None:
            lock = threading.RLock()
            _session_locks[sid] = lock
        return lock


def normalize_skill_relative_file_path(relative_file_path: str) -> str:
    """Canonical relative path for the primary skill entry file (``SKILL.md``).

    Models and legacy paths sometimes pass ``SKILL`` without ``.md``; state keys,
    pins, and ``skill_tool`` must agree on ``SKILL.md`` so lookups stay stable.
    """
    raw = (relative_file_path or "").strip()
    if not raw:
        return "SKILL.md"
    p = raw.replace("\\", "/").removeprefix("./")
    if "/" in p:
        prefix, base = p.rsplit("/", 1)
    else:
        prefix, base = "", p
    if "." not in base and base.casefold() == "skill":
        return f"{prefix}/SKILL.md" if prefix else "SKILL.md"
    return raw


def _state_key(skill_name: str, relative_file_path: str) -> str:
    # NOTE: ``session.update_state`` routes through ``expand_nested_structure``
    # in ``openjiuwen/core/session/utils.py``, which splits dict keys on ``.``
    # and rebuilds them as nested dicts. That mangles ``SKILL.md`` into
    # ``{"...SKILL": {"md": entry}}`` so pin readers can't find ``entry["body"]``.
    # Escape both the path-internal ``.`` and the name/path separator using
    # control characters that never appear in skill names or POSIX paths.
    path = normalize_skill_relative_file_path(relative_file_path)
    safe_path = path.replace(".", "\x02")
    return f"{skill_name}\x01{safe_path}"


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _replace_active_skill_state(
    session: Any,
    active: Dict[str, Any],
    evictions: Dict[str, Any],
) -> None:
    """Write ``active_skill_bodies`` and eviction maps as full snapshots.

    ``Session.update_state`` merges dict-shaped values into existing keys via
    ``update_dict``; keys removed in the new snapshot are not deleted from the
    stored map. Clearing both roots with ``None`` first makes the follow-up
    write a full replace (required for unregister and eviction).
    """
    session.update_state({
        ACTIVE_SKILL_BODIES_STATE_KEY: None,
        ACTIVE_SKILL_EVICTIONS_STATE_KEY: None,
    })
    session.update_state({
        ACTIVE_SKILL_BODIES_STATE_KEY: dict(active),
        ACTIVE_SKILL_EVICTIONS_STATE_KEY: dict(evictions),
    })


def _migrate_skill_body_keyed_map(raw: Any) -> Tuple[Dict[str, Any], bool]:
    """Re-key maps stored under ``active_skill_bodies`` / eviction keys.

    Legacy entries used ``relative_file_path`` ``SKILL`` (no ``.md``), producing
    compound keys that no longer match ``skill_tool`` / pin metadata.
    """
    src = _coerce_dict(raw)
    out: Dict[str, Any] = {}
    changed = False
    for old_k, v in src.items():
        if not isinstance(v, dict):
            out[old_k] = v
            continue
        name = (v.get("skill_name") or "").strip()
        if not name:
            out[old_k] = v
            continue
        path = normalize_skill_relative_file_path(str(v.get("relative_file_path") or ""))
        new_k = _state_key(name, path)
        entry = dict(v)
        entry["relative_file_path"] = path
        if new_k != old_k or (v.get("relative_file_path") or "") != path:
            changed = True
        prev = out.get(new_k)
        if prev is None or float(entry.get("invoked_at", 0.0)) >= float(
            prev.get("invoked_at", 0.0)
        ):
            out[new_k] = entry
        elif prev is not None:
            changed = True
    if set(src.keys()) != set(out.keys()) or len(src) != len(out):
        changed = True
    return out, changed


def _read_skill_content(result: Any) -> str:
    """Extract structured skill_content from a ToolOutput.data dict."""
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        content = data.get("skill_content")
        if isinstance(content, str) and content:
            return content
    return ""


def _read_directory_tree_from_result(result: Any) -> Optional[List[str]]:
    """Extract directory_tree from skill_tool ToolOutput.data (flat path list or one ASCII-tree block)."""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return None
    tree = data.get("directory_tree")
    if isinstance(tree, str) and tree.strip():
        return [tree.strip()]
    if not isinstance(tree, list) or not tree:
        return None
    out: List[str] = []
    for item in tree:
        if isinstance(item, str) and item.strip():
            out.append(item)
    return out or None


def _directory_tree_is_ascii_blob(directory_tree: List[str]) -> bool:
    if len(directory_tree) != 1:
        return False
    chunk = directory_tree[0]
    return "├──" in chunk or "└──" in chunk


def format_directory_tree_markdown_for_llm(
    directory_tree: Optional[List[str]],
    *,
    max_lines: int = 120,
) -> str:
    """Markdown block for directory_tree (used in [ACTIVE SKILL BODY] pin)."""
    if not directory_tree:
        return ""
    cap = max(1, min(max_lines, 500))
    if _directory_tree_is_ascii_blob(directory_tree):
        inner = directory_tree[0].splitlines()
        body_lines = inner[:cap]
        suffix = "\n…(truncated)" if len(inner) > cap else ""
        body = "\n".join(body_lines) + suffix
        title = "\n## Directory layout\n"
    else:
        lines = directory_tree[:cap]
        suffix = "\n…(truncated)" if len(directory_tree) > cap else ""
        body = "\n".join(lines) + suffix
        title = "\n## Directory layout (relative paths; directories end with /)\n"
    return f"{title}```\n{body}\n```\n"


def record_active_skill_body(
    session: Any,
    tool_message: Optional[ToolMessage],
    result: Any,
    *,
    max_active_skill_bodies: int = DEFAULT_MAX_ACTIVE_SKILL_BODIES,
) -> bool:
    """Record an active skill body in session state.

    Returns True iff the body was stored; the caller may then stub the
    original ToolMessage. Returns False on any failure / disabled mode so
    the caller keeps the full ToolMessage as the model's only view.
    """
    if max_active_skill_bodies is None or max_active_skill_bodies <= 0:
        return False
    if session is None or tool_message is None or result is None:
        return False

    metadata = getattr(tool_message, "metadata", None) or {}
    if not metadata.get("is_skill_body"):
        return False

    skill_name = metadata.get("skill_name") or ""
    relative_file_path = normalize_skill_relative_file_path(
        str(metadata.get("relative_file_path") or "")
    )
    if not skill_name:
        return False

    body = _read_skill_content(result)
    if not body:
        return False

    directory_tree = _read_directory_tree_from_result(result)

    lock = _get_session_lock(session)
    try:
        with lock:
            return _record_locked(
                session,
                tool_message,
                skill_name,
                relative_file_path,
                body,
                max_active_skill_bodies,
                directory_tree=directory_tree,
            )
    except Exception as exc:
        logger.warning(f"[active_skill_bodies] record failed: {exc}")
        return False


def _record_locked(
    session: Any,
    tool_message: ToolMessage,
    skill_name: str,
    relative_file_path: str,
    body: str,
    max_active_skill_bodies: int,
    *,
    directory_tree: Optional[List[str]] = None,
) -> bool:
    active = _coerce_dict(session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY))
    evictions = _coerce_dict(session.get_state(ACTIVE_SKILL_EVICTIONS_STATE_KEY))
    active, _ = _migrate_skill_body_keyed_map(active)
    evictions, _ = _migrate_skill_body_keyed_map(evictions)

    key = _state_key(skill_name, relative_file_path)
    entry: Dict[str, Any] = {
        "skill_name": skill_name,
        "relative_file_path": relative_file_path,
        "body": body,
        "tool_call_id": getattr(tool_message, "tool_call_id", None),
        "invoked_at": time.time(),
        "source_session_id": _safe_session_id(session),
    }
    if directory_tree:
        entry["directory_tree"] = list(directory_tree)
    active[key] = entry

    # Reloading a skill clears any stale eviction notice for it.
    if key in evictions:
        evictions.pop(key, None)

    # Bound the active set to max_active_skill_bodies; oldest by invoked_at.
    evicted_keys: List[str] = []
    if len(active) > max_active_skill_bodies:
        sorted_items = sorted(active.items(), key=lambda kv: kv[1].get("invoked_at", 0.0))
        overflow = len(active) - max_active_skill_bodies
        for evict_key, evict_val in sorted_items[:overflow]:
            evicted_keys.append(evict_key)
            active.pop(evict_key, None)
            evictions[evict_key] = {
                "skill_name": evict_val.get("skill_name"),
                "relative_file_path": evict_val.get("relative_file_path"),
                "evicted_at": time.time(),
                "reason": "max_active_skill_bodies",
            }

    _replace_active_skill_state(session, active, evictions if evictions else {})
    trace_ids = resolve_context_trace_ids(session, None)
    # [PIN_DIAG] write-side: capture session identity + post-write keys
    try:
        _post_write = _coerce_dict(session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY))
        _post_keys = list(_post_write.keys())
    except Exception:
        _post_keys = ["<get_state failed>"]
    write_context_trace(
        "pin_diag.record",
        {
            **trace_ids,
            "session_obj_id": id(session),
            "active_keys_after_write": _post_keys,
            "skill_name": skill_name,
            "relative_file_path": relative_file_path,
            "body_len": len(body),
            "directory_tree_lines": len(directory_tree) if directory_tree else 0,
        },
    )
    logger.info(
        "[active_skill_bodies] record session_id=%s skill=%s path=%s body_len=%s "
        "directory_tree_lines=%s active_entries_after=%s evicted_keys=%s",
        trace_ids.get("session_id"),
        skill_name,
        relative_file_path,
        len(body),
        len(directory_tree) if directory_tree else 0,
        len(active),
        evicted_keys or [],
    )
    write_context_trace(
        "skill.lifecycle.active_state_record",
        {
            **trace_ids,
            "skill_name": skill_name,
            "relative_file_path": relative_file_path,
            "body_len": len(body),
            "directory_tree_lines": len(directory_tree) if directory_tree else 0,
            "active_entries_after": len(active),
            "evicted_keys": evicted_keys,
        },
    )
    return True


def unregister_active_skill_body(
    session: Any,
    skill_name: str,
    relative_file_path: Optional[str] = None,
) -> int:
    """Remove active skill body entries; returns count removed."""
    if session is None or not skill_name:
        return 0
    lock = _get_session_lock(session)
    try:
        with lock:
            return _unregister_locked(session, skill_name, relative_file_path)
    except Exception as exc:
        logger.warning(f"[active_skill_bodies] unregister failed: {exc}")
        return 0


def _unregister_locked(
    session: Any,
    skill_name: str,
    relative_file_path: Optional[str],
) -> int:
    active = _coerce_dict(session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY))
    evictions = _coerce_dict(session.get_state(ACTIVE_SKILL_EVICTIONS_STATE_KEY))
    active, _ = _migrate_skill_body_keyed_map(active)
    evictions, _ = _migrate_skill_body_keyed_map(evictions)

    removed = 0
    if relative_file_path is not None:
        key = _state_key(skill_name, normalize_skill_relative_file_path(relative_file_path))
        if key in active:
            active.pop(key, None)
            removed += 1
        evictions.pop(key, None)
    else:
        for key in list(active.keys()):
            if active[key].get("skill_name") == skill_name:
                active.pop(key, None)
                removed += 1
        for key in list(evictions.keys()):
            if evictions[key].get("skill_name") == skill_name:
                evictions.pop(key, None)

    _replace_active_skill_state(session, active, evictions)
    trace_ids = resolve_context_trace_ids(session, None)
    logger.info(
        "[active_skill_bodies] unregister session_id=%s skill=%s path_filter=%s "
        "removed_entries=%s active_entries_after=%s",
        trace_ids.get("session_id"),
        skill_name,
        relative_file_path,
        removed,
        len(active),
    )
    write_context_trace(
        "skill.lifecycle.active_state_unregister",
        {
            **trace_ids,
            "skill_name": skill_name,
            "relative_file_path_filter": relative_file_path,
            "removed_entries": removed,
            "active_entries_after": len(active),
        },
    )
    return removed


def _safe_session_id(session: Any) -> Optional[str]:
    try:
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            return getter()
    except Exception:
        return None
    return None


def _build_pin_content(
    skill_name: str,
    relative_file_path: str,
    body: str,
    directory_tree: Optional[List[str]] = None,
    *,
    max_tree_lines_in_pin: int = 250,
) -> str:
    tree_md = ""
    if directory_tree:
        cap = max(1, min(max_tree_lines_in_pin, 500))
        tree_md = format_directory_tree_markdown_for_llm(directory_tree, max_lines=cap)
    return (
        f"[ACTIVE SKILL BODY] {skill_name} / {relative_file_path}\n"
        f"This skill body remains in effect until skill_complete is called.\n"
        f"{tree_md}"
        f"---\n{body}"
    )


def _build_eviction_notice(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    head = items[0]
    name = head.get("skill_name") or ""
    path = head.get("relative_file_path") or "SKILL.md"
    extra = len(items) - 1
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return (
        "[ACTIVE SKILL EVICTED] "
        f"{name} / {path}{suffix} was dropped due to active-skill cap. "
        "Re-call skill_tool if you still need it."
    )


def _has_existing_pin(messages: List[BaseMessage], skill_name: str, path: str) -> bool:
    for msg in messages or []:
        meta = getattr(msg, "metadata", None) or {}
        if (
            meta.get("active_skill_pin")
            and meta.get("skill_name") == skill_name
            and meta.get("relative_file_path") == path
        ):
            return True
    return False


def _find_skill_stub_index(
    messages: List[BaseMessage],
    skill_name: str,
    relative_file_path: str,
    *,
    expected_tool_call_id: Optional[str] = None,
) -> Optional[int]:
    """Locate the live load-stub ToolMessage for a given active skill.

    Matches the metadata that ``SkillUseRail.after_tool_call`` writes when it
    stubs the original ``skill_tool`` ToolMessage on successful body recording:
    ``skill_body_stub`` is True, ``skill_body_active`` is True (so unloaded
    stubs from ``skill_complete`` are skipped), and the skill identity matches.

    When the same skill has been loaded more than once without an intervening
    ``skill_complete`` (e.g. a re-load), the window may contain multiple live
    load stubs for the same ``(skill_name, relative_file_path)``. The pin must
    anchor to the *most recent* one — the registry only stores the latest body
    for that key, so anchoring to an older stub would put the pin far from the
    current tool result and confuse the model. Lookup order:

    1. If ``expected_tool_call_id`` is supplied (registry entry's
       ``tool_call_id``), prefer the exact match — that's the stub which
       produced the body currently in the registry.
    2. Otherwise return the *last* (highest-index) matching stub in the window.
    """
    if not skill_name:
        return None
    last_match: Optional[int] = None
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, ToolMessage):
            continue
        meta = getattr(msg, "metadata", None) or {}
        if not meta.get("skill_body_stub"):
            continue
        if not meta.get("skill_body_active"):
            continue
        if meta.get("skill_name") != skill_name:
            continue
        if meta.get("relative_file_path") != relative_file_path:
            continue
        if (
            expected_tool_call_id is not None
            and getattr(msg, "tool_call_id", None) == expected_tool_call_id
        ):
            return idx
        last_match = idx
    return last_match


def append_active_skill_pins_to_window(
    context: Any,
    window: Any,
    *,
    max_active_skill_bodies: int = DEFAULT_MAX_ACTIVE_SKILL_BODIES,
    pin_target: ActiveSkillPinTarget = "system",
) -> List[BaseMessage]:
    """Append active skill pins (and any eviction notice) to the window.

    Called after all processors have run, before validation. Returns the
    list of newly inserted pin messages so callers can post-process them
    (e.g. assign context_message_id).
    """
    if max_active_skill_bodies is None or max_active_skill_bodies <= 0:
        return []

    session = None
    getter = getattr(context, "get_session_ref", None)
    if callable(getter):
        try:
            session = getter()
        except Exception:
            session = None
    # [PIN_DIAG] read-side: capture context + session identity at window-build time
    _diag_trace_ids = resolve_context_trace_ids(
        session, context if session is not None else None
    ) if session is not None else {"session_id": None, "context_id": None}
    write_context_trace(
        "pin_diag.append.entry",
        {
            **_diag_trace_ids,
            "context_obj_id": id(context),
            "context_session_ref_id": id(getattr(context, "_session_ref", None)),
            "session_obj_id": id(session) if session is not None else None,
            "session_id_safe": _safe_session_id(session) if session is not None else None,
            "session_is_none": session is None,
        },
    )
    if session is None:
        return []

    try:
        active = _coerce_dict(session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY))
        evictions = _coerce_dict(session.get_state(ACTIVE_SKILL_EVICTIONS_STATE_KEY))
        active, a_mig = _migrate_skill_body_keyed_map(active)
        evictions, e_mig = _migrate_skill_body_keyed_map(evictions)
        if a_mig or e_mig:
            try:
                _replace_active_skill_state(session, active, evictions)
            except Exception:
                pass
        prev_signature = session.get_state(ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY) or ""
    except Exception as _exc:
        write_context_trace(
            "pin_diag.append.get_state_error",
            {**_diag_trace_ids, "error": repr(_exc)},
        )
        return []
    write_context_trace(
        "pin_diag.append.state",
        {
            **_diag_trace_ids,
            "active_keys_at_read": list(active.keys()),
            "eviction_keys": list(evictions.keys()),
            "prev_signature": prev_signature,
        },
    )

    if not active and not evictions:
        return []

    items: List[Tuple[str, Dict[str, Any]]] = sorted(
        active.items(), key=lambda kv: kv[1].get("invoked_at", 0.0)
    )

    # Eviction notice is shown only when its signature changed since the
    # last window build, to avoid repeatedly nudging the model.
    eviction_signature = "|".join(sorted(evictions.keys())) if evictions else ""
    notice_text = ""
    if evictions and eviction_signature != prev_signature:
        notice_text = _build_eviction_notice(list(evictions.values()))

    sys_msgs = list(getattr(window, "system_messages", None) or [])
    ctx_msgs = list(getattr(window, "context_messages", None) or [])

    pin_messages: List[BaseMessage] = []

    if notice_text:
        pin_messages.append(_make_pin_message(
            content=notice_text,
            metadata={
                "active_skill_pin": True,
                "active_skill_eviction_notice": True,
            },
            target=pin_target,
        ))

    for _, entry in items[-max_active_skill_bodies:]:
        skill_name = entry.get("skill_name") or ""
        path = entry.get("relative_file_path") or "SKILL.md"
        body = entry.get("body") or ""
        tree = entry.get("directory_tree")
        directory_tree = tree if isinstance(tree, list) else None
        if not skill_name or not body:
            continue
        if _has_existing_pin(ctx_msgs, skill_name, path) or _has_existing_pin(sys_msgs, skill_name, path):
            continue
        entry_tool_call_id = entry.get("tool_call_id")
        pin_meta: Dict[str, Any] = {
            "active_skill_pin": True,
            "is_skill_body": True,
            "skill_name": skill_name,
            "relative_file_path": path,
            "attachment_id": entry_tool_call_id or f"skill_pin:{skill_name}:{path}",
        }
        # ``source_tool_call_id`` lets ``after_skill_tool`` dispatch anchor to
        # the *exact* stub that produced the registry's current body (not just
        # any stub matching the (name, path) key), which matters when the same
        # skill was reloaded without an intervening skill_complete.
        if entry_tool_call_id:
            pin_meta["source_tool_call_id"] = entry_tool_call_id
        pin_messages.append(_make_pin_message(
            content=_build_pin_content(skill_name, path, body, directory_tree),
            metadata=pin_meta,
            target=pin_target,
        ))

    if not pin_messages:
        # Persist signature even if we suppressed the notice this round.
        try:
            session.update_state({ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY: eviction_signature})
        except Exception:
            pass
        return []

    if pin_target == "system":
        sys_msgs.extend(pin_messages)
        window.system_messages = sys_msgs
    elif pin_target == "after_skill_tool":
        # Each body pin anchors right after its corresponding stub ToolMessage
        # so the wire shape mirrors claude-code's
        # [assistant(tool_use), tool_result(short ack), user(SKILL.md body)].
        # Pins whose stub has slid out of the window (or whose stub is already
        # an unload stub) fall back to the user_prefix position. The eviction
        # notice always rides the user_prefix block — it has no 1:1 stub.
        notice_pin: Optional[BaseMessage] = None
        body_pins: List[BaseMessage] = []
        for m in pin_messages:
            meta = getattr(m, "metadata", None) or {}
            if meta.get("active_skill_eviction_notice"):
                notice_pin = m
            else:
                body_pins.append(m)

        anchored: List[Tuple[int, BaseMessage]] = []
        fallback_pins: List[BaseMessage] = []
        for pin in body_pins:
            meta = getattr(pin, "metadata", None) or {}
            sn = str(meta.get("skill_name") or "")
            rp = str(meta.get("relative_file_path") or "SKILL.md")
            expected_tcid = meta.get("source_tool_call_id")
            stub_idx = _find_skill_stub_index(
                ctx_msgs, sn, rp,
                expected_tool_call_id=expected_tcid if isinstance(expected_tcid, str) else None,
            )
            if stub_idx is None:
                if isinstance(meta, dict):
                    meta["pin_anchor"] = "user_prefix_fallback"
                fallback_pins.append(pin)
            else:
                if isinstance(meta, dict):
                    meta["pin_anchor"] = "after_skill_tool"
                anchored.append((stub_idx, pin))

        # Insert anchored pins from highest index to lowest so earlier inserts
        # do not shift the indices of later ones.
        for stub_idx, pin in sorted(anchored, key=lambda t: t[0], reverse=True):
            ctx_msgs.insert(stub_idx + 1, pin)

        prefix_block: List[BaseMessage] = []
        if notice_pin is not None:
            prefix_block.append(notice_pin)
        prefix_block.extend(fallback_pins)
        if prefix_block:
            insert_at = 0
            for idx, msg in enumerate(ctx_msgs):
                if isinstance(msg, UserMessage):
                    insert_at = idx
                    break
            ctx_msgs = ctx_msgs[:insert_at] + prefix_block + ctx_msgs[insert_at:]

        window.context_messages = ctx_msgs
    else:
        # user_prefix: insert before first UserMessage in context_messages
        insert_at = 0
        for idx, msg in enumerate(ctx_msgs):
            if isinstance(msg, UserMessage):
                insert_at = idx
                break
        else:
            insert_at = 0
        window.context_messages = ctx_msgs[:insert_at] + pin_messages + ctx_msgs[insert_at:]

    try:
        session.update_state({ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY: eviction_signature})
    except Exception:
        pass

    trace_ids = resolve_context_trace_ids(session, context)
    pinned_skills: List[str] = []
    has_eviction_notice = False
    for m in pin_messages:
        meta = getattr(m, "metadata", None) or {}
        if meta.get("active_skill_eviction_notice"):
            has_eviction_notice = True
            continue
        sn = meta.get("skill_name")
        if sn:
            pinned_skills.append(str(sn))
    logger.info(
        "[active_skill_bodies] append_pins session_id=%s context_id=%s pin_target=%s "
        "pin_count=%s has_eviction_notice=%s pinned_skills=%s",
        trace_ids.get("session_id"),
        trace_ids.get("context_id"),
        pin_target,
        len(pin_messages),
        has_eviction_notice,
        pinned_skills,
    )

    return pin_messages


def _make_pin_message(
    *,
    content: str,
    metadata: Dict[str, Any],
    target: ActiveSkillPinTarget,
) -> BaseMessage:
    if target == "system":
        return SystemMessage(content=content, metadata=metadata)
    return UserMessage(content=content, metadata=metadata)


# ----------------------------------------------------------------------
# Active skill hints (for child sessions spawned by TaskTool / spawn executor)
# ----------------------------------------------------------------------

ACTIVE_SKILL_HINTS_STATE_KEY = "active_skill_hints"

# Hints staged by spawn paths before the child session exists. The child's
# SkillUseRail.before_invoke pops them and writes them onto its own session
# state, so the section builder can render a hint prompt.
_pending_hints: Dict[str, List[Dict[str, Any]]] = {}
_pending_hints_guard = threading.Lock()


def stage_active_skill_hints_for_session(session_id: str, hints: List[Dict[str, Any]]) -> None:
    """Stage active-skill hints for a child session that hasn't been created yet."""
    if not session_id or not hints:
        return
    with _pending_hints_guard:
        _pending_hints[session_id] = list(hints)


def pop_active_skill_hints_for_session(session_id: str) -> List[Dict[str, Any]]:
    """Pop staged hints (if any) for a session id."""
    if not session_id:
        return []
    with _pending_hints_guard:
        return _pending_hints.pop(session_id, []) or []


def derive_hints_from_session(session: Any) -> List[Dict[str, Any]]:
    """Build name/path-only hints from a parent session's active_skill_bodies."""
    if session is None:
        return []
    try:
        active = _coerce_dict(session.get_state(ACTIVE_SKILL_BODIES_STATE_KEY))
        active, _ = _migrate_skill_body_keyed_map(active)
    except Exception:
        return []
    hints: List[Dict[str, Any]] = []
    for entry in active.values():
        name = entry.get("skill_name")
        if not name:
            continue
        hints.append({
            "skill_name": name,
            "relative_file_path": normalize_skill_relative_file_path(
                str(entry.get("relative_file_path") or "")
            ),
        })
    return hints


__all__ = [
    "ACTIVE_SKILL_BODIES_STATE_KEY",
    "ACTIVE_SKILL_EVICTIONS_STATE_KEY",
    "ACTIVE_SKILL_EVICTION_SIGNATURE_STATE_KEY",
    "ACTIVE_SKILL_HINTS_STATE_KEY",
    "DEFAULT_MAX_ACTIVE_SKILL_BODIES",
    "ActiveSkillPinTarget",
    "append_active_skill_pins_to_window",
    "derive_hints_from_session",
    "normalize_skill_relative_file_path",
    "pop_active_skill_hints_for_session",
    "record_active_skill_body",
    "stage_active_skill_hints_for_session",
    "unregister_active_skill_body",
]
