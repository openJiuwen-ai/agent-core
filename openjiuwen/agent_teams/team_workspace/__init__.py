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
- block C member-directory topology (link/migrate/refs):
  ``prepare_member_workspace``, ``MemberWorkspaceBinder``,
  ``TeamMemberBinding``, ``TeamWorkspaceMigrator``, ``MemberRefStore``,
  ``MEMBER_MODE_*``, ``member_dir_name``, ``member_real_dir``,
  ``create_dir_link``, ``is_dir_link``, ``remove_dir_link``.
"""

from openjiuwen.agent_teams.team_workspace.assembler import WorkspaceAssembler
from openjiuwen.agent_teams.team_workspace.binder import (
    MemberWorkspaceBinder,
    TeamMemberBinding,
    prepare_member_workspace,
)
from openjiuwen.agent_teams.team_workspace.dir_links import (
    create_dir_link,
    is_dir_link,
    remove_dir_link,
)
from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
from openjiuwen.agent_teams.team_workspace.migrator import TeamWorkspaceMigrator
from openjiuwen.agent_teams.team_workspace.models import (
    ConflictStrategy,
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    member_dir_name,
    member_real_dir,
)
from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail
from openjiuwen.agent_teams.team_workspace.ref_store import MemberRefStore
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
    # Block C: member directory topology
    "MEMBER_MODE_DYNAMIC",
    "MEMBER_MODE_LEADER",
    "MEMBER_MODE_PREDEFINED",
    "member_dir_name",
    "member_real_dir",
    "create_dir_link",
    "is_dir_link",
    "remove_dir_link",
    "MemberRefStore",
    "TeamMemberBinding",
    "MemberWorkspaceBinder",
    "TeamWorkspaceMigrator",
    "prepare_member_workspace",
]
