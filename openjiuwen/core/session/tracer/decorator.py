# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from functools import wraps
from types import MethodType

from docutils.nodes import description

from openjiuwen.core.session.utils import create_wrapper_class
from openjiuwen.core.session.tracer.data import InvokeType


def _should_decorate(obj, session):
    return (obj and
            session and
            hasattr(session, "tracer") and
            session.tracer() and
            hasattr(session, "span"))


def decorate_model_with_trace(model, agent_session):
    if not _should_decorate(model, agent_session):
        return model
    wrapped_model = create_wrapper_class(model, "WrappedModel")
    try:
        model_name = model.config.model_config.model_name
    except Exception:
        model_name = type(model).__name__
    instance_info = {"class_name": model_name, "type": "llm"}
    wrapped_model.invoke = MethodType(
        async_trace(wrapped_model.invoke, agent_session, InvokeType.LLM, instance_info,
                    index=2, inputs_field_name="messages"), wrapped_model)
    wrapped_model.stream = MethodType(
        async_trace_stream(wrapped_model.stream, agent_session, InvokeType.LLM, instance_info,
                           index=2, inputs_field_name="messages"), wrapped_model)
    return wrapped_model


def decorate_tool_with_trace(tool, agent_session):
    if not _should_decorate(tool, agent_session):
        return tool
    wrapped_tool = create_wrapper_class(tool, "WrappedTool")
    instance_info = {"class_name": tool.name if hasattr(tool, "name") else type(tool).__name__, "type": "tool"}
    wrapped_tool.invoke = MethodType(
        async_trace(wrapped_tool.invoke, agent_session, InvokeType.PLUGIN, instance_info), wrapped_tool
    )
    return wrapped_tool


def decorate_workflow_with_trace(workflow, agent_session):
    if not _should_decorate(workflow, agent_session):
        return workflow
    wrapped_workflow = create_wrapper_class(workflow, "WrappedWorkflow")
    metadata = dict(id=wrapped_workflow.card.id, name=wrapped_workflow.card.name,
                    description=wrapped_workflow.card.description,
                    version=wrapped_workflow.card.version) if wrapped_workflow else {}
    try:
        workflow_name = workflow.card.name
    except Exception:
        workflow_name = type(workflow).__name__
    instance_info = {"class_name": workflow_name, "type": "workflow", "metadata": dict(metadata)}
    wrapped_workflow.invoke = MethodType(
        async_trace(wrapped_workflow.invoke, agent_session, InvokeType.WORKFLOW, instance_info),
        wrapped_workflow)
    wrapped_workflow.stream = MethodType(
        async_trace_stream(wrapped_workflow.stream, agent_session, InvokeType.WORKFLOW, instance_info),
        wrapped_workflow)
    return wrapped_workflow


def trace(func, session, invoke_type: InvokeType, instance_info, index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    def decorator(*args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                inputs={"inputs": args[index] if args and len(args) > index
                                else kwargs.get(inputs_field_name, {})},
                                instance_info=instance_info)

            args = args[1:]
            result = func(*args, **kwargs)
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_end", span=span,
                                outputs={"outputs": result})
            return result
        except Exception as error:
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_error", span=span, error=error)
            raise error

    return decorator


def async_trace(func, session, invoke_type: InvokeType, instance_info,
                index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    async def decorator(*args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                 inputs={"inputs": args[index] if args and len(args) > index
                                 else kwargs.get(inputs_field_name, {})},
                                 instance_info=instance_info)

            args = args[1:]

            # record llm request data
            async def tracer_record_data(**record_kwargs):
                await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_request", span=span, **record_kwargs)

            call_kwargs = dict(kwargs)
            if invoke_type.value == InvokeType.LLM.value:
                call_kwargs["tracer_record_data"] = tracer_record_data
            result = await func(*args, **call_kwargs)

            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_end", span=span,
                                 outputs={"outputs": result})
            return result
        except Exception as error:
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_error", span=span, error=error)
            raise error

    return decorator


def trace_stream(func, session, invoke_type: InvokeType, instance_info,
                 index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    def decorator(*args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                inputs={"inputs": args[index] if args and len(args) > index
                                else kwargs.get(inputs_field_name, {})},
                                instance_info=instance_info)
            args = args[1:]
            result = func(*args, **kwargs)
            results = []
            if hasattr(result, "__iter__") or hasattr(result, "__getitem__"):
                for item in result:
                    yield item
                    results.append(item)
            else:
                results.append(result)
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_end", span=span,
                                outputs={"outputs": result})
        except Exception as error:
            tracer.sync_trigger("tracer_agent", "on_" + invoke_type.value + "_error", span=span, error=error)
            raise error

    return decorator


def async_trace_stream(func, session, invoke_type: InvokeType, instance_info,
                       index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    async def decorator(*args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                 inputs={"inputs": args[index] if args and len(args) > index
                                 else kwargs.get(inputs_field_name, {})},
                                 instance_info=instance_info)
            args = args[1:]

            # record llm request data
            async def tracer_record_data(**record_kwargs):
                await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_request", span=span, **record_kwargs)

            call_kwargs = dict(kwargs)
            if invoke_type.value == InvokeType.LLM.value:
                call_kwargs["tracer_record_data"] = tracer_record_data
            result = func(*args, **call_kwargs)

            results = []
            if hasattr(result, "__aiter__") or hasattr(result, "__anext__"):
                async for item in result:
                    results.append(item)
                    yield item
            else:
                results.append(result)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_end", span=span,
                                 outputs={"outputs": results})
        except Exception as error:
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_error", span=span, error=error)
            raise error

    return decorator
