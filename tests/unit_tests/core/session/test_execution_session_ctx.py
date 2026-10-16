# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for ``openjiuwen.core.session.execution_session_ctx``.

Covers the two ContextVar channels used to carry execution-scoped state that
must NOT live on reusable (build-time) objects:

* ``_workflow_session`` — set/reset by ``CompiledGraph._invoke``;
* ``_component_exec``  — set by branch-router owner components, never reset
  (dispatch happens after ``invoke`` returns, inside the same NodeTask).

Because ``_component_exec`` is deliberately not reset, ownership tagging is
the only thing preventing a stale value written by one component from being
read by an unrelated router later in the same task context. These tests pin
that contract down.
"""
import asyncio

import pytest

from openjiuwen.core.session import execution_session_ctx as ctx


class _FakeSession:
    """Stand-in for a BaseSession; only identity matters here."""

    def __init__(self, name: str):
        self.name = name


class _Wrapper:
    """Stand-in for a component-facing session wrapper exposing ``_inner``."""

    def __init__(self, inner):
        self._inner = inner


@pytest.fixture(autouse=True)
def _clean_channels():
    """ContextVars are inherited by tasks; snapshot/restore around each test."""
    yield
    ctx.set_workflow_session(None)
    ctx.set_component_exec(None, None, None)


def test_workflow_session_set_and_reset_is_stack_scoped():
    s1, s2 = _FakeSession("s1"), _FakeSession("s2")

    assert ctx.current_workflow_session() is None

    token1 = ctx.set_workflow_session(s1)
    assert ctx.current_workflow_session() is s1

    # Nested execution (sub-workflow) pushes its own session on top.
    token2 = ctx.set_workflow_session(s2)
    assert ctx.current_workflow_session() is s2

    ctx.reset_workflow_session(token2)
    assert ctx.current_workflow_session() is s1

    ctx.reset_workflow_session(token1)
    assert ctx.current_workflow_session() is None


def test_component_exec_default_is_empty_triple():
    assert ctx.current_component_exec() == (None, None, None)


def test_resolve_routing_session_prefers_component_level_and_unwraps():
    inner = _FakeSession("inner")
    ctx.set_component_exec(_Wrapper(inner), {"k": "v"})

    resolved = ctx.resolve_routing_session()

    # The read side is responsible for unwrapping to a BaseSession.
    assert resolved is inner


def test_resolve_routing_session_falls_back_to_workflow_level():
    workflow_session = _FakeSession("workflow")
    ctx.set_workflow_session(workflow_session)

    # No component-level value -> workflow level wins.
    assert ctx.resolve_routing_session() is workflow_session


def test_resolve_routing_session_falls_back_to_caller_value():
    fallback = _FakeSession("fallback")

    # No channel populated (e.g. router evaluated outside any execution).
    assert ctx.resolve_routing_session(fallback) is fallback


def test_resolve_routing_session_ownership_guard_rejects_foreign_value():
    """A stale component value must not be read by an unrelated router."""
    owner_a = object()
    owner_b = object()
    stale_inner = _FakeSession("stale")
    workflow_session = _FakeSession("workflow")

    ctx.set_component_exec(_Wrapper(stale_inner), None, owner=owner_a)
    ctx.set_workflow_session(workflow_session)

    # Same owner -> trusted.
    assert ctx.resolve_routing_session(None, owner=owner_a) is stale_inner
    # Different owner -> degrade to workflow level rather than mis-resolve.
    assert ctx.resolve_routing_session(None, owner=owner_b) is workflow_session


def test_resolve_routing_session_owner_none_keeps_legacy_trust_all():
    """Omitting ``owner`` preserves the unconditional-trust behaviour."""
    inner = _FakeSession("inner")
    workflow_session = _FakeSession("workflow")

    ctx.set_component_exec(_Wrapper(inner), None, owner=object())
    ctx.set_workflow_session(workflow_session)

    assert ctx.resolve_routing_session() is inner


def test_untagged_component_value_is_trusted_by_any_owner():
    """Writers that omit ``owner`` stay compatible with owner-scoped reads."""
    inner = _FakeSession("inner")
    ctx.set_component_exec(_Wrapper(inner), None)  # no owner -> None

    assert ctx.resolve_routing_session(None, owner=object()) is inner


def test_channels_are_isolated_between_tasks():
    """Concurrent executions each see their own value (context is copied)."""
    results = {}

    async def worker(name: str, barrier: asyncio.Barrier):
        session = _FakeSession(name)
        token = ctx.set_workflow_session(session)
        await barrier.wait()  # force genuine interleaving
        results[name] = ctx.current_workflow_session()
        ctx.reset_workflow_session(token)

    async def main():
        barrier = asyncio.Barrier(3)
        await asyncio.gather(worker("a", barrier), worker("b", barrier), worker("c", barrier))

    asyncio.run(main())

    assert results["a"].name == "a"
    assert results["b"].name == "b"
    assert results["c"].name == "c"
    # The parent context was never polluted.
    assert ctx.current_workflow_session() is None
