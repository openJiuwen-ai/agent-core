# coding: utf-8

"""Team workspace rail for transparent version control and locking.

Intercepts standard filesystem tool calls targeting the .team/ mount point
and applies workspace policies (lock checking, auto-commit, push) without
the agent needing special workspace APIs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from openjiuwen.agent_teams.team_workspace.models import (
    ConflictStrategy,
    WorkspaceMode,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager


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

    def __init__(self, workspace_manager: TeamWorkspaceManager, member_name: str):
        super().__init__()
        self._ws = workspace_manager
        self._member_name = member_name
        self._last_pull_time: float = 0.0
        self._pull_interval: float = 5.0

    def init(self, agent) -> None:
        """Populate team_workspace on the agent's CwdState.

        Runs inside the owning agent's asyncio Task context (invoked
        from ``DeepAgent._ensure_initialized`` after ``init_cwd``), so
        the ContextVar-based CwdState created there is the one we
        mutate here.  Future tool calls in this agent can read the
        team workspace root via ``get_team_workspace()``.
        """
        from openjiuwen.core.sys_operation.cwd import set_team_workspace

        set_team_workspace(self._ws.workspace_path)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Before file operations on .team/: pull for reads, check lock for writes.

        Extracts tool_call from ctx.inputs. If the target path starts with
        .team/, applies read (pull) or write (lock check) policies.

        Args:
            ctx: Agent callback context with tool_call in inputs.
        """
        tool_name = ctx.inputs.tool_name
        tool_args = ctx.inputs.tool_args if isinstance(ctx.inputs.tool_args, dict) else {}
        path = tool_args.get("file_path", "")
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
            if lock and lock.holder_id != self._member_name and not lock.is_expired():
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
        tool_name = ctx.inputs.tool_name
        if tool_name not in self.WRITE_TOOLS:
            return

        tool_args = ctx.inputs.tool_args if isinstance(ctx.inputs.tool_args, dict) else {}
        path = tool_args.get("file_path", "")
        if not path.startswith(self.TEAM_PREFIX):
            return

        real_path = self._resolve_workspace_relative(path)

        # Auto version control (includes push in distributed mode)
        if self._ws.config.version_control:
            await self._ws.auto_commit(real_path, self._member_name)

        # Publish event via callback
        if self._ws.publish_event:
            from openjiuwen.agent_teams.schema.events import TeamEvent, WorkspaceArtifactEvent

            await self._ws.publish_event(
                TeamEvent.WORKSPACE_ARTIFACT_UPDATED,
                WorkspaceArtifactEvent(
                    team_name=self._ws.team_name,
                    member_name=self._member_name,
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

    def _resolve_workspace_relative(self, path: str) -> str:
        """Extract the workspace-relative path from a .team/ prefixed path.

        Handles both layouts:
        - Hub: ``.team/{team_name}/artifacts/report.md`` → ``artifacts/report.md``
        - Legacy: ``.team/artifacts/report.md`` → ``artifacts/report.md``

        Uses ``self._ws.team_name`` to detect the hub layout.

        Args:
            path: File path starting with ".team/".

        Returns:
            Path relative to the team workspace root.
        """
        after_prefix = path[len(self.TEAM_PREFIX):]
        # Hub layout: first segment matches team_name
        team_name_prefix = self._ws.team_name + "/"
        if after_prefix.startswith(team_name_prefix):
            return after_prefix[len(team_name_prefix):]
        return after_prefix
