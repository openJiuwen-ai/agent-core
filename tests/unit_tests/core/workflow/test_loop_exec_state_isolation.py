# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Concurrency-isolation tests for loop-component execution state.

``AdvancedLoopComponent`` used to keep its per-run state on the shared
instance: ``self._node_session`` (through which ``break_loop``/``is_broken``
read and write the ``_broken`` flag) and ``self._post_body._finish_index``.
When one component instance serves several concurrent executions, the last
execution to enter the loop overwrites ``_node_session``, so an earlier
execution writes its break flag into *another* session and then never sees it
— the loop does not exit and the loop body runs again.

The fix routes the "current execution" session through a ContextVar; this
module pins that behaviour down.
"""
import asyncio
import types

import pytest

from openjiuwen.core.common.constants.constant import FINISH_INDEX
from openjiuwen.core.workflow.components.flow.loop import loop_comp as loop_mod

BROKEN = loop_mod.BROKEN


class _FakeState:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def update(self, values: dict):
        self._data.update(values)

    def commit(self):
        return None


class _FakeSession:
    def __init__(self, name: str):
        self.name = name
        self._state = _FakeState()

    def state(self):
        return self._state


def _component(session):
    """Minimal stand-in for AdvancedLoopComponent (only the field is used)."""
    return types.SimpleNamespace(_node_session=session)


@pytest.fixture(autouse=True)
def _reset_channel():
    token = loop_mod._LOOP_EXEC_SESSION.set(None)
    yield
    loop_mod._LOOP_EXEC_SESSION.reset(token)


def test_resolve_falls_back_to_instance_field():
    session = _FakeSession("fallback")
    component = _component(session)

    assert loop_mod._resolve_loop_session(component) is session


def test_resolve_prefers_execution_scoped_session():
    shared = _FakeSession("shared")
    current = _FakeSession("current")
    component = _component(shared)

    token = loop_mod._LOOP_EXEC_SESSION.set(current)
    try:
        assert loop_mod._resolve_loop_session(component) is current
    finally:
        loop_mod._LOOP_EXEC_SESSION.reset(token)


def test_break_loop_is_visible_to_the_same_execution_only():
    """A break must land in the session of the execution that called it."""
    session_a = _FakeSession("a")
    session_b = _FakeSession("b")
    component = _component(session_a)  # last-entered execution owns the field

    # Execution B is running: its break must not go to A's session.
    token = loop_mod._LOOP_EXEC_SESSION.set(session_b)
    try:
        loop_mod.AdvancedLoopComponent.break_loop(component)
        assert loop_mod.AdvancedLoopComponent.is_broken(component) is True
    finally:
        loop_mod._LOOP_EXEC_SESSION.reset(token)

    assert session_b.state().get(BROKEN) is True
    assert session_a.state().get(BROKEN) is None


def test_break_loop_still_works_without_execution_context():
    """Outside an execution the old instance-field behaviour is preserved."""
    session = _FakeSession("legacy")
    component = _component(session)

    loop_mod.AdvancedLoopComponent.break_loop(component)

    assert session.state().get(BROKEN) is True
    assert loop_mod.AdvancedLoopComponent.is_broken(component) is True


def test_is_broken_is_false_for_a_pristine_session():
    assert loop_mod.AdvancedLoopComponent.is_broken(_component(_FakeSession("clean"))) is False


def test_concurrent_executions_keep_their_own_loop_state():
    """Two executions sharing one component must not observe each other's flag."""
    component = _component(_FakeSession("shared"))
    results = {}

    async def worker(name: str, should_break: bool, barrier: asyncio.Barrier):
        session = _FakeSession(name)
        token = loop_mod._LOOP_EXEC_SESSION.set(session)
        try:
            if should_break:
                loop_mod.AdvancedLoopComponent.break_loop(component)
            await barrier.wait()  # force a genuine interleaving
            results[name] = loop_mod.AdvancedLoopComponent.is_broken(component)
        finally:
            loop_mod._LOOP_EXEC_SESSION.reset(token)

    async def main():
        barrier = asyncio.Barrier(2)
        await asyncio.gather(worker("breaker", True, barrier), worker("runner", False, barrier))

    asyncio.run(main())

    assert results["breaker"] is True
    assert results["runner"] is False


def test_finish_index_is_read_from_the_execution_session():
    """PostLoopBody round counting must follow the execution, not the instance."""
    post_body = loop_mod.PostLoopBody()
    session = _FakeSession("round-counter")
    session.state().update({FINISH_INDEX: 7})

    token = loop_mod._LOOP_EXEC_SESSION.set(session)
    try:
        assert post_body.get_finish_index() == 7
        post_body.set_finish_index(9)
    finally:
        loop_mod._LOOP_EXEC_SESSION.reset(token)

    assert session.state().get(FINISH_INDEX) == 9
