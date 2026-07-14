# coding: utf-8

"""Worktree tools for entering and exiting git worktree sessions.

Provides ``EnterWorktreeTool`` and ``ExitWorktreeTool`` as plain
``Tool`` implementations. Both delegate to ``WorktreeManager`` for the
actual lifecycle operations.

Owner identification: ``invoke()`` reads ``owner_id`` / ``tag`` from the
caller's kwargs (or the legacy ``member_name`` / ``team_name`` keys, for
the team framework's existing call shape). Single-agent callers may
omit them entirely.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, TYPE_CHECKING

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation.cwd import get_workspace
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput
from openjiuwen.harness.tools.worktree.git import GitError
from openjiuwen.harness.tools.worktree.session import (
    get_current_session,
    get_default_worktree_name,
    set_default_worktree_name,
)
from openjiuwen.harness.tools.worktree.slug import validate_slug, worktree_path_for

if TYPE_CHECKING:
    from openjiuwen.harness.tools.worktree.manager import WorktreeManager


class _WorktreeToolBase(Tool):
    """Common scaffolding for worktree tools.

    Worktree operations are inherently single-shot lifecycle calls,
    so streaming is not supported.
    """

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        """Streaming is not supported for worktree tools."""
        raise NotImplementedError("Worktree tools do not support streaming")


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


def _resolve_owner(kwargs: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve owner_id and tag from caller kwargs.

    Accepts both the generic ``owner_id`` / ``tag`` keys and the legacy
    ``member_name`` / ``team_name`` keys used by the team framework.
    """
    owner_id = kwargs.get("owner_id") or kwargs.get("member_name")
    tag = kwargs.get("tag") or kwargs.get("team_name")
    return owner_id, tag


def _resolve_workspace_worktree_path(slug: str) -> str:
    workspace = get_workspace()
    if workspace is None:
        raise RuntimeError(
            "Cannot resolve worktree path: DeepAgent workspace is not set. "
            "Worktrees must be created from an agent that has a workspace "
            "configured (init_cwd was called with workspace=...)."
        )
    return worktree_path_for(workspace, slug)


class EnterWorktreeTool(_WorktreeToolBase):
    """Create or enter an isolated git worktree.

    Gives the calling agent its own working copy of the repository.
    File changes in the worktree do not affect the main repo or
    other worktrees.
    """

    def __init__(
        self,
        manager: WorktreeManager,
        language: str = "cn",
        agent_id: str | None = None,
    ):
        super().__init__(
            build_tool_card(
                "enter_worktree",
                "worktree.enter",
                language=language,
                agent_id=agent_id,
            )
        )
        self._manager = manager

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Enter a worktree session.

        Args:
            inputs: Tool inputs. Optional "name" key for worktree slug.
            **kwargs: Optional ``owner_id`` / ``tag`` (or legacy
                ``member_name`` / ``team_name``) propagated through events.

        Returns:
            ToolOutput with worktree_path, worktree_branch, and message on success.
        """
        existing = get_current_session()
        if existing:
            return ToolOutput(
                success=False,
                error=(
                    f"Already in worktree '{existing.worktree_name}'. "
                    "Exit first with exit_worktree."
                ),
            )

        try:
            slug, existed = await self._resolve_slug(inputs)
        except ValueError as e:
            return ToolOutput(success=False, error=str(e))

        owner_id, tag = _resolve_owner(kwargs)

        try:
            session = await self._manager.enter(
                slug,
                member_name=owner_id,
                team_name=tag,
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

        output = ToolOutput(
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

        if existed:
            output.data["existed"] = True
            output.data["message"] = (
                f"Entered existing worktree at {session.worktree_path} "
                f"on branch {session.worktree_branch}. CWD switched to worktree."
            )
        return output

    async def _resolve_slug(self, inputs: Dict[str, Any]) -> tuple[str, bool]:
        requested = inputs.get("name")
        if requested is None:
            slug = get_default_worktree_name()
            if slug is None:
                slug = _generate_random_slug()
                validate_slug(slug)
                set_default_worktree_name(slug)
            else:
                validate_slug(slug)

            return slug, await self._slug_exists(slug)

        if not isinstance(requested, str):
            raise ValueError("'name' must be a string")

        selected = requested.strip()
        if not selected:
            raise ValueError("'name' must not be empty")

        validate_slug(selected)
        return selected, await self._slug_exists(selected)

    async def _slug_exists(self, slug: str) -> bool:
        try:
            target_path = _resolve_workspace_worktree_path(slug)
            return await self._manager.backend.exists(target_path)
        except (AttributeError, RuntimeError):
            return False


class ExitWorktreeTool(_WorktreeToolBase):
    """Exit the current worktree session.

    Choose "keep" to preserve the worktree for later use,
    or "remove" to delete it and discard all changes.
    """

    def __init__(
        self,
        manager: WorktreeManager,
        language: str = "cn",
        agent_id: str | None = None,
    ):
        super().__init__(
            build_tool_card(
                "exit_worktree",
                "worktree.exit",
                language=language,
                agent_id=agent_id,
            )
        )
        self._manager = manager

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
        del kwargs
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
        except ValidationError as e:
            # Two-phase confirmation: worktree has changes or state
            # cannot be verified, and discard_changes was not set.
            return ToolOutput(success=False, error=e.message)

        from openjiuwen.core.sys_operation.cwd import set_cwd, set_original_cwd

        original_cwd = result.get("original_cwd")
        if original_cwd:
            set_cwd(original_cwd)
            set_original_cwd(original_cwd)

        # Build human-readable message.
        branch = result.get("worktree_branch") or "unknown"
        if action == "keep":
            message = (
                f"Kept worktree '{session.worktree_name}' (branch {branch}). "
                "In this session, enter_worktree without a name will re-enter it; "
                f"from another session, pass name='{session.worktree_name}'. "
                f"Returned to {original_cwd}"
            )
        else:
            message = (
                f"Removed worktree '{session.worktree_name}' "
                f"(branch {branch}). Returned to {original_cwd}"
            )

        data = {
            **result,
            "worktree_name": session.worktree_name,
            "message": message,
        }
        if discarded_files is not None:
            data["discarded_files"] = discarded_files
        if discarded_commits is not None:
            data["discarded_commits"] = discarded_commits

        return ToolOutput(success=True, data=data)
