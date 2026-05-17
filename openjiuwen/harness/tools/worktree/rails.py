# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Worktree rails.

Two distinct rail flavours live here:

* :class:`WorktreeRail` — **injection rail**. Mount this on a
  ``DeepAgent`` (via ``rails=[WorktreeRail()]``) to give the agent
  ``enter_worktree`` / ``exit_worktree`` tools. The rail owns the
  per-agent :class:`WorktreeManager` so callers do not have to wire it
  by hand.
* :class:`WorktreeLifecycleRail` — **hook base class**. Subclass to
  intercept worktree lifecycle events (create / exit / commit / sync).
  :class:`WorktreeManager` calls these hooks directly (not via
  ``AgentCallbackEvent`` routing) because worktree events span across
  tool calls rather than within the agent task-loop.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.sys_operation.cwd import set_cwd, set_original_cwd
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.worktree.events import WorktreeEventHandler
from openjiuwen.harness.tools.worktree.git import _run_git
from openjiuwen.harness.tools.worktree.manager import WorktreeManager
from openjiuwen.harness.tools.worktree.models import WorktreeConfig, WorktreeSession
from openjiuwen.harness.tools.worktree.session import (
    get_current_session,
    init_session_state,
    set_current_session,
)
from openjiuwen.harness.tools.worktree.tools import EnterWorktreeTool, ExitWorktreeTool

if TYPE_CHECKING:
    from openjiuwen.core.foundation.tool.base import Tool


# Key under which the active worktree session is mirrored onto the
# agent's ``Session`` state, so interrupt/resume restores it across
# invocations. The ContextVar layer in ``session.py`` is the per-invoke
# cache; agent ``Session.state`` is the persistent authority.
_SESSION_STATE_KEY = "_worktree_session"


class WorktreeRail(DeepAgentRail):
    """Inject ``enter_worktree`` / ``exit_worktree`` into a DeepAgent.

    The rail owns the per-agent :class:`WorktreeManager` and registers
    the two worktree tools on the agent during ``init``. ``uninit``
    cleans both the agent's ability manager and the shared resource
    manager so hot-reload / re-registration stays consistent.

    The rail also bridges the in-process ``ContextVar`` session
    (``openjiuwen.harness.tools.worktree.session``) and the agent's
    persistent :class:`Session` state: ``before_invoke`` rehydrates
    the worktree session from ``Session.state`` and ``after_invoke``
    persists it back. This lets the worktree presence survive
    interrupt / resume across separate ``agent.invoke()`` calls when
    callers share a ``Session``.

    Example::

        rail = WorktreeRail(event_handler=on_event)
        agent = create_deep_agent(
            ...,
            rails=[SysOperationRail(), rail],
        )

    Args:
        config: Optional :class:`WorktreeConfig`. Defaults to
            ``WorktreeConfig(enabled=True)``.
        event_handler: Async callback fired on
            ``WorktreeCreatedEvent`` / ``WorktreeRemovedEvent``.
        lifecycle_rails: Optional list of
            :class:`WorktreeLifecycleRail` subclasses to plug into the
            manager's hook chain (commit lint, auto-setup, etc.).
    """

    # Match SysOperationRail so tool injection happens at the same
    # priority tier as the filesystem/bash toolset.
    priority = 100

    def __init__(
        self,
        *,
        config: WorktreeConfig | None = None,
        event_handler: WorktreeEventHandler | None = None,
        lifecycle_rails: list["WorktreeLifecycleRail"] | None = None,
    ) -> None:
        super().__init__()
        self._user_config = config or WorktreeConfig(enabled=True)
        self._event_handler = event_handler
        self._lifecycle_rails: list[WorktreeLifecycleRail] = list(lifecycle_rails or [])
        self._manager: WorktreeManager | None = None
        self._tools: list[Tool] = []

    @property
    def manager(self) -> WorktreeManager | None:
        """The owned :class:`WorktreeManager`, available after ``init``."""
        return self._manager

    def init(self, agent) -> None:
        """Build the manager and register enter/exit tools on ``agent``."""
        lang = agent.system_prompt_builder.language
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        self._manager = WorktreeManager(
            self._user_config,
            event_handler=self._event_handler,
            rails=list(self._lifecycle_rails),
        )

        # Eagerly create the session container in the current task so
        # tool calls within the agent share the same mutable holder
        # across ``asyncio.gather`` boundaries.
        init_session_state()

        self._tools = [
            EnterWorktreeTool(self._manager, language=lang, agent_id=agent_id),
            ExitWorktreeTool(self._manager, language=lang, agent_id=agent_id),
        ]

        Runner.resource_mgr.add_tool(self._tools)
        for tool in self._tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent) -> None:
        """Detach the tools and drop the manager reference."""
        for tool in self._tools:
            name = getattr(tool.card, "name", None)
            if name and hasattr(agent, "ability_manager"):
                agent.ability_manager.remove(name)
            tool_id = getattr(tool.card, "id", None)
            if tool_id:
                Runner.resource_mgr.remove_tool(tool_id)
        self._tools = []
        self._manager = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Restore the worktree session from agent ``Session`` state.

        Reads the worktree session dict stashed under
        ``_SESSION_STATE_KEY`` and writes it into the per-invoke
        ``ContextVar`` so the resumed task sees the active worktree
        as if it had never been interrupted. A missing or ``None``
        entry resets the ContextVar to ``None``.

        Restoring the worktree is not just about flipping the
        session pointer: :class:`EnterWorktreeTool` also redirects
        the cwd ContextVar to ``worktree_path`` on entry, so a
        partial restore (session yes, cwd no) would leave resumed
        tool calls running shell commands in the wrong directory.
        We mirror the enter-time cwd update here. cwd is *not*
        cleared on the no-session branch because cwd is shared
        with non-worktree agents and reset semantics belong to
        whoever last set it.

        Tolerates both ``dict`` (the canonical, JSON-friendly form
        produced by :meth:`after_invoke`) and a bare
        :class:`WorktreeSession` (defensive fallback for legacy
        in-process checkpointers).
        """
        if ctx.session is None:
            return
        stored = ctx.session.get_state(_SESSION_STATE_KEY)
        if stored is None:
            set_current_session(None)
            return
        if isinstance(stored, dict):
            stored = WorktreeSession.model_validate(stored)
        set_current_session(stored)
        set_cwd(stored.worktree_path)
        set_original_cwd(stored.worktree_path)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Persist the worktree session into agent ``Session`` state.

        Mirrors the current ``ContextVar`` session (or ``None`` after
        ``exit_worktree``) into ``Session.state`` so the next
        ``agent.invoke()`` sharing the same session — including the
        resume path from a tool interrupt — can rehydrate it.

        :meth:`AgentCallbackContext.lifecycle` fires ``after_invoke``
        inside a ``finally`` block, so this also runs on exception
        and interrupt paths.
        """
        if ctx.session is None:
            return
        current = get_current_session()
        payload = current.model_dump() if current is not None else None
        ctx.session.update_state({_SESSION_STATE_KEY: payload})


class WorktreeLifecycleRail(DeepAgentRail):
    """Hook base class for worktree lifecycle events.

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


class AutoSetupRail(WorktreeLifecycleRail):
    """Run setup commands after worktree creation.

    Auto-detects project type (Python/Node.js) when no explicit commands
    are provided, then runs dependency installation in the new worktree.
    """

    def __init__(self, commands: list[str] | None = None):
        super().__init__()
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
                agent_logger.warning("Setup command '%s' failed: %s", cmd, stderr.decode())

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


class DiffSummaryRail(WorktreeLifecycleRail):
    """Generate diff summary before worktree exit.

    Logs a stat-level diff when the exit action is "keep", so the
    caller can see what changed in the worktree at a glance.
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
            agent_logger.info("Worktree '%s' diff summary:\n%s", session.worktree_name, diff.stdout)
        return None
