# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Guards for scheduled (cron) execution sessions.

A cron-triggered session has no operator on the other end. The host does not
expose the cron tool set there, so the remaining way to operate cron is to
mutate the scheduler's on-disk store directly from the shell or file tools.
That bypasses the tool-level guard and the scheduler's store hot reload then
picks the fabricated job up as a legitimate one (nested cron creation).

These helpers give every mutation surface one shared rule: while running
inside a scheduled execution session, refuse tool input that references the
scheduler's control-plane state. Normal sessions are unchanged.
"""
from __future__ import annotations

from typing import Any

#: Session-id conventions used for scheduled execution runs. Mirrors the
#: host conventions: ``cron_*`` (proactive / legacy per-run sessions) and
#: ``__cron___<ts>_<rand>`` (internal ``__cron__`` channel sessions).
CRON_SESSION_ID_PREFIXES: tuple[str, ...] = ("cron", "__cron__")

#: Files owned by the scheduler's file store. Only the scheduler may write
#: them; shell redirection or ``write_file`` from an agent would go around
#: the tool layer and be reloaded as a real job.
SCHEDULER_STORE_FILENAMES: tuple[str, ...] = (
    "cron_jobs.json",
    "cron_lifecycle_owners.json",
)


def is_cron_execution_session(session_id: Any) -> bool:
    """Return True when *session_id* belongs to a scheduled execution session."""
    sid = str(session_id or "").strip()
    return sid.startswith(CRON_SESSION_ID_PREFIXES)


def _current_session_id() -> str | None:
    """Resolve the session id for the current tool call."""
    from openjiuwen.core.session import get_current_session

    session = get_current_session()
    if session is not None:
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            sid = str(getter() or "").strip()
            if sid:
                return sid
    try:
        from openjiuwen.core.sys_operation.shell_process_registry import (
            get_shell_session_id,
        )

        return get_shell_session_id()
    except Exception:  # noqa: BLE001 - absence of the registry must not break tools
        return None


def scheduled_store_reference(text: Any) -> str | None:
    """Return the scheduler-store file name referenced by *text*, else None."""
    haystack = str(text or "").replace("\\", "/")
    if not haystack:
        return None
    for name in SCHEDULER_STORE_FILENAMES:
        if name in haystack:
            return name
    return None


def scheduled_session_guard(text: Any) -> str | None:
    """Return a denial reason when a cron run touches the scheduler store.

    Args:
        text: The shell command, target path, or file content about to be
            handed to a mutating tool.

    Returns:
        A model-visible denial reason, or ``None`` when access is allowed.
    """
    if not is_cron_execution_session(_current_session_id()):
        return None
    token = scheduled_store_reference(text)
    if token is None:
        return None
    return (
        "Scheduled (cron) execution sessions cannot access the scheduler store "
        f"({token}). Cron jobs are managed from a normal chat session; nested "
        "task creation is not allowed."
    )


__all__ = [
    "CRON_SESSION_ID_PREFIXES",
    "SCHEDULER_STORE_FILENAMES",
    "is_cron_execution_session",
    "scheduled_session_guard",
    "scheduled_store_reference",
]
