# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 18080

    llm_url: str = "http://127.0.0.1:18000"
    judge_url: str = "http://127.0.0.1:18001"
    model_id: str = ""
    judge_model: str = ""

    request_timeout: float = 120.0
    llm_api_key: str = ""
    judge_api_key: str = ""
    gateway_api_key: str = ""

    record_dir: str = "records"
    log_level: str = "INFO"
    dump_token_ids: bool = False

    lora_repo_root: str = ""
    redis_url: str = ""

    upstream_max_retries: int = 2
    upstream_retry_backoff_sec: float = 0.2
    upstream_retry_max_backoff_sec: float = 2.0
    disable_gateway_trajectory_collection: bool = False
