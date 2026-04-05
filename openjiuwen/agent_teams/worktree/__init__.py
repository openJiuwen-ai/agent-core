# coding: utf-8
"""Agent Teams Worktree — Git worktree isolation for team members."""

from openjiuwen.agent_teams.worktree.models import (
    ConflictStrategy,
    TeamWorkspaceConfig,
    WorkspaceFileLock,
    WorkspaceMode,
    WorktreeChangeSummary,
    WorktreeConfig,
    WorktreeCreateResult,
    WorktreeLifecyclePolicy,
    WorktreeSession,
)
from openjiuwen.agent_teams.worktree.slug import (
    validate_slug,
    worktree_branch_name,
    worktree_path_for,
    worktrees_dir,
)
from openjiuwen.agent_teams.worktree.backend import (
    GitBackend,
    WorktreeBackend,
    create_backend,
    register_worktree_backend,
)
from openjiuwen.agent_teams.worktree.manager import WorktreeManager
from openjiuwen.agent_teams.worktree.remote import (
    RemoteWorktreeBackend,
    WorktreeRemoteHandler,
    WorktreeRemoteRequest,
    WorktreeRemoteResponse,
)
from openjiuwen.agent_teams.worktree.session import (
    get_current_session,
    set_current_session,
)
from openjiuwen.agent_teams.worktree.cleanup import cleanup_stale_worktrees
from openjiuwen.agent_teams.worktree.notice import build_worktree_notice
from openjiuwen.agent_teams.worktree.workspace import TeamWorkspaceManager
from openjiuwen.agent_teams.worktree.tools import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorkspaceMetaTool,
)
from openjiuwen.agent_teams.worktree.rails import (
    AutoSetupRail,
    DiffSummaryRail,
    TeamWorkspaceRail,
)

__all__ = [
    # Models
    "WorktreeConfig",
    "WorktreeSession",
    "WorktreeCreateResult",
    "WorktreeChangeSummary",
    "WorktreeLifecyclePolicy",
    "TeamWorkspaceConfig",
    "ConflictStrategy",
    "WorkspaceFileLock",
    "WorkspaceMode",
    # Slug
    "validate_slug",
    "worktree_branch_name",
    "worktree_path_for",
    "worktrees_dir",
    # Backend
    "WorktreeBackend",
    "GitBackend",
    "create_backend",
    "register_worktree_backend",
    # Manager
    "WorktreeManager",
    # Remote
    "RemoteWorktreeBackend",
    "WorktreeRemoteHandler",
    "WorktreeRemoteRequest",
    "WorktreeRemoteResponse",
    # Session
    "get_current_session",
    "set_current_session",
    # Cleanup
    "cleanup_stale_worktrees",
    # Notice
    "build_worktree_notice",
    # Workspace
    "TeamWorkspaceManager",
    # Tools
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "WorkspaceMetaTool",
    # Rails
    "AutoSetupRail",
    "DiffSummaryRail",
    "TeamWorkspaceRail",
]
