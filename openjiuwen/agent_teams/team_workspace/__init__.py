# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team shared workspace — per-team lifecycle artifact management."""

from openjiuwen.agent_teams.team_workspace.assembler import prepare_member_workspace
from openjiuwen.agent_teams.team_workspace.binder import MemberWorkspaceBinder, TeamMemberBinding
from openjiuwen.agent_teams.team_workspace.dir_links import create_dir_link, is_dir_link, remove_dir_link
from openjiuwen.agent_teams.team_workspace.migrator import TeamWorkspaceMigrator
from openjiuwen.agent_teams.team_workspace.models import (
    ConflictStrategy,
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
)
from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    member_dir_name,
    member_real_dir,
)
from openjiuwen.agent_teams.team_workspace.ref_store import MemberRefStore
from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail

__all__ = [
    # Models
    "ConflictStrategy",
    "TeamWorkspaceConfig",
    "WorkspaceFileLock",
    "WorkspaceMode",
    # Manager
    "TeamWorkspaceManager",
    # Tools
    "WorkspaceMetaTool",
    # Rails
    "TeamWorkspaceRail",
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
