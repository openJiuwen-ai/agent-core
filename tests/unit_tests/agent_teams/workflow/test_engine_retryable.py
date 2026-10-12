# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task-3 engine plumbing: ``BackendError.retryable`` + the ``_build_opts`` whitelist.

A ``retryable=False`` backend error (e.g. a third-party model-option rule
violation) must execute exactly once: ``_attempt_calls`` fails fast with a
failed result (carrying the message + tokens) before the retry accounting,
so ``agent()`` emits ONE ``AGENT_FAILED`` and returns ``None`` instead of
re-raising (which would otherwise be swallowed by ``parallel()`` or escalate
to run-level ``WORKFLOW_FAILED`` on a direct call).
Default errors keep the existing retry/feedback behaviour untouched.
``_build_opts`` accepts the two new third-party tuning keys (``builtin_model`` /
``effort``) and still rejects an unknown key.
"""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.workflow.engine import primitives as _p
from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger
from openjiuwen.agent_teams.workflow.engine.errors import BackendError, EngineError
from openjiuwen.agent_teams.workflow.engine.journal import Journal
from openjiuwen.agent_teams.workflow.engine.runtime import Runtime


def _rt() -> Runtime:
    return Runtime(backend=None, journal=None, budget=BudgetLedger(total=10_000))


class _Sink:
    def __init__(self):
        self.events = []

    def __call__(self, ev):
        self.events.append(ev)


class _BoomBackend:
    """Fake ``AgentBackend`` whose ``run`` always raises a fixed error."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    async def run(self, prompt, opts, json_schema, *, call_key=None):
        self.calls += 1
        raise self.exc


@pytest.mark.asyncio
async def test_attempt_calls_fails_fast_on_non_retryable_backend_error():
    """A ``retryable=False`` BackendError is invoked exactly once, no propagation.

    ``_attempt_calls`` returns a failed ``_BackendCallResult`` carrying the error
    message + tokens instead of raising, so ``agent()`` can emit a single
    ``AGENT_FAILED`` and resolve to ``None``.
    """
    calls = 0
    boom = BackendError("boom", tokens=7, retryable=False)

    async def make_call(feedback=None):
        nonlocal calls
        calls += 1
        raise boom

    out = await _p._attempt_calls(_rt(), {"label": "t"}, None, None, make_call)
    assert calls == 1                 # no retry despite rt.retries == 2
    assert out.succeeded is False
    assert out.error_detail == "boom" # the same message, tokens intact
    assert out.tokens == 7


@pytest.mark.asyncio
async def test_agent_non_retryable_error_emits_single_agent_failed():
    """Full ``agent()`` path: a non-retryable backend error fails once, returns None.

    The backend runs exactly once, a single ``AGENT_FAILED`` progress event
    carries the error copy, and ``agent()`` resolves to ``None`` — so a
    ``parallel()`` branch wrapper sees a plain ``None`` and a direct call does
    not escalate to run-level ``WORKFLOW_FAILED``.
    """
    boom = BackendError(
        "model 'X' is not in the team model pool; retry can only fail again",
        retryable=False,
    )
    backend = _BoomBackend(boom)
    sink = _Sink()
    rt = Runtime(backend=backend, journal=Journal(), budget=BudgetLedger(total=10_000))
    rt.progress_sink = sink
    token = _p._rt.set(rt)
    try:
        out = await _p.agent("hi", label="t")
    finally:
        _p._rt.reset(token)

    assert out is None
    assert backend.calls == 1
    failed = [e for e in sink.events if e.kind == _p.ProgressKind.AGENT_FAILED]
    assert len(failed) == 1
    assert "model 'X' is not in the team model pool" in failed[0].message


@pytest.mark.asyncio
async def test_attempt_calls_retries_default_backend_error():
    """A default (retryable=True) BackendError still retries to ``rt.retries + 1``."""
    calls = 0

    async def make_call(feedback=None):
        nonlocal calls
        calls += 1
        raise BackendError(f"try {calls}")

    rt = _rt()  # rt.retries == 2
    out = await _p._attempt_calls(rt, {"label": "t"}, None, None, make_call)
    assert calls == 3                 # 1 + rt.retries (2)
    assert out.succeeded is False
    assert out.attempts == 3
    assert out.error_detail == "try 3"


# ---------------------------------------------------------------------------
# _build_opts whitelist
# ---------------------------------------------------------------------------

def test_build_opts_accepts_third_party_keys():
    rt = _rt()
    opts = _p._build_opts(rt, {}, {"builtin_model": "gpt-5-codex", "effort": "high"})
    assert opts["builtin_model"] == "gpt-5-codex"
    assert opts["effort"] == "high"
    # None values are still stripped (byte-compatible native opts).
    assert _p._build_opts(rt, {}, {"builtin_model": None, "effort": None}) == {}


def test_build_opts_still_rejects_unknown_key():
    rt = _rt()
    with pytest.raises(EngineError):
        _p._build_opts(rt, {}, {"not_a_knob": 1})
