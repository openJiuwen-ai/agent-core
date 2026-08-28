# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Legacy Python completion forwarding used until the test adapter replaces it."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request

from openjiuwen.core.common.logging import logger

from ...lora_runtime import build_lora_info
from ..upstream import Forwarder
from .http_helpers import build_upstream_headers
from .request_context import require_messages, require_user_id, resolve_trace_id


def _inject_latest_lora(
    *,
    body: dict[str, Any],
    user_id: str,
    lora_repo: Any = None,
    lora_default_policy: str = "disabled",
) -> dict[str, Any] | None:
    if lora_repo is None or lora_default_policy != "latest_by_user":
        return None
    latest_lora = lora_repo.get_latest(user_id)
    if latest_lora:
        body["model"] = user_id
        extra_body = body.get("extra_body")
        if isinstance(extra_body, dict):
            extra_body.pop("lora_name", None)
            if not extra_body:
                body.pop("extra_body", None)
        return build_lora_info(user_id, latest_lora, default_policy=lora_default_policy)
    return None


class GatewayCompletionRuntime:
    """Forward provider-normalized completions without owning RL state."""

    def __init__(
        self,
        *,
        config: Any,
        forwarder: Forwarder,
        lora_repo: Any = None,
    ) -> None:
        self._config = config
        self._forwarder = forwarder
        self._lora_repo = lora_repo

    async def execute(
        self,
        *,
        request: Request,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        user_id = require_user_id(request, self._config)
        lora_info = _inject_latest_lora(
            body=body,
            user_id=user_id,
            lora_repo=self._lora_repo,
            lora_default_policy=getattr(self._config, "lora_default_policy", "disabled"),
        )
        if lora_info:
            logger.info(
                "[Gateway] applied LoRA adapter user=%s version=%s path=%s via model field",
                user_id,
                lora_info.get("version"),
                lora_info.get("path"),
            )

        client_wants_stream = bool(body.pop("stream", False))
        response_json = await self._forward(request=request, body=body)

        if lora_info:
            response_json["rl_lora"] = lora_info
        return response_json, client_wants_stream

    async def _forward(self, *, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        trace_id = resolve_trace_id(request)
        messages = require_messages(body)
        upstream_headers = build_upstream_headers(request, llm_api_key=self._config.llm_api_key)
        logger.debug(
            "[Gateway %s] proxy_only messages=%d stream=%s",
            trace_id,
            len(messages),
            bool(body.get("stream", False)),
        )
        response = await self._forwarder.forward(body=body, headers=upstream_headers)
        logger.debug("[Gateway] chat_completions cost_ms=%.1f", (time.perf_counter() - started_at) * 1000)
        return response
