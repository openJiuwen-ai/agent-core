# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""FastAPI route binding for the online-RL gateway."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .http_helpers import build_upstream_headers, ensure_gateway_auth, stream_chat_response
from .request_context import require_messages, require_user_id, resolve_trace_id
from ..trajectory import GatewayTrajectoryRuntime
from ..upstream import Forwarder, UpstreamGatewayClient

logger = logging.getLogger("online_rl.gateway")


def _inject_latest_lora(*, body: dict[str, Any], user_id: str, lora_repo: Any = None) -> None:
    if lora_repo is None:
        return
    latest_lora = lora_repo.get_latest(user_id)
    if latest_lora:
        body.setdefault("extra_body", {})["lora_name"] = user_id


async def _forward_chat_completions(
    *,
    request: Request,
    body: dict[str, Any],
    config: Any,
    forwarder: Forwarder,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    trace_id = resolve_trace_id(request)
    messages = require_messages(body)
    require_user_id(request)
    upstream_headers = build_upstream_headers(request, llm_api_key=config.llm_api_key)

    logger.debug(
        "[Gateway %s] proxy_only messages=%d stream=%s",
        trace_id,
        len(messages),
        bool(body.get("stream", False)),
    )

    response_json = await forwarder.forward(body=body, headers=upstream_headers)
    logger.debug(
        "[Gateway] chat_completions cost_ms=%.1f",
        (time.perf_counter() - t0) * 1000,
    )
    return response_json


async def _snapshot_stats(*, trajectory_runtime: GatewayTrajectoryRuntime, total_requests: int) -> dict[str, Any]:
    trajectory_stats = await trajectory_runtime.snapshot_stats()
    return {
        "total_requests": total_requests,
        "total_samples": trajectory_stats["total_samples"],
        "trajectory_store_total": trajectory_stats["trajectory_store_total"],
        "trajectory_store_pending": trajectory_stats["trajectory_store_pending"],
    }


def build_gateway_app(
    *,
    config: Any,
    forwarder: Forwarder,
    upstream_client: UpstreamGatewayClient,
    trajectory_runtime: GatewayTrajectoryRuntime,
    close_resources: Callable[[], Awaitable[None]],
    lora_repo: Any = None,
) -> FastAPI:
    """Assemble FastAPI app and bind gateway public/internal routes."""
    metrics_lock = asyncio.Lock()
    total_requests = 0

    async def _inc_request_counter() -> None:
        nonlocal total_requests
        async with metrics_lock:
            total_requests += 1

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        try:
            yield
        finally:
            await close_resources()

    app = FastAPI(title="Online-RL Gateway", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/v1/gateway/stats")
    async def gateway_stats(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        async with metrics_lock:
            request_count = total_requests
        return await _snapshot_stats(
            trajectory_runtime=trajectory_runtime,
            total_requests=request_count,
        )

    @app.post("/v1/gateway/upload/batch")
    async def create_upload_batch(
        payload: dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        logger.debug(
            "[GatewayAPI] rail_upload session=%s trajectory=%s samples=%s",
            payload.get("session_id"),
            payload.get("trajectory_id"),
            len(payload.get("samples") or []) if isinstance(payload.get("samples"), list) else None,
        )
        try:
            result = await trajectory_runtime.rail_ingestor.ingest_rail_batch(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        await _inc_request_counter()
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

        _inject_latest_lora(body=body, user_id=request.headers.get("x-user-id", "anonymous"), lora_repo=lora_repo)

        client_wants_stream = bool(body.pop("stream", False))

        response_json = await _forward_chat_completions(
            request=request,
            body=body,
            config=config,
            forwarder=forwarder,
        )

        if client_wants_stream:
            return StreamingResponse(
                stream_chat_response(response_json, model_id=config.model_id),
                media_type="text/event-stream",
            )
        return JSONResponse(content=response_json)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_other(
        path: str,
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        await ensure_gateway_auth(config.gateway_api_key, authorization)
        await _inc_request_counter()
        target_url = f"{config.llm_url.rstrip('/')}/{path}"
        upstream_headers = build_upstream_headers(request, llm_api_key=config.llm_api_key)
        body_bytes = await request.body()
        try:
            resp = await upstream_client.request(
                method=request.method,
                url=target_url,
                params=dict(request.query_params),
                headers=upstream_headers,
                content=body_bytes,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"proxy failed: {exc}") from exc

        response_headers = {
            key: value
            for key, value in resp.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}
        }
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type"),
        )

    return app
