# coding: utf-8

"""Worktree rail base class with lifecycle hooks.

Extends DeepAgentRail with 8 hook methods for worktree lifecycle events:
create, exit, file write, commit, and sync phases. WorktreeManager calls
these hooks directly (not via AgentCallbackEvent routing) because worktree
events span across tool calls rather than within the agent task-loop.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, TYPE_CHECKING

from openjiuwen.agent_teams.worktree.git import _run_git
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.worktree.models import WorktreeSession


class WorktreeRail(DeepAgentRail):
    """Rail providing hooks for worktree lifecycle events.

    Subclass this to inject custom behavior at worktree boundaries.
    All hooks receive the full AgentCallbackContext for access to
    agent state, tools, and session.

    Hook categories:
        - Create phase: before/after worktree creation.
        - Exit phase: before/after worktree exit.
        - File write: intercept file writes for access control.
        - Commit: intercept commits for message lint or CI triggers.
        - Sync: filter files during worktree-workspace sync.
    """

    # ── Create phase ────────────────────────────────────────

    async def before_worktree_create(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        slug: str,  # noqa: ARG002
        repo_root: str,  # noqa: ARG002
    ) -> str | None:
        """Called before worktree creation.

        Can return a modified slug, or None to proceed unchanged.
        Raise to abort creation.

        Args:
            ctx: Agent callback context.
            slug: Proposed worktree slug.
            repo_root: Absolute path to the repository root.

        Returns:
            Modified slug string, or None to keep original.
        """
        return None

    async def after_worktree_create(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
    ) -> None:
        """Called after worktree creation and post-setup.

        Use for: dependency installation, setup scripts,
        environment validation, workspace initialization.

        Args:
            ctx: Agent callback context.
            session: The newly created worktree session.
        """

    # ── Exit phase ──────────────────────────────────────────

    async def before_worktree_exit(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        action: str,  # noqa: ARG002
    ) -> str | None:
        """Called before worktree exit.

        Can override the action ("keep"/"remove") or return None to proceed.
        Use for: generating diff summaries, running final checks,
        committing staged changes.

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            action: Requested exit action ("keep" or "remove").

        Returns:
            Overridden action string, or None to keep original.
        """
        return None

    async def after_worktree_exit(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        action: str,  # noqa: ARG002
    ) -> None:
        """Called after worktree exit completes.

        Use for: cleanup notifications, metrics reporting,
        triggering CI pipelines.

        Args:
            ctx: Agent callback context.
            session: The exited worktree session.
            action: The action that was performed ("keep" or "remove").
        """

    # ── File write interception ─────────────────────────────

    async def on_worktree_file_write(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
    ) -> bool:
        """Called when agent writes a file in worktree.

        Return False to block the write (e.g., protected paths).

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            file_path: Absolute path to the file being written.

        Returns:
            True to allow the write, False to block.
        """
        return True

    # ── Commit interception ─────────────────────────────────

    async def before_worktree_commit(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        message: str,  # noqa: ARG002
        files: list[str],  # noqa: ARG002
    ) -> str | None:
        """Called before a commit in worktree.

        Can modify commit message or return None to proceed.
        Raise to abort commit.

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            message: Proposed commit message.
            files: List of files to be committed.

        Returns:
            Modified commit message, or None to keep original.
        """
        return None

    async def after_worktree_commit(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        commit_sha: str,  # noqa: ARG002
    ) -> None:
        """Called after a commit succeeds.

        Use for: triggering CI, notifying leader, updating task status.

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            commit_sha: The SHA of the new commit.
        """

    # ── Sync interception ───────────────────────────────────

    async def on_worktree_sync(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
        session: WorktreeSession,  # noqa: ARG002
        direction: str,  # noqa: ARG002
        files: list[str],
    ) -> list[str]:
        """Called when syncing files between worktree and shared workspace.

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            direction: "push" (worktree -> workspace) or "pull" (workspace -> worktree).
            files: List of relative file paths being synced.

        Returns:
            Filtered file list. Remove entries to skip them.
        """
        return files


# ── Built-in rails ──────────────────────────────────────────────


class AutoSetupRail(WorktreeRail):
    """Run setup commands after worktree creation.

    Auto-detects project type (Python/Node.js) when no explicit commands
    are provided, then runs dependency installation in the new worktree.
    """

    def __init__(self, commands: list[str] | None = None):
        self._commands = commands

    async def after_worktree_create(
        self,
        ctx: AgentCallbackContext,
        session: WorktreeSession,
    ) -> None:
        """Run setup commands in the newly created worktree.

        Args:
            ctx: Agent callback context.
            session: The newly created worktree session.
        """
        commands = self._commands or await self._detect_setup(session.worktree_path)
        for cmd in commands:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=session.worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                team_logger.warning("Setup command '%s' failed: %s", cmd, stderr.decode())

    @staticmethod
    async def _detect_setup(path: str) -> list[str]:
        """Detect project type and return appropriate setup commands.

        Args:
            path: Worktree root directory path.

        Returns:
            List of shell commands to run for project setup.
        """
        if os.path.exists(os.path.join(path, "pyproject.toml")):
            return ["uv sync --quiet"]
        if os.path.exists(os.path.join(path, "package.json")):
            return ["npm install --silent"]
        return []


class DiffSummaryRail(WorktreeRail):
    """Generate diff summary before worktree exit.

    Logs a stat-level diff when the exit action is "keep", so the
    team can see what changed in the worktree at a glance.
    """

    async def before_worktree_exit(
        self,
        ctx: AgentCallbackContext,
        session: WorktreeSession,
        action: str,
    ) -> str | None:
        """Log diff summary when keeping a worktree.

        Args:
            ctx: Agent callback context.
            session: Current worktree session.
            action: Requested exit action ("keep" or "remove").

        Returns:
            None (does not override the action).
        """
        if action != "keep":
            return None
        diff = await _run_git(
            ["diff", "--stat", f"{session.original_head_commit}..HEAD"],
            cwd=session.worktree_path,
        )
        if diff.ok and diff.stdout:
            team_logger.info("Worktree '%s' diff summary:\n%s", session.worktree_name, diff.stdout)
        return None


