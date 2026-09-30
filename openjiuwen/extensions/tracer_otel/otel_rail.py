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

from openjiuwen.core.common.logging import session_logger
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

    Every callback is try/except-guarded, mirroring the handlers'
    invariant: OTel failures never propagate into the business flow.
    This matters here specifically — the rail dispatch layer treats a
    raising before-callback as a business failure (recorded in retry
    history), so an OTel bug must not escape this class.

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
    # Common info shared by root / LLM / tool spans
    # ------------------------------------------------------------------

    @staticmethod
    def _build_common_info(ctx: AgentCallbackContext) -> dict[str, Any]:
        """Identity + conversation fields forwarded on every tracer event.

        Source of truth for the GenAI identity attributes
        (``gen_ai.agent.id`` / ``gen_ai.agent.description`` /
        ``gen_ai.conversation.id``) and the session-carried project
        extensions (``openjiuwen.gen_ai.user.id`` /
        ``openjiuwen.gen_ai.metadata``). ``agent_name`` feeds the trace
        display name (``openjiuwen.trace.name``) and tool-span
        ``gen_ai.agent.name``; ``session_id`` mirrors the conversation id.
        Shared by the root, LLM, and tool span builders so the three stay
        in sync.
        """
        source_metadata = getattr(ctx.session, "_source_metadata", None) or {}
        card = getattr(ctx.agent, "card", None)
        session_id = ctx.session.get_session_id() if ctx.session is not None else ""
        return {
            "agent_id": getattr(card, "id", None),
            "agent_name": str(getattr(card, "name", "") or ""),
            "user_id": source_metadata.get("user_id", ""),
            "metadata": source_metadata,
            # Conversation id and agent description follow metadata.
            "conversation_id": session_id,
            "session_id": session_id,
            "agent_description": str(getattr(card, "description", "") or ""),
        }

    # ------------------------------------------------------------------
    # Root span (BEFORE_INVOKE / AFTER_INVOKE)
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            session = ctx.session
            if session is None:
                return

            tracer = session.tracer()
            root_span = tracer.tracer_agent_span_manager.create_agent_span()
            instance_info = {
                **self._build_common_info(ctx),
                "class_name": ctx.agent.card.name,
                "type": "agent",
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
        except Exception as exc:
            session_logger.warning("otel rail: before_invoke failed: %s", exc)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
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
        except Exception as exc:
            session_logger.warning("otel rail: after_invoke failed: %s", exc)

    # ------------------------------------------------------------------
    # LLM child spans (BEFORE_MODEL_CALL / AFTER_MODEL_CALL / ON_MODEL_EXCEPTION)
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        try:
            session = ctx.session
            if session is None:
                return

            tracer = session.tracer()
            parent_span = session.agent_span
            llm_span = tracer.tracer_agent_span_manager.create_agent_span(parent_span)
            self._llm_spans.append(llm_span)

            # Build instance_info — model name comes from the request config the
            # call will actually use (see _resolve_model_config).
            model_config = self._resolve_model_config(ctx.agent)
            model_name = str(getattr(model_config, "model_name", "") or "") or "LLM"
            instance_info = {
                **self._build_common_info(ctx),
                "class_name": model_name,
                "type": InvokeType.LLM.value,
            }
            request_params = self._extract_request_params(model_config)
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
        except Exception as exc:
            session_logger.warning("otel rail: before_model_call failed: %s", exc)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Finalize the LLM span on success.

        When ctx.exception is set, the error path (on_model_exception)
        has already consumed the span — skip here.
        """
        try:
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
        except Exception as exc:
            session_logger.warning("otel rail: after_model_call failed: %s", exc)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Handle LLM call error — pop and mark the span as error."""
        try:
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
        except Exception as exc:
            session_logger.warning("otel rail: on_model_exception failed: %s", exc)

    # ------------------------------------------------------------------
    # Tool child spans (BEFORE_TOOL_CALL / AFTER_TOOL_CALL / ON_TOOL_EXCEPTION)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_model_config(agent: Any) -> Any:
        """Return the ``ModelRequestConfig`` the next model call will use.

        The agent's live LLM object is authoritative — it covers agents wired
        via ``set_llm`` whose config carries no ``model_config_obj``, and any
        params the ``Model`` was built with. Fall back to the config object
        while the LLM has not been constructed yet (lazy agents). ``getattr``
        keeps this defensive: the rail must never break the model call.
        """
        model_config = getattr(getattr(agent, "_llm", None), "model_config", None)
        if model_config is None:
            model_config = getattr(getattr(agent, "config", None), "model_config_obj", None)
        return model_config

    @staticmethod
    def _extract_request_params(model_config: Any) -> dict[str, Any]:
        """Normalize a ``ModelRequestConfig`` into GenAI request parameters.

        ``top_k`` is not a declared field — it rides in as an extra field
        (``extra="allow"``), so ``getattr`` covers both cases. ``stop`` is a
        single string in the config but the semconv attribute is a sequence;
        ``reasoning.effort`` maps to the reasoning level.
        """
        params: dict[str, Any] = {}
        if model_config is None:
            return params
        for key in ("temperature", "top_p", "top_k", "max_tokens"):
            value = getattr(model_config, key, None)
            if value is not None:
                params[key] = value
        stop = getattr(model_config, "stop", None)
        if stop:
            stops = stop if isinstance(stop, (list, tuple)) else [stop]
            params["stop_sequences"] = [str(s) for s in stops]
        reasoning = getattr(model_config, "reasoning", None)
        effort = getattr(reasoning, "effort", None)
        if not effort and isinstance(reasoning, dict):
            effort = reasoning.get("effort")
        if effort:
            params["reasoning_level"] = str(effort)
        return params

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        try:
            session = ctx.session
            if session is None:
                return

            tracer = session.tracer()
            tool_span = tracer.tracer_agent_span_manager.create_agent_span(session.agent_span)
            self._tool_spans.append(tool_span)

            tool_call = getattr(ctx.inputs, "tool_call", None)
            tool_name = getattr(ctx.inputs, "tool_name", "") or (getattr(tool_call, "name", "") if tool_call else "")
            instance_info = {
                **self._build_common_info(ctx),
                "class_name": tool_name,
                "type": InvokeType.PLUGIN.value,
                "tool_type": str(getattr(tool_call, "type", "") or "function") if tool_call else "function",
                "agent_name": getattr(ctx.agent.card, "name", ""),
                # Handler reads the tool-call id from here (OtelRail is its source).
                "tool_call_id": str(getattr(tool_call, "id", "") or ""),
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
        except Exception as exc:
            session_logger.warning("otel rail: before_tool_call failed: %s", exc)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        try:
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
        except Exception as exc:
            session_logger.warning("otel rail: after_tool_call failed: %s", exc)

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        try:
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
        except Exception as exc:
            session_logger.warning("otel rail: on_tool_exception failed: %s", exc)
