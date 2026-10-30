# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Concurrency regression test for ``Tracer`` + shared extension handlers.

Handlers registered via ``TracerHandlerRegistry`` are process-wide singletons,
and ``Tracer.init()`` writes the owning tracer's ``_trace_id`` into them. When
two tracers are alive at the same time the later ``init()`` used to overwrite
the earlier tracer's id, so events were mis-attributed (spans lost / mixed up).

The fix makes ``Tracer.trigger`` forward the owning tracer's identity with every
event, so a handler can attribute the event deterministically regardless of how
many other tracers are alive.
"""
import unittest

from openjiuwen.core.session.tracer.handler import (
    TraceExtWorkflowHandler,
    TracerHandlerName,
)
from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry


class CapturingWorkflowHandler(TraceExtWorkflowHandler):
    """Minimal workflow extension handler recording the identity of each event."""

    def __init__(self):
        super().__init__()
        self.trace_ids = []
        self.session_ids = []

    async def on_call_start(self, invoke_id, metadata=None, inputs=None,
                            need_send=False, source_ids=None, **kwargs):
        return None

    async def on_call_done(self, invoke_id, outputs=None, **kwargs):
        # Prefer the per-event injected value; fall back to the instance field.
        self.trace_ids.append(kwargs.get("trace_id") or self._trace_id)
        self.session_ids.append(kwargs.get("session_id"))

    async def on_pre_invoke(self, invoke_id, inputs, component_metadata,
                            need_send=False, **kwargs):
        return None

    async def on_pre_stream(self, invoke_id, chunk, need_send=False, **kwargs):
        return None

    async def on_invoke(self, invoke_id, on_invoke_data=None,
                        exception=None, **kwargs):
        return None

    async def on_post_invoke(self, invoke_id, outputs, inputs=None, **kwargs):
        return None

    async def on_post_stream(self, invoke_id, chunk, **kwargs):
        return None

    async def on_interact(self, invoke_id, inputs, component_metadata,
                          need_send=False, **kwargs):
        return None


class TestTracerConcurrency(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        TracerHandlerRegistry.clear()

    async def asyncTearDown(self):
        TracerHandlerRegistry.clear()

    async def _call_done(self, tracer):
        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value,
            "on_call_done",
            invoke_id="workflow-1",
            outputs={},
        )

    async def test_trigger_injects_owning_trace_id_under_concurrency(self):
        handler = CapturingWorkflowHandler()
        TracerHandlerRegistry.register_handler("capturing", handler)

        first, second = Tracer("session-1"), Tracer("session-2")

        first.init()
        await self._call_done(first)
        before = handler.trace_ids[-1]

        second.init()  # the later init() overwrites the shared handler singleton
        await self._call_done(first)
        after_other_init = handler.trace_ids[-1]

        await self._call_done(second)
        from_second = handler.trace_ids[-1]

        # first's events must keep first's identity no matter what second does ...
        self.assertEqual(before, first._trace_id)
        self.assertEqual(after_other_init, first._trace_id)
        # ... and second's event must be attributed to second, not first.
        self.assertEqual(from_second, second._trace_id)
        # the owning session_id must be injected alongside the trace_id.
        self.assertEqual(handler.session_ids, ["session-1", "session-1", "session-2"])

    async def test_trigger_does_not_override_explicit_trace_id(self):
        handler = CapturingWorkflowHandler()
        TracerHandlerRegistry.register_handler("capturing", handler)

        tracer = Tracer("session-1")
        tracer.init()

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value,
            "on_call_done",
            invoke_id="workflow-1",
            outputs={},
            trace_id="explicit-trace-id",
        )

        self.assertEqual(handler.trace_ids, ["explicit-trace-id"])

    async def test_trigger_does_not_override_explicit_session_id(self):
        handler = CapturingWorkflowHandler()
        TracerHandlerRegistry.register_handler("capturing", handler)

        tracer = Tracer("session-1")
        tracer.init()

        await tracer.trigger(
            TracerHandlerName.TRACER_WORKFLOW.value,
            "on_call_done",
            invoke_id="workflow-1",
            outputs={},
            session_id="explicit-session-id",
        )

        self.assertEqual(handler.session_ids, ["explicit-session-id"])