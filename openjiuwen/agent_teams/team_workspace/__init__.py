# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team workspace — shared workspace management + evolvable workspace.

Two families of functionality merged in this package:

- team shared workspace (locking/versioning/sync): ``TeamWorkspaceManager``,
  ``TeamWorkspaceConfig``, ``WorkspaceFileLock``, ``WorkspaceMode``,
  ``ConflictStrategy``, ``WorkspaceMetaTool``, ``TeamWorkspaceRail``.
- evolvable workspace (assembly/read):
  ``WorkspaceAssembler``, ``WorkspaceStore``, ``WorkspaceCache``,
  ``SessionFileStore``.
"""

from openjiuwen.agent_teams.team_workspace.assembler import WorkspaceAssembler
from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
from openjiuwen.agent_teams.team_workspace.models import (
    ConflictStrategy,
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail
from openjiuwen.agent_teams.team_workspace.session_file_store import (
    FileAddress,
    SessionFileStore,
)
from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore

__all__ = [
    # Evolvable workspace
    "FileAddress",
    "SessionFileStore",
    "WorkspaceAssembler",
    "WorkspaceCache",
    "WorkspaceStore",
    # Team shared workspace
    "ConflictStrategy",
    "TeamWorkspaceConfig",
    "TeamWorkspaceManager",
    "TeamWorkspaceRail",
    "WorkspaceFileLock",
    "WorkspaceMetaTool",
    "WorkspaceMode",
]
