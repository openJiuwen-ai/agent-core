# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online PPO rail that uploads turn-level RL trajectory samples."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
    SESSION_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, span_attributes, span_sort_key
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution import PreparedEvolutionInput

from ...core.interaction import TokenInTokenOutForwarder
from ...core.llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids
from ...core.rail import BaseOnlineTrainingRail
from ...core.uploader import TrajectoryUploader
from .collector import RLTrajectoryCollector
from .converter import OnlineTrajectoryConverter

logger = logging.getLogger(__name__)


class RLOnlineRail(BaseOnlineTrainingRail):
    """Rail-based online PPO collector and uploader."""

    def __init__(
        self,
        *,
        session_id: str,
        gateway_endpoint: str,
        tenant_id: Optional[str] = None,
        uploader: Optional[TrajectoryUploader] = None,
        converter: Optional[OnlineTrajectoryConverter] = None,
        collector: Optional[RLTrajectoryCollector] = None,
        forwarder: Optional[TokenInTokenOutForwarder] = None,
        session_done: bool = True,
        capture_mode: str = "ppo_turn",
        session_done_on_invoke_end: Optional[bool] = None,
        sft_scenario: str = "multi_turn_supervisor",
        session_flush_token_threshold_k: int = 0,
        **kwargs: Any,
    ) -> None:
        # ``capture_mode`` and SFT-specific kwargs are accepted only as a
        # compatibility shim for older launch configs. SFT is handled by
        # SFTOnlineRail.
        del sft_scenario, session_flush_token_threshold_k
        normalized_mode = (capture_mode or "ppo_turn").strip().lower()
        if normalized_mode not in {"ppo", "ppo_turn", "rail-v1"}:
            logger.warning("RLOnlineRail ignores non-PPO capture_mode=%s; use SFTOnlineRail for SFT", capture_mode)
        super().__init__(
            session_id=session_id,
            gateway_endpoint=gateway_endpoint,
            tenant_id=tenant_id,
            uploader=uploader,
            source="rl_online",
            **kwargs,
        )
        self._collector = collector or RLTrajectoryCollector(
            converter=converter or OnlineTrajectoryConverter(tenant_id=tenant_id),
        )
        self._forwarder = forwarder or TokenInTokenOutForwarder()
        self._session_done = session_done if session_done_on_invoke_end is None else session_done_on_invoke_end
        self._llm_step_count = 0
        self._started_at = 0.0
        self._fallback_uploaded_this_invoke = False

    def _enable_token_capture(self, ctx: AgentCallbackContext) -> None:
        config = self._react_config(ctx)
        if config is None:
            return
        config.llm_return_token_ids = True
        config.llm_logprobs = True
        config.llm_top_logprobs = 1
        self._enable_user_header(ctx)

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._llm_step_count = 0
        self._started_at = time.time()
        self._status = "ok"
        self._exception = None
        self._fallback_uploaded_this_invoke = False
        self._ensure_tenant_from_ctx(ctx)
        self._enable_token_capture(ctx)

    async def _on_after_model_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        self._llm_step_count += 1
        if trajectory is None:
            fallback = self._fallback_trajectory_from_model_call(ctx, step_index=self._llm_step_count - 1)
            if fallback is None:
                logger.info(
                    "[RLOnlineRail] captured llm callback=%d trajectory= missing_fallback=False",
                    self._llm_step_count,
                )
                return
            self._fallback_uploaded_this_invoke = True
            logger.info(
                "[RLOnlineRail] captured llm callback=%d trajectory=%s fallback=True",
                self._llm_step_count,
                fallback.trajectory_id,
            )
            await self.run_evolution(PreparedEvolutionInput(trajectory=fallback, messages=()))
            return

        logger.info(
            "[RLOnlineRail] captured llm callback=%d trajectory=%s",
            self._llm_step_count,
            getattr(trajectory, "trajectory_id", ""),
        )

    def _fallback_trajectory_from_model_call(
        self,
        ctx: AgentCallbackContext,
        *,
        step_index: int,
    ) -> Trajectory | None:
        """Build a minimal ``llm.call`` trajectory when OTel spans are unavailable."""

        inputs = getattr(ctx, "inputs", None)
        if not isinstance(inputs, ModelCallInputs) or inputs.response is None:
            return None

        session_id = self._resolve_callback_session_id(ctx)
        now_ns = time.time_ns()
        trace_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        response = self._message_to_dict(inputs.response)
        attrs: dict[str, Any] = {
            semconv.GEN_AI_REQUEST_MODEL: self._resolve_model_name(ctx),
            semconv.GEN_AI_REQUEST_MESSAGE_COUNT: len(inputs.messages or []),
            semconv.GEN_AI_OPERATION_NAME: "chat",
            semconv.GEN_AI_SYSTEM: "openjiuwen",
            "evolution.rl.fallback_capture": True,
            "evolution.rl.turn_id": step_index,
        }
        self._write_indexed_messages(attrs, semconv.GEN_AI_PROMPT, inputs.messages or [])
        self._write_indexed_messages(attrs, semconv.GEN_AI_COMPLETION, [response])
        if inputs.tools:
            attrs[semconv.GEN_AI_TOOL_DEFINITIONS] = json.dumps(
                self._json_safe(inputs.tools),
                ensure_ascii=False,
                default=str,
            )

        usage = getattr(inputs.response, "usage_metadata", None)
        self._write_usage_attrs(attrs, usage)
        finish_reason = getattr(inputs.response, "finish_reason", None)
        if finish_reason and finish_reason != "null":
            attrs[semconv.GEN_AI_RESPONSE_FINISH_REASON] = str(finish_reason)

        prompt_ids = self._direct_prompt_ids(inputs.response)
        completion_ids = self._direct_completion_ids(inputs.response)
        logprobs = self._direct_logprobs(inputs.response)
        if prompt_ids is not None:
            attrs[RL_PROMPT_TOKEN_IDS] = prompt_ids
        if completion_ids is not None:
            attrs[RL_COMPLETION_TOKEN_IDS] = completion_ids
        if logprobs is not None:
            attrs[RL_LOGPROBS] = logprobs

        resource_attrs = {
            TRAJECTORY_ID: uuid.uuid4().hex,
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            TRAJECTORY_SOURCE: "rl_online",
            SESSION_ID: session_id,
            "tenant_id": self._tenant_id,
            "status": self._status,
            "started_at": self._started_at,
        }
        return Trajectory.from_otlp(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": attributes_from_map(resource_attrs)},
                        "scopeSpans": [
                            {
                                "scope": {"name": "openjiuwen.agent_evolving.online.rl.fallback"},
                                "spans": [
                                    {
                                        "traceId": trace_id,
                                        "spanId": span_id,
                                        "name": "llm.call",
                                        "kind": "SPAN_KIND_CLIENT",
                                        "startTimeUnixNano": str(now_ns),
                                        "endTimeUnixNano": str(now_ns),
                                        "attributes": attributes_from_map(attrs),
                                        "status": {"code": "STATUS_CODE_OK"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [RLOnlineRail._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [RLOnlineRail._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): RLOnlineRail._json_safe(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            try:
                return RLOnlineRail._json_safe(value.model_dump())
            except Exception as exc:
                logger.debug("failed to model_dump value type=%s: %r", type(value).__name__, exc)
        return str(value)

    @classmethod
    def _message_to_dict(cls, message: Any) -> dict[str, Any]:
        data = cls._json_safe(message)
        if isinstance(data, dict):
            return data
        return {"role": getattr(message, "role", "unknown"), "content": str(data or "")}

    @classmethod
    def _write_indexed_messages(cls, attrs: dict[str, Any], base: str, messages: list[Any]) -> None:
        for index, message in enumerate(messages):
            data = cls._message_to_dict(message)
            attrs[f"{base}.{index}.role"] = data.get("role", "unknown")
            attrs[f"{base}.{index}.content"] = data.get("content", "")
            tool_calls = data.get("tool_calls")
            if tool_calls:
                attrs[f"{base}.{index}.tool_calls"] = json.dumps(tool_calls, ensure_ascii=False, default=str)
                if base == semconv.GEN_AI_COMPLETION:
                    attrs[semconv.GEN_AI_TOOL_CALLS] = json.dumps(tool_calls, ensure_ascii=False, default=str)

    @staticmethod
    def _resolve_callback_session_id(ctx: AgentCallbackContext) -> str:
        session = getattr(ctx, "session", None)
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id):
            try:
                session_id = str(get_session_id())
                if session_id:
                    return session_id
            except Exception as exc:
                logger.debug("failed to resolve callback session id from session: %r", exc)
        context = getattr(ctx, "context", None)
        context_session_id = getattr(context, "session_id", None)
        if callable(context_session_id):
            try:
                session_id = str(context_session_id())
                if session_id:
                    return session_id
            except Exception as exc:
                logger.debug("failed to resolve callback session id from context: %r", exc)
        return "default_session"

    def _resolve_model_name(self, ctx: AgentCallbackContext) -> str:
        config = self._react_config(ctx)
        model_config = getattr(config, "model_config_obj", None)
        return str(getattr(config, "model_name", "") or getattr(model_config, "model", "") or "unknown")

    @staticmethod
    def _write_usage_attrs(attrs: dict[str, Any], usage: Any) -> None:
        if usage is None:
            return
        for source, target in (
            ("input_tokens", semconv.GEN_AI_USAGE_PROMPT_TOKENS),
            ("output_tokens", semconv.GEN_AI_USAGE_COMPLETION_TOKENS),
            ("total_tokens", semconv.GEN_AI_USAGE_TOTAL_TOKENS),
        ):
            value = getattr(usage, source, None)
            if value:
                attrs[target] = int(value)

    @staticmethod
    def _direct_prompt_ids(response: Any) -> list[int] | None:
        return getattr(response, "prompt_token_ids", None) or extract_prompt_ids(response)

    @staticmethod
    def _direct_completion_ids(response: Any) -> list[int] | None:
        return getattr(response, "completion_token_ids", None) or extract_token_ids(response)

    @staticmethod
    def _direct_logprobs(response: Any) -> Any:
        return getattr(response, "logprobs", None) or extract_logprobs(response)

    def _enrich_latest_llm(self, trajectory: Trajectory, response: Any) -> Trajectory:
        """Attach provider-nested token fields without mutating the response."""

        enriched = super()._enrich_latest_llm(trajectory, response)
        prompt_ids = extract_prompt_ids(response)
        completion_ids = extract_token_ids(response)
        logprobs = extract_logprobs(response)
        if prompt_ids is None and completion_ids is None and logprobs is None:
            return enriched

        payload = enriched.to_otlp()
        target: dict[str, Any] | None = None
        for resource_span in payload.get("resourceSpans") or []:
            for scope_span in resource_span.get("scopeSpans") or []:
                for span in scope_span.get("spans") or []:
                    if not isinstance(span, dict) or span_category(span) != "llm":
                        continue
                    if target is None or span_sort_key(span) >= span_sort_key(target):
                        target = span
        if target is None:
            return enriched

        attrs = span_attributes(target)
        if prompt_ids is not None:
            attrs[RL_PROMPT_TOKEN_IDS] = prompt_ids
        if completion_ids is not None:
            attrs[RL_COMPLETION_TOKEN_IDS] = completion_ids
        if logprobs is not None:
            attrs[RL_LOGPROBS] = logprobs
        target["attributes"] = attributes_from_map(attrs)
        return Trajectory.from_otlp(payload)

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        del ctx, trajectory
        self._reset_current_scope()

    async def _on_after_evolution_triggered(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        del trajectory, ctx

    def _allow_evolution_trigger(self, trigger_point: Any, ctx: AgentCallbackContext) -> bool:
        del trigger_point, ctx
        return not self._fallback_uploaded_this_invoke

    async def run_evolution(
        self,
        prepared: PreparedEvolutionInput,
    ) -> None:
        trajectory = prepared.trajectory
        metadata = trajectory.resource_attributes
        trajectory = trajectory.with_resource_attributes(
            {
                "ended_at": time.time(),
                "tenant_id": metadata.get("tenant_id") or self._tenant_id,
                "status": metadata.get("status") or self._status,
            },
        )
        batch = self._collector.build_batch(
            trajectory,
            tenant_id=self._tenant_id,
            session_done=self._session_done,
        )
        logger.info(
            "[RLOnlineRail] run_evolution rail-v1 trajectory=%s samples=%d tenant=%s",
            trajectory.trajectory_id,
            len(batch.samples),
            self._tenant_id,
        )
        if batch.samples:
            await self._uploader.enqueue(batch)
