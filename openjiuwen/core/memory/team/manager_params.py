# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Construction parameters for :class:`~openjiuwen.core.memory.team.manager.TeamMemoryManager`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.sys_operation.sys_operation import SysOperation
from openjiuwen.harness.workspace.workspace import Workspace

TeamRole = Literal["leader", "teammate"]
TeamLifecycle = Literal["temporary", "persistent"]
TeamScenario = Literal["general", "coding"]
TeamLanguage = Literal["cn", "en"]
PromptMode = Literal["proactive", "passive"]


@dataclass
class TeamMemoryManagerParams:
    """Inputs required to construct :class:`TeamMemoryManager`.

    Groups identity, workspace/runtime handles, prompt preferences, and optional team-memory extraction.
    """

    member_name: str
    team_name: str
    role: TeamRole
    lifecycle: TeamLifecycle
    scenario: TeamScenario
    embedding_config: Optional[EmbeddingConfig]
    workspace: Optional[Workspace]
    sys_operation: Optional[SysOperation]
    team_memory_dir: Optional[str]
    language: TeamLanguage
    prompt_mode: PromptMode
    enable_auto_extract: bool
    read_only_source_workspace: Optional[str]
    db: Optional[TeamDatabase] = None
    task_manager: Optional[TeamTaskManager] = None
    extraction_model: Optional[Model] = None
    timezone_offset_hours: float = 8.0


__all__ = [
    "PromptMode",
    "TeamLanguage",
    "TeamLifecycle",
    "TeamMemoryManagerParams",
    "TeamRole",
    "TeamScenario",
]
