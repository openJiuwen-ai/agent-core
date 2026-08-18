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

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.ports import GatewayCollector
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.runtime import GatewayTrajectoryCollector

from ...core.store_factory import (
    backend_from_env,
    build_gateway_store_bundle,
    local_store_dir_from_env,
)
from ...judge.judge_scorer import JudgeScorer
from ..config import GatewayConfig
from ..trajectory import GatewayTrajectoryRuntime
from ..upstream import Forwarder, HTTPXUpstreamGatewayClient, RetryPolicy
from .server import build_gateway_app

logger = logging.getLogger("online_rl.gateway")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_required(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _build_config_from_env() -> GatewayConfig:
    """Build config purely from environment variables (for uvicorn factory mode)."""
    inference_url = _env("INFERENCE_URL", _env("LLM_URL", "http://127.0.0.1:18000"))
    return GatewayConfig(
        host=_env("GATEWAY_HOST", "127.0.0.1"),
        port=int(_env_required("GATEWAY_PORT")),
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
        lora_default_policy=_env("LORA_DEFAULT_POLICY", "disabled"),
        redis_url=_env("REDIS_URL", ""),
        trajectory_store_backend=_env("TRAJECTORY_STORE_BACKEND", "auto"),
        local_trajectory_store_dir=_env("LOCAL_TRAJECTORY_STORE_DIR", ""),
        training_backend=_env("TRAIN_BACKEND", "PPO"),
        supervisor_url=_env("SUPERVISOR_URL", ""),
        supervisor_token=_env("SUPERVISOR_TOKEN", ""),
        sft_capture_mode=_env("RL_ONLINE_CAPTURE_MODE", "ppo_turn"),
        sft_scenario=_env("SFT_SCENARIO", "multi_turn_supervisor"),
        session_done_on_invoke_end=_env("RL_ONLINE_SESSION_DONE_ON_INVOKE_END", "true").lower()
        in ("1", "true", "yes", "on"),
        session_flush_token_threshold_k=int(_env("TRAJECTORY_SESSION_FLUSH_TOKEN_THRESHOLD_K", "0")),
        upstream_max_retries=int(_env("UPSTREAM_MAX_RETRIES", "2")),
        upstream_retry_backoff_sec=float(_env("UPSTREAM_RETRY_BACKOFF_SEC", "0.2")),
        upstream_retry_max_backoff_sec=float(_env("UPSTREAM_RETRY_MAX_BACKOFF_SEC", "2.0")),
        anthropic_max_completion_tokens=int(_env("ANTHROPIC_MAX_COMPLETION_TOKENS", "0")),
        tool_parser_name=_env("TOOL_PARSER_NAME", ""),
        disable_gateway_trajectory_collection=_env(
            "DISABLE_GATEWAY_TRAJECTORY_COLLECTION", "",
        ).lower() in ("1", "true"),
    )


def build_app_from_config(
    config: GatewayConfig,
    *,
    http_client: Any = None,
    redis_client: Any = None,
    collector: GatewayCollector | None = None,
) -> FastAPI:
    """Assemble gateway app from config and injectable dependencies."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    store_bundle = build_gateway_store_bundle(
        backend=getattr(config, "trajectory_store_backend", None) or backend_from_env(),
        redis_url=config.redis_url,
        local_store_dir=getattr(config, "local_trajectory_store_dir", "") or local_store_dir_from_env(),
        record_dir=config.record_dir,
        redis_client=redis_client,
    )
    redis_client = store_bundle.redis_client
    logger.info(
        "Gateway trajectory store backend=%s local_dir=%s redis_url=%s",
        store_bundle.backend,
        getattr(config, "local_trajectory_store_dir", "") or "",
        config.redis_url,
    )

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
        trajectory_store=store_bundle.trajectory_store,
        sft_store=store_bundle.sft_store,
        pending_judge_store=store_bundle.pending_judge_store,
        task_reward_redis=redis_client,
    )
    training_task_store = store_bundle.training_task_store

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
        if store_bundle.owns_redis_client and redis_client is not None and hasattr(redis_client, "aclose"):
            with suppress(Exception):
                await redis_client.aclose()

    gateway_collection_enabled = _env("ENABLE_GATEWAY_TRAJECTORY_COLLECTION", "").lower() in ("1", "true", "yes", "on")
    if (
        collector is None
        and gateway_collection_enabled
        and not getattr(config, "disable_gateway_trajectory_collection", False)
    ):
        if redis_client is None:
            raise ValueError("gateway trajectory collection requires a Redis storage backend")
        collector = GatewayTrajectoryCollector(
            redis=redis_client,
            sample_pipeline=trajectory_runtime,
        )

    return build_gateway_app(
        config=config,
        forwarder=forwarder,
        upstream_client=upstream_client,
        trajectory_runtime=trajectory_runtime,
        training_task_store=training_task_store,
        close_resources=close_resources,
        lora_repo=lora_repo,
        collector=collector,
    )
