# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid

from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.tracer.handler import (
    TraceAgentHandler, TraceWorkflowHandler,
    TracerHandlerName,
    TraceExtAgentHandler, TraceExtWorkflowHandler,
)
from openjiuwen.core.session.tracer.span import SpanManager


class TracerHandlerRegistry:
    """Global registry for tracer extension handlers.

    External handlers should be registered here before calling agent.invoke or
    workflow.invoke. All Tracer instances created afterwards will automatically
    pick up the registered handlers.

    Usage:
        from openjiuwen.core.session.tracer import TracerHandlerRegistry

        TracerHandlerRegistry.register_handler("otel_agent", my_otel_agent_handler)
        TracerHandlerRegistry.register_handler("otel_workflow", my_otel_workflow_handler)
    """
    _agent_handlers: dict[str, TraceExtAgentHandler] = {}
    _workflow_handlers: dict[str, TraceExtWorkflowHandler] = {}

    _RESERVED_NAMES = {name.value for name in TracerHandlerName}

    @classmethod
    def register_handler(cls, handler_name: str, handler: TraceExtAgentHandler | TraceExtWorkflowHandler):
        """Register an extension handler globally.

        Args:
            handler_name: Unique name for the handler.
            handler: TraceExtAgentHandler or TraceExtWorkflowHandler instance.

        Raises:
            ValueError: If handler_name is already registered, is a reserved name,
                or handler type is wrong.
        """
        if handler_name in cls._RESERVED_NAMES:
            raise ValueError(
                f"Handler '{handler_name}' is a reserved name for built-in handlers, "
                f"cannot be used for extension handler registration"
            )
        if handler_name in cls._agent_handlers or handler_name in cls._workflow_handlers:
            raise ValueError(f"Handler '{handler_name}' already registered")
        if isinstance(handler, TraceExtAgentHandler):
            cls._agent_handlers[handler_name] = handler
        elif isinstance(handler, TraceExtWorkflowHandler):
            cls._workflow_handlers[handler_name] = handler
        else:
            raise ValueError(
                f"Handler '{handler_name}' must be TraceExtAgentHandler or "
                f"TraceExtWorkflowHandler, got {type(handler).__name__}"
            )

    @classmethod
    def get_agent_handlers(cls) -> dict[str, TraceExtAgentHandler]:
        """Return all registered agent handlers."""
        return dict(cls._agent_handlers)

    @classmethod
    def get_workflow_handlers(cls) -> dict[str, TraceExtWorkflowHandler]:
        """Return all registered workflow handlers."""
        return dict(cls._workflow_handlers)

    @classmethod
    def unregister_handler(cls, handler_name: str):
        """Remove a previously registered handler by name.

        Args:
            handler_name: Name of the handler to remove.

        Returns:
            True if the handler was found and removed, False otherwise.
        """
        if handler_name in cls._agent_handlers:
            cls._agent_handlers.pop(handler_name)
            return True
        if handler_name in cls._workflow_handlers:
            cls._workflow_handlers.pop(handler_name)
            return True
        return False

    @classmethod
    def clear(cls):
        """Remove all registered handlers. Useful for test cleanup."""
        cls._agent_handlers.clear()
        cls._workflow_handlers.clear()


class Tracer:
    def __init__(self):
        self._trace_id = str(uuid.uuid4())
        self.tracer_agent_span_manager = SpanManager(self._trace_id)
        self.tracer_workflow_span_manager_dict = {}
        self._agent_handlers = {}
        self._workflow_handlers = {}
        self._stream_writer_manager = None

    def init(self, stream_writer_manager=None):
        """Initialize tracer: pick up extension handlers and, if trace writer exists, register built-in handlers.

        If stream_writer_manager is provided and has a trace writer (i.e., BaseStreamMode.TRACE
        is enabled), built-in TraceSchema handlers are automatically registered.
        Extension handlers are always picked up from TracerHandlerRegistry regardless.

        Args:
            stream_writer_manager: Optional StreamWriterManager. When provided with a trace
                writer, built-in TraceSchema handlers are registered for chunk output.
        """
        self._stream_writer_manager = stream_writer_manager
        # Pick up globally registered extension handlers
        for name, handler in TracerHandlerRegistry.get_agent_handlers().items():
            self._agent_handlers[name] = handler
        for name, handler in TracerHandlerRegistry.get_workflow_handlers().items():
            self._workflow_handlers[name] = handler

        # Register built-in TraceSchema handlers only when trace writer is available
        if stream_writer_manager is not None and stream_writer_manager.get_trace_writer() is not None:
            agent_handler = TraceAgentHandler(stream_writer_manager, self.tracer_agent_span_manager)
            parent_wf_span_manager = SpanManager(self._trace_id)
            wf_handler = TraceWorkflowHandler(stream_writer_manager, parent_wf_span_manager)
            self.tracer_workflow_span_manager_dict[""] = parent_wf_span_manager
            self._agent_handlers[TracerHandlerName.TRACE_AGENT.value] = agent_handler
            self._workflow_handlers[TracerHandlerName.TRACER_WORKFLOW.value] = {
                TracerHandlerName.TRACER_WORKFLOW.value: wf_handler
            }

    def register_workflow_span_manager(self, parent_node_id: str):
        span_manager = SpanManager(self._trace_id, parent_node_id=parent_node_id)
        self.tracer_workflow_span_manager_dict[parent_node_id] = span_manager
        # Only create TraceWorkflowHandler when TraceSchema output is enabled
        # (i.e., init() was called with a stream_writer_manager that has a trace writer)
        if self._stream_writer_manager is not None:
            handler = TraceWorkflowHandler(self._stream_writer_manager, span_manager)
            trace_schema_dict = self._workflow_handlers.get(TracerHandlerName.TRACER_WORKFLOW.value)
            if trace_schema_dict is not None:
                trace_schema_dict[TracerHandlerName.TRACER_WORKFLOW.value + "." + parent_node_id] = handler

    def get_workflow_span(self, invoke_id: str, parent_node_id: str):
        workflow_span_manager = self.tracer_workflow_span_manager_dict.get(parent_node_id, None)
        if workflow_span_manager is None:
            return None
        return workflow_span_manager.get_span(invoke_id)

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        parent_node_id = kwargs.get("parent_node_id", None)
        # Normalize parent_node_id: None means root (same as "")
        effective_parent = parent_node_id if parent_node_id is not None else ""

        if handler_class_name == TracerHandlerName.TRACE_AGENT.value:
            # Iterate over all agent handlers (built-in + extension)
            for _name, handler in self._agent_handlers.items():
                if handler is not None and hasattr(handler, event_name):
                    await getattr(handler, event_name)(**kwargs)
            return

        if handler_class_name == TracerHandlerName.TRACER_WORKFLOW.value:
            # 1. Built-in trace_schema handler: O(1) lookup by concatenated key
            trace_schema_dict = self._workflow_handlers.get(TracerHandlerName.TRACER_WORKFLOW.value, {})
            lookup_key = handler_class_name
            if effective_parent != "":
                lookup_key += "." + effective_parent
            builtin_handler = trace_schema_dict.get(lookup_key)
            if builtin_handler and hasattr(builtin_handler, event_name):
                await getattr(builtin_handler, event_name)(**kwargs)
            # 2. Extension workflow handlers: invoke directly
            for name, handler in self._workflow_handlers.items():
                if name == TracerHandlerName.TRACER_WORKFLOW.value:
                    continue
                if handler is not None and hasattr(handler, event_name):
                    await getattr(handler, event_name)(**kwargs)
            return

        session_logger.warning(
            "Unknown handler_class_name in tracer trigger, skipping",
            event_type=LogEventType.SYSTEM_ERROR,
            metadata={"handler_class_name": handler_class_name, "event_name": event_name}
        )

    def sync_trigger(self, handler_class_name: str, event_name: str, **kwargs):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self.trigger(handler_class_name, event_name, **kwargs), loop)
        else:
            loop.run_until_complete(self.trigger(handler_class_name, event_name, **kwargs))

    def pop_workflow_span(self, invoke_id: str, parent_node_id: str):
        if parent_node_id not in self.tracer_workflow_span_manager_dict:
            return
        self.tracer_workflow_span_manager_dict.get(parent_node_id).pop_span(invoke_id)
