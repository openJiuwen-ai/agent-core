# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Online RL Rail that reuses the agent trajectory hook mechanism."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails import EvolutionRail

from .converter import OnlineTrajectoryConverter
from .llm_response import extract_logprobs, extract_prompt_ids, extract_token_ids
from .uploader import TrajectoryUploader

logger = logging.getLogger(__name__)



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
        session_done_on_invoke_end: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._session_id = session_id
        self._tenant_id = tenant_id
        self._uploader = uploader or TrajectoryUploader(gateway_endpoint)
        self._converter = converter or OnlineTrajectoryConverter(tenant_id=tenant_id)
        self._session_done_on_invoke_end = session_done_on_invoke_end
        self._llm_step_count = 0
        self._started_at = 0.0

    def _resolve_user_id(self, ctx: AgentCallbackContext) -> str:
        return str(self._tenant_id or ctx.extra.get("user_id") or "").strip()

    def _enable_token_capture(self, ctx: AgentCallbackContext) -> None:
        react_agent = getattr(ctx.agent, "react_agent", None)
        config = getattr(react_agent, "config", None)
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
            configure_custom_headers = getattr(config, "configure_custom_headers", None)
            if callable(configure_custom_headers):
                configure_custom_headers(headers)
            else:
                config.custom_headers = headers

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
            return
        last_step = self._builder.steps[-1]
        if last_step.kind != "llm":
            return
        response = getattr(ctx.inputs, "response", None)
        prompt_ids = extract_prompt_ids(response)
        token_ids = extract_token_ids(response)
        logprobs = extract_logprobs(response)
        if prompt_ids is not None:
            last_step.meta["prompt_ids"] = prompt_ids
        if token_ids is not None:
            last_step.token_ids = token_ids
        if logprobs is not None:
            last_step.log_probs = logprobs
        last_step.meta.update({
            "turn_id": self._llm_step_count - 1,
            "source": "rl_online",
            "tenant_id": self._tenant_id,
        })

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        if self._builder is not None:
            self._builder.meta["status"] = "invoke_error"
            self._builder.meta["exception"] = repr(ctx.exception)

    async def run_evolution(
        self,
        trajectory: Trajectory,
        ctx: Optional[AgentCallbackContext] = None,
        *,
        snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        trajectory.meta.setdefault("tenant_id", self._tenant_id)
        trajectory.meta.setdefault("status", "ok")
        trajectory.meta["ended_at"] = time.time()
        batch = self._converter.convert(
            trajectory,
            tenant_id=self._tenant_id,
            session_done=self._session_done_on_invoke_end,
        )
        if not batch.samples:
            logger.debug("[RLOnlineRail] no LLM samples to upload trajectory=%s", trajectory.execution_id)
            return
        await self._uploader.enqueue(batch)
