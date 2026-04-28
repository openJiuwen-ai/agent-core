# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""LLM request forwarder for upstream chat completions."""

from __future__ import annotations
import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from ..common import NON_STANDARD_BODY_KEYS
from .upstream_client import UpstreamGatewayClient

logger = logging.getLogger("online_rl.gateway")


class Forwarder:
    """Forward chat requests to /v1/chat/completions."""

    def __init__(
        self,
        *,
        upstream_client: UpstreamGatewayClient,
        model_id: str,
    ) -> None:
        """Initialize string-mode forwarder.

        Args:
            upstream_client: Transport client used for upstream HTTP calls.
            model_id: Default model id injected when request omits model.
        """
        self._upstream_client = upstream_client
        self.model_id = model_id

    def _clean_body(self, body: dict[str, Any]) -> dict[str, Any]:
        send_body = {k: v for k, v in body.items() if k not in NON_STANDARD_BODY_KEYS}
        send_body["stream"] = False
        send_body.pop("stream_options", None)
        send_body.setdefault("model", self.model_id)
        send_body["logprobs"] = True
        send_body["top_logprobs"] = 1
        return send_body

    async def forward(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """Forward one non-streaming chat-completion request.

        Args:
            body: OpenAI-compatible request body.
            headers: Sanitized upstream headers.

        Returns:
            Normalized upstream response payload.

        Raises:
            HTTPException: If upstream request fails.
        """
        send_body = self._clean_body(body)
        t0 = time.perf_counter()
        resp = await self._upstream_client.post_chat_completions(
            json_body=send_body,
            headers=headers,
        )
        logger.debug("forward status=%s cost_ms=%.1f", resp.status_code, (time.perf_counter() - t0) * 1000)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            raise HTTPException(status_code=502, detail=f"upstream error: {detail}") from exc

        return resp.json()
