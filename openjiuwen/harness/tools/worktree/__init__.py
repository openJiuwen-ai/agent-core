# coding: utf-8
"""Harness Worktree — Git worktree isolation as a reusable harness tool.

Single-agent (deepagent) and multi-agent (team) callers both share this
implementation. The team-specific bridge (event translation, role-aware
mounting) lives in ``openjiuwen.agent_teams``; the cross-machine remote
backend lives in ``openjiuwen.agent_teams.worktree_remote`` and registers
itself via ``register_worktree_backend`` at import time.
"""

from openjiuwen.harness.tools.worktree.backend import (
    GitBackend,
    WorktreeBackend,
    create_backend,
    register_worktree_backend,
)
from openjiuwen.harness.tools.worktree.cleanup import cleanup_stale_worktrees
from openjiuwen.harness.tools.worktree.events import (
    WorktreeCreatedEvent,
    WorktreeEvent,
    WorktreeEventHandler,
    WorktreeRemovedEvent,
)
from openjiuwen.harness.tools.worktree.manager import WorktreeManager
from openjiuwen.harness.tools.worktree.models import (
    WorktreeChangeSummary,
    WorktreeConfig,
    WorktreeCreateResult,
    WorktreeLifecyclePolicy,
    WorktreeSession,
)
from openjiuwen.harness.tools.worktree.notice import build_worktree_notice
from openjiuwen.harness.tools.worktree.rails import (
    AutoSetupRail,
    DiffSummaryRail,
    WorktreeLifecycleRail,
    WorktreeRail,
)
from openjiuwen.harness.tools.worktree.session import (
    get_current_session,
    get_default_worktree_name,
    init_session_state,
    require_current_session,
    set_default_worktree_name,
    set_current_session,
)
from openjiuwen.harness.tools.worktree.slug import (
    validate_slug,
    worktree_branch_name,
    worktree_path_for,
    worktrees_dir,
)
from openjiuwen.harness.tools.worktree.tools import (
    EnterWorktreeTool,
    ExitWorktreeTool,
)

__all__ = [
    # Models
    "WorktreeConfig",
    "WorktreeSession",
    "WorktreeCreateResult",
    "WorktreeChangeSummary",
    "WorktreeLifecyclePolicy",
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
    # Session
    "get_current_session",
    "set_current_session",
    "get_default_worktree_name",
    "set_default_worktree_name",
    "init_session_state",
    "require_current_session",
    # Cleanup
    "cleanup_stale_worktrees",
    # Notice
    "build_worktree_notice",
    # Tools
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    # Rails
    "WorktreeRail",
    "WorktreeLifecycleRail",
    "AutoSetupRail",
    "DiffSummaryRail",
    # Events
    "WorktreeCreatedEvent",
    "WorktreeRemovedEvent",
    "WorktreeEvent",
    "WorktreeEventHandler",
]
