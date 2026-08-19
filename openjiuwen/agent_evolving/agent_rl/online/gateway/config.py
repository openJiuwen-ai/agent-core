# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway runtime configuration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class GatewayConfig:
    port: int
    host: str = "127.0.0.1"

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
    lora_default_policy: str = "disabled"
    redis_url: str = ""
    trajectory_store_backend: str = "auto"
    local_trajectory_store_dir: str = ""
    training_backend: str = "PPO"
    supervisor_url: str = ""
    supervisor_token: str = ""
    sft_capture_mode: str = "ppo_turn"
    sft_scenario: str = "multi_turn_supervisor"
    session_done_on_invoke_end: bool = True
    session_flush_token_threshold_k: int = 0

    upstream_max_retries: int = 2
    upstream_retry_backoff_sec: float = 0.2
    upstream_retry_max_backoff_sec: float = 2.0
    anthropic_max_completion_tokens: int = 0
    tool_parser_name: str = ""
    disable_gateway_trajectory_collection: bool = False
    single_user_default: bool = True
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
