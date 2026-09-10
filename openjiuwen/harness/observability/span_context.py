# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-keyed root-span registry for single-agent runs.

The per-request root span is published through the shared ContextVar-backed
state in :mod:`openjiuwen.extensions.observability.span_context`, but a single
agent executes in a session-setup supervisor task the request's ContextVar does
not reach, so lookups from there see nothing. This module keeps a registry
keyed by session id — readable regardless of task boundary — and wraps the
shared ``get_root_span`` accessor so every parent lookup falls back to it.

The registry is keyed rather than a single "current run" slot because sessions
overlap: a process serves several chats at once, and one slot made them fight
over it. Whoever finished first cleared it, so a run still in progress silently
lost its agent-tier spans from that moment on (its sub-agents landed flat under
the dispatching agent) — and before that, whoever opened last owned the slot,
so the other run's spans would have joined the wrong trace.
"""

from __future__ import annotations

import sys
from typing import Any

from openjiuwen.core.common.logging import logger

# Marker set on the wrapped ``get_root_span`` callable so installation stays
# idempotent and tests can assert the fallback is in place.
ROOT_SPAN_FALLBACK_ATTR = "openjiuwen_agent_root_span_fallback"

# Root spans of the runs currently in flight, keyed by session id.
_ROOT_SPANS: dict[str, Any] = {}

# Modules that bind ``get_root_span`` by name at import time. One imported
# before the fallback is installed still holds the unwrapped accessor, so its
# binding is refreshed too — but only when it is already loaded, so installing
# the fallback never drags an unrelated (or higher-layer) module in.
_EARLY_BINDING_MODULES = (
    "openjiuwen.extensions.observability.callback_handler",
    "openjiuwen.agent_teams.observability.span_context",
    "openjiuwen.harness.rails.evolution.evolution_rail",
)


def _is_recording(span: Any) -> bool:
    """Report whether *span* is still open, tolerating stubs without the API."""
    try:
        return bool(span is not None and span.is_recording())
    except Exception:
        return False


def register_run_root_span(span: Any, *, session_id: str = "") -> None:
    """Register *span* as the root of the run owning *session_id*.

    Args:
        span: The run's root span.
        session_id: Session the run belongs to; empty is a valid key for runs
            that carry no session.
    """
    _ROOT_SPANS[session_id or ""] = span


def unregister_run_root_span(span: Any, *, session_id: str = "") -> None:
    """Drop *session_id*'s entry, and only when *span* still owns it.

    Sessions overlap, so clearing whatever happens to be registered would blind
    a run that is still going — its sub-agents would lose their spans mid-run.

    Args:
        span: The span the caller believes it registered.
        session_id: Session whose entry is dropped.
    """
    key = session_id or ""
    if _ROOT_SPANS.get(key) is span:
        _ROOT_SPANS.pop(key, None)


def reset_run_root_spans() -> None:
    """Clear every registered run root, for shutdown and isolated tests."""
    _ROOT_SPANS.clear()


def resolve_run_root_span(*, session_id: str | None = None) -> Any:
    """Return the root span of the run the calling task belongs to, or None.

    Resolution is by session id first: the session id is set around agent
    execution, so it is readable from the tasks the ContextVar cannot reach —
    which is exactly where this fallback is needed.

    When no session id is in reach, a single run in flight is unambiguous and
    answers. Several in flight with no way to tell them apart returns None
    rather than a guess: attaching one run's spans to another run's trace is
    worse than the span being missing.
    """
    requested_session_id = str(session_id or current_session_id() or "")

    span = _ROOT_SPANS.get(requested_session_id)
    if _is_recording(span):
        return span

    # A concrete owner that is not registered is not ambiguous: it has no
    # live run root.  Never let a late callback from that operation adopt the
    # sole root of a different conversation.
    if requested_session_id:
        return None

    live = [candidate for candidate in list(_ROOT_SPANS.values()) if _is_recording(candidate)]
    if len(live) == 1:
        return live[0]
    return None


def current_session_id() -> str:
    """Read the ambient session id, tolerating a runtime that sets none.

    Two sources, because no single one covers every runtime: a Team run binds
    the session on its own ContextVar around agent execution, while a
    single-agent run publishes it to the shared observability state when it
    opens the run root. Whichever is set answers; neither being set is normal
    (an agent invoked outside any host-managed session).
    """
    try:
        from openjiuwen.agent_teams.context import get_session_id

        session_id = get_session_id() or ""
        if session_id:
            return session_id
    except Exception as exc:
        logger.debug("[AgentObservability] team session id lookup failed: %s", exc)

    from openjiuwen.extensions.observability.span_context import get_current_session_id

    return get_current_session_id() or ""


def install_root_span_fallback() -> None:
    """Wrap the shared root-span accessor with the session-keyed fallback.

    Keeps both the rail / callback parent lookup and ``ActiveSpanTracker``
    parent resolution able to see the single-agent root even when the request's
    ContextVar is invisible to the supervisor task.

    Best-effort, idempotent, never raises — observability must never break a run.
    """
    try:
        from openjiuwen.extensions.observability import span_context as shared
    except Exception as exc:  # pragma: no cover - observability deps unavailable
        logger.debug("[AgentObservability] skip root-span fallback install: %s", exc)
        return

    original = getattr(shared, "get_root_span", None)
    if original is None or getattr(original, ROOT_SPAN_FALLBACK_ATTR, False):
        return

    def get_root_span_with_fallback(*, session_id: str | None = None):
        """Resolve the shared root span, falling back to the run registry."""
        try:
            span = original(session_id=session_id)
        except TypeError:
            span = original()
        if _is_recording(span):
            return span
        return resolve_run_root_span(session_id=session_id)

    setattr(get_root_span_with_fallback, ROOT_SPAN_FALLBACK_ATTR, True)
    shared.get_root_span = get_root_span_with_fallback
    for module_name in _EARLY_BINDING_MODULES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if getattr(module, "get_root_span", None) is original:
            module.get_root_span = get_root_span_with_fallback


__all__ = [
    "ROOT_SPAN_FALLBACK_ATTR",
    "current_session_id",
    "install_root_span_fallback",
    "register_run_root_span",
    "reset_run_root_spans",
    "resolve_run_root_span",
    "unregister_run_root_span",
]
