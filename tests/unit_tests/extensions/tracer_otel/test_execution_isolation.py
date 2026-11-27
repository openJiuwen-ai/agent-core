# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Execution isolation tests for OtelWorkflowHandler (issue #2430).

The registry injects one handler instance into every Tracer. These tests
drive two executions through the same handler and assert their workflow
state, span trees, and outputs never cross.
"""

import ast
import asyncio

import pytest
from opentelemetry import trace

from openjiuwen.core.session.tracer.data import NodeStatus
from openjiuwen.core.session.tracer.tracer import TracerHandlerRegistry
from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.semconv import (
    OJ_SESSION_ID,
    OJ_STATUS,
    OJ_WORKFLOW_OUTPUTS,
)
from tests.conftest_otel import _EXPORTER, _OTEL_TRACER

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_registry_and_exporter():
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()
    yield
    TracerHandlerRegistry.clear()
    _EXPORTER.clear()


def _handler() -> OtelWorkflowHandler:
    return OtelWorkflowHandler(_OTEL_TRACER, OtelTracerConfig(redaction_enabled=False))


def _root_metadata(execution_id: str, workflow_id: str = "wf", name: str = "WF") -> dict:
    return {
        "workflow_id": workflow_id,
        "workflow_name": name,
        "execution_id": execution_id,
    }


def _component_metadata(
    execution_id: str,
    component_id: str = "n1",
    workflow_id: str = "wf",
    component_type: str = "Flow",
) -> dict:
    return {
        "workflow_id": workflow_id,
        "component_id": component_id,
        "component_name": component_id,
        "component_type": component_type,
        "execution_id": execution_id,
    }


def _spans_by_name(name: str) -> list:
    return [s for s in _EXPORTER.get_finished_spans() if s.name == name]


def _outputs(span) -> dict:
    return ast.literal_eval(span.attributes[OJ_WORKFLOW_OUTPUTS])


async def test_interleaved_executions_isolate_roots_components_and_outputs():
    """A/B share workflow_id and invoke_id; each keeps its own trace and outputs."""
    handler = _handler()
    barrier = asyncio.Barrier(2)

    async def run(execution_id: str, session_id: str, trace_id: str, token: str):
        await handler.on_call_start(
            invoke_id="wf",
            parent_node_id="",
            metadata=_root_metadata(execution_id),
            inputs={"in": token},
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await barrier.wait()
        await handler.on_call_start(
            invoke_id="wf.n1",
            parent_node_id="",
            metadata=_component_metadata(execution_id),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await barrier.wait()
        await handler.on_call_done(
            invoke_id="wf.n1",
            outputs={"out": token},
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_done(
            invoke_id="wf",
            outputs={"root": token},
            execution_id=execution_id,
            trace_id=trace_id,
        )

    await asyncio.gather(
        run("exec-A", "sess-A", "trace-A", "A"),
        run("exec-B", "sess-B", "trace-B", "B"),
    )

    roots = _spans_by_name("WF")
    assert len(roots) == 2
    assert len({s.context.trace_id for s in roots}) == 2

    roots_by_session = {s.attributes[OJ_SESSION_ID]: s for s in roots}
    assert _outputs(roots_by_session["sess-A"]) == {"root": "A"}
    assert _outputs(roots_by_session["sess-B"]) == {"root": "B"}

    components = _spans_by_name("component.n1")
    assert len(components) == 2
    for component in components:
        owner = next(r for r in roots if r.context.trace_id == component.context.trace_id)
        assert component.parent is not None
        assert component.parent.span_id == owner.context.span_id
        assert _outputs(component) == {"out": component.attributes[OJ_SESSION_ID][-1]}

    assert set(handler._exec_handlers) == {"exec-A", "exec-B"}
    assert handler._span_manager._spans == {}


async def test_different_workflow_ids_do_not_clean_each_other():
    """Starting B must not clear A's live parent mappings."""
    handler = _handler()
    barrier = asyncio.Barrier(2)

    async def run(execution_id: str, workflow_id: str, name: str, token: str):
        await handler.on_call_start(
            invoke_id=workflow_id,
            parent_node_id="",
            metadata=_root_metadata(execution_id, workflow_id=workflow_id, name=name),
            session_id=f"sess-{token}",
            execution_id=execution_id,
            trace_id=f"trace-{token}",
        )
        await barrier.wait()
        await handler.on_call_start(
            invoke_id=f"{workflow_id}.n1",
            parent_node_id="",
            metadata=_component_metadata(execution_id, workflow_id=workflow_id),
            session_id=f"sess-{token}",
            execution_id=execution_id,
            trace_id=f"trace-{token}",
        )
        await barrier.wait()
        await handler.on_call_done(
            invoke_id=f"{workflow_id}.n1",
            outputs={"out": token},
            execution_id=execution_id,
            trace_id=f"trace-{token}",
        )
        await handler.on_call_done(
            invoke_id=workflow_id,
            outputs={"root": token},
            execution_id=execution_id,
            trace_id=f"trace-{token}",
        )

    await asyncio.gather(
        run("exec-A", "wf-a", "WFA", "A"),
        run("exec-B", "wf-b", "WFB", "B"),
    )

    roots = _spans_by_name("WFA") + _spans_by_name("WFB")
    assert len(roots) == 2
    assert len({s.context.trace_id for s in roots}) == 2
    for component in _spans_by_name("component.n1"):
        owner = next(r for r in roots if r.context.trace_id == component.context.trace_id)
        assert component.parent.span_id == owner.context.span_id


async def test_exception_pops_only_owning_execution():
    """A's error must not end or mislabel B's component span."""
    handler = _handler()

    for execution_id, trace_id, session_id in (
        ("exec-A", "trace-A", "sess-A"),
        ("exec-B", "trace-B", "sess-B"),
    ):
        await handler.on_call_start(
            invoke_id="wf",
            parent_node_id="",
            metadata=_root_metadata(execution_id),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_start(
            invoke_id="wf.n1",
            parent_node_id="",
            metadata=_component_metadata(execution_id),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    await handler.on_invoke(
        invoke_id="wf.n1",
        exception=RuntimeError("boom"),
        execution_id="exec-A",
        trace_id="trace-A",
    )

    finished = _EXPORTER.get_finished_spans()
    assert len(finished) == 1
    failed = finished[0]
    assert failed.attributes[OJ_SESSION_ID] == "sess-A"
    assert failed.status.status_code == trace.StatusCode.ERROR
    assert failed.attributes[OJ_STATUS] == NodeStatus.ERROR.value

    # B's component is still open in its own sub-handler.
    assert handler._exec_handlers["exec-B"]._span_manager.get("wf.n1") is not None
    assert handler._exec_handlers["exec-A"]._span_manager.get("wf.n1") is None

    await handler.on_call_done(invoke_id="wf.n1", outputs={"out": "B"}, execution_id="exec-B", trace_id="trace-B")
    done = _spans_by_name("component.n1")
    assert len(done) == 2
    ok_span = next(s for s in done if s.attributes[OJ_SESSION_ID] == "sess-B")
    assert ok_span.status.status_code == trace.StatusCode.OK
    assert _outputs(ok_span) == {"out": "B"}


async def test_deferred_end_node_state_is_per_execution():
    """Deferred end-node stashes keyed by the same invoke_id must not cross."""
    handler = _handler()

    for execution_id, trace_id, session_id in (
        ("exec-A", "trace-A", "sess-A"),
        ("exec-B", "trace-B", "sess-B"),
    ):
        await handler.on_call_start(
            invoke_id="wf",
            parent_node_id="",
            metadata=_root_metadata(execution_id),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_start(
            invoke_id="end1",
            parent_node_id="",
            metadata={
                **_component_metadata(execution_id, component_id="end1", component_type="End"),
                "defer_span_start": True,
            },
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_pre_stream(
            invoke_id="end1",
            chunk={"in": execution_id},
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_post_invoke(
            invoke_id="end1",
            outputs={"answer": execution_id},
            component_metadata=_component_metadata(execution_id, component_id="end1", component_type="End"),
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_finalize(invoke_id="end1", execution_id=execution_id, trace_id=trace_id)
        await handler.on_call_done(invoke_id="end1", execution_id=execution_id, trace_id=trace_id)

    ends = _spans_by_name("component.end1")
    assert len(ends) == 2
    by_session = {s.attributes[OJ_SESSION_ID]: s for s in ends}
    assert _outputs(by_session["sess-A"]) == {"answer": "exec-A"}
    assert _outputs(by_session["sess-B"]) == {"answer": "exec-B"}
    for execution_id in ("exec-A", "exec-B"):
        assert handler._exec_handlers[execution_id]._deferred_end_starts == {}


async def test_sub_workflow_parents_to_host_component_within_execution():
    """Sub-workflow roots must attach to the host component of their own execution."""
    handler = _handler()

    for execution_id, trace_id, session_id in (
        ("exec-A", "trace-A", "sess-A"),
        ("exec-B", "trace-B", "sess-B"),
    ):
        await handler.on_call_start(
            invoke_id="wf",
            parent_node_id="",
            metadata=_root_metadata(execution_id),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_start(
            invoke_id="host",
            parent_node_id="",
            metadata=_component_metadata(execution_id, component_id="host"),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )
        await handler.on_call_start(
            invoke_id="sub",
            parent_node_id="host",
            metadata=_root_metadata(execution_id, workflow_id="sub", name="SubWF"),
            session_id=session_id,
            execution_id=execution_id,
            trace_id=trace_id,
        )

    for execution_id, trace_id in (("exec-A", "trace-A"), ("exec-B", "trace-B")):
        for invoke_id in ("sub", "host", "wf"):
            await handler.on_call_done(
                invoke_id=invoke_id,
                execution_id=execution_id,
                trace_id=trace_id,
            )

    sub_roots = _spans_by_name("SubWF")
    hosts = _spans_by_name("component.host")
    assert len(sub_roots) == 2 and len(hosts) == 2
    for sub in sub_roots:
        session_id = sub.attributes[OJ_SESSION_ID]
        owner_host = next(h for h in hosts if h.attributes[OJ_SESSION_ID] == session_id)
        assert sub.context.trace_id == owner_host.context.trace_id
        assert sub.parent.span_id == owner_host.context.span_id


async def test_same_execution_new_trace_id_starts_new_trace_when_idle():
    """A new trace_id on an idle execution cleans stale cache and starts fresh."""
    handler = _handler()

    await handler.on_call_start(
        invoke_id="wf",
        parent_node_id="",
        metadata=_root_metadata("exec-A"),
        execution_id="exec-A",
        trace_id="trace-1",
        session_id="sess-A",
    )
    await handler.on_call_done(invoke_id="wf", outputs={"round": 1}, execution_id="exec-A", trace_id="trace-1")

    await handler.on_call_start(
        invoke_id="wf",
        parent_node_id="",
        metadata=_root_metadata("exec-A"),
        execution_id="exec-A",
        trace_id="trace-2",
        session_id="sess-A",
    )
    await handler.on_call_done(invoke_id="wf", outputs={"round": 2}, execution_id="exec-A", trace_id="trace-2")

    roots = _spans_by_name("WF")
    assert len(roots) == 2
    assert len({s.context.trace_id for s in roots}) == 2


async def test_same_execution_same_trace_reuses_cached_context():
    """QA resume with the same trace_id must keep one OTel trace."""
    handler = _handler()

    for round_no in (1, 2):
        await handler.on_call_start(
            invoke_id="wf",
            parent_node_id="",
            metadata=_root_metadata("exec-A"),
            execution_id="exec-A",
            trace_id="trace-1",
            session_id="sess-A",
        )
        await handler.on_call_done(
            invoke_id="wf", outputs={"round": round_no}, execution_id="exec-A", trace_id="trace-1"
        )

    roots = _spans_by_name("WF")
    assert len(roots) == 2
    assert len({s.context.trace_id for s in roots}) == 1


async def test_legacy_events_without_execution_id_keep_flat_state():
    """Direct callers that omit execution_id keep using the root handler."""
    handler = _handler()

    await handler.on_call_start(
        invoke_id="wf",
        parent_node_id="",
        metadata={"workflow_id": "wf", "workflow_name": "WF"},
        session_id="sess-legacy",
    )
    await handler.on_call_done(invoke_id="wf", outputs={"ok": True})

    assert handler._exec_handlers == {}
    assert len(_spans_by_name("WF")) == 1


async def test_route_failure_falls_back_without_raising(monkeypatch):
    """A routing failure must degrade to the root handler, never break tracing."""
    handler = _handler()

    def boom(*args, **kwargs):
        raise RuntimeError("no child for you")

    monkeypatch.setattr(handler, "_new_execution_handler", boom)

    await handler.on_call_start(
        invoke_id="wf",
        parent_node_id="",
        metadata=_root_metadata("exec-A"),
        execution_id="exec-A",
        trace_id="trace-A",
        session_id="sess-A",
    )
    await handler.on_call_done(invoke_id="wf", outputs={"ok": True}, execution_id="exec-A", trace_id="trace-A")

    assert len(_spans_by_name("WF")) == 1


async def test_child_handler_shares_tracer_and_config_but_not_state():
    handler = _handler()

    await handler.on_call_start(
        invoke_id="wf",
        parent_node_id="",
        metadata=_root_metadata("exec-A"),
        execution_id="exec-A",
        trace_id="trace-A",
        session_id="sess-A",
    )

    child = handler._exec_handlers["exec-A"]
    assert child is not handler
    assert child._otel_tracer is handler._otel_tracer
    assert child._config is handler._config
    assert child._span_manager is not handler._span_manager
    assert child._trace_id == "trace-A"
    assert child._session_id == "sess-A"
