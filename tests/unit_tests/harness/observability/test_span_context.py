# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-keyed run-root registry and the shared-accessor fallback."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.extensions.observability import span_context as shared_span_context
from openjiuwen.harness.observability import span_context as agent_span_context


def _root_span(name: str = "agent.run.test"):
    """Start a real, still-recording span to register as a run root."""
    return TracerProvider().get_tracer("run-root-test").start_span(name)


def test_fallback_returns_the_single_run_in_flight() -> None:
    """No session id in reach: one live run is unambiguous and answers."""
    agent_span_context.install_root_span_fallback()
    agent_span_context.reset_run_root_spans()
    shared_span_context.reset_state()
    span = _root_span()
    agent_span_context.register_run_root_span(span, session_id="sess-A")
    try:
        assert shared_span_context.get_root_span() is span
    finally:
        agent_span_context.reset_run_root_spans()


def test_ambiguous_runs_resolve_to_nothing_rather_than_the_wrong_trace() -> None:
    """Two runs in flight with no session id in reach: refuse to guess."""
    agent_span_context.install_root_span_fallback()
    agent_span_context.reset_run_root_spans()
    agent_span_context.register_run_root_span(_root_span("a"), session_id="sess-A")
    agent_span_context.register_run_root_span(_root_span("b"), session_id="sess-B")
    try:
        assert agent_span_context.resolve_run_root_span() is None
    finally:
        agent_span_context.reset_run_root_spans()


def test_run_is_resolved_by_session_id_when_available(monkeypatch) -> None:
    """With the session id in context, each run resolves to its own span."""
    agent_span_context.reset_run_root_spans()
    mine = _root_span("mine")
    agent_span_context.register_run_root_span(_root_span("other"), session_id="sess-A")
    agent_span_context.register_run_root_span(mine, session_id="sess-B")
    monkeypatch.setattr(agent_span_context, "current_session_id", lambda: "sess-B")
    try:
        assert agent_span_context.resolve_run_root_span() is mine
    finally:
        agent_span_context.reset_run_root_spans()


def test_known_session_without_registered_root_never_adopts_another_run(monkeypatch) -> None:
    """A cancelled run's late callback must not migrate into the next trace."""
    agent_span_context.reset_run_root_spans()
    other = _root_span("other")
    agent_span_context.register_run_root_span(other, session_id="sess-B")
    monkeypatch.setattr(
        agent_span_context,
        "current_session_id",
        lambda: "sess-A-sub-worker",
    )
    try:
        assert agent_span_context.resolve_run_root_span() is None
        assert agent_span_context.resolve_run_root_span(
            session_id="sess-A-sub-worker"
        ) is None
    finally:
        agent_span_context.reset_run_root_spans()


def test_explicit_session_resolution_does_not_use_ambient_session(monkeypatch) -> None:
    """The wrapper's explicit owner argument wins over stale ambient state."""
    agent_span_context.reset_run_root_spans()
    root_a = _root_span("a")
    root_b = _root_span("b")
    agent_span_context.register_run_root_span(root_a, session_id="sess-A")
    agent_span_context.register_run_root_span(root_b, session_id="sess-B")
    monkeypatch.setattr(agent_span_context, "current_session_id", lambda: "sess-A")
    try:
        assert agent_span_context.resolve_run_root_span(session_id="sess-B") is root_b
    finally:
        agent_span_context.reset_run_root_spans()


def test_one_session_closing_does_not_blind_another_still_running() -> None:
    """A finished run drops only its own entry.

    Clearing the whole registry used to blind a run still in flight — from that
    moment its sub-agents got no agent span and landed flat under the
    dispatching agent.
    """
    agent_span_context.reset_run_root_spans()
    running = _root_span("still-running")
    finished = _root_span("finished")
    agent_span_context.register_run_root_span(running, session_id="sess-A")
    agent_span_context.register_run_root_span(finished, session_id="sess-B")
    try:
        agent_span_context.unregister_run_root_span(finished, session_id="sess-B")
        assert agent_span_context.resolve_run_root_span() is running
    finally:
        agent_span_context.reset_run_root_spans()


def test_unregister_leaves_a_replacement_registered_under_the_same_session() -> None:
    """A stale handle must not evict the run that replaced it."""
    agent_span_context.reset_run_root_spans()
    stale = _root_span("stale")
    current = _root_span("current")
    agent_span_context.register_run_root_span(current, session_id="sess-A")
    try:
        agent_span_context.unregister_run_root_span(stale, session_id="sess-A")
        assert agent_span_context.resolve_run_root_span() is current
    finally:
        agent_span_context.reset_run_root_spans()


def test_fallback_install_is_idempotent() -> None:
    """A second install must not stack wrappers around the shared accessor."""
    agent_span_context.install_root_span_fallback()
    wrapped = shared_span_context.get_root_span
    agent_span_context.install_root_span_fallback()

    assert shared_span_context.get_root_span is wrapped
    assert getattr(wrapped, agent_span_context.ROOT_SPAN_FALLBACK_ATTR, False)


def test_early_binding_module_gets_its_accessor_refreshed(monkeypatch) -> None:
    """A module that imported get_root_span before the install is rebound.

    ``callback_handler`` binds the accessor by name at import time, so without
    this refresh the LLM/tool parent lookup would keep calling the unwrapped
    one and never see the single-agent root.
    """
    def plain_get_root_span(*, session_id: str | None = None):
        """Stand in for the unwrapped shared accessor."""
        del session_id
        return None

    monkeypatch.setattr(shared_span_context, "get_root_span", plain_get_root_span)
    early = SimpleNamespace(get_root_span=plain_get_root_span)
    monkeypatch.setitem(sys.modules, "early-binding-probe", early)
    monkeypatch.setattr(
        agent_span_context, "_EARLY_BINDING_MODULES", ("early-binding-probe",)
    )

    agent_span_context.install_root_span_fallback()

    assert early.get_root_span is shared_span_context.get_root_span
    assert early.get_root_span is not plain_get_root_span


def test_llm_span_lookup_falls_back_to_the_run_root() -> None:
    """The open llm.call span stays findable from the supervisor task.

    ``ActiveSpanTracker._find_llm_span`` resolves the trace through
    ``get_root_span``. Without the session-keyed fallback it returns None when
    the ContextVar is invisible, so ``on_llm_output`` never finds the span it
    must close and the LLM span is exported with input but no completion/usage.
    """
    agent_span_context.install_root_span_fallback()
    agent_span_context.reset_run_root_spans()
    shared_span_context.reset_state()
    trace_id = 0x1234

    class _Span:
        """Hashable span stub — ActiveSpanTracker keeps spans in a set."""

        def __init__(self, name: str, span_id: int, parent: object = None) -> None:
            self.name = name
            self.context = SimpleNamespace(trace_id=trace_id, span_id=span_id)
            self.parent = parent.context if parent is not None else None

        def is_recording(self) -> bool:
            """Report the span as still open."""
            return True

    root_span = _Span("agent.code.normal.sess-1", 0x1)
    # The llm span hangs off the root span, as one opened with the root as
    # parent does — that link is what the tracker matches on when the callback
    # carries no LLM call id.
    llm_span = _Span("llm.call", 0x2, parent=root_span)

    tracker = shared_span_context.ActiveSpanTracker()
    tracker.on_start(llm_span)
    previous_tracker = shared_span_context.get_active_span_tracker()
    shared_span_context.set_active_span_tracker(tracker)
    agent_span_context.register_run_root_span(root_span, session_id="sess-1")
    try:
        assert shared_span_context.get_current_llm_span() is llm_span
        assert shared_span_context.pop_current_llm_span() is llm_span
    finally:
        agent_span_context.reset_run_root_spans()
        shared_span_context.set_active_span_tracker(previous_tracker)
