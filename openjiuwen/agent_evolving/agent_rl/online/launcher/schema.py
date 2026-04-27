# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Schema definitions for online RL launcher runtime config."""

from __future__ import annotations

from dataclasses import dataclass, field

from omegaconf import MISSING


@dataclass
class VLLMServiceConfig:
    model_path: str = MISSING
    model_name: str = MISSING
    host: str = MISSING
    port: int = MISSING
    gpu_ids: str = MISSING
    tp: int = MISSING
    existing_url: str | None = None
    health_timeout: float = MISSING
    env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)


@dataclass
class JudgeConfig(VLLMServiceConfig):
    reuse_inference_if_same_model: bool = MISSING


@dataclass
class GatewayServiceConfig:
    host: str = MISSING
    port: int = MISSING
    redis_url: str = MISSING
    record_dir: str = MISSING
    log_level: str = MISSING
    health_timeout: float = MISSING
    disable_trajectory_collection: bool = MISSING
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class TrajectoryConfig:
    batch_size: int = MISSING
    mode: str = MISSING


@dataclass
class TrainingConfig:
    gpu_ids: str = MISSING
    threshold: int = MISSING
    scan_interval: int = MISSING
    ppo_config: str | None = None
    lora_repo: str | None = None


@dataclass
class JiuwenConfig:
    enabled: bool = MISSING
    agent_server_port: int = MISSING
    app_host: str = MISSING
    ws_port: int = MISSING
    web_host: str = MISSING
    web_port: int = MISSING


@dataclass
class OnlineRLConfig:
    demo: bool = MISSING
    inference: VLLMServiceConfig = field(default_factory=VLLMServiceConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    gateway: GatewayServiceConfig = field(default_factory=GatewayServiceConfig)
    trajectory: TrajectoryConfig = field(default_factory=TrajectoryConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    jiuwen: JiuwenConfig = field(default_factory=JiuwenConfig)
