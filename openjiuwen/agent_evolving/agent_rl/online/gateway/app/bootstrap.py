# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway app assembly from config and environment."""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from typing import Any, Optional

import httpx
from fastapi import FastAPI

from ..config import GatewayConfig
from ...judge.judge_scorer import JudgeScorer
from ..trajectory import GatewayTrajectoryRuntime
from ..upstream import Forwarder, HTTPXUpstreamGatewayClient, RetryPolicy
from .server import build_gateway_app

logger = logging.getLogger("online_rl.gateway")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _build_config_from_env() -> GatewayConfig:
    """Build config purely from environment variables (for uvicorn factory mode)."""
    inference_url = _env("INFERENCE_URL", _env("LLM_URL", "http://127.0.0.1:18000"))
    return GatewayConfig(
        host=_env("GATEWAY_HOST", "0.0.0.0"),
        port=int(_env("GATEWAY_PORT", "18080")),
        llm_url=inference_url,
        judge_url=_env("JUDGE_URL", inference_url),
        model_id=_env("MODEL_ID", _env("SERVED_MODEL_NAME", "")),
        judge_model=_env("JUDGE_MODEL", ""),
        request_timeout=float(_env("REQUEST_TIMEOUT", "120")),
        llm_api_key=_env("LLM_API_KEY", ""),
        judge_api_key=_env("JUDGE_API_KEY", "EMPTY"),
        gateway_api_key=_env("GATEWAY_API_KEY", ""),
        record_dir=_env("RECORD_DIR", "records"),
        log_level=_env("LOG_LEVEL", "INFO"),
        dump_token_ids=_env("DUMP_TOKEN_IDS", "").lower() in ("1", "true"),
        lora_repo_root=_env("LORA_REPO_ROOT", ""),
        redis_url=_env("REDIS_URL", ""),
        upstream_max_retries=int(_env("UPSTREAM_MAX_RETRIES", "2")),
        upstream_retry_backoff_sec=float(_env("UPSTREAM_RETRY_BACKOFF_SEC", "0.2")),
        upstream_retry_max_backoff_sec=float(_env("UPSTREAM_RETRY_MAX_BACKOFF_SEC", "2.0")),
        disable_gateway_trajectory_collection=_env(
            "DISABLE_GATEWAY_TRAJECTORY_COLLECTION", "",
        ).lower() in ("1", "true"),
    )


def build_app_from_config(
    config: GatewayConfig,
    *,
    http_client: Any = None,
    redis_client: Any = None,
) -> FastAPI:
    """Assemble gateway app from config and injectable dependencies."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    owns_redis_client = redis_client is None
    if redis_client is None and config.redis_url:
        try:
            from redis.asyncio import from_url as redis_from_url
            redis_client = redis_from_url(config.redis_url, decode_responses=False)
            logger.info("Redis client created: %s", config.redis_url)
        except Exception:
            logger.warning("Failed to create Redis client from %s", config.redis_url)
            raise
    if redis_client is None:
        raise ValueError("gateway requires redis_url or injected redis_client")

    owns_http_client = http_client is None
    http_client = http_client or httpx.AsyncClient(timeout=config.request_timeout)
    upstream_client = HTTPXUpstreamGatewayClient(
        http_client=http_client,
        llm_url=config.llm_url,
        retry_policy=RetryPolicy(
            max_retries=max(0, int(config.upstream_max_retries)),
            backoff_base_sec=max(0.0, float(config.upstream_retry_backoff_sec)),
            backoff_max_sec=max(0.0, float(config.upstream_retry_max_backoff_sec)),
        ),
    )
    forwarder = Forwarder(
        upstream_client=upstream_client,
        model_id=config.model_id,
    )
    trajectory_runtime = GatewayTrajectoryRuntime(
        config,
        redis=redis_client,
    )

    judge_scorer: Optional[JudgeScorer] = None
    if config.judge_url:
        judge_scorer = JudgeScorer(
            judge_url=config.judge_url,
            judge_model=config.judge_model or config.model_id,
            api_key=config.judge_api_key or "EMPTY",
            max_retries=config.upstream_max_retries,
            retry_backoff_sec=config.upstream_retry_backoff_sec,
        )
    trajectory_runtime.set_judge_scorer(judge_scorer)

    lora_repo = None
    if config.lora_repo_root:
        try:
            from ....storage.lora_repo import LoRARepository
            lora_repo = LoRARepository(config.lora_repo_root)
        except Exception:
            logger.warning("LoRA repo not available at %s", config.lora_repo_root)

    async def close_resources() -> None:
        if owns_http_client:
            await http_client.aclose()
        if owns_redis_client and hasattr(redis_client, "aclose"):
            with suppress(Exception):
                await redis_client.aclose()

    return build_gateway_app(
        config=config,
        forwarder=forwarder,
        upstream_client=upstream_client,
        trajectory_runtime=trajectory_runtime,
        close_resources=close_resources,
        lora_repo=lora_repo,
    )
