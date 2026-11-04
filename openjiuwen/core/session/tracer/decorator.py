# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import time
from typing import Any

from openjiuwen.core.runner.callback.decorator import WrapHandler, create_wrap_decorator
from openjiuwen.core.session.tracer.data import InvokeType

# Model.invoke/stream keyword parameters reported as GenAI request params.
# "stop" is renamed to its semantic-convention name "stop_sequences" below.
_LLM_REQUEST_PARAM_KWARGS = ("temperature", "top_p", "top_k", "max_tokens", "reasoning_level")


class _TraceProxy:
    """Lightweight proxy that shadows specific methods with traced versions."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _should_decorate(obj, session):
    return (obj and
            session and
            hasattr(session, "tracer") and
            session.tracer() and
            session.tracer().agent_handler_valid() and
            hasattr(session, "span"))


def _llm_call_info(instance_info: dict, inputs_value: Any, kwargs: dict) -> dict:
    """Per-call enrichment of the decoration-time instance_info for LLM calls.

    Adds the same request-params / message-count facts OtelRail builds for
    rail-raised events, so decorator-raised on_llm_start carries them too
    (read by on_llm_start and its merge path). Non-LLM invoke types never
    call this — their payloads stay untouched.
    """
    call_info = dict(instance_info)
    request_params = {key: kwargs[key] for key in _LLM_REQUEST_PARAM_KWARGS if kwargs.get(key) is not None}
    stop = kwargs.get("stop")
    if stop is not None:
        # Same shape contract as OtelRail._extract_request_params: the
        # gen_ai.request.stop_sequences semconv attribute is a str list.
        stops = stop if isinstance(stop, (list, tuple)) else [stop]
        request_params["stop_sequences"] = [str(s) for s in stops]
    call_info["request_params"] = request_params
    if isinstance(inputs_value, (list, tuple)):
        call_info["message_count"] = len(inputs_value)
    return call_info


def _llm_usage_snapshot(item: Any) -> dict | None:
    """Plain-dict copy of an LLM result's usage metadata; never the live object.

    Returns ``None`` when the item carries no ``usage_metadata`` (non-LLM
    results, intermediate stream chunks)."""
    usage = getattr(item, "usage_metadata", None)
    if usage is None:
        return None
    try:
        dumped = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    except (TypeError, ValueError):
        return None
    return dumped if isinstance(dumped, dict) else None


def _mean_inter_token_latency_ms(usage: dict | None, first_chunk_at: float | None,
                                 last_chunk_at: float | None, chunk_count: int) -> float | None:
    """Mean inter-token latency (TPOT) in ms for a streamed LLM call.

    Token 口径优先（output_tokens - 1，与 ReAct 引擎回填同口径、跨模型可比）；
    usage 缺 token 数时回退块级（chunk_count - 1，与 planner 侧同口径，块大小
    因模型而异仅作近似）。单块 / 零间隔 / 无时间戳返回 None——缺席即诚实，不造 0。
    """
    if first_chunk_at is None or last_chunk_at is None:
        return None
    span_s = last_chunk_at - first_chunk_at
    if span_s <= 0:
        return None
    output_tokens = (usage or {}).get("output_tokens")
    if isinstance(output_tokens, (int, float)) and output_tokens > 1:
        return span_s / (output_tokens - 1) * 1000
    if chunk_count > 1:
        return span_s / (chunk_count - 1) * 1000
    return None


def _llm_end_payload(result: Any, ttft_seconds: float | None, usage: dict | None = None,
                     tpot_ms: float | None = None) -> dict:
    """Build the on_llm_end outputs payload with usage as a "outputs" sibling.

    The tracer-otel handler reads a container-level "usage_metadata" fallback
    in _set_llm_response_attrs: stream results are a chunk list, so usage is
    not reachable off the unwrapped payload itself. For non-streaming calls
    there is no first-token event, so the full call duration is the honest
    time to first (== final) response. Streams pass the snapshot taken off
    the last usage-bearing chunk via ``usage``. A ttft already stamped by a
    streaming producer (e.g. the ReAct engine, #1833) is authoritative and
    is never overwritten; the same precedence applies to the wrapper-measured
    mean inter-token latency (``tpot_ms``)."""
    payload = {"outputs": result}
    if usage is None:
        usage = _llm_usage_snapshot(result)
    if usage is not None:
        # Guard on a None value, not just key presence: UsageMetadata's
        # model_dump always carries the key, and setdefault would leave an
        # unreported ttft as None instead of the measured one.
        if ttft_seconds is not None and usage.get("ttft_seconds") is None:
            usage["ttft_seconds"] = max(ttft_seconds, 0.0)
        if tpot_ms is not None and usage.get("inter_token_latency_ms") is None:
            usage["inter_token_latency_ms"] = round(tpot_ms, 6)
        payload["usage_metadata"] = usage
    return payload


def _make_trace_wrap_handler(
    session,
    invoke_type: InvokeType,
    instance_info: dict,
    index: int = 0,
    inputs_field_name: str = "inputs",
) -> WrapHandler:
    async def handler(call_next, /, *args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            inputs_value = args[index] if len(args) > index else kwargs.get(inputs_field_name, {})
            call_info = _llm_call_info(instance_info, inputs_value, kwargs) if invoke_type == InvokeType.LLM \
                else instance_info
            await tracer.trigger(
                "tracer_agent", f"on_{invoke_type.value}_start",
                span=span,
                inputs={"inputs": inputs_value},
                instance_info=call_info,
            )
            call_kwargs = dict(kwargs)
            if invoke_type == InvokeType.LLM:
                async def tracer_record_data(**kw):
                    await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_request", span=span, **kw)
                call_kwargs["tracer_record_data"] = tracer_record_data
                started_at = time.monotonic()
            result = await call_next(*args, **call_kwargs)
            if invoke_type == InvokeType.LLM:
                end_outputs = _llm_end_payload(result, time.monotonic() - started_at)
            else:
                end_outputs = {"outputs": result}
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_end",
                                 span=span, outputs=end_outputs)
            return result
        except Exception as error:
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_error", span=span, error=error)
            raise

    return handler


def async_trace(func, session, invoke_type: InvokeType, instance_info,
                index: int = 0, inputs_field_name: str = "inputs"):
    handler = _make_trace_wrap_handler(session, invoke_type, instance_info, index, inputs_field_name)
    return create_wrap_decorator(handler)(func)


def _make_trace_stream_wrap_handler(
    session,
    invoke_type: InvokeType,
    instance_info: dict,
    index: int = 0,
    inputs_field_name: str = "inputs",
) -> WrapHandler:
    async def handler(call_next, /, *args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            inputs_value = args[index] if len(args) > index else kwargs.get(inputs_field_name, {})
            call_info = _llm_call_info(instance_info, inputs_value, kwargs) if invoke_type == InvokeType.LLM \
                else instance_info
            await tracer.trigger(
                "tracer_agent", f"on_{invoke_type.value}_start",
                span=span,
                inputs={"inputs": inputs_value},
                instance_info=call_info,
            )
            call_kwargs = dict(kwargs)
            if invoke_type == InvokeType.LLM:
                async def tracer_record_data(**kw):
                    await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_request", span=span, **kw)
                call_kwargs["tracer_record_data"] = tracer_record_data
            results = []
            started_at = time.monotonic() if invoke_type == InvokeType.LLM else None
            first_chunk_at = None
            last_chunk_at = None
            chunk_count = 0
            final_usage = None
            async for item in call_next(*args, **call_kwargs):
                if invoke_type == InvokeType.LLM:
                    now = time.monotonic()
                    if first_chunk_at is None:
                        first_chunk_at = now
                    last_chunk_at = now
                    chunk_count += 1
                    # Usage-only final frame: keep the freshest snapshot seen.
                    if getattr(item, "usage_metadata", None) is not None:
                        final_usage = _llm_usage_snapshot(item)
                results.append(item)
                yield item
            if invoke_type == InvokeType.LLM:
                ttft = (first_chunk_at - started_at) if first_chunk_at is not None else None
                tpot_ms = _mean_inter_token_latency_ms(
                    final_usage, first_chunk_at, last_chunk_at, chunk_count
                )
                end_outputs = _llm_end_payload(results, ttft, usage=final_usage, tpot_ms=tpot_ms)
            else:
                end_outputs = {"outputs": results}
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_end",
                                 span=span, outputs=end_outputs)
        except Exception as error:
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_error", span=span, error=error)
            raise

    return handler


def async_trace_stream(func, session, invoke_type: InvokeType, instance_info,
                       index: int = 0, inputs_field_name: str = "inputs"):
    handler = _make_trace_stream_wrap_handler(session, invoke_type, instance_info, index, inputs_field_name)

    async def _as_async_gen(*args, **kwargs):
        """Normalize func to async generator so create_wrap_decorator takes the generator path."""
        result = func(*args, **kwargs)
        if hasattr(result, "__aiter__") or hasattr(result, "__anext__"):
            async for item in result:
                yield item

    return create_wrap_decorator(handler)(_as_async_gen)


def decorate_model_with_trace(model, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return model
    session = getattr(agent_session, "_inner")
    if not _should_decorate(model, session):
        return model
    try:
        model_name = model.config.model_config.model_name
    except Exception:
        model_name = type(model).__name__
    instance_info = {"class_name": model_name, "type": "llm"}
    proxy = _TraceProxy(model)
    proxy.invoke = async_trace(model.invoke, session, InvokeType.LLM, instance_info,
                               index=0, inputs_field_name="messages")
    proxy.stream = async_trace_stream(model.stream, session, InvokeType.LLM, instance_info,
                                      index=0, inputs_field_name="messages")
    return proxy


def decorate_tool_with_trace(tool, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return tool
    session = getattr(agent_session, "_inner")
    if not _should_decorate(tool, session):
        return tool
    instance_info = {"class_name": tool.card.name if hasattr(tool, "card") else type(tool).__name__, "type": "tool"}
    proxy = _TraceProxy(tool)
    proxy.invoke = async_trace(tool.invoke, session, InvokeType.PLUGIN, instance_info)
    return proxy


def decorate_workflow_with_trace(workflow, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return workflow
    session = getattr(agent_session, "_inner")
    if not _should_decorate(workflow, session):
        return workflow
    metadata = dict(id=workflow.card.id, name=workflow.card.name,
                    description=workflow.card.description,
                    version=workflow.card.version) if workflow else {}
    try:
        workflow_name = workflow.card.name
    except Exception:
        workflow_name = type(workflow).__name__
    instance_info = {"class_name": workflow_name, "type": "workflow", "metadata": dict(metadata)}
    proxy = _TraceProxy(workflow)
    proxy.invoke = async_trace(workflow.invoke, session, InvokeType.WORKFLOW, instance_info)
    proxy.stream = async_trace_stream(workflow.stream, session, InvokeType.WORKFLOW, instance_info)
    return proxy
