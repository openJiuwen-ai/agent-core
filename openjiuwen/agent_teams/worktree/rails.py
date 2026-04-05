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
import time
from typing import Any, TYPE_CHECKING

from openjiuwen.agent_teams.worktree.git import _run_git
from openjiuwen.agent_teams.worktree.models import ConflictStrategy, WorkspaceMode
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.worktree.models import WorktreeSession
    from openjiuwen.agent_teams.worktree.workspace import TeamWorkspaceManager


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


class TeamWorkspaceRail(DeepAgentRail):
    """Transparent version control and locking for team shared space.

    Intercepts standard filesystem tool calls (write_file, edit_file).
    When the target path is under .team/, applies workspace policies
    (lock checking, auto-commit, push) without the agent needing to know.

    Agent uses standard read_file/write_file — this rail adds behavior.
    """

    TEAM_PREFIX = ".team/"
    WRITE_TOOLS = frozenset({"write_file", "edit_file"})
    READ_TOOLS = frozenset({"read_file", "glob", "grep", "list_files"})

    def __init__(self, workspace_manager: TeamWorkspaceManager, member_id: str):
        super().__init__()
        self._ws = workspace_manager
        self._member_id = member_id
        self._last_pull_time: float = 0
        self._pull_interval: float = 5.0

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Before file operations on .team/: pull for reads, check lock for writes.

        Extracts tool_call from ctx.inputs. If the target path starts with
        .team/, applies read (pull) or write (lock check) policies.

        Args:
            ctx: Agent callback context with tool_call in inputs.
        """
        tool_call = ctx.inputs.get("tool_call")
        if not tool_call:
            return

        tool_name = getattr(tool_call, "tool_name", None) or ctx.inputs.get("tool_name", "")
        arguments = getattr(tool_call, "arguments", {}) or {}
        path = arguments.get("file_path", "")
        if not path or not path.startswith(self.TEAM_PREFIX):
            return

        # Read path: pull before read (distributed mode, throttled)
        if tool_name in self.READ_TOOLS:
            await self._maybe_pull()
            return

        if tool_name not in self.WRITE_TOOLS:
            return

        # Write path: pull + lock check
        await self._maybe_pull()

        if self._ws.config.conflict_strategy == ConflictStrategy.LOCK:
            lock = self._ws.get_lock(path)
            if lock and lock.holder_id != self._member_id and not lock.is_expired():
                from openjiuwen.harness.rails.interrupt.interrupt_base import RejectResult

                tool_msg_text = (
                    f"File '{path}' is locked by {lock.holder_name} ({lock.holder_id})"
                )
                team_logger.warning(tool_msg_text)
                # Store rejection info in extra for downstream handling
                ctx.extra["workspace_lock_rejected"] = tool_msg_text

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """After write/edit to .team/: git commit (+ push) + publish event.

        Args:
            ctx: Agent callback context with tool_call in inputs.
        """
        tool_call = ctx.inputs.get("tool_call")
        if not tool_call:
            return

        tool_name = getattr(tool_call, "tool_name", None) or ctx.inputs.get("tool_name", "")
        if tool_name not in self.WRITE_TOOLS:
            return

        arguments = getattr(tool_call, "arguments", {}) or {}
        path = arguments.get("file_path", "")
        if not path.startswith(self.TEAM_PREFIX):
            return

        real_path = path[len(self.TEAM_PREFIX):]

        # Auto version control (includes push in distributed mode)
        if self._ws.config.version_control:
            await self._ws.auto_commit(real_path, self._member_id)

        # Publish event via callback
        if self._ws.publish_event:
            from openjiuwen.agent_teams.schema.events import TeamEvent, WorkspaceArtifactEvent

            await self._ws.publish_event(
                TeamEvent.WORKSPACE_ARTIFACT_UPDATED,
                WorkspaceArtifactEvent(
                    team_id=self._ws.team_id,
                    member_id=self._member_id,
                    artifact_path=real_path,
                ),
            )

    async def _maybe_pull(self) -> None:
        """Throttled pull: at most once per _pull_interval seconds."""
        if self._ws.mode != WorkspaceMode.DISTRIBUTED:
            return
        now = time.monotonic()
        if now - self._last_pull_time < self._pull_interval:
            return
        self._last_pull_time = now
        await self._ws.pull()
