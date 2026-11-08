# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from openjiuwen.core.session.tracer.data import InvokeType
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.session.tracer.span import TraceAgentSpan


class TracerAgentUtils:
    """Static helpers for emitting agent tracer events from tool bodies.

    decorator.py emits agent events by wrapping whole tool/model/workflow
    invocations.  These helpers complement it: a tool body that performs a
    distinct sub-operation (knowledge retrieval inside a retrieval tool)
    can emit its own event pair so handlers open a child span for the
    sub-operation under the wrapping tool span.

    The retriever events are the long-dormant first-class family declared on
    the handler interface (on_retriever_start/end/error) — reuse them rather
    than adding a parallel family.  The built-in TraceAgentHandler already
    implements the family, so frontend trace-schema compatibility is native.
    """

    @staticmethod
    async def trace_retriever_start(
        session, query: Optional[str] = None, instance_name: str = ""
    ) -> Optional[TraceAgentSpan]:
        """Emit on_retriever_start for a knowledge search beginning now.

        The returned span must be passed back to trace_retriever_end /
        trace_retriever_error.  Returns None when tracing is unavailable so
        callers can skip the matching end event.
        """
        tracer = session.tracer() if session is not None else None
        if tracer is None or not tracer.agent_handler_valid():
            return None
        span = tracer.tracer_agent_span_manager.create_agent_span(None)
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_retriever_start",
            span=span,
            inputs={"query": query} if query is not None else None,
            instance_info={
                "class_name": instance_name or "retrieval",
                "type": InvokeType.RETRIEVER.value,
            },
        )
        return span

    @staticmethod
    async def trace_retriever_end(
        session, span: Optional[TraceAgentSpan], documents: Optional[list] = None
    ) -> None:
        """Emit on_retriever_end for a knowledge search that succeeded."""
        if span is None:
            return
        tracer = session.tracer() if session is not None else None
        if tracer is None:
            return
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_retriever_end",
            span=span,
            outputs={"results": documents} if documents is not None else None,
        )

    @staticmethod
    async def trace_retriever_error(session, span: Optional[TraceAgentSpan], error) -> None:
        """Emit on_retriever_error for a knowledge search that failed."""
        if span is None:
            return
        tracer = session.tracer() if session is not None else None
        if tracer is None:
            return
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_retriever_error",
            span=span,
            error=error,
        )
