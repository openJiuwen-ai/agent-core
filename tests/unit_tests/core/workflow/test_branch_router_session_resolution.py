# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session-resolution tests for ``BranchRouter``.

A ``BranchRouter`` instance is created once at build time and shared by every
execution of the graph. Historically ``__call__`` read ``self._session``, a
shared field rewritten by ``set_session`` on each run, so concurrent (or
merely repeated) executions resolved to the wrong session.

The router now resolves through two execution-scoped ContextVar channels:
component level (written by the router's owner component) then workflow level
(written by ``CompiledGraph._invoke``), falling back to ``self._session``.
"""
import pytest

from openjiuwen.core.session import execution_session_ctx as ctx
from openjiuwen.core.session.internal.workflow import WorkflowSession, NodeSession
from openjiuwen.core.workflow import BranchRouter
from openjiuwen.core.workflow.components.condition.condition import Condition


def _session(session_id: str) -> WorkflowSession:
    return WorkflowSession(workflow_id="wf", parent=None, session_id=session_id)


@pytest.fixture(autouse=True)
def _clean_channels():
    yield
    ctx.set_workflow_session(None)
    ctx.set_component_exec(None, None, None)


def _recording_router():
    """A router whose single branch records the session it was evaluated with."""
    seen = []

    class _RecordingCondition(Condition):
        """Records the session passed by the router and always matches."""

        def invoke(self, inputs, session):
            seen.append(session)
            return True

        def trace_info(self, session=None):
            return "recording"

    router = BranchRouter(report_trace=False)
    router.add_branch(_RecordingCondition(), "target-a", branch_id="a")
    return router, seen


@pytest.mark.asyncio
async def test_falls_back_to_shared_field_outside_any_execution():
    """Outside an execution there is no channel value: legacy behaviour wins."""
    router, seen = _recording_router()
    shared = _session("shared")
    router.set_session(shared)

    assert await router() == ["target-a"]
    assert seen[0] is shared


@pytest.mark.asyncio
async def test_component_level_value_takes_priority_and_is_unwrapped():
    """The router's owner component writes a wrapper; the read side unwraps it."""
    router, seen = _recording_router()
    outer = _session("outer")
    router.set_session(outer)

    inner_session = _session("component")

    class _Wrapper:
        """Component-facing session wrapper exposing ``_inner``."""

        def __init__(self, inner):
            self._inner = inner

    ctx.set_component_exec(_Wrapper(inner_session), None, owner=router)
    ctx.set_workflow_session(_session("workflow"))

    await router()

    # Read side unwraps ``_inner``; component-level wins over workflow-level.
    assert seen[0] is inner_session


@pytest.mark.asyncio
async def test_node_session_component_value_is_passed_through():
    """``NodeSession`` has no ``_inner``: unwrapping must not over-unwrap."""
    router, seen = _recording_router()
    router.set_session(_session("shared"))

    node_session = NodeSession(_session("component"), "branch-node")
    ctx.set_component_exec(node_session, None, owner=router)
    ctx.set_workflow_session(_session("workflow"))

    await router()

    assert seen[0] is node_session


@pytest.mark.asyncio
async def test_workflow_level_value_used_when_no_component_value():
    router, seen = _recording_router()
    router.set_session(_session("shared"))

    workflow_session = _session("workflow")
    ctx.set_workflow_session(workflow_session)

    await router()

    assert seen[0] is workflow_session


@pytest.mark.asyncio
async def test_ownership_guard_rejects_foreign_component_value():
    """A component value written by another router must not be consumed.

    ``_component_exec`` is deliberately not reset (dispatch happens after the
    owner's ``invoke`` returns, in the same task), so without ownership
    tagging a stale value could leak into an unrelated router's evaluation.
    """
    router, seen = _recording_router()
    router.set_session(_session("shared"))

    workflow_session = _session("workflow")
    ctx.set_workflow_session(workflow_session)
    ctx.set_component_exec(NodeSession(_session("foreign"), "other-node"), None, owner=object())

    await router()

    assert seen[0] is workflow_session


@pytest.mark.asyncio
async def test_shared_field_is_not_read_while_channels_are_populated():
    """The racy shared field must stay unused whenever a channel has a value."""
    router, seen = _recording_router()
    racy = _session("racy")
    router.set_session(racy)

    ctx.set_component_exec(NodeSession(_session("fresh"), "n"), None, owner=router)

    await router()

    assert seen[0] is not racy
