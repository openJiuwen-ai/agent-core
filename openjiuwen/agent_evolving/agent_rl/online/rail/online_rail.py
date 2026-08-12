# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online RL Rail that reuses the agent trajectory hook mechanism."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import (
    RL_COMPLETION_TOKEN_IDS,
    RL_LOGPROBS,
    RL_PROMPT_TOKEN_IDS,
)
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map, span_attributes, span_sort_key
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution import PreparedEvolutionInput

from .converter import OnlineTrajectoryConverter
from .llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids
from .uploader import TrajectoryUploader

logger = logging.getLogger(__name__)
runtime_logger = logging.getLogger("jiuwenswarm.agents.harness.common.rails.rl_online_rail_loader")


class RLOnlineRail(RLRail):
    """Rail-based online RL collector and uploader."""

    priority = 100

    def __init__(
        self,
        *,
        session_id: str,
        gateway_endpoint: str,
        tenant_id: Optional[str] = None,
        uploader: Optional[TrajectoryUploader] = None,
        converter: Optional[OnlineTrajectoryConverter] = None,
        lora_repo: Optional[Any] = None,
        lora_default_policy: str = "disabled",
        gateway_api_key: str = "",
        lora_gateway_client: Optional[Any] = None,
        lora_runtime_base_url: str = "",
        lora_runtime_timeout: float = 30.0,
        session_done_on_invoke_end: bool = True,
        trajectory_span_processor: TrajectorySpanProcessor,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            session_id=session_id,
            source="rl_online",
            trajectory_span_processor=trajectory_span_processor,
            **kwargs,
        )
        del lora_repo, lora_runtime_base_url
        self._tenant_id = tenant_id
        self._gateway_endpoint = gateway_endpoint.rstrip("/")
        self._gateway_api_key = gateway_api_key
        self._uploader = uploader or TrajectoryUploader(gateway_endpoint)
        self._converter = converter or OnlineTrajectoryConverter(tenant_id=tenant_id)
        self._lora_default_policy = lora_default_policy
        self._lora_gateway_client = lora_gateway_client
        self._lora_runtime_timeout = lora_runtime_timeout
        self._session_done_on_invoke_end = session_done_on_invoke_end
        self._started_at = 0.0
        self._status = "ok"
        self._exception: str | None = None

    def _resolve_user_id(self, ctx: AgentCallbackContext) -> str:
        extra = getattr(ctx, "extra", None) or {}
        return str(self._tenant_id or extra.get("user_id") or "").strip()

    def _enable_token_capture(self, ctx: AgentCallbackContext) -> None:
        config = self._react_config(ctx)
        if config is None:
            return
        config.llm_return_token_ids = True
        config.llm_logprobs = True
        config.llm_top_logprobs = 1
        user_id = self._resolve_user_id(ctx)
        if user_id:
            headers = dict(getattr(config, "custom_headers", None) or {})
            existing_key = next((key for key in headers if key.lower() == "x-user-id"), None)
            if existing_key is not None:
                headers[existing_key] = user_id
            else:
                headers["x-user-id"] = user_id
            self._configure_custom_headers(config, headers)

    @staticmethod
    def _configure_custom_headers(config: Any, headers: Optional[dict[str, Any]]) -> None:
        configure_custom_headers = getattr(config, "configure_custom_headers", None)
        if callable(configure_custom_headers):
            configure_custom_headers(headers)
        else:
            config.custom_headers = headers

    @staticmethod
    def _react_config(ctx: AgentCallbackContext) -> Any | None:
        react_agent = getattr(ctx.agent, "react_agent", None) or ctx.agent
        return getattr(react_agent, "config", None) or getattr(react_agent, "_config", None)

    def _gateway_headers(self) -> dict[str, str]:
        if not self._gateway_api_key:
            return {}
        return {"Authorization": f"Bearer {self._gateway_api_key}"}

    async def _request_effective_lora(self, user_id: str) -> dict[str, Any] | None:
        if self._lora_default_policy != "latest_by_user":
            return None

        async def _post(client: Any) -> Any:
            return await client.post(
                f"{self._gateway_endpoint}/v1/rl/lora/effective",
                json={"model_id": user_id, "ensure_loaded": True},
                headers=self._gateway_headers(),
            )

        try:
            if self._lora_gateway_client is not None:
                response = await _post(self._lora_gateway_client)
            else:
                async with httpx.AsyncClient(timeout=self._lora_runtime_timeout) as client:
                    response = await _post(client)

            if response.status_code >= 400:
                logger.warning(
                    "[RLOnlineRail] gateway effective LoRA failed user=%s status=%s body=%s",
                    user_id,
                    response.status_code,
                    response.text[:300],
                )
                return None

            payload = response.json()
            if not payload.get("enabled"):
                logger.debug(
                    "[RLOnlineRail] no effective LoRA user=%s reason=%s",
                    user_id,
                    payload.get("reason"),
                )
                return None
            return payload
        except Exception as exc:
            logger.warning(
                "[RLOnlineRail] gateway effective LoRA request failed user=%s err=%r",
                user_id,
                exc,
            )
            return None

    async def _apply_latest_lora_model(self, ctx: AgentCallbackContext) -> None:
        if self._lora_default_policy != "latest_by_user":
            return
        user_id = self._resolve_user_id(ctx)
        if not user_id:
            return
        lora_info = await self._request_effective_lora(user_id)
        if lora_info is None:
            return

        config = self._react_config(ctx)
        if config is None:
            return
        ctx.extra.setdefault("_rl_online_original_model_name", getattr(config, "model_name", ""))
        model_config_obj = getattr(config, "model_config_obj", None)
        if model_config_obj is not None:
            ctx.extra.setdefault(
                "_rl_online_original_model_config_name",
                getattr(model_config_obj, "model_name", ""),
            )
            model_config_obj.model_name = user_id
        config.model_name = user_id
        ctx.extra["rl_online_lora_model"] = lora_info.get("model_id", user_id)
        ctx.extra["rl_online_lora_id"] = lora_info.get("lora_id", "")
        ctx.extra["rl_online_lora_version"] = lora_info.get("version", "")
        ctx.extra["rl_online_lora_path"] = lora_info.get("path", "")
        logger.info(
            "[RLOnlineRail] using latest LoRA user=%s lora_id=%s version=%s path=%s",
            lora_info.get("model_id", user_id),
            lora_info.get("lora_id", ""),
            lora_info.get("version", ""),
            lora_info.get("path", ""),
        )
        runtime_logger.info(
            "[RLOnlineRail] using latest LoRA user=%s lora_id=%s version=%s path=%s",
            lora_info.get("model_id", user_id),
            lora_info.get("lora_id", ""),
            lora_info.get("version", ""),
            lora_info.get("path", ""),
        )

    def _restore_model(self, ctx: AgentCallbackContext) -> None:
        if "_rl_online_original_model_name" not in ctx.extra:
            return
        config = self._react_config(ctx)
        if config is None:
            return
        config.model_name = ctx.extra.pop("_rl_online_original_model_name")
        model_config_obj = getattr(config, "model_config_obj", None)
        if model_config_obj is not None and "_rl_online_original_model_config_name" in ctx.extra:
            model_config_obj.model_name = ctx.extra.pop("_rl_online_original_model_config_name")

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._apply_latest_lora_model(ctx)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        try:
            await super().after_model_call(ctx)
        finally:
            self._restore_model(ctx)

    def _scope_metadata(self, capture: Any) -> dict[str, Any]:
        """Include online status metadata in each detached rail projection."""

        metadata = super()._scope_metadata(capture)
        if self._tenant_id is not None:
            metadata["tenant_id"] = self._tenant_id
        metadata["status"] = self._status
        if self._started_at:
            metadata["started_at"] = self._started_at
        if self._exception is not None:
            metadata["exception"] = self._exception
        return metadata

    def get_trajectory(
        self,
        *,
        session_id: str,
        member_id: str | None = None,
        team_id: str | None = None,
    ) -> Trajectory | None:
        """Return the detached clean projection with current online metadata."""

        trajectory = super().get_trajectory(session_id=session_id, member_id=member_id, team_id=team_id)
        if trajectory is None:
            return None
        return trajectory.with_resource_attributes(self._scope_metadata_for_projection())

    def _scope_metadata_for_projection(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {"status": self._status}
        if self._tenant_id is not None:
            attributes["tenant_id"] = self._tenant_id
        if self._started_at:
            attributes["started_at"] = self._started_at
        if self._exception is not None:
            attributes["exception"] = self._exception
        return attributes

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._started_at = time.time()
        self._status = "ok"
        self._exception = None
        self._enable_token_capture(ctx)
        if self._tenant_id is None:
            user_id = self._resolve_user_id(ctx)
            self._tenant_id = user_id or None

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

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._restore_model(ctx)
        self._status = "invoke_error"
        self._exception = repr(ctx.exception)

    async def _on_after_invoke(
        self,
        ctx: AgentCallbackContext,
        trajectory: Trajectory | None,
    ) -> None:
        """End the current RL sample without retaining it into the next invoke."""

        del ctx, trajectory
        self._reset_current_scope()

    async def run_evolution(
        self,
        prepared: PreparedEvolutionInput,
    ) -> None:
        """Convert one frozen prepared input and enqueue it for the gateway."""

        trajectory = prepared.trajectory
        metadata = trajectory.resource_attributes
        trajectory = trajectory.with_resource_attributes(
            {
                "ended_at": time.time(),
                "tenant_id": metadata.get("tenant_id", self._tenant_id),
                "status": metadata.get("status", self._status),
            }
        )
        batch = self._converter.convert(
            trajectory,
            tenant_id=self._tenant_id,
            session_done=self._session_done_on_invoke_end,
        )
        logger.info(
            "[RLOnlineRail] run_evolution trajectory=%s samples=%d tenant=%s",
            trajectory.trajectory_id,
            len(batch.samples),
            self._tenant_id,
        )
        if not batch.samples:
            logger.debug("[RLOnlineRail] no LLM samples to upload trajectory=%s", trajectory.trajectory_id)
            return
        await self._uploader.enqueue(batch)
