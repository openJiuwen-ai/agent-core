# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from functools import wraps
from types import MethodType

from openjiuwen.core.runtime.utils import create_wrapper_class
from openjiuwen.core.tracer.data import InvokeType


def _should_decorate(obj, runtime):
    return (obj and
            runtime and
            runtime.tracer() and
            hasattr(runtime, "span"))


def decorate_model_with_trace(model, agent_runtime):
    if not _should_decorate(model, agent_runtime):
        return model
    wrapped_model = create_wrapper_class(model, "WrappedModel")
    try:
        model_name = model.config.model.model_info.model_name
    except Exception:
        model_name = type(model).__name__
    instance_info = {"class_name": model_name, "type": "llm"}
    wrapped_model.invoke = MethodType(
        trace(wrapped_model.invoke, agent_runtime, InvokeType.LLM, instance_info,
              index=2, inputs_field_name="messages"), wrapped_model)
    wrapped_model.ainvoke = MethodType(
        async_trace(wrapped_model.ainvoke, agent_runtime, InvokeType.LLM, instance_info,
                    index=2, inputs_field_name="messages"), wrapped_model)
    wrapped_model.stream = MethodType(
        trace_stream(wrapped_model.stream, agent_runtime, InvokeType.LLM, instance_info,
                     index=2, inputs_field_name="messages"), wrapped_model)
    wrapped_model.astream = MethodType(
        async_trace_stream(wrapped_model.astream, agent_runtime, InvokeType.LLM, instance_info, index=2,
                           inputs_field_name="messages"), wrapped_model)
    return wrapped_model


def decorate_tool_with_trace(tool, agent_runtime):
    if not _should_decorate(tool, agent_runtime):
        return tool
    wrapped_tool = create_wrapper_class(tool, "WrappedTool")
    instance_info = {"class_name": tool.name if hasattr(tool, "name") else type(tool).__name__, "type": "tool"}
    wrapped_tool.invoke = MethodType(
        trace(wrapped_tool.invoke, agent_runtime, InvokeType.PLUGIN, instance_info), wrapped_tool)
    wrapped_tool.ainvoke = MethodType(
        async_trace(wrapped_tool.ainvoke, agent_runtime, InvokeType.PLUGIN, instance_info), wrapped_tool)
    return wrapped_tool


def decorate_workflow_with_trace(workflow, agent_runtime):
    if not _should_decorate(workflow, agent_runtime):
        return workflow
    wrapped_workflow = create_wrapper_class(workflow, "WrappedWorkflow")
    metadata = wrapped_workflow.config().metadata if wrapped_workflow and wrapped_workflow.config() else {}
    try:
        workflow_name = workflow.config().metadata.name
    except Exception:
        workflow_name = type(workflow).__name__
    instance_info = {"class_name": workflow_name, "type": "workflow", "metadata": dict(metadata)}
    wrapped_workflow.invoke = MethodType(
        async_trace(wrapped_workflow.invoke, agent_runtime, InvokeType.WORKFLOW, instance_info),
        wrapped_workflow)
    wrapped_workflow.stream = MethodType(
        async_trace_stream(wrapped_workflow.stream, agent_runtime, InvokeType.WORKFLOW, instance_info),
        wrapped_workflow)
    return wrapped_workflow


def trace(func, runtime, invoke_type: InvokeType, instance_info, index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    def decorator(*args, **kwargs):
        tracer = runtime.tracer()
        span = None
        try:
            agent_span = runtime.span()
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


def async_trace(func, runtime, invoke_type: InvokeType, instance_info,
                index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    async def decorator(*args, **kwargs):
        tracer = runtime.tracer()
        span = None
        try:
            agent_span = runtime.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                 inputs={"inputs": args[index] if args and len(args) > index
                                 else kwargs.get(inputs_field_name, {})},
                                 instance_info=instance_info)

            args = args[1:]
            result = await func(*args, **kwargs)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_end", span=span,
                                 outputs={"outputs": result})
            return result
        except Exception as error:
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_error", span=span, error=error)
            raise error

    return decorator


def trace_stream(func, runtime, invoke_type: InvokeType, instance_info,
                 index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    def decorator(*args, **kwargs):
        tracer = runtime.tracer()
        span = None
        try:
            agent_span = runtime.span()
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


def async_trace_stream(func, runtime, invoke_type: InvokeType, instance_info,
                       index: int = 1, inputs_field_name: str = "inputs"):
    @wraps(func)
    async def decorator(*args, **kwargs):
        tracer = runtime.tracer()
        span = None
        try:
            agent_span = runtime.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger("tracer_agent", "on_" + invoke_type.value + "_start", span=span,
                                 inputs={"inputs": args[index] if args and len(args) > index
                                 else kwargs.get(inputs_field_name, {})},
                                 instance_info=instance_info)
            args = args[1:]
            result = func(*args, **kwargs)
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
