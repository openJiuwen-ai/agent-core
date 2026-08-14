# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared runtime utilities for online training rails."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from openjiuwen.agent_evolving.agent_rl.rl_rail import RLRail
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_SOURCE
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from .uploader import TrajectoryUploader

logger = logging.getLogger(__name__)
runtime_logger = logging.getLogger("jiuwenswarm.agents.harness.common.rails.rl_online_rail_loader")


class BaseOnlineTrainingRail(RLRail):
    """Common gateway, tenant and LoRA helpers shared by RL/SFT rails."""

    priority = 100

    def __init__(
        self,
        *,
        session_id: str,
        gateway_endpoint: str,
        tenant_id: Optional[str] = None,
        uploader: Optional[TrajectoryUploader] = None,
        lora_repo: Optional[Any] = None,
        lora_default_policy: str = "disabled",
        gateway_api_key: str = "",
        lora_gateway_client: Optional[Any] = None,
        lora_runtime_base_url: str = "",
        lora_runtime_timeout: float = 30.0,
        trajectory_span_processor: TrajectorySpanProcessor,
        source: str = "online",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            session_id=session_id,
            source=source,
            trajectory_span_processor=trajectory_span_processor,
            **kwargs,
        )
        del lora_repo, lora_runtime_base_url
        self._tenant_id = tenant_id
        self._gateway_endpoint = gateway_endpoint.rstrip("/")
        self._gateway_api_key = gateway_api_key
        self._uploader = uploader or TrajectoryUploader(gateway_endpoint, api_key=gateway_api_key)
        self._lora_default_policy = lora_default_policy
        self._lora_gateway_client = lora_gateway_client
        self._lora_runtime_timeout = lora_runtime_timeout
        self._status = "ok"
        self._exception: str | None = None
        self._started_at = 0.0

    def _resolve_user_id(self, ctx: AgentCallbackContext) -> str:
        return str(self._tenant_id or ctx.extra.get("user_id") or "").strip()

    def _ensure_tenant_from_ctx(self, ctx: AgentCallbackContext) -> None:
        if self._tenant_id is None:
            user_id = self._resolve_user_id(ctx)
            self._tenant_id = user_id or None

    def _scope_metadata(self, capture: Any) -> dict[str, Any]:
        """Attach online-training metadata to each clean trajectory projection."""

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
        """Return a detached projection with the current online metadata."""

        trajectory = super().get_trajectory(session_id=session_id, member_id=member_id, team_id=team_id)
        if trajectory is None:
            return None
        return trajectory.with_resource_attributes(self._scope_metadata_for_projection())

    def _scope_metadata_for_projection(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {TRAJECTORY_SOURCE: self._source, "status": self._status}
        if self._tenant_id is not None:
            attributes["tenant_id"] = self._tenant_id
        if self._started_at:
            attributes["started_at"] = self._started_at
        if self._exception is not None:
            attributes["exception"] = self._exception
        return attributes

    def _enable_user_header(self, ctx: AgentCallbackContext) -> None:
        config = self._react_config(ctx)
        if config is None:
            return
        user_id = self._resolve_user_id(ctx)
        if not user_id:
            return
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
                    "[%s] gateway effective LoRA failed user=%s status=%s body=%s",
                    type(self).__name__,
                    user_id,
                    response.status_code,
                    response.text[:300],
                )
                return None

            payload = response.json()
            if not payload.get("enabled"):
                logger.debug(
                    "[%s] no effective LoRA user=%s reason=%s",
                    type(self).__name__,
                    user_id,
                    payload.get("reason"),
                )
                return None
            return payload
        except Exception as exc:
            logger.warning(
                "[%s] gateway effective LoRA request failed user=%s err=%r",
                type(self).__name__,
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
            "[%s] using latest LoRA user=%s lora_id=%s version=%s path=%s",
            type(self).__name__,
            lora_info.get("model_id", user_id),
            lora_info.get("lora_id", ""),
            lora_info.get("version", ""),
            lora_info.get("path", ""),
        )
        runtime_logger.info(
            "[%s] using latest LoRA user=%s lora_id=%s version=%s path=%s",
            type(self).__name__,
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

    def _lora_step_meta(self, ctx: AgentCallbackContext) -> dict[str, Any]:
        if not ctx.extra.get("rl_online_lora_model"):
            return {}
        return {
            "rl_online_lora_model": ctx.extra.get("rl_online_lora_model"),
            "rl_online_lora_version": ctx.extra.get("rl_online_lora_version"),
            "rl_online_lora_path": ctx.extra.get("rl_online_lora_path"),
        }

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._apply_latest_lora_model(ctx)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        try:
            await super().after_model_call(ctx)
        finally:
            self._restore_model(ctx)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._restore_model(ctx)
        self._status = "invoke_error"
        self._exception = repr(ctx.exception)

    @staticmethod
    def _original_task_from_ctx(ctx: AgentCallbackContext) -> str:
        inputs = getattr(ctx, "inputs", None)
        query = getattr(inputs, "query", None)
        if query is not None:
            return str(query)
        messages = getattr(inputs, "messages", None)
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                return str(last.get("content") or "")
            return str(getattr(last, "content", "") or "")
        return ""
