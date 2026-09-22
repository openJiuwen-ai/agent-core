# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organization workspace policy and local Git/lock integration."""

from __future__ import annotations

from openjiuwen.agent_teams.organization.workspace import OrganizationWorkspaceManager
from openjiuwen.agent_teams.team_workspace.models import ConflictStrategy
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class OrganizationWorkspaceRail(DeepAgentRail):
    """Route ``.organization`` file calls through Organization policies."""

    WRITE_TOOLS = frozenset({"write_file", "edit_file"})
    READ_TOOLS = frozenset({"read_file", "glob", "grep", "list_files"})
    _LOCK_KEY = "organization_workspace_lock"

    def __init__(
        self,
        manager: OrganizationWorkspaceManager,
        *,
        team_id: str,
        member_name: str,
        summary_team: bool = False,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._team_id = team_id
        self._member_name = member_name
        self._summary_team = summary_team
        self._prompt_builder = None

    def init(self, agent) -> None:
        self._prompt_builder = getattr(agent, "system_prompt_builder", None)
        if self._prompt_builder is None:
            return
        mount = f".organization/{self._manager.mount_name}/"
        own_target = "summary/" if self._summary_team else f"teams/{self._team_id}/"
        content = (
            "## Organization shared workspace\n"
            f"The organization workspace is mounted at `{mount}`. "
            "Use the Team workspace for drafts and internal intermediate files. "
            f"Publish cross-Team deliverables under `{mount}{own_target}`; shared inputs may be written under "
            f"`{mount}shared/`. You may read the whole Organization workspace, but must not modify another "
            "Team's published files."
        )
        self._prompt_builder.add_section(
            PromptSection(
                name="organization_workspace",
                content={"cn": content, "en": content},
                priority=82,
            )
        )

    def uninit(self, agent) -> None:
        _ = agent
        if self._prompt_builder is not None:
            self._prompt_builder.remove_section("organization_workspace")
        self._prompt_builder = None

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name
        args = ctx.inputs.tool_args if isinstance(ctx.inputs.tool_args, dict) else {}
        path = args.get("file_path", "")
        if not isinstance(path, str) or not self._manager.references_workspace(path):
            return
        try:
            relative = self._manager.relative_path(path)
        except ValueError as exc:
            self._reject(ctx, str(exc))
            return
        if tool_name not in self.WRITE_TOOLS:
            return
        if not self._manager.can_write(
            relative,
            team_id=self._team_id,
            summary_team=self._summary_team,
        ):
            self._reject(
                ctx,
                f"Team '{self._team_id}' cannot write Organization path '{relative}'",
            )
            return
        if self._manager.config.conflict_strategy is ConflictStrategy.LOCK:
            holder = f"{self._team_id}:{self._member_name}"
            acquired = await self._manager.acquire_lock(relative, holder, holder)
            if not acquired:
                self._reject(ctx, f"Organization file '{relative}' is locked")
                return
            ctx.extra[self._LOCK_KEY] = (relative, holder)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        lock_info = ctx.extra.pop(self._LOCK_KEY, None)
        try:
            if ctx.inputs.tool_name not in self.WRITE_TOOLS or lock_info is None:
                return
            relative, _ = lock_info
            if self._manager.config.version_control:
                await self._manager.auto_commit_for_actor(
                    relative,
                    team_id=self._team_id,
                    member_name=self._member_name,
                )
        finally:
            if lock_info is not None:
                relative, holder = lock_info
                await self._manager.release_lock(relative, holder)

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        """Release a file lock when the underlying write tool fails."""

        lock_info = ctx.extra.pop(self._LOCK_KEY, None)
        if lock_info is not None:
            relative, holder = lock_info
            await self._manager.release_lock(relative, holder)

    @staticmethod
    def _reject(ctx: AgentCallbackContext, reason: str) -> None:
        """Stop an unauthorized file tool before it reaches the filesystem."""
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call is not None else ""
        ctx.extra["workspace_lock_rejected"] = reason
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": reason}
        ctx.inputs.tool_msg = ToolMessage(content=reason, tool_call_id=tool_call_id)


__all__ = ["OrganizationWorkspaceRail"]
