# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Anthropic Messages HTTP adapter for the online-RL gateway."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from openjiuwen.core.common.logging import logger

from .anthropic_protocol import (
    AnthropicRequestError,
    anthropic_error_response,
    anthropic_response,
    convert_anthropic_request,
)
from .completion_runtime import GatewayCompletionRuntime
from .http_helpers import ensure_gateway_auth


def create_anthropic_router(
    *,
    config: Any,
    completion_runtime: GatewayCompletionRuntime,
    increment_request_counter: Callable[[], Awaitable[None]],
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/messages")
    async def anthropic_messages(
        request: Request,
        authorization: Optional[str] = Header(default=None),
        x_api_key: Optional[str] = Header(default=None),
    ):
        anthropic_authorization = authorization or (f"Bearer {x_api_key}" if x_api_key else None)
        try:
            await ensure_gateway_auth(config.gateway_api_key, anthropic_authorization)
        except HTTPException as exc:
            return anthropic_error_response(exc.status_code, str(exc.detail))
        await increment_request_counter()
        try:
            payload = await request.json()
        except Exception:
            return anthropic_error_response(400, "Invalid JSON body")
        if not isinstance(payload, dict):
            return anthropic_error_response(400, "JSON body must be an object")
        try:
            body = convert_anthropic_request(payload)
        except AnthropicRequestError as exc:
            return anthropic_error_response(400, str(exc))

        capture_metadata: dict[str, Any] | None = None
        max_completion_tokens = max(0, int(getattr(config, "anthropic_max_completion_tokens", 0)))
        requested_max_tokens = body.get("max_tokens")
        if (
            max_completion_tokens
            and isinstance(requested_max_tokens, int)
            and requested_max_tokens > max_completion_tokens
        ):
            body["max_tokens"] = max_completion_tokens
            capture_metadata = {
                "_gateway_request_adjustments": {
                    "anthropic_max_tokens": {
                        "requested": requested_max_tokens,
                        "effective": max_completion_tokens,
                    }
                }
            }
            logger.info(
                "[Gateway] clamped Anthropic max_tokens requested=%d effective=%d",
                requested_max_tokens,
                max_completion_tokens,
            )

        try:
            response_json, client_wants_stream = await completion_runtime.execute(
                request=request,
                body=body,
                capture_metadata=capture_metadata,
            )
        except HTTPException as exc:
            return anthropic_error_response(exc.status_code, str(exc.detail))

        model = str(response_json.get("model") or payload.get("model") or config.model_id)
        return anthropic_response(response_json, model=model, stream=client_wants_stream)

    return router
