# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.runner.runner_config import RunnerConfig


class SpawnAgentKind(str, Enum):
    """Supported spawned-agent bootstrap kinds."""

    CLASS_AGENT = "class_agent"
    TEAM_AGENT = "team_agent"


class SpawnAgentConfig(BaseModel):
    """Base spawn config shared by all child-process agent bootstraps."""

    agent_kind: SpawnAgentKind
    runner_config: Optional[dict[str, Any]] = None
    logging_config: Optional[dict[str, Any]] = None
    session_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class ClassAgentSpawnConfig(SpawnAgentConfig):
    """JSON-safe config for constructing an agent class in the child process."""

    agent_kind: SpawnAgentKind = SpawnAgentKind.CLASS_AGENT
    agent_module: str
    agent_class: str
    init_kwargs: dict[str, Any] = Field(default_factory=dict)


def serialize_runner_config(config: RunnerConfig) -> dict[str, Any]:
    """Serialize RunnerConfig to a JSON-safe dictionary."""
    return config.model_dump(mode="json")


def deserialize_runner_config(payload: dict[str, Any]) -> RunnerConfig:
    """Rebuild RunnerConfig from JSON-safe payload."""
    return RunnerConfig.model_validate(payload)


def parse_spawn_agent_config(payload: dict[str, Any]) -> SpawnAgentConfig:
    """Validate spawn config with the schema matching agent_kind."""
    agent_kind = payload.get("agent_kind")
    if agent_kind == SpawnAgentKind.CLASS_AGENT.value:
        return ClassAgentSpawnConfig.model_validate(payload)
    return SpawnAgentConfig.model_validate(payload)


__all__ = [
    "ClassAgentSpawnConfig",
    "SpawnAgentConfig",
    "SpawnAgentKind",
    "deserialize_runner_config",
    "parse_spawn_agent_config",
    "serialize_runner_config",
]
