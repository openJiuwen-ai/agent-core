# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.memory.lite.embeddings import resolve_embedding_config_from_env


class TeamMemoryConfig(BaseModel):
    enabled: bool = False
    scenario: str = "general"  # "general" | "coding"
    embedding_config: Optional[EmbeddingConfig] = Field(default=None, exclude=True)
    auto_extract: bool = True
    shared_memory: bool = True
    member_memory_prompt_mode: str = "proactive"
    timezone_offset_hours: float = 8.0

    """
    Temporary read-only memory source for the team.
    Points to the workspace path of the parent agent that created the team.
    Passed in by the caller when creating TeamAgentSpec and is not serialized.
    """
    parent_workspace_path: Optional[str] = Field(default=None, exclude=True)

    """
    Absolute path to the team's shared memory directory.
    If None, it is derived from team_home(team_name) / "team-memory".
    Allows the caller to override the default path. Not serialized."""
    team_memory_dir: Optional[str] = Field(default=None, exclude=True)


def resolve_embedding_config(config: Optional[TeamMemoryConfig]) -> Optional[EmbeddingConfig]:
    """
    Prioritize in-memory -> environment variable -> None.
    """
    if config and config.embedding_config:
        return config.embedding_config
    return resolve_embedding_config_from_env()