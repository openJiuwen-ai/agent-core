# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


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
        path = self._path_from_tool_args(tool_name, tool_args)
        if not path:
            return

        # Flat mount: ``.team`` → team-workspace. Rewrite legacy /
        # confused paths (``.team/{team}/...``, ``{ws}/.team/...``) onto
        # the canonical relative mount before policy checks.
        canonical = self._canonicalize_team_path(path)
        if canonical != path:
            self._set_path_on_tool_args(tool_name, tool_args, canonical)
            team_logger.info(
                "[{}] rewrote team path {} -> {}",
                self._member_name,
                path,
                canonical,
            )
            path = canonical

        if not path.startswith(self.TEAM_PREFIX):
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
        path = self._path_from_tool_args(tool_name, tool_args)
        if not path:
            return
        path = self._canonicalize_team_path(path)
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

    @staticmethod
    def _path_from_tool_args(tool_name: str, tool_args: dict) -> str:
        """Pick the path argument used by filesystem team tools."""
        if tool_name in {"glob", "grep", "list_files"}:
            raw = tool_args.get("path") or tool_args.get("file_path") or ""
        else:
            raw = tool_args.get("file_path") or tool_args.get("path") or ""
        return str(raw).replace("\\", "/")

    @staticmethod
    def _set_path_on_tool_args(tool_name: str, tool_args: dict, path: str) -> None:
        """Write the rewritten path back into the tool args the executor will use."""
        if tool_name in {"glob", "grep", "list_files"}:
            if "path" in tool_args or "file_path" not in tool_args:
                tool_args["path"] = path
            if "file_path" in tool_args:
                tool_args["file_path"] = path
            return
        if "file_path" in tool_args or "path" not in tool_args:
            tool_args["file_path"] = path
        if "path" in tool_args:
            tool_args["path"] = path

    def _canonicalize_team_path(self, path: str) -> str:
        """Normalize confused / legacy team paths onto flat ``.team/...``.

        Flat mount is ``{member_cwd}/.team`` → shared team-workspace, so the
        canonical tool path is ``.team/<rel>`` (no embedded team_name).

        Rewrites:
        - Legacy hub: ``.team/{team_name}/foo`` → ``.team/foo``
        - Abs nest mistake: ``{team_workspace}/.team/foo`` → ``.team/foo``
          (models often join the absolute root with the mount prefix)
        - Abs + legacy: ``{team_workspace}/.team/{team_name}/foo`` → ``.team/foo``
        """
        normalized = str(path or "").replace("\\", "/")
        if not normalized:
            return normalized

        team_name = (self._ws.team_name or "").strip()
        ws = str(self._ws.workspace_path or "").replace("\\", "/").rstrip("/")

        # Absolute path under the shared workspace root.
        if ws and normalized.lower().startswith(ws.lower() + "/"):
            rel = normalized[len(ws) + 1 :]  # noqa: E203
            if rel == ".team" or rel.startswith(".team/"):
                after = "" if rel == ".team" else rel[len(self.TEAM_PREFIX) :]  # noqa: E203
                after = self._strip_legacy_team_segment(after, team_name)
                return f"{self.TEAM_PREFIX}{after}" if after else self.TEAM_PREFIX.rstrip("/")
            return normalized

        if not normalized.startswith(self.TEAM_PREFIX) and normalized != ".team":
            return normalized

        after = "" if normalized == ".team" else normalized[len(self.TEAM_PREFIX) :]  # noqa: E203
        after = self._strip_legacy_team_segment(after, team_name)
        return f"{self.TEAM_PREFIX}{after}" if after else self.TEAM_PREFIX.rstrip("/")

    @staticmethod
    def _strip_legacy_team_segment(after: str, team_name: str) -> str:
        """Drop a leading ``{team_name}/`` segment left from the hub mount era."""
        if not team_name:
            return after
        if after == team_name:
            return ""
        prefix = team_name + "/"
        if after.startswith(prefix):
            return after[len(prefix) :]  # noqa: E203
        return after

    def _resolve_workspace_relative(self, path: str) -> str:
        """Extract the workspace-relative path from a ``.team/`` prefixed path.

        Flat mount: ``.team/artifacts/report.md`` → ``artifacts/report.md``.
        Also tolerates a leftover hub segment ``.team/{team_name}/...``.

        Args:
            path: File path starting with ".team/".

        Returns:
            Path relative to the team workspace root.
        """
        if path == ".team":
            return ""
        after_prefix = path[len(self.TEAM_PREFIX) :]  # noqa: E203
        return self._strip_legacy_team_segment(
            after_prefix, (self._ws.team_name or "").strip()
        )
