# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""Team workspace rail for transparent version control and locking.

Intercepts standard filesystem tool calls targeting the team shared
deliverables directory (``team-workspace/artifacts/<date>/chat-<n>/outputs/``)
and applies workspace policies (lock checking, auto-commit, push) without
the agent needing special workspace APIs. The ``.team/{team}/`` mount point
is gone: members address shared files by absolute path, so the rail matches
against the configured outputs directory instead of a ``.team/`` prefix.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

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

    Intercepts standard filesystem tool calls (write_file, edit_file). When the
    target path resolves into the shared team deliverables directory, applies
    workspace policies (lock checking, auto-commit, push) without the agent
    needing to know.

    Agent uses standard read_file/write_file — this rail adds behavior.
    """

    WRITE_TOOLS = frozenset({"write_file", "edit_file"})
    READ_TOOLS = frozenset({"read_file", "glob", "grep", "list_files"})

    def __init__(
        self,
        workspace_manager: TeamWorkspaceManager,
        member_name: str,
        outputs_dir: str | None = None,
    ):
        super().__init__()
        self._ws = workspace_manager
        self._member_name = member_name
        self._outputs_dir = os.path.abspath(os.path.expanduser(outputs_dir)) if outputs_dir else None
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

    @staticmethod
    def _extract_file_path(tool_args: Any) -> str:
        """Return the ``file_path`` argument from raw tool-call args.

        ``ctx.inputs.tool_args`` is the LMT tool-call ``arguments`` field, which
        arrives as a JSON string in the rail callback phase (the framework only
        parses it to a dict inside ``_execute_single_tool_call``, and only writes
        back when the JSON needed repair). Accept both shapes so the rail sees the
        real path instead of an empty string that silently skips every policy.
        """
        if isinstance(tool_args, dict):
            args = tool_args
        elif isinstance(tool_args, str) and tool_args.strip():
            try:
                args = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return ""
            if not isinstance(args, dict):
                return ""
        else:
            return ""
        return str(args.get("file_path") or "")

    def _is_deliverable_path(self, path: str) -> bool:
        """Return whether ``path`` targets a file in the shared outputs dir.

        A member writes deliverables by absolute path; this resolves the
        requested path and asks whether it lives under the configured outputs
        directory. When no outputs directory is configured (a member bound to a
        project, which keeps deliverables in the project) the rail never
        intercepts.
        """
        if not self._outputs_dir or not isinstance(path, str) or not path.strip():
            return False
        try:
            resolved = os.path.abspath(os.path.expanduser(path.strip()))
        except (OSError, ValueError):
            return False
        return os.path.commonpath([resolved, self._outputs_dir]) == self._outputs_dir

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Before deliverable file ops: pull for reads, check lock for writes.

        Extracts tool_call from ctx.inputs. If the target path is under the
        shared outputs directory, applies read (pull) or write (lock check)
        policies.
        """
        tool_name = ctx.inputs.tool_name
        path = self._extract_file_path(ctx.inputs.tool_args)
        if not self._is_deliverable_path(path):
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
        """After write/edit to a deliverable: git commit (+ push) + publish.

        Args:
            ctx: Agent callback context with tool_call in inputs.
        """
        tool_name = ctx.inputs.tool_name
        if tool_name not in self.WRITE_TOOLS:
            return

        path = self._extract_file_path(ctx.inputs.tool_args)
        if not self._is_deliverable_path(path):
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
        """Return the team-workspace-relative form of an absolute deliverable path.

        The shared outputs directory lives under ``team-workspace/artifacts/``,
        so a deliverable path is already inside the team workspace's git
        repository. Resolving to the workspace root keeps ``auto_commit`` and the
        publish event's ``artifact_path`` in the shape the manager expects.
        """
        try:
            resolved = os.path.abspath(os.path.expanduser(path.strip()))
        except (OSError, ValueError):
            return path
        ws_root = self._ws.workspace_path
        try:
            return os.path.relpath(resolved, ws_root)
        except ValueError:
            return resolved
