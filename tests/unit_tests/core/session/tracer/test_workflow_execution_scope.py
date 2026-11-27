# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Execution-scope propagation for workflow tracer events (issue #2430)."""

import pytest

from openjiuwen.core.session.tracer.handler import (
    TraceExtWorkflowHandler,
    TracerHandlerName,
)
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry
from openjiuwen.core.session.tracer.workflow_tracer import (
    TracerWorkflowUtils,
    _execution_scope,
)

pytestmark = pytest.mark.asyncio

_EVENT = TracerHandlerName.TRACER_WORKFLOW.value


@pytest.fixture(autouse=True)
def _clean_registry():
    TracerHandlerRegistry.clear()
    yield
    TracerHandlerRegistry.clear()


class _RecordingWorkflowHandler(TraceExtWorkflowHandler):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def on_call_start(self, **kwargs):
        self.calls.append(("on_call_start", kwargs))

    async def on_call_finalize(self, **kwargs):
        self.calls.append(("on_call_finalize", kwargs))

    async def on_call_done(self, **kwargs):
        self.calls.append(("on_call_done", kwargs))

    async def on_pre_invoke(self, **kwargs):
        self.calls.append(("on_pre_invoke", kwargs))

    async def on_pre_stream(self, **kwargs):
        self.calls.append(("on_pre_stream", kwargs))

    async def on_invoke(self, **kwargs):
        self.calls.append(("on_invoke", kwargs))

    async def on_post_invoke(self, **kwargs):
        self.calls.append(("on_post_invoke", kwargs))

    async def on_post_stream(self, **kwargs):
        self.calls.append(("on_post_stream", kwargs))

    async def on_interact(self, **kwargs):
        self.calls.append(("on_interact", kwargs))


class _StubState:
    @staticmethod
    def get_global(_key):
        return None


class _StubConfig:
    @staticmethod
    def get_workflow_config(_workflow_id):
        return None


class _StubSession:
    """Minimal session surface used by every TracerWorkflowUtils event."""

    def __init__(self, tracer, session_id="sess-1", execution_id=None):
        self._tracer = tracer
        self._session_id = session_id
        self._execution_id = execution_id

    def tracer(self):
        return self._tracer

    def session_id(self):
        return self._session_id

    def get_session_id(self):
        return self._session_id

    def get_execution_id(self):
        return self._execution_id

    def workflow_id(self):
        return "wf_root"

    def main_workflow_id(self):
        return "wf_root"

    def workflow_nesting_depth(self):
        return 0

    def executable_id(self):
        return "node_a"

    def parent_id(self):
        return ""

    def node_id(self):
        return "node_a"

    def node_name(self):
        return "node_a"

    def node_type(self):
        return "Flow"

    def state(self):
        return _StubState()

    def config(self):
        return _StubConfig()

    def get_env_scope(self):
        return {}

    def parent(self):
        return None


def _make_tracer() -> tuple[Tracer, _RecordingWorkflowHandler]:
    handler = _RecordingWorkflowHandler()
    TracerHandlerRegistry.register_handler("recording_workflow", handler)
    tracer = Tracer(session_id="sess-1", trace_id="trace-1")
    tracer.init()
    return tracer, handler


_CASES = [
    ("workflow_start", lambda s: TracerWorkflowUtils.trace_workflow_start(s, {"a": 1})),
    ("component_begin", lambda s: TracerWorkflowUtils.trace_component_begin(s)),
    ("component_inputs", lambda s: TracerWorkflowUtils.trace_component_inputs(s, {"a": 1})),
    ("component_stream_input", lambda s: TracerWorkflowUtils.trace_component_stream_input(s, {"c": 1})),
    ("component_outputs", lambda s: TracerWorkflowUtils.trace_component_outputs(s, {"o": 1})),
    ("component_stream_output", lambda s: TracerWorkflowUtils.trace_component_stream_output(s, {"c": 1})),
    ("component_finalize", lambda s: TracerWorkflowUtils.trace_component_finalize(s)),
    ("workflow_done", lambda s: TracerWorkflowUtils.trace_workflow_done(s, {"o": 1})),
    ("component_done", lambda s: TracerWorkflowUtils.trace_component_done(s)),
    ("trace", lambda s: TracerWorkflowUtils.trace(s, {"d": 1})),
    ("trace_error", lambda s: TracerWorkflowUtils.trace_error(s, RuntimeError("x"))),
    ("interactive_inputs", lambda s: TracerWorkflowUtils.trace_component_interactive_inputs(s, {"i": 1})),
]


@pytest.mark.parametrize("_name, call", _CASES, ids=[case[0] for case in _CASES])
async def test_all_workflow_events_carry_execution_id(_name, call):
    tracer, handler = _make_tracer()
    session = _StubSession(tracer, execution_id="exec-1")

    await call(session)

    assert handler.calls, "event was not delivered to the extension handler"
    assert handler.calls[-1][1].get("execution_id") == "exec-1"


@pytest.mark.parametrize("_name, call", _CASES, ids=[case[0] for case in _CASES])
async def test_workflow_events_fall_back_to_session_id(_name, call):
    tracer, handler = _make_tracer()
    session = _StubSession(tracer, execution_id=None)

    await call(session)

    assert handler.calls[-1][1].get("execution_id") == "sess-1"


async def test_workflow_start_metadata_only_writes_real_execution_id():
    tracer, handler = _make_tracer()

    session = _StubSession(tracer, execution_id="exec-1")
    await TracerWorkflowUtils.trace_workflow_start(session, {"a": 1})
    assert handler.calls[-1][1]["metadata"]["execution_id"] == "exec-1"

    session = _StubSession(tracer, execution_id=None)
    await TracerWorkflowUtils.trace_workflow_start(session, {"a": 1})
    assert "execution_id" not in handler.calls[-1][1]["metadata"]


async def test_trigger_injects_trace_id_and_keeps_explicit_value():
    tracer, handler = _make_tracer()

    await tracer.trigger(_EVENT, "on_call_start", invoke_id="wf_root")
    assert handler.calls[-1][1]["trace_id"] == "trace-1"

    await tracer.trigger(_EVENT, "on_call_start", invoke_id="wf_root", trace_id="explicit")
    assert handler.calls[-1][1]["trace_id"] == "explicit"


class _ThrowingExecutionGetter:
    def __init__(self):
        self._session_id = "sess-x"

    def get_execution_id(self):
        raise RuntimeError("broken session")

    def session_id(self):
        return self._session_id


async def test_execution_scope_priority_and_fallback():
    assert _execution_scope(_StubSession(None, execution_id="exec-1")) == "exec-1"
    assert _execution_scope(_StubSession(None, execution_id=None)) == "sess-1"
    assert _execution_scope(_ThrowingExecutionGetter()) == "sess-x"
    assert _execution_scope(object()) == ""
