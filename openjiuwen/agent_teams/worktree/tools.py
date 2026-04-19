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
            **kwargs: May contain "member_name" and "team_name" from caller context.

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
                member_name=kwargs.get("member_name"),
                team_name=kwargs.get("team_name"),
            )
        except (RuntimeError, GitError) as e:
            return ToolOutput(
                success=False,
                error=f"Failed to create worktree: {e}",
            )

        from openjiuwen.core.sys_operation.cwd import set_cwd, set_original_cwd

        set_cwd(session.worktree_path)
        set_original_cwd(session.worktree_path)
        # project_root stays unchanged

        return ToolOutput(
            success=True,
            data={
                "worktree_path": session.worktree_path,
                "worktree_branch": session.worktree_branch,
                "message": (
                    f"Created worktree at {session.worktree_path} "
                    f"on branch {session.worktree_branch}. "
                    f"CWD switched to worktree."
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
            ToolOutput with exit result data including action, paths,
            optional change counts, and a human-readable message.
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

        # Count changes before exit for reporting (remove + discard only).
        discarded_files: int | None = None
        discarded_commits: int | None = None
        if action == "remove" and discard:
            summary = await self._manager.count_changes(session)
            if summary:
                discarded_files = summary.changed_files
                discarded_commits = summary.commits

        try:
            result = await self._manager.exit(action, discard_changes=discard)
        except (RuntimeError, GitError) as e:
            return ToolOutput(
                success=False,
                error=f"Failed to exit worktree: {e}",
            )
        except ValueError as e:
            # Two-phase confirmation: worktree has changes, discard_changes not set.
            return ToolOutput(success=False, error=str(e))

        from openjiuwen.core.sys_operation.cwd import set_cwd, set_original_cwd

        original_cwd = result.get("original_cwd")
        if original_cwd:
            set_cwd(original_cwd)
            set_original_cwd(original_cwd)

        # Build human-readable message (spec §2.5).
        branch = result.get("worktree_branch") or "unknown"
        if action == "keep":
            message = f"Kept worktree (branch {branch}). Returned to {original_cwd}"
        else:
            message = f"Removed worktree (branch {branch}). Returned to {original_cwd}"

        data = {
            **result,
            "message": message,
        }
        if discarded_files is not None:
            data["discarded_files"] = discarded_files
        if discarded_commits is not None:
            data["discarded_commits"] = discarded_commits

        return ToolOutput(success=True, data=data)


