# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online RL Rail that reuses the agent trajectory hook mechanism."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from openjiuwen.agent_evolving.trajectory import (
    Trajectory,
    set_trajectory_resource_attributes,
    trajectory_execution_id,
    trajectory_meta,
    trajectory_steps,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails import EvolutionRail

from .converter import OnlineTrajectoryConverter
from .llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids
from .uploader import TrajectoryUploader

logger = logging.getLogger(__name__)
runtime_logger = logging.getLogger("jiuwenswarm.agents.harness.common.rails.rl_online_rail_loader")



class RLOnlineRail(EvolutionRail):
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
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("max_trajectory_steps", None)
        super().__init__(**kwargs)
        del lora_repo, lora_runtime_base_url
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._gateway_endpoint = gateway_endpoint.rstrip("/")
        self._gateway_api_key = gateway_api_key
        self._uploader = uploader or TrajectoryUploader(gateway_endpoint)
        self._converter = converter or OnlineTrajectoryConverter(tenant_id=tenant_id)
        self._lora_default_policy = lora_default_policy
        self._lora_gateway_client = lora_gateway_client
        self._lora_runtime_timeout = lora_runtime_timeout
        self._session_done_on_invoke_end = session_done_on_invoke_end
        self._llm_step_count = 0
        self._started_at = 0.0

    def _resolve_user_id(self, ctx: AgentCallbackContext) -> str:
        return str(self._tenant_id or ctx.extra.get("user_id") or "").strip()

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

    async def _on_before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._llm_step_count = 0
        self._started_at = time.time()
        self._enable_token_capture(ctx)
        if self._tenant_id is None:
            user_id = self._resolve_user_id(ctx)
            self._tenant_id = user_id or None
        if self._builder is not None:
            self._builder.session_id = self._session_id or self._builder.session_id
            self._builder.source = "rl_online"
            self._builder.meta.update({
                "tenant_id": self._tenant_id,
                "status": "ok",
                "started_at": self._started_at,
            })

    async def _on_after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._llm_step_count += 1
        if self._builder is None or not self._builder.steps:
            logger.info("[RLOnlineRail] after_model_call skipped: builder_empty=%s", self._builder is None)
            return
        last_step = self._builder.steps[-1]
        if last_step.kind != "llm":
            logger.info("[RLOnlineRail] after_model_call skipped: last_step_kind=%s", last_step.kind)
            return
        # The base ``EvolutionRail`` already lifts top-level
        # prompt/completion token ids and logprobs from the response into
        # the step. Fall back to deeper extraction (e.g. nested
        # ``response.metadata.choices[0]``) only when those are empty.
        response = getattr(ctx.inputs, "response", None)
        if last_step.prompt_token_ids is None:
            prompt_ids = extract_prompt_ids(response)
            if prompt_ids is not None:
                last_step.prompt_token_ids = prompt_ids
        if last_step.completion_token_ids is None:
            token_ids = extract_token_ids(response)
            if token_ids is not None:
                last_step.completion_token_ids = token_ids
        if last_step.logprobs is None:
            logprobs = extract_logprobs(response)
            if logprobs is not None:
                last_step.logprobs = logprobs
        last_step.meta.update({
            "turn_id": self._llm_step_count - 1,
            "source": "rl_online",
            "tenant_id": self._tenant_id,
        })
        if ctx.extra.get("rl_online_lora_model"):
            last_step.meta.update({
                "rl_online_lora_model": ctx.extra.get("rl_online_lora_model"),
                "rl_online_lora_version": ctx.extra.get("rl_online_lora_version"),
                "rl_online_lora_path": ctx.extra.get("rl_online_lora_path"),
            })
        logger.info(
            "[RLOnlineRail] captured llm step=%d prompt_ids=%s response_tokens=%s logprobs=%s response_type=%s",
            self._llm_step_count,
            len(last_step.prompt_token_ids or []),
            len(last_step.completion_token_ids or []),
            len(last_step.logprobs or []),
            type(response).__name__,
        )

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._restore_model(ctx)
        if self._builder is not None:
            self._builder.meta["status"] = "invoke_error"
            self._builder.meta["exception"] = repr(ctx.exception)

    async def _on_after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Keep uploaded online RL trajectories scoped to one invoke."""
        self._reset_trajectory_builder()

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        metadata = trajectory_meta(trajectory)
        attributes = {"ended_at": time.time()}
        attributes["tenant_id"] = metadata.get("tenant_id", self._tenant_id)
        attributes["status"] = metadata.get("status", "ok")
        set_trajectory_resource_attributes(trajectory, attributes)
        batch = self._converter.convert(
            trajectory,
            tenant_id=self._tenant_id,
            session_done=self._session_done_on_invoke_end,
        )
        logger.info(
            "[RLOnlineRail] run_evolution trajectory=%s steps=%d samples=%d tenant=%s",
            trajectory_execution_id(trajectory),
            len(trajectory_steps(trajectory)),
            len(batch.samples),
            self._tenant_id,
        )
        if not batch.samples:
            logger.debug("[RLOnlineRail] no LLM samples to upload trajectory=%s", trajectory_execution_id(trajectory))
            return
        await self._uploader.enqueue(batch)
