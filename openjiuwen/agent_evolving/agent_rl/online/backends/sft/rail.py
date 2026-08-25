# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online SFT rail that uploads session-level raw trajectories."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
    assistant_text,
    build_direct_supervisor_sft_samples,
    json_safe,
    normalize_assistant_message,
    normalize_message,
    normalize_messages,
    normalize_tool_definitions,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import (
    SESSION_ID,
    TRAJECTORY_ID,
    TRAJECTORY_SCHEMA_VERSION,
    TRAJECTORY_SCHEMA_VERSION_ATTR,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, iter_spans, read_usage
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution import PreparedEvolutionInput

from ...core.rail import BaseOnlineTrainingRail
from ...core.uploader import TrajectoryUploader
from .collector import SFTTrajectoryCollector
from .converter import SFTRawTrajectoryConverter

logger = logging.getLogger(__name__)

SFT_UPLOAD_MODE_RAW = "raw"
SFT_UPLOAD_MODE_SAMPLE = "sample"


class SFTSessionFlushPolicy:
    """Decide when a cross-invoke SFT session should be emitted."""

    def __init__(
        self,
        *,
        done_on_invoke_end: bool = False,
        token_threshold_k: int = 0,
    ) -> None:
        self.done_on_invoke_end = done_on_invoke_end
        self.token_threshold_k = max(0, int(token_threshold_k))

    def resolve(self, ctx: AgentCallbackContext, *, token_count: int) -> str | None:
        if self.done_on_invoke_end:
            return "invoke_end"
        if self._session_done_requested(ctx):
            return "explicit_close"
        if self.token_threshold_k > 0 and token_count >= self.token_threshold_k * 1000:
            return "token_threshold"
        return None

    @staticmethod
    def _session_done_requested(ctx: AgentCallbackContext) -> bool:
        for key in ("rl_online_session_done", "sft_online_session_done", "session_done", "close_session"):
            if bool(ctx.extra.get(key)):
                return True
        inputs = getattr(ctx, "inputs", None)
        if isinstance(inputs, dict):
            return any(bool(inputs.get(key)) for key in ("session_done", "close_session", "done"))
        return any(bool(getattr(inputs, key, False)) for key in ("session_done", "close_session", "done"))


class SFTOnlineRail(BaseOnlineTrainingRail):
    """Rail-based SFT raw-session collector and uploader."""

    def __init__(
        self,
        *,
        session_id: str,
        gateway_endpoint: str,
        tenant_id: Optional[str] = None,
        uploader: Optional[TrajectoryUploader] = None,
        sft_scenario: str = "multi_turn_supervisor",
        collector: Optional[SFTTrajectoryCollector] = None,
        sft_raw_converter: Optional[SFTRawTrajectoryConverter] = None,
        forwarder: Any = None,
        session_done_on_invoke_end: bool = False,
        session_flush_token_threshold_k: int = 0,
        upload_mode: str = "raw",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            session_id=session_id,
            gateway_endpoint=gateway_endpoint,
            tenant_id=tenant_id,
            uploader=uploader,
            source="sft_online",
            **kwargs,
        )
        self._collector = collector or SFTTrajectoryCollector(
            converter=sft_raw_converter or SFTRawTrajectoryConverter(
                tenant_id=tenant_id,
                scenario=sft_scenario,
            ),
        )
        # Kept as a compatibility-only constructor argument for older callers.
        # SFT stores structured messages/tools directly from ModelCallInputs;
        # TokenInTokenOutRecord belongs to the RL token-capture path.
        del forwarder
        self._flush_policy = SFTSessionFlushPolicy(
            done_on_invoke_end=session_done_on_invoke_end,
            token_threshold_k=session_flush_token_threshold_k,
        )
        self._sft_scenario = sft_scenario
        self._upload_mode = self._normalize_upload_mode(upload_mode)
        self._pending_flush_reason: str | None = None
        self._session_metadata: dict[str, Any] = {}
        self._fallback_turns: list[dict[str, Any]] = []
        self._llm_step_count = 0
        self._started_at = 0.0

    # Lifecycle phase 1: initialize a session-level builder before the agent
    # starts work. SFT keeps a whole session as one raw trajectory unless the
    # flush policy decides the session is complete.
    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._started_at = time.time()
        self._status = "ok"
        self._exception = None
        self._ensure_tenant_from_ctx(ctx)
        self._enable_user_header(ctx)
        self._llm_step_count = len(self._fallback_turns)
        logger.info(
            "[SFTOnlineRail] before_invoke tenant=%s fallback_turns=%d inputs=%s",
            self._tenant_id,
            len(self._fallback_turns),
            type(getattr(ctx, "inputs", None)).__name__,
        )
        self._refresh_session_metadata(ctx)

    # Lifecycle phase 2: mirror the just-finished model call into the SFT
    # collector. The collector owns the normalized string/token record format.
    async def _on_after_model_call(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        del trajectory
        self._llm_step_count += 1
        self._collect_fallback_llm_interaction(ctx)

    # Lifecycle phase 3: decide whether this invoke closes the current SFT
    # session and expose a snapshot to the base evolution upload path.
    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        self._pending_flush_reason = self._flush_policy.resolve(
            ctx,
            token_count=self._trajectory_token_count(trajectory),
        )
        if self._pending_flush_reason:
            self._record_text_fallback_turn(ctx)
        logger.info(
            "[SFTOnlineRail] after_invoke tenant=%s flush_reason=%s tokens=%d steps=%d",
            self._tenant_id,
            self._pending_flush_reason,
            self._trajectory_token_count(trajectory),
            self._llm_span_count(trajectory),
        )
        if not self._pending_flush_reason:
            logger.warning(
                "[SFTOnlineRail] after_invoke skipped flush tenant=%s done_on_invoke_end=%s "
                "token_threshold_k=%d ctx_extra_keys=%s inputs_type=%s",
                self._tenant_id,
                self._flush_policy.done_on_invoke_end,
                self._flush_policy.token_threshold_k,
                sorted(ctx.extra.keys()),
                type(getattr(ctx, "inputs", None)).__name__,
            )
        if self._pending_flush_reason:
            await self._flush_current_session(ctx, trajectory)

    def _refresh_session_metadata(self, ctx: AgentCallbackContext) -> None:
        """Attach SFT task metadata from callback context or rollout container env."""

        original_task = self._original_task_from_ctx(ctx)
        self._session_metadata.setdefault("started_at", self._started_at)
        self._session_metadata.setdefault("original_task", original_task)
        self._session_metadata["sft_scenario"] = self._sft_scenario
        self._session_metadata["dataset_case"] = (
            ctx.extra.get("dataset_case")
            or ctx.extra.get("rl_online_dataset_case")
            or self._session_metadata.get("dataset_case")
            or self._dataset_case_from_env()
            or {}
        )
        self._session_metadata["workspace_ref"] = (
            ctx.extra.get("workspace_ref")
            or ctx.extra.get("rl_online_workspace_ref")
            or self._session_metadata.get("workspace_ref")
            or self._workspace_ref_from_env()
            or {}
        )
        self._session_metadata["tenant_id"] = self._tenant_id
        self._session_metadata["status"] = self._status
        if not self._session_metadata.get("original_task"):
            self._session_metadata["original_task"] = self._task_prompt_from_env()

    def _collect_fallback_llm_interaction(self, ctx: AgentCallbackContext) -> None:
        """Keep structured ChatML fields when OTel spans are unavailable."""

        inputs = getattr(ctx, "inputs", None)
        messages = normalize_messages(getattr(inputs, "messages", None) or [])
        response_value = getattr(inputs, "response", None)
        if response_value is None and not messages:
            logger.info("[SFTOnlineRail] fallback model call skipped: empty inputs")
            return

        response = normalize_assistant_message(response_value or {})
        tools = normalize_tool_definitions(getattr(inputs, "tools", None))
        llm_str = assistant_text(response)
        turn = {
            "turn_id": self._llm_step_count - 1,
            "model_id": self._model_id_from_ctx(ctx),
            "messages": messages,
            "response": response,
            "tools": tools,
            "prompt_ids": None,
            "completion_token_ids": None,
            "prompt_str": "",
            "llm_str": llm_str,
            "meta": {
                "turn_id": self._llm_step_count - 1,
                "source": "sft_online",
                "tenant_id": self._tenant_id,
                "llm_str": llm_str,
                **self._lora_step_meta(ctx),
            },
        }
        self._fallback_turns.append(turn)
        logger.info(
            "[SFTOnlineRail] captured llm step=%d messages=%d tools=%d",
            self._llm_step_count,
            len(messages),
            len(tools),
        )

    async def _on_after_evolution_triggered(
        self,
        trajectory: Trajectory,
        ctx: AgentCallbackContext,
    ) -> None:
        del trajectory, ctx
        if self._pending_flush_reason:
            self._reset_sft_session()
            self._pending_flush_reason = None

    def _allow_evolution_trigger(self, trigger_point: Any, ctx: AgentCallbackContext) -> bool:
        del trigger_point, ctx
        # SFTOnlineRail flushes synchronously in _on_after_invoke so the task
        # container can finish only after samples are visible to the gateway.
        return False

    async def _flush_current_session(self, ctx: AgentCallbackContext, trajectory: Trajectory | None) -> None:
        """Upload the current SFT session before the host request cleans up."""

        raw_trajectory = self._trajectory_for_upload(ctx, trajectory)
        if raw_trajectory is None:
            logger.info("[SFTOnlineRail] flush skipped: trajectory_empty=True")
            return
        flush_reason = self._pending_flush_reason
        prepared = PreparedEvolutionInput(trajectory=raw_trajectory, messages=())
        await self.run_evolution(prepared)
        logger.info(
            "[SFTOnlineRail] flush completed tenant=%s reason=%s steps=%d",
            self._tenant_id,
            flush_reason,
            self._llm_span_count(raw_trajectory),
        )
        self._reset_sft_session()
        self._pending_flush_reason = None

    def _trajectory_token_count(self, trajectory: Trajectory | None) -> int:
        if trajectory is None:
            return self._fallback_token_count()
        total = 0
        for span in iter_spans(trajectory):
            if span_category(span) != "llm":
                continue
            usage = read_usage(span)
            total += usage.get("prompt_tokens", 0)
            total += usage.get("completion_tokens", 0)
        return total or self._fallback_token_count()

    def _fallback_token_count(self) -> int:
        total = 0
        for turn in self._fallback_turns:
            total += len(turn.get("prompt_ids") or [])
            total += len(turn.get("completion_token_ids") or [])
        return total

    @staticmethod
    def _llm_span_count(trajectory: Trajectory | None) -> int:
        if trajectory is None:
            return 0
        return sum(1 for span in iter_spans(trajectory) if span_category(span) == "llm")

    def _record_text_fallback_turn(self, ctx: AgentCallbackContext) -> None:
        """Ensure a closing SFT session has at least one string-level LLM step."""

        if self._fallback_turns:
            return
        prompt = self._original_task_from_ctx(ctx) or self._task_prompt_from_env()
        response_text = self._response_text_from_ctx(ctx)
        if not prompt and not response_text:
            return
        if not response_text:
            response_text = "SFT session completed without captured assistant text."

        model_id = self._model_id_from_ctx(ctx)
        self._fallback_turns.append(
            {
                "turn_id": self._llm_step_count,
                "model_id": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "response": {"role": "assistant", "content": response_text},
                "prompt_ids": None,
                "completion_token_ids": None,
                "prompt_str": prompt,
                "llm_str": response_text,
                "meta": {
                    "turn_id": self._llm_step_count,
                    "source": "sft_online_text_fallback",
                    "tenant_id": self._tenant_id,
                    "prompt_str": prompt,
                    "llm_str": response_text,
                    "fallback_reason": "no_model_callback",
                },
            }
        )
        self._llm_step_count += 1
        logger.info(
            "[SFTOnlineRail] recorded text fallback step prompt_chars=%d response_chars=%d",
            len(prompt),
            len(response_text),
        )

    @staticmethod
    def _response_text_from_ctx(ctx: AgentCallbackContext) -> str:
        result = getattr(getattr(ctx, "inputs", None), "result", None)
        if isinstance(result, dict):
            output = result.get("output")
            if isinstance(output, dict):
                return str(output.get("output") or output.get("content") or "")
            return str(output or result.get("content") or result.get("response_text") or "")
        return str(result or "")

    def _model_id_from_ctx(self, ctx: AgentCallbackContext) -> str:
        config = self._react_config(ctx)
        model_id = (
            getattr(config, "model_name", "")
            or getattr(config, "model", "")
            or os.getenv("MODEL_NAME", "")
            or os.getenv("SUPERVISOR_MODEL", "")
            or "unknown"
        )
        return str(model_id)

    @staticmethod
    def _env_json_dict(key: str) -> dict[str, Any]:
        raw = os.getenv(key, "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[SFTOnlineRail] invalid JSON env %s", key)
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _dataset_case_from_env(cls) -> dict[str, Any]:
        dataset_case = cls._env_json_dict("SFT_DATASET_CASE_JSON")
        docker_image = os.getenv("SFT_DOCKER_IMAGE", "").strip()
        task_prompt = cls._task_prompt_from_env()
        instance_id = os.getenv("SFT_INSTANCE_ID", "").strip()
        if docker_image:
            dataset_case.setdefault("docker_image", docker_image)
            dataset_case.setdefault("image", docker_image)
        if task_prompt:
            dataset_case.setdefault("task_prompt", task_prompt)
            dataset_case.setdefault("prompt", task_prompt)
        if instance_id:
            dataset_case.setdefault("instance_id", instance_id)
        return dataset_case

    @staticmethod
    def _workspace_ref_from_env() -> dict[str, Any]:
        return SFTOnlineRail._env_json_dict("SFT_WORKSPACE_REF_JSON")

    @staticmethod
    def _task_prompt_from_env() -> str:
        return os.getenv("SFT_TASK_PROMPT", "").strip()

    async def run_evolution(self, prepared: PreparedEvolutionInput) -> None:
        trajectory = prepared.trajectory
        metadata = trajectory.resource_attributes
        trajectory = trajectory.with_resource_attributes(
            {
                "ended_at": time.time(),
                "tenant_id": metadata.get("tenant_id") or self._tenant_id,
                "status": metadata.get("status") or self._status,
            },
        )
        flush_reason = self._pending_flush_reason or ""
        raw_metadata = trajectory.resource_attributes
        raw_batch = self._collector.build_raw_batch(
            trajectory,
            tenant_id=self._tenant_id,
            user_id=str(raw_metadata.get("tenant_id") or self._tenant_id or ""),
            session_done=True,
            flush_reason=flush_reason,
            original_task=str(raw_metadata.get("original_task") or ""),
            dataset_case=(
                raw_metadata.get("dataset_case")
                if isinstance(raw_metadata.get("dataset_case"), dict)
                else {}
            ),
            workspace_ref=(
                raw_metadata.get("workspace_ref")
                if isinstance(raw_metadata.get("workspace_ref"), dict)
                else {}
            ),
            context_compression=(
                raw_metadata.get("context_compression")
                if isinstance(raw_metadata.get("context_compression"), dict)
                else {}
            ),
        )
        await self._upload_raw_or_samples(
            raw_batch,
            trajectory_id=trajectory.trajectory_id,
            step_count=self._llm_span_count(trajectory),
            flush_reason=flush_reason,
        )

    async def upload_text_session(
        self,
        *,
        prompt: str,
        response_text: str,
        session_id: str = "",
        flush_reason: str = "invoke_result",
        model_id: str = "",
    ) -> None:
        """Upload one string-only SFT session when a host runtime bypasses core callbacks."""

        prompt = str(prompt or "")
        response_text = str(response_text or "")
        if not prompt and not response_text:
            return
        self._ensure_env_metadata_defaults()
        metadata = {
            "tenant_id": self._tenant_id,
            "status": "ok",
            "sft_scenario": self._sft_scenario,
            "original_task": self._task_prompt_from_env() or prompt,
            "dataset_case": self._dataset_case_from_env(),
            "workspace_ref": self._workspace_ref_from_env(),
        }
        trajectory = self._build_fallback_trajectory(
            session_id=session_id,
            metadata=metadata,
            turns=[
                {
                    "turn_id": 0,
                    "model_id": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "response": {"role": "assistant", "content": response_text},
                    "prompt_ids": None,
                    "completion_token_ids": None,
                    "prompt_str": prompt,
                    "llm_str": response_text,
                    "meta": {
                        "turn_id": 0,
                        "source": "sft_online_text_fallback",
                        "tenant_id": self._tenant_id,
                        "prompt_str": prompt,
                        "llm_str": response_text,
                    },
                }
            ],
        )
        raw_batch = self._collector.build_raw_batch(
            trajectory,
            tenant_id=self._tenant_id,
            user_id=str(self._tenant_id or ""),
            session_done=True,
            flush_reason=flush_reason,
            original_task=str(metadata.get("original_task") or ""),
            dataset_case=metadata["dataset_case"] if isinstance(metadata.get("dataset_case"), dict) else {},
            workspace_ref=metadata["workspace_ref"] if isinstance(metadata.get("workspace_ref"), dict) else {},
            context_compression={},
        )
        await self._upload_raw_or_samples(
            raw_batch,
            trajectory_id=raw_batch.trajectory_id,
            step_count=len(raw_batch.steps),
            flush_reason=flush_reason,
        )

    def _trajectory_for_upload(self, ctx: AgentCallbackContext, trajectory: Trajectory | None) -> Trajectory | None:
        metadata = {
            **self._session_metadata,
            "tenant_id": self._tenant_id,
            "status": self._status,
            "sft_scenario": self._sft_scenario,
        }
        if not metadata.get("original_task"):
            metadata["original_task"] = self._original_task_from_ctx(ctx) or self._task_prompt_from_env()
        if isinstance(trajectory, Trajectory) and self._collector.has_llm_steps(trajectory):
            return trajectory.with_resource_attributes(metadata)
        if not self._fallback_turns:
            return None
        session_id = self._resolve_upload_session_id(ctx, trajectory)
        return self._build_fallback_trajectory(
            session_id=session_id,
            metadata=metadata,
            turns=self._fallback_turns,
        )

    def _resolve_upload_session_id(self, ctx: AgentCallbackContext, trajectory: Trajectory | None) -> str:
        if isinstance(trajectory, Trajectory) and trajectory.session_id:
            return trajectory.session_id
        session = getattr(ctx, "session", None)
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id):
            try:
                return str(get_session_id())
            except Exception as exc:
                logger.debug("failed to resolve SFT upload session id: %r", exc)
        inputs = getattr(ctx, "inputs", None)
        return str(getattr(inputs, "conversation_id", "") or "")

    def _build_fallback_trajectory(
        self,
        *,
        session_id: str,
        metadata: dict[str, Any],
        turns: list[dict[str, Any]],
    ) -> Trajectory:
        resource_attrs = {
            TRAJECTORY_ID: str(uuid.uuid4()),
            TRAJECTORY_SCHEMA_VERSION_ATTR: TRAJECTORY_SCHEMA_VERSION,
            TRAJECTORY_SOURCE: "sft_online",
            SESSION_ID: session_id or str(uuid.uuid4()),
            **metadata,
        }
        spans = [self._fallback_turn_to_span(index, turn) for index, turn in enumerate(turns)]
        return Trajectory.from_otlp(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": attributes_from_map(resource_attrs)},
                        "scopeSpans": [{"scope": {"name": "sft_online_fallback"}, "spans": spans}],
                    }
                ]
            }
        )

    @staticmethod
    def _fallback_turn_to_span(index: int, turn: dict[str, Any]) -> dict[str, Any]:
        messages = turn.get("messages") or []
        response = normalize_assistant_message(turn.get("response") or {})

        attrs: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: "chat",
            semconv.GEN_AI_REQUEST_MODEL: str(turn.get("model_id") or "unknown"),
            "openjiuwen.legacy.step.meta": turn.get("meta") or {},
        }
        tools = normalize_tool_definitions(turn.get("tools"))
        if tools:
            attrs[semconv.GEN_AI_TOOL_DEFINITIONS] = tools

        for message_index, message in enumerate(messages):
            normalized = normalize_message(message)
            base = f"{semconv.GEN_AI_PROMPT}.{message_index}"
            for key in (
                "role",
                "content",
                "name",
                "tool_call_id",
                "reasoning_content",
                "reasoning",
                "refusal",
            ):
                if key in normalized:
                    attrs[f"{base}.{key}"] = normalized[key]
            if normalized.get("tool_calls"):
                attrs[f"{base}.tool_calls"] = normalized["tool_calls"]

        attrs[f"{semconv.GEN_AI_COMPLETION}.0.role"] = response.get("role") or "assistant"
        response_content = response.get("content")
        if response_content is not None:
            attrs[f"{semconv.GEN_AI_COMPLETION}.0.content"] = response_content
        elif not response.get("tool_calls"):
            attrs[f"{semconv.GEN_AI_COMPLETION}.0.content"] = turn.get("llm_str") or ""
        for key in (
            "name",
            "reasoning_content",
            "reasoning",
            "refusal",
        ):
            if key in response:
                attrs[f"{semconv.GEN_AI_COMPLETION}.0.{key}"] = response[key]
        if response.get("tool_calls"):
            attrs[f"{semconv.GEN_AI_COMPLETION}.0.tool_calls"] = response["tool_calls"]
            attrs[semconv.GEN_AI_TOOL_CALLS] = response["tool_calls"]
        if turn.get("prompt_ids") is not None:
            attrs["prompt_ids"] = turn.get("prompt_ids")
        if turn.get("completion_token_ids") is not None:
            attrs["completion_token_ids"] = turn.get("completion_token_ids")
        return {
            "traceId": f"{uuid.uuid4().int & ((1 << 128) - 1):032x}",
            "spanId": f"{index + 1:016x}",
            "name": "llm.call",
            "startTimeUnixNano": str(index + 1),
            "endTimeUnixNano": str(index + 2),
            "attributes": attributes_from_map(attrs),
        }

    def _reset_sft_session(self) -> None:
        self._fallback_turns.clear()
        self._session_metadata.clear()
        self._reset_current_scope()

    def _ensure_env_metadata_defaults(self) -> None:
        if self._tenant_id is None:
            tenant_id = os.getenv("RL_ONLINE_TENANT_ID", "").strip() or os.getenv("WEB_USER_ID", "").strip()
            self._tenant_id = tenant_id or None

    @staticmethod
    def _normalize_upload_mode(upload_mode: str) -> str:
        normalized = str(upload_mode or "raw").strip().lower()
        if normalized in {"sample", "samples", "sft_sample", "direct_sample"}:
            return SFT_UPLOAD_MODE_SAMPLE
        return SFT_UPLOAD_MODE_RAW

    async def _upload_raw_or_samples(
        self,
        raw_batch: Any,
        *,
        trajectory_id: str,
        step_count: int,
        flush_reason: str,
    ) -> None:
        """Upload either raw replay input or direct SFT samples.

        ``raw`` mode is the legacy path: scheduler later replays the task and
        builds training samples. ``sample`` mode is the v2 optimize path: the
        Docker task container already used the supervisor model, so the rail can
        upload trainable ``sft-sample-v1`` payloads immediately.
        """

        raw_payload = self._raw_batch_to_payload(raw_batch)
        raw_steps = raw_payload.get("steps") if isinstance(raw_payload.get("steps"), list) else []
        if not raw_steps:
            logger.warning(
                "[SFTOnlineRail] upload skipped: raw_steps_empty trajectory=%s tenant=%s "
                "mode=%s reason=%s raw_keys=%s",
                trajectory_id,
                self._tenant_id,
                self._upload_mode,
                flush_reason,
                sorted(raw_payload.keys()),
            )
            return
        if self._upload_mode == SFT_UPLOAD_MODE_SAMPLE:
            await self._upload_direct_samples(
                raw_payload,
                trajectory_id=trajectory_id,
                step_count=step_count,
                flush_reason=flush_reason,
            )
            return
        logger.info(
            "[SFTOnlineRail] upload sft-raw-v1 trajectory=%s steps=%d tenant=%s reason=%s",
            trajectory_id,
            step_count,
            self._tenant_id,
            flush_reason,
        )
        await self._upload_payload(raw_batch)

    @staticmethod
    def _raw_batch_to_payload(raw_batch: Any) -> dict[str, Any]:
        if hasattr(raw_batch, "to_dict"):
            payload = raw_batch.to_dict()
            return payload if isinstance(payload, dict) else {}
        return dict(raw_batch) if isinstance(raw_batch, dict) else {}

    async def _upload_direct_samples(
        self,
        raw_payload: dict[str, Any],
        *,
        trajectory_id: str,
        step_count: int,
        flush_reason: str,
    ) -> None:
        samples = build_direct_supervisor_sft_samples(
            raw_payload,
            scenario=str(raw_payload.get("scenario") or self._sft_scenario),
            default_user_id=str(self._tenant_id or ""),
            target_model_id=str(raw_payload.get("model_id") or os.getenv("MODEL_NAME", "")),
            flush_reason=flush_reason,
        )
        logger.info(
            "[SFTOnlineRail] upload sft-sample-v1 trajectory=%s raw_steps=%d samples=%d tenant=%s reason=%s",
            trajectory_id,
            step_count,
            len(samples),
            self._tenant_id,
            flush_reason,
        )
        if not samples:
            first_llm = next(
                (
                    step
                    for step in raw_payload.get("steps") or []
                    if isinstance(step, dict) and step.get("type") == "llm"
                ),
                {},
            )
            logger.warning(
                "[SFTOnlineRail] direct sample conversion produced no samples trajectory=%s tenant=%s "
                "messages=%d response_chars=%d first_llm_keys=%s",
                trajectory_id,
                self._tenant_id,
                len(first_llm.get("messages") or []) if isinstance(first_llm, dict) else 0,
                len(str(first_llm.get("response_text") or "")) if isinstance(first_llm, dict) else 0,
                sorted(first_llm.keys()) if isinstance(first_llm, dict) else [],
            )
        for sample in samples:
            await self._upload_payload(sample)

    async def _upload_payload(self, payload: Any) -> None:
        upload_now = getattr(self._uploader, "upload_now", None)
        if callable(upload_now):
            await upload_now(payload)
        else:
            await self._uploader.enqueue(payload)
