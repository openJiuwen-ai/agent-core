# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OtelRail — OTel trace span lifecycle management via AgentRail callbacks.

Leverages ReActAgent's existing BEFORE_INVOKE / AFTER_INVOKE /
BEFORE_MODEL_CALL / AFTER_MODEL_CALL / ON_MODEL_EXCEPTION /
BEFORE_TOOL_CALL / AFTER_TOOL_CALL / ON_TOOL_EXCEPTION callback points
to create and finalize agent root span, LLM child spans, and tool child spans.

Usage (opt-in)::

    from openjiuwen.extensions.tracer_otel.otel_rail import OtelRail
    await agent.register_rail(OtelRail())
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.session.tracer.data import InvokeType
from openjiuwen.core.session.tracer.handler import TracerHandlerName
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentRail,
    InvokeInputs,
)


class OtelRail(AgentRail):
    """Rail that manages agent root span and LLM child span lifecycles.

    Hooks into agent callbacks to create OTel-compatible trace spans
    via the tracer infrastructure. Designed as an opt-in rail — only
    registered when OTel tracing is desired.

    priority=0 (lowest) ensures it runs LAST among callbacks of the same
    event: span creation in before hooks does not block other rails,
    and span finalization in after hooks occurs after all other rails
    have completed.
    """

    priority: int = 0

    def __init__(self) -> None:
        self._llm_spans: list = []
        self._tool_spans: list = []

    # ------------------------------------------------------------------
    # Root span (BEFORE_INVOKE / AFTER_INVOKE)
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        root_span = tracer.tracer_agent_span_manager.create_agent_span()
        instance_info = {
            "class_name": ctx.agent.card.name,
            "type": "agent",
            "agent_id": getattr(ctx.agent.card, "id", None),
        }

        inputs_dict = {"query": ctx.inputs.query} if isinstance(ctx.inputs, InvokeInputs) else {}

        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_chain_start",
            span=root_span,
            inputs=inputs_dict,
            instance_info=instance_info,
        )
        session.agent_span = root_span

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        root_span = session.agent_span

        if root_span is None:
            return

        if ctx.exception is not None:
            await tracer.trigger(
                TracerHandlerName.TRACE_AGENT.value,
                "on_chain_error",
                span=root_span,
                error=ctx.exception,
            )
        else:
            result = ctx.inputs.result if isinstance(ctx.inputs, InvokeInputs) else None
            await tracer.trigger(
                TracerHandlerName.TRACE_AGENT.value,
                "on_chain_end",
                span=root_span,
                outputs={"outputs": result},
            )

    # ------------------------------------------------------------------
    # LLM child spans (BEFORE_MODEL_CALL / AFTER_MODEL_CALL / ON_MODEL_EXCEPTION)
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        parent_span = session.agent_span
        llm_span = tracer.tracer_agent_span_manager.create_agent_span(parent_span)
        self._llm_spans.append(llm_span)

        # Build instance_info — prefer model name from agent config
        model_name = "LLM"
        agent_config = ctx.agent.config
        if agent_config is not None:
            config_model_name = ""
            if hasattr(agent_config, "model_config_obj"):
                config_model_name = getattr(agent_config.model_config_obj, "model_name", "")
            if not config_model_name:
                config_model_name = getattr(agent_config, "model_name", "")
            model_name = config_model_name or model_name
        instance_info = {"class_name": model_name, "type": InvokeType.LLM.value}
        request_params = self._extract_request_params(agent_config)
        if request_params:
            instance_info["request_params"] = request_params

        inputs_dict = {}
        if hasattr(ctx.inputs, "messages") and ctx.inputs.messages is not None:
            inputs_dict = {"messages": ctx.inputs.messages}
            instance_info["message_count"] = len(ctx.inputs.messages)

        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_llm_start",
            span=llm_span,
            inputs=inputs_dict,
            instance_info=instance_info,
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Finalize the LLM span on success.

        When ctx.exception is set, the error path (on_model_exception)
        has already consumed the span — skip here.
        """
        if ctx.exception is not None:
            return
        if not self._llm_spans:
            return

        llm_span = self._llm_spans.pop()
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        outputs_dict = {}
        if hasattr(ctx.inputs, "response") and ctx.inputs.response is not None:
            outputs_dict = {"outputs": ctx.inputs.response}

        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_llm_end",
            span=llm_span,
            outputs=outputs_dict,
        )

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Handle LLM call error — pop and mark the span as error."""
        if not self._llm_spans:
            return

        llm_span = self._llm_spans.pop()
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_llm_error",
            span=llm_span,
            error=ctx.exception,
        )

    # ------------------------------------------------------------------
    # Tool child spans (BEFORE_TOOL_CALL / AFTER_TOOL_CALL / ON_TOOL_EXCEPTION)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_request_params(agent_config: Any) -> dict[str, Any]:
        """Extract GenAI request parameters from the agent's model request config.

        ``top_k`` is not a declared ``ModelRequestConfig`` field — it rides in as
        an extra field (``extra="allow"``), so ``getattr`` covers both cases.
        """
        params: dict[str, Any] = {}
        if agent_config is None:
            return params
        model_config_obj = getattr(agent_config, "model_config_obj", None)
        if model_config_obj is None:
            return params
        for key in ("temperature", "top_p", "top_k", "max_tokens"):
            value = getattr(model_config_obj, key, None)
            if value is not None:
                params[key] = value
        return params

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        tool_span = tracer.tracer_agent_span_manager.create_agent_span(session.agent_span)
        self._tool_spans.append(tool_span)

        tool_call = getattr(ctx.inputs, "tool_call", None)
        tool_name = getattr(ctx.inputs, "tool_name", "") or (getattr(tool_call, "name", "") if tool_call else "")
        instance_info = {
            "class_name": tool_name,
            "type": InvokeType.PLUGIN.value,
            "tool_type": str(getattr(tool_call, "type", "") or "function") if tool_call else "function",
            "agent_name": getattr(ctx.agent.card, "name", ""),
        }

        inputs_dict: dict = {}
        if tool_call is not None:
            inputs_dict["id"] = str(getattr(tool_call, "id", "") or "")
            inputs_dict["name"] = str(getattr(tool_call, "name", "") or "")
            inputs_dict["type"] = str(getattr(tool_call, "type", "") or "")

        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_plugin_start",
            span=tool_span,
            inputs=inputs_dict,
            instance_info=instance_info,
        )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._tool_spans:
            return
        tool_span = self._tool_spans.pop()
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_plugin_end",
            span=tool_span,
            outputs=getattr(ctx.inputs, "tool_result", None),
        )

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        if not self._tool_spans:
            return
        tool_span = self._tool_spans.pop()
        session = ctx.session
        if session is None:
            return

        tracer = session.tracer()
        await tracer.trigger(
            TracerHandlerName.TRACE_AGENT.value,
            "on_plugin_error",
            span=tool_span,
            error=ctx.exception,
        )
