# coding: utf-8

"""Worktree tools for entering and exiting git worktree sessions.

Provides EnterWorktreeTool and ExitWorktreeTool as TeamTool implementations.
Both delegate to WorktreeManager for actual worktree lifecycle operations.
"""

from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team_tools import TeamTool
from openjiuwen.agent_teams.worktree.git import GitError
from openjiuwen.agent_teams.worktree.session import get_current_session
from openjiuwen.agent_teams.worktree.slug import validate_slug
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.worktree.manager import WorktreeManager
    from openjiuwen.agent_teams.worktree.workspace import TeamWorkspaceManager


def _generate_random_slug() -> str:
    """Generate a short random worktree name.

    Returns:
        Slug in the format "<adjective>-<noun>-<4hex>" (e.g. "swift-fox-a3b1").
    """
    import secrets

    adjectives = ["swift", "bright", "calm", "keen", "bold"]
    nouns = ["fox", "owl", "elm", "oak", "ray"]
    adj = secrets.choice(adjectives)
    noun = secrets.choice(nouns)
    suffix = secrets.token_hex(2)
    return f"{adj}-{noun}-{suffix}"


class EnterWorktreeTool(TeamTool):
    """Create or enter an isolated git worktree.

    Gives the calling agent its own working copy of the repository.
    File changes in the worktree do not affect the main repo or
    other worktrees.
    """

    def __init__(self, manager: WorktreeManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.enter_worktree",
                name="enter_worktree",
                description=t("enter_worktree"),
            )
        )
        self._manager = manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": t("enter_worktree", "name"),
                },
            },
            "required": [],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Enter a worktree session.

        Args:
            inputs: Tool inputs. Optional "name" key for worktree slug.
            **kwargs: May contain "member_id" and "team_id" from caller context.

        Returns:
            ToolOutput with worktree_path, worktree_branch, and message on success.
        """
        existing = get_current_session()
        if existing:
            return ToolOutput(
                success=False,
                error=(
                    f"Already in worktree '{existing.worktree_name}'. "
                    f"Exit first with exit_worktree."
                ),
            )

        slug = inputs.get("name")
        if not slug:
            slug = _generate_random_slug()

        try:
            validate_slug(slug)
        except ValueError as e:
            return ToolOutput(success=False, error=str(e))

        try:
            session = await self._manager.enter(
                slug,
                member_id=kwargs.get("member_id"),
                team_id=kwargs.get("team_id"),
            )
        except (RuntimeError, GitError) as e:
            return ToolOutput(
                success=False,
                error=f"Failed to create worktree: {e}",
            )

        return ToolOutput(
            success=True,
            data={
                "worktree_path": session.worktree_path,
                "worktree_branch": session.worktree_branch,
                "message": (
                    f"Created worktree at {session.worktree_path} "
                    f"on branch {session.worktree_branch}."
                ),
            },
        )


class ExitWorktreeTool(TeamTool):
    """Exit the current worktree session.

    Choose "keep" to preserve the worktree for later use,
    or "remove" to delete it and discard all changes.
    """

    def __init__(self, manager: WorktreeManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.exit_worktree",
                name="exit_worktree",
                description=t("exit_worktree"),
            )
        )
        self._manager = manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": t("exit_worktree", "action"),
                },
                "discard_changes": {
                    "type": "boolean",
                    "description": t("exit_worktree", "discard_changes"),
                },
            },
            "required": ["action"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Exit the current worktree session.

        Args:
            inputs: Tool inputs with required "action" ("keep" or "remove")
                and optional "discard_changes" boolean.
            **kwargs: Unused.

        Returns:
            ToolOutput with exit result data on success.
        """
        session = get_current_session()
        if not session:
            return ToolOutput(
                success=False,
                error="No active worktree session to exit.",
            )

        action = inputs.get("action")
        if action not in ("keep", "remove"):
            return ToolOutput(
                success=False,
                error="'action' must be 'keep' or 'remove'.",
            )

        discard = inputs.get("discard_changes", False)

        try:
            result = await self._manager.exit(action, discard_changes=discard)
        except (RuntimeError, GitError) as e:
            return ToolOutput(
                success=False,
                error=f"Failed to exit worktree: {e}",
            )

        return ToolOutput(success=True, data=result)


class WorkspaceMetaTool(TeamTool):
    """Workspace metadata operations (lock management and version history).

    File I/O goes through standard read_file/write_file/glob via the .team/
    mount point. This tool ONLY handles lock management and version history
    queries that have no filesystem equivalent.
    """

    def __init__(self, workspace: TeamWorkspaceManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.workspace_meta",
                name="workspace_meta",
                description=t("workspace_meta"),
            )
        )
        self._ws = workspace
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["lock", "unlock", "locks", "history"],
                    "description": t("workspace_meta", "action"),
                },
                "path": {
                    "type": "string",
                    "description": t("workspace_meta", "path"),
                },
            },
            "required": ["action"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Execute a workspace metadata action.

        Args:
            inputs: Tool inputs with required "action" and optional "path".
            **kwargs: May contain "member_id" and "member_name" from caller context.

        Returns:
            ToolOutput with action-specific result data.
        """
        action = inputs.get("action")
        path = inputs.get("path", "")
        member_id = kwargs.get("member_id", "unknown")
        member_name = kwargs.get("member_name", member_id)

        if action == "lock":
            if not path:
                return ToolOutput(success=False, error="'path' is required for lock action")
            acquired = await self._ws.acquire_lock(path, member_id, member_name)
            if not acquired:
                lock = self._ws.get_lock(path)
                return ToolOutput(
                    success=False,
                    error=f"Locked by {lock.holder_name}" if lock else "Lock failed",
                )
            return ToolOutput(success=True, data={"locked": path})

        if action == "unlock":
            if not path:
                return ToolOutput(success=False, error="'path' is required for unlock action")
            released = await self._ws.release_lock(path, member_id)
            return ToolOutput(success=True, data={"released": released})

        if action == "locks":
            locks = await self._ws.list_locks()
            return ToolOutput(
                success=True,
                data={"locks": [lock.model_dump() for lock in locks]},
            )

        if action == "history":
            if not path:
                return ToolOutput(success=False, error="'path' is required for history action")
            history = await self._ws.get_history(path)
            return ToolOutput(success=True, data={"history": history})

        return ToolOutput(success=False, error=f"Unknown action '{action}'")
