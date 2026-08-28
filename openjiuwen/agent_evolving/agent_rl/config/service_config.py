# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Static configuration for the independent online-RL Service."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.security.path_checker import is_sensitive_path


class RLServiceConfig(BaseModel):
    """All process-owned settings for one fixed-model RL Service."""

    model_config = ConfigDict(extra="forbid")

    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=18081, ge=1, le=65535)
    redis_url: str
    trajectory_retention_seconds: int = Field(default=7 * 24 * 3600, ge=1)
    model_id: str
    base_model_path: str
    aigw_endpoint: str = "http://127.0.0.1:8080"
    judge_endpoint: str = ""
    judge_model: str = ""
    judge_api_key: str = ""
    judge_votes: int = Field(default=1, ge=1)
    judge_retries: int = Field(default=2, ge=0)
    judge_timeout: float = Field(default=30.0, gt=0)
    lora_activation_timeout: float = Field(default=150.0, gt=0)
    min_samples_for_training: int = Field(default=32, ge=1)
    max_samples_per_run: int = Field(default=32, ge=1)
    ppo_samples_per_step: int = Field(default=0, ge=0)
    ppo_config_path: str | None = None
    nproc_per_node: int = Field(default=1, ge=1)
    training_gpu_ids: str = ""
    lora_repository_path: str
    record_dir: str = "records"
    log_path: str = "rl-service.log"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    log_backup_count: int = Field(default=5, ge=1)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_service(self) -> RLServiceConfig:
        """Validate fixed-model and loopback invariants."""

        if self.listen_host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("listen_host must be a loopback address")
        if not self.model_id.strip():
            raise ValueError("model_id is required")
        if not self.base_model_path.strip():
            raise ValueError("base_model_path is required")
        if self.max_samples_per_run < self.min_samples_for_training:
            raise ValueError("max_samples_per_run must be >= min_samples_for_training")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> RLServiceConfig:
        """Load one static YAML config file."""

        config_path = Path(path)
        if ".." in config_path.parts:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                error_msg="RL Service config path must not contain parent traversal",
            )
        try:
            config_path = config_path.expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                cause=exc,
                error_msg=str(exc),
            ) from exc
        if is_sensitive_path(config_path):
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                error_msg="RL Service config path is sensitive",
            )
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                cause=exc,
                error_msg=str(exc),
            ) from exc
        if not isinstance(payload, dict):
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                error_msg="RL Service config must be a YAML object",
            )
        try:
            return cls.model_validate(payload)
        except PydanticValidationError as exc:
            raise build_error(
                StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR,
                cause=exc,
                error_msg=str(exc),
            ) from exc


__all__ = ["RLServiceConfig"]
