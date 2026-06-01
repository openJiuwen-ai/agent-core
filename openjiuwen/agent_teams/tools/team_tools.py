# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Team Tools Module

This module provides tool wrappers for agent team functionality,
exposing team management, member management, task management,
and messaging capabilities as tools for agents to use.
"""

import json
import re
from abc import ABC
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from pydantic import PrivateAttr

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation

from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import (
    CapabilityOverrides,
    TeamBackend,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import (
    Tool,
    ToolCard,
)
from openjiuwen.harness.tools.base_tool import ToolOutput


class MappedToolOutput(ToolOutput):
    """ToolOutput with custom string representation for LLM consumption.

    The ability_manager converts tool results to LLM messages via str(result).
    This subclass overrides __str__ to return model-optimized text instead of
    Pydantic's default representation.
    """

    _mapped_content: str = PrivateAttr(default="")

    @classmethod
    def from_output(cls, output: ToolOutput, mapped_content: str) -> "MappedToolOutput":
        """Create a MappedToolOutput from an existing ToolOutput."""
        obj = cls(success=output.success, data=output.data, error=output.error)
        obj._mapped_content = mapped_content
        return obj

    def __str__(self) -> str:
        return self._mapped_content


class TeamTool(Tool, ABC):
    """Base class for team tools with model-facing result mapping.

    Subclasses override map_result() to control what the LLM sees.
    Default implementation returns JSON for success, error text for failure.
    """

    def map_result(self, output: ToolOutput) -> str:
        """Map tool output to model-facing text.

        Override in subclasses for custom formatting. The returned string
        becomes the ToolMessage.content that the LLM receives.
        """
        if not output.success:
            return output.error or "Operation failed"
        if output.data is None:
            return "OK"
        return json.dumps(output.data, ensure_ascii=False)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError("TeamTool does not support streaming")


# ========== Tool Permission Sets ==========

# Tools that only the leader can use
LEADER_ONLY_TOOLS: Set[str] = {
    "build_team",  # Create a new team
    "clean_team",  # Clean up a team
    "spawn_member",  # Create a new team member
    "shutdown_member",  # Shutdown a team member
    "approve_plan",  # Approve or reject a member's plan
    "approve_tool",  # Approve or reject a teammate tool call
    "create_task",  # Create tasks (batch / with deps)
    "update_task",  # Update task content / cancel tasks
    "list_members",  # List all members
}

# Tools that only members can use
MEMBER_ONLY_TOOLS: Set[str] = {
    "claim_task",  # Claim or complete a task
    "submit_plan",  # Submit a plan before executing in plan_mode
    # Worktree tools — members work in isolated worktrees
    # "enter_worktree",          # Enter an isolated git worktree
    # "exit_worktree",           # Exit the current worktree session
}

# Tools that both leader and members can use
SHARED_TOOLS: Set[str] = {
    # Query tools
    # "get_team_info",           # Get team information
    # "get_member",              # Get member information
    "view_task",  # View tasks (unified - supports get/list/claimable)
    # Messaging tools
    "send_message",  # Send a message (point-to-point or broadcast)
    "workspace_meta",  # Workspace lock management and version history
}

# All tools available to leader
LEADER_TOOLS: Set[str] = LEADER_ONLY_TOOLS | SHARED_TOOLS

# All tools available to members
MEMBER_TOOLS: Set[str] = MEMBER_ONLY_TOOLS | SHARED_TOOLS

# Tools available to the reserved ``human_agent`` role. The human
# agent acts on its corresponding external user's behalf, so it gets
# read access to tasks, a self-only completion tool, and the shared
# ``send_message`` tool so the user can ask the avatar to relay a
# message to other members ("tell the leader I'm in a meeting").
# The behavioural constraint — that the avatar must **not** speak on
# its own initiative and may only send when the user explicitly
# instructs a relay — is enforced in the HITT system prompt section,
# not in the tool's ``invoke``. The user's own voice still flows
# through ``HumanAgentInbox`` with explicit ``@target`` routing; this
# tool is a complementary path for user-driven outbound speech.
#
# ``claim_task`` is intentionally absent — autonomous claiming is a
# teammate behavior; the user's avatar must wait for explicit leader
# assignment via ``update_task(assignee=...)`` instead.
#
# Workspace lock/version (``workspace_meta``) is attached separately
# by ``TeamToolRail`` whenever a workspace_manager is configured —
# same path leader/teammate use — so this set doesn't list it.
HUMAN_AGENT_TOOLS: Set[str] = {
    "view_task",
    "member_complete_task",
    "send_message",
}


# ``member_name`` is used verbatim as a primary key, a message routing
# token, and a path segment under ``team_home``. Allowing non-ASCII
# (e.g. CJK) or shell-significant characters in any of those positions
# silently breaks routing on some transports and produces unreadable
# directory layouts on disk. Restrict to the DNS-label-style alphabet
# used everywhere else in the ecosystem (k8s pods, docker containers):
# lowercase ASCII letters, digits, and hyphen, with a leading letter so
# the name is never a bare number or starts with a separator. Enforced
# at the only LLM-facing entry point (``spawn_member``).
_MEMBER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


# ========== Team Management ==========


class BuildTeamTool(TeamTool):
    """Create a new team"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.build_team",
                name="build_team",
                description=t("build_team"),
            )
        )
        self.team = team
        self.db = team.db
        self.messager = team.messager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": t("build_team", "display_name")},
                "team_desc": {"type": "string", "description": t("build_team", "team_desc")},
                "leader_display_name": {
                    "type": "string",
                    "description": t("build_team", "leader_display_name"),
                },
                "leader_desc": {"type": "string", "description": t("build_team", "leader_desc")},
                "enable_hitt": {
                    "type": "boolean",
                    "description": t("build_team", "enable_hitt"),
                },
            },
            "required": ["display_name", "team_desc", "leader_display_name", "leader_desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        display_name = inputs.get("display_name")
        leader_display_name = inputs["leader_display_name"]
        # None when LLM omits the field — backend.build_team inherits the
        # spec ceiling. True/False explicitly set the runtime instance flag
        # (subject to the spec ceiling check).
        enable_hitt_arg = inputs.get("enable_hitt")
        await self.team.build_team(
            display_name=display_name,
            desc=inputs.get("team_desc"),
            leader_display_name=leader_display_name,
            leader_desc=inputs["leader_desc"],
            overrides=CapabilityOverrides(enable_hitt=enable_hitt_arg),
        )
        return ToolOutput(
            success=True,
            data={
                "team_name": self.team.team_name,
                "display_name": display_name,
                "leader_member_name": self.team.member_name,
                "leader_display_name": leader_display_name,
                "enable_hitt": self.team.hitt_enabled(),
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to build team"
        d = output.data or {}
        return (
            f"Team created: team_name={d.get('team_name')} "
            f"display_name={d.get('display_name')} "
            f"leader_member_name={d.get('leader_member_name')} "
            f"leader_display_name={d.get('leader_display_name')} "
            f"hitt_enabled={d.get('enable_hitt')}"
        )


class CleanTeamTool(TeamTool):
    """Clean up a team when all members are shutdown"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.clean_team",
                name="clean_team",
                description=t("clean_team"),
            )
        )
        self.team = team
        self.card.input_params = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        try:
            team_name = self.team.team_name
            success = await self.team.clean_team()
            if not success:
                return ToolOutput(
                    success=False,
                    error="Active members remain. Use shutdown_member to close all members first.",
                )
            return ToolOutput(success=True, data={"team_name": team_name})
        except Exception as e:
            team_logger.error(f"clean_team failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to clean team"
        return f"Team cleaned: team_name={output.data['team_name']}"


# ========== Member Management ==========


class SpawnMemberTool(TeamTool):
    """Create a new team member"""

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        model_config_allocator: Optional[Callable[[Optional[str]], Optional["Allocation"]]] = None,
    ):
        super().__init__(
            ToolCard(
                id="team.spawn_member",
                name="spawn_member",
                description=t("spawn_member"),
            )
        )
        self.team = team
        self._allocate_model_config = model_config_allocator
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_member", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_member", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_member", "desc")},
                "role_type": {
                    "type": "string",
                    "enum": ["teammate", "human_agent", "bridge_agent", "external_cli"],
                    "default": "teammate",
                    "description": t("spawn_member", "role_type"),
                },
                "cli_agent": {
                    "type": "string",
                    "description": t("spawn_member", "cli_agent"),
                },
                "prompt": {"type": "string", "description": t("spawn_member", "prompt")},
                "model_name": {
                    "type": "string",
                    "description": t("spawn_member", "model_name"),
                },
                "mailbox_inject_mode": {
                    "type": "string",
                    "enum": ["passthrough", "rephrase"],
                    "default": "passthrough",
                    "description": t("spawn_member", "mailbox_inject_mode"),
                },
                "protocol": {
                    "type": "string",
                    "description": t("spawn_member", "protocol"),
                },
                "adapter_config": {
                    "type": "object",
                    "description": t("spawn_member", "adapter_config"),
                },
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        member_name = inputs.get("member_name")
        display_name = inputs.get("display_name")
        desc = inputs.get("desc", "")
        role_type = (inputs.get("role_type") or "teammate").lower()

        if not member_name or not _MEMBER_NAME_PATTERN.match(member_name):
            return ToolOutput(
                success=False,
                error=(
                    f"Invalid member_name {member_name!r}: must start with a "
                    "lowercase ASCII letter (a-z), followed by lowercase "
                    "letters, digits (0-9) or hyphen (-); no uppercase, "
                    "underscore, whitespace, or non-ASCII characters "
                    "(including CJK) — member_name is reused as a routing "
                    "token and a filesystem path segment"
                ),
            )

        if role_type not in {"teammate", "human_agent", "bridge_agent", "external_cli"}:
            return ToolOutput(
                success=False,
                error=(
                    f"Invalid role_type '{role_type}'; expected 'teammate', "
                    "'human_agent', 'bridge_agent', or 'external_cli'"
                ),
            )

        if role_type == "bridge_agent":
            return await self._spawn_bridge(inputs)

        if role_type == "external_cli":
            return await self._spawn_external_cli(inputs)

        if role_type == "human_agent":
            # Capability gate: fail fast to LLM before touching backend.
            if not self.team.hitt_enabled():
                return ToolOutput(
                    success=False,
                    error=(
                        "Cannot spawn human agent: HITT capability is disabled "
                        "(enable_hitt=False on TeamAgentSpec or build_team). "
                        "Either enable HITT in the team spec or use role_type='teammate'."
                    ),
                )
            if inputs.get("model_name") or inputs.get("prompt"):
                return ToolOutput(
                    success=False,
                    error=(
                        "role_type='human_agent' does not accept 'model_name' or 'prompt'; "
                        "human members use the framework template — remove these fields"
                    ),
                )
            result = await self.team.spawn_human_agent(
                member_name=member_name,
                display_name=display_name,
                desc=desc,
            )
            return ToolOutput(
                success=result.ok,
                data={
                    "member_name": member_name,
                    "display_name": display_name,
                    "role_type": "human_agent",
                },
                error=None if result.ok else result.reason,
            )

        # teammate path (default)
        mode_str = self.team.teammate_mode.value
        mode = MemberMode(mode_str)
        model_name = inputs.get("model_name")
        allocation = self._allocate_model_config(model_name) if self._allocate_model_config else None
        card_id = f"{self.team.team_name}_{member_name}"
        agent_card = AgentCard(id=card_id, name=display_name, description=desc)
        result = await self.team.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=agent_card,
            desc=desc,
            prompt=inputs.get("prompt"),
            mode=mode,
            allocation=allocation,
        )
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "display_name": display_name,
                "role_type": "teammate",
            },
            error=None if result.ok else result.reason,
        )

    async def _spawn_bridge(self, inputs: Dict[str, Any]) -> ToolOutput:
        """Dispatch path for ``role_type='bridge_agent'``.

        Bridge agents are full local teammates paired with a remote
        independent agent reachable through a pure-text protocol.
        Persona is required because it is sent to the remote agent
        via ``adapter.connect`` as the briefing the remote adopts.
        ``model_name`` is optional (falls back to framework default).
        """
        from openjiuwen.agent_teams.schema.team import BridgeMailboxInjectMode

        if not self.team.bridge_enabled():
            return ToolOutput(
                success=False,
                error=(
                    "Cannot spawn bridge agent: Bridge capability is "
                    "disabled (enable_bridge=False on TeamAgentSpec or "
                    "build_team). Either enable Bridge in the team "
                    "spec or use role_type='teammate'."
                ),
            )

        member_name = inputs.get("member_name")
        display_name = inputs.get("display_name")
        # ``desc`` is the per-tool persona surface; ``prompt`` is a
        # plain hint (optional). For bridge agents the persona MUST
        # be non-empty because it doubles as the remote's briefing.
        persona = inputs.get("desc") or inputs.get("prompt") or ""
        if not persona:
            return ToolOutput(
                success=False,
                error=(
                    "role_type='bridge_agent' requires a non-empty "
                    "'desc' (or 'prompt') — it is the persona/briefing "
                    "the remote agent adopts via adapter.connect"
                ),
            )

        mode_raw = (inputs.get("mailbox_inject_mode") or "passthrough").lower()
        try:
            inject_mode = BridgeMailboxInjectMode(mode_raw)
        except ValueError:
            return ToolOutput(
                success=False,
                error=(f"Invalid mailbox_inject_mode '{mode_raw}'; expected 'passthrough' or 'rephrase'"),
            )

        adapter_config = inputs.get("adapter_config") or {}
        if not isinstance(adapter_config, dict):
            return ToolOutput(
                success=False,
                error="adapter_config must be an object/dict",
            )

        result = await self.team.spawn_bridge_agent(
            member_name=member_name,
            display_name=display_name,
            persona=persona,
            desc=inputs.get("desc"),
            model_name=inputs.get("model_name"),
            mailbox_inject_mode=inject_mode,
            protocol=inputs.get("protocol") or "",
            adapter_config=adapter_config,
        )
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "display_name": display_name,
                "role_type": "bridge_agent",
                "mailbox_inject_mode": inject_mode.value,
                "protocol": inputs.get("protocol") or "",
            },
            error=None if result.ok else result.reason,
        )

    async def _spawn_external_cli(self, inputs: Dict[str, Any]) -> ToolOutput:
        """Dispatch path for ``role_type='external_cli'``.

        Spawns a teammate whose brain is a third-party CLI subprocess
        (claudecode / codex / ...) driven by an ``ExternalCliRuntime``. The
        CLI kind is named by ``cli_agent`` and must match a static config
        declared in ``TeamAgentSpec.external_cli_agents`` — all launch
        knowledge (command, cwd, MCP injection, env) lives there, so this
        call carries only the role type and the CLI identifier. ``desc`` is
        the persona stored on the member row.
        """
        member_name = inputs.get("member_name")
        display_name = inputs.get("display_name")
        cli_agent = (inputs.get("cli_agent") or "").strip()
        persona = inputs.get("desc") or inputs.get("prompt") or ""

        if not cli_agent:
            return ToolOutput(
                success=False,
                error=(
                    "role_type='external_cli' requires 'cli_agent' naming a CLI "
                    "kind declared in TeamAgentSpec.external_cli_agents "
                    "(e.g. 'claude' or 'codex')"
                ),
            )
        if not persona:
            return ToolOutput(
                success=False,
                error="role_type='external_cli' requires a non-empty 'desc' (the member persona)",
            )

        result = await self.team.spawn_external_cli_agent(
            member_name=member_name,
            display_name=display_name,
            cli_agent=cli_agent,
            persona=persona,
            desc=inputs.get("desc"),
        )
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "display_name": display_name,
                "role_type": "external_cli",
                "cli_agent": cli_agent,
            },
            error=None if result.ok else result.reason,
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to spawn member"
        d = output.data
        role = d.get("role_type", "teammate")
        cli_agent = d.get("cli_agent")
        suffix = f" cli_agent={cli_agent}" if cli_agent else ""
        return f"Member spawned: member_name={d['member_name']} display_name={d['display_name']} role={role}{suffix}"


class ShutdownMemberTool(TeamTool):
    """Shutdown a team member"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.shutdown_member",
                name="shutdown_member",
                description=t("shutdown_member"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("shutdown_member", "member_name"),
                },
                "force": {"type": "boolean", "description": t("shutdown_member", "force")},
            },
            "required": ["member_name"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        result = await self.team.shutdown_member(
            member_name=member_name,
            force=inputs.get("force", False),
        )
        return ToolOutput(
            success=result.ok,
            data={"member_name": member_name},
            error=None if result.ok else result.reason,
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to shutdown member"
        return f"Member shutdown: member_name={output.data['member_name']}"


class ApprovePlanTool(TeamTool):
    """Approve or reject a member's plan"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.approve_plan",
                name="approve_plan",
                description=t("approve_plan"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": t("approve_plan", "plan_id"),
                },
                "approved": {"type": "boolean", "description": t("approve_plan", "approved")},
                "feedback": {"type": "string", "description": t("approve_plan", "feedback")},
            },
            "required": ["plan_id", "approved"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        approved = inputs.get("approved")
        plan_id = inputs.get("plan_id")
        success = await self.team.approve_plan(
            plan_id=plan_id,
            approved=approved,
            feedback=inputs.get("feedback"),
        )
        return ToolOutput(
            success=success,
            data={
                "plan_id": plan_id,
                "approved": approved,
            },
            error=None if success else "Failed to approve/reject plan",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject plan"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return f"Plan {decision}: plan_id={d['plan_id']} decision={decision}"


class ApproveToolCallTool(TeamTool):
    """Approve or reject one teammate tool call."""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.approve_tool",
                name="approve_tool",
                description=t("approve_tool"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("approve_tool", "member_name"),
                },
                "tool_call_id": {"type": "string", "description": t("approve_tool", "tool_call_id")},
                "approved": {"type": "boolean", "description": t("approve_tool", "approved")},
                "feedback": {"type": "string", "description": t("approve_tool", "feedback")},
                "auto_confirm": {"type": "boolean", "description": t("approve_tool", "auto_confirm")},
            },
            "required": ["member_name", "tool_call_id", "approved"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        member_name = inputs.get("member_name")
        tool_call_id = inputs.get("tool_call_id")
        approved = inputs.get("approved")
        success = await self.team.approve_tool(
            member_name=member_name,
            tool_call_id=tool_call_id,
            approved=approved,
            feedback=inputs.get("feedback"),
            auto_confirm=inputs.get("auto_confirm", False),
        )
        return ToolOutput(
            success=success,
            data={"member_name": member_name, "tool_call_id": tool_call_id, "approved": approved},
            error=None if success else "Failed to approve/reject tool call",
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to approve/reject tool call"
        d = output.data
        decision = "approved" if d["approved"] else "rejected"
        return (
            f"Tool call {decision}: tool_call_id={d['tool_call_id']} member_name={d['member_name']} decision={decision}"
        )


class ListMembersTool(TeamTool):
    """List all team members"""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.list_members",
                name="list_members",
                description=t("list_members"),
            )
        )
        self.team = team
        self.card.input_params = {"type": "object", "properties": {}, "required": []}

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        members = await self.team.list_members()
        return ToolOutput(
            success=True, data={"members": [member.model_dump() for member in members], "count": len(members)}
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list members"
        members = output.data["members"]
        if not members:
            return "No members"
        lines = [
            f"member_name={m['member_name']} display_name={m['display_name']} status={m['status']}" for m in members
        ]
        return "\n".join(lines)


# ========== Task Management ==========


class TaskCreateTool(TeamTool):
    """Create team tasks (Leader only).

    Unified creation: tasks with depended_by auto-route to add_with_priority(),
    plain tasks route to add() / add_batch().
    """

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.create_task",
                name="create_task",
                description=t("create_task"),
            )
        )
        self.task_manager = agent_team.task_manager

        _task_schema: dict = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("create_task", "task.task_id")},
                "title": {"type": "string", "description": t("create_task", "task.title")},
                "content": {"type": "string", "description": t("create_task", "task.content")},
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("create_task", "task.depends_on"),
                },
                "depended_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("create_task", "task.depended_by"),
                },
            },
            "required": ["title", "content"],
        }

        self.card.input_params = {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": _task_schema,
                    "description": t("create_task", "tasks"),
                },
            },
            "required": ["tasks"],
        }

    async def _create_one(self, spec: dict):
        """Create one task via the right add path; returns a TaskCreateResult."""
        if spec.get("depended_by"):
            return await self.task_manager.add_with_priority(
                title=spec["title"],
                content=spec["content"],
                task_id=spec.get("task_id"),
                dependencies=spec.get("depends_on"),
                dependent_task_ids=spec.get("depended_by"),
            )
        return await self.task_manager.add(
            title=spec["title"],
            content=spec["content"],
            task_id=spec.get("task_id"),
            dependencies=spec.get("depends_on"),
        )

    @staticmethod
    def _spec_label(spec: dict) -> str:
        return spec.get("task_id") or spec.get("title") or "<unnamed>"

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        tasks = inputs.get("tasks", [])
        if not tasks:
            return ToolOutput(success=False, error="'tasks' is required")

        if len(tasks) == 1:
            spec = tasks[0]
            if not spec.get("title") or not spec.get("content"):
                return ToolOutput(
                    success=False,
                    error=f"Task {self._spec_label(spec)!r} missing required title/content",
                )
            result = await self._create_one(spec)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            return ToolOutput(success=True, data=result.task.brief())

        # Batch path — call add* one by one so we can capture per-task reasons
        # and return them to the LLM. The previous implementation routed
        # plain specs through add_batch() which silently dropped failures.
        created: list = []
        failures: list[dict] = []
        for spec in tasks:
            if not spec.get("title") or not spec.get("content"):
                failures.append(
                    {
                        "spec": self._spec_label(spec),
                        "reason": "missing required title/content",
                    }
                )
                continue
            result = await self._create_one(spec)
            if result.ok:
                created.append(result.task)
            else:
                failures.append(
                    {
                        "spec": self._spec_label(spec),
                        "reason": result.reason,
                    }
                )

        if not created and failures:
            joined = "; ".join(f"{f['spec']}: {f['reason']}" for f in failures)
            return ToolOutput(
                success=False,
                error=f"All {len(failures)} task creations failed: {joined}",
            )

        return ToolOutput(
            success=True,
            data={
                "tasks": [t.brief() for t in created],
                "count": len(created),
                "skipped": len(failures),
                "failures": failures,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        # Single task
        if "task_id" in d and "title" in d:
            return f"Task created: task_id={d['task_id']} title={d['title']}"
        # Batch
        tasks = d.get("tasks", [])
        lines = [f"task_id={t['task_id']} title={t['title']}" for t in tasks]
        lines.append(f"Created {d['count']}, skipped {d.get('skipped', 0)}")
        for f in d.get("failures", []) or []:
            lines.append(f"  - skipped {f['spec']}: {f['reason']}")
        return "\n".join(lines)


class ViewTaskToolV2(TeamTool):
    """Unified task viewing tool (V2).

    Explicit action enum instead of implicit param-based dispatch.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.view_task",
                name="view_task",
                description=t("view_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "list", "claimable"],
                    "description": t("view_task", "action"),
                },
                "task_id": {"type": "string", "description": t("view_task", "task_id")},
                "status": {
                    "type": "string",
                    "description": t("view_task", "status"),
                },
            },
            "required": [],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        action = inputs.get("action", "list")

        if action == "get":
            task_id = inputs.get("task_id")
            if not task_id:
                return ToolOutput(success=False, error="task_id required for get action")
            detail = await self.task_manager.get_task_detail(task_id=task_id)
            if detail:
                return ToolOutput(success=True, data=detail.model_dump(exclude_none=True))
            return ToolOutput(success=False, error="Task not found")

        if action == "claimable":
            result = await self.task_manager.list_tasks_with_deps(
                status=TaskStatus.PENDING.value,
            )
        else:
            result = await self.task_manager.list_tasks_with_deps(
                status=inputs.get("status"),
            )

        return ToolOutput(success=True, data=result.model_dump())

    def map_result(self, output: ToolOutput) -> str:
        """Map view_task result — tiered output by action.

        Both tiers render the task's last-transition time as ``<absolute
        local time> (<relative diff>)`` so the model can tell how long a
        task has been sitting in its current status.
        """
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        now_ms = get_current_time()
        # Detail view (get action) — mirrors TaskGetTool
        if "content" in d:
            lines = [
                f"Task #{d['task_id']}: {d['title']}",
                f"Status: {d['status']}",
                f"Content: {d['content']}",
            ]
            if d.get("assignee"):
                lines.append(f"Assignee: {d['assignee']}")
            if d.get("updated_at") is not None:
                lines.append(f"Updated: {format_time_context(d['updated_at'], now_ms)}")
            if d.get("blocked_by"):
                lines.append(f"Blocked by: {', '.join(f'#{tid}' for tid in d['blocked_by'])}")
            if d.get("blocks"):
                lines.append(f"Blocks: {', '.join(f'#{tid}' for tid in d['blocks'])}")
            return "\n".join(lines)
        # List view (list/claimable action) — mirrors TaskListTool
        tasks = d.get("tasks", [])
        if not tasks:
            return "No tasks found"
        lines = []
        for task in tasks:
            parts = [f"#{task['task_id']} [{task['status']}] {task['title']}"]
            if task.get("assignee"):
                parts.append(f"({task['assignee']})")
            if task.get("updated_at") is not None:
                parts.append(f"({format_time_context(task['updated_at'], now_ms)})")
            if task.get("blocked_by"):
                parts.append(f"[blocked by {', '.join(f'#{tid}' for tid in task['blocked_by'])}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)


class UpdateTaskTool(TeamTool):
    """Update task content or cancel tasks (Leader only)."""

    def __init__(self, agent_team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.update_task",
                name="update_task",
                description=t("update_task"),
            )
        )
        self.agent_team = agent_team
        self.task_manager = agent_team.task_manager
        self.t = t
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("update_task", "task_id")},
                "status": {
                    "type": "string",
                    "enum": ["cancelled"],
                    "description": t("update_task", "status"),
                },
                "title": {"type": "string", "description": t("update_task", "title")},
                "content": {"type": "string", "description": t("update_task", "content")},
                "assignee": {"type": "string", "description": t("update_task", "assignee")},
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("update_task", "add_blocked_by"),
                },
            },
            "required": ["task_id"],
        }

    def _is_cancellable_assignee(self, assignee: str | None) -> bool:
        """Whether an assignee owns an execution process the team can cancel.

        Human-agent members are first-class team members but run no
        background process — cancel operations must skip all of them,
        otherwise the backend would try to stop something that never
        existed.
        """
        return bool(assignee) and not self.agent_team.is_human_agent(assignee)

    async def _cancel_member_if_claimed(self, task_id: str) -> None:
        """Cancel the assignee if task is currently claimed.

        Skips human-agent members: they own no execution process to cancel.
        """
        task = await self.task_manager.get(task_id)
        if not task or task.status != TaskStatus.CLAIMED.value:
            return
        if self._is_cancellable_assignee(task.assignee):
            await self.agent_team.cancel_member(member_name=task.assignee)

    async def _cancel_claimed_members(self) -> None:
        """Cancel all members who have claimed tasks.

        Skips human-agent members so a batch cancel does not try to
        cancel a member that has no execution process.
        """
        claimed_tasks = await self.task_manager.list_tasks(status=TaskStatus.CLAIMED.value)
        cancelled: set[str] = set()
        for task in claimed_tasks:
            if task.assignee in cancelled or not self._is_cancellable_assignee(task.assignee):
                continue
            await self.agent_team.cancel_member(member_name=task.assignee)
            cancelled.add(task.assignee)

    def _is_human_agent_locked(self, task) -> bool:
        """Whether a task is held by a human-agent member and therefore
        leader-immutable.

        The leader may not cancel or reassign such tasks — only the human
        collaborator can release them (by completing, or by the team
        being cleaned). The leader's only recourse is send_message nudges.
        """
        return self.agent_team.is_human_agent(task.assignee) and task.status == TaskStatus.CLAIMED.value

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        status = inputs.get("status")
        title = inputs.get("title")
        content = inputs.get("content")
        assignee = inputs.get("assignee")
        add_blocked_by = inputs.get("add_blocked_by")

        # cancel_all: task_id="*" + status="cancelled"
        if task_id == "*" and status == "cancelled":
            await self._cancel_claimed_members()
            # Preserve every human-agent-claimed task in a single batch
            # cancel. Passing an empty set is fine — the backend treats
            # None and empty uniformly.
            skip = set(self.agent_team.human_agent_names())
            count = await self.agent_team.cancel_all_tasks(skip_assignees=skip or None)
            return ToolOutput(success=True, data={"cancelled_count": count})

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        # Cancel single task
        if status == "cancelled":
            if self._is_human_agent_locked(task):
                return ToolOutput(
                    success=False,
                    error=self.t(
                        "update_task",
                        "error_human_agent_locked_cancel",
                        task_id=task_id,
                    ),
                )
            await self._cancel_member_if_claimed(task_id)
            success = await self.agent_team.cancel_task(task_id=task_id)
            if not success:
                return ToolOutput(success=False, error="Failed to cancel task")
            return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

        # Collect all field updates in one pass
        updated: list[str] = []

        # Content update (title and/or content)
        if title or content:
            await self._cancel_member_if_claimed(task_id)
            result = await self.task_manager.update_task(task_id, title=title, content=content)
            if not result.ok:
                return ToolOutput(success=False, error=result.reason)
            if title:
                updated.append("title")
            if content:
                updated.append("content")

        # Assign task to member. When the task is already claimed by a
        # different member, treat this as a leader-driven reassignment:
        # cancel the previous claimer's execution, reset the task back to
        # PENDING, then assign to the new member. Same-member is idempotent.
        if assignee:
            if task.assignee and task.assignee != assignee:
                if self._is_human_agent_locked(task):
                    return ToolOutput(
                        success=False,
                        error=self.t(
                            "update_task",
                            "error_human_agent_locked_reassign",
                            task_id=task_id,
                            new_assignee=assignee,
                        ),
                    )
                await self.agent_team.cancel_member(member_name=task.assignee)
                reset_result = await self.task_manager.reset(task_id)
                if not reset_result.ok:
                    return ToolOutput(
                        success=False,
                        error=(
                            f"Failed to reset task before reassigning from "
                            f"{task.assignee} to {assignee}: {reset_result.reason}"
                        ),
                    )
            assign_result = await self.task_manager.assign(task_id, assignee)
            if not assign_result.ok:
                return ToolOutput(success=False, error=assign_result.reason)
            updated.append("assignee")

        # Add dependencies (blocked_by edges)
        if add_blocked_by:
            deps_result = await self.task_manager.add_dependencies(task_id, add_blocked_by)
            if not deps_result.ok:
                return ToolOutput(success=False, error=deps_result.reason)
            updated.append("blocked_by")

        if not updated:
            return ToolOutput(
                success=False, error="No update specified — provide status, title, content, assignee, or add_blocked_by"
            )

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "updated",
                "updated_fields": updated,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Operation failed"
        d = output.data
        if "cancelled_count" in d:
            return f"Cancelled {d['cancelled_count']} tasks"
        return f"Task #{d['task_id']} {d['status']}"


class SubmitPlanTool(TeamTool):
    """Submit an execution plan for a plan-mode task."""

    def __init__(
        self,
        task_manager: TeamTaskManager,
        t: Translator,
        *,
        name: str = "submit_plan",
        tool_id: str = "team.submit_plan",
    ):
        super().__init__(
            ToolCard(
                id=tool_id,
                name=name,
                description=t("submit_plan"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("submit_plan", "task_id")},
                "plan_id": {"type": "string", "description": t("submit_plan", "plan_id")},
                "plan_path": {"type": "string", "description": t("submit_plan", "plan_path")},
            },
            "required": ["task_id", "plan_path"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        result = await self.task_manager.submit_plan(
            task_id=inputs.get("task_id"),
            plan_id=inputs.get("plan_id"),
            plan_path=inputs.get("plan_path") or "",
        )
        return ToolOutput(
            success=bool(result.get("success")),
            data=result,
            error=None if result.get("success") else result.get("message", "Failed to submit member plan"),
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to submit member plan"
        d = output.data
        return (
            f"Member plan submitted: task_id={d.get('task_id')} plan_id={d.get('plan_id')} "
            f"status={d.get('status')} "
            f"member_plan_md={d.get('member_plan_md')}"
        )


class ClaimTaskTool(TeamTool):
    """Claim or complete a task (Teammate only)."""

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.claim_task",
                name="claim_task",
                description=t("claim_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("claim_task", "task_id")},
                "status": {
                    "type": "string",
                    "enum": ["claimed", "completed"],
                    "description": t("claim_task", "status"),
                },
            },
            "required": ["task_id", "status"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        task_id = inputs.get("task_id")
        status = inputs.get("status")

        task = await self.task_manager.get(task_id)
        if not task:
            return ToolOutput(success=False, error="Task not found")

        if status == "claimed":
            claim_result = await self.task_manager.claim(task_id=task_id)
            if not claim_result.ok:
                return ToolOutput(success=False, error=claim_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.CLAIMED.value}

        elif status == "completed":
            complete_result = await self.task_manager.complete(task_id=task_id)
            if not complete_result.ok:
                return ToolOutput(success=False, error=complete_result.reason)
            status_change = {"from": task.status, "to": TaskStatus.COMPLETED.value}

        else:
            return ToolOutput(success=False, error=f"Invalid status: {status}")

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "updated_fields": ["status"],
                "status_change": status_change,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        """Map claim_task result with behavior guidance on completion."""
        if not output.success:
            return output.error or "Task not found"
        d = output.data
        sc = d["status_change"]
        result = f"Task #{d['task_id']} {sc['from']} → {sc['to']}"
        if sc["to"] == TaskStatus.COMPLETED.value:
            result += "\n\nTask completed. Call view_task now to find your next available task."
        return result


class MemberCompleteTaskTool(TeamTool):
    """Complete a task whose ``assignee`` is the calling member.

    Self-only by design: the tool refuses any task whose ``assignee``
    differs from the caller's ``member_name``. Distinct from
    ``ClaimTaskTool`` (which couples claim and complete and is
    teammate-only) and from leader's ``UpdateTaskTool`` (which manages
    the team-wide task graph). Wired into ``HUMAN_AGENT_TOOLS`` so the
    user's avatar can mark its leader-assigned tasks as done without
    inheriting any of leader's coordination authority.
    """

    def __init__(self, task_manager: TeamTaskManager, t: Translator):
        super().__init__(
            ToolCard(
                id="team.member_complete_task",
                name="member_complete_task",
                description=t("member_complete_task"),
            )
        )
        self.task_manager = task_manager
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": t("member_complete_task", "task_id"),
                },
                "note": {
                    "type": "string",
                    "description": t("member_complete_task", "note"),
                },
            },
            "required": ["task_id"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")

        try:
            task = await self.task_manager.get(task_id)
        except Exception as e:
            team_logger.error("member_complete_task: get(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not task:
            return ToolOutput(success=False, error=f"Task '{task_id}' not found")

        caller = self.task_manager.member_name
        if task.assignee != caller:
            return ToolOutput(
                success=False,
                error=(
                    f"Task '{task_id}' is assigned to "
                    f"'{task.assignee or '<unassigned>'}', not '{caller}'; "
                    "you can only complete tasks assigned to yourself"
                ),
            )

        try:
            result = await self.task_manager.complete(task_id=task_id)
        except Exception as e:
            team_logger.error("member_complete_task: complete(%s) failed: %s", task_id, e)
            return ToolOutput(success=False, error=f"Internal error: {e}")
        if not result.ok:
            return ToolOutput(success=False, error=result.reason)

        note = (inputs.get("note") or "").strip() or None
        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "completed",
                "note": note,
            },
        )

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to complete task"
        d = output.data
        line = f"Task #{d['task_id']} completed"
        if d.get("note"):
            line += f" (note: {d['note']})"
        return line


# ========== Messaging ==========


class SendMessageTool(TeamTool):
    """Send a message to team members (point-to-point or broadcast)."""

    def __init__(
        self,
        message_manager: TeamMessageManager,
        t: Translator,
        team: TeamBackend | None = None,
        on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__(
            ToolCard(
                id="team.send_message",
                name="send_message",
                description=t("send_message"),
            )
        )
        self.message_manager = message_manager
        self._team = team
        self._on_teammate_created = on_teammate_created
        self.card.input_params = {
            "type": "object",
            "properties": {
                "to": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ],
                    "description": t("send_message", "to"),
                },
                "content": {"type": "string", "description": t("send_message", "content")},
                "summary": {"type": "string", "description": t("send_message", "summary")},
            },
            "required": ["to", "content"],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        to_raw = inputs.get("to")
        content = inputs.get("content", "").strip()
        summary = inputs.get("summary", "").strip()

        if not content:
            return ToolOutput(success=False, error="'content' is required")

        try:
            return await self._dispatch(to_raw, content, summary)
        except Exception as e:
            team_logger.error(f"send_message failed: {e}")
            return ToolOutput(success=False, error=f"Internal error: {e}")

    async def _dispatch(self, to_raw: Any, content: str, summary: str) -> ToolOutput:
        """Route the request based on the runtime type of ``to``."""
        if isinstance(to_raw, list):
            return await self._multicast(to_raw, content, summary)
        if isinstance(to_raw, str):
            to = to_raw.strip()
            if not to:
                return ToolOutput(success=False, error="'to' is required")
            if to == "*":
                return await self._broadcast(content, summary)
            return await self._send(to, content, summary)
        return ToolOutput(
            success=False,
            error="'to' must be a string or an array of strings",
        )

    async def _broadcast(self, content: str, summary: str) -> ToolOutput:
        await self._auto_start_members()
        msg_id = await self.message_manager.broadcast_message(content=content)
        if not msg_id:
            return ToolOutput(success=False, error="Failed to broadcast message")
        return ToolOutput(
            success=True,
            data={
                "type": "broadcast",
                "from": self.message_manager.member_name,
                "summary": summary or None,
            },
        )

    async def _send(self, to: str, content: str, summary: str) -> ToolOutput:
        # "user" is the pseudo-member representing the human caller; skip
        # roster validation so teammates can reply through the same tool.
        if self._team and to != "user":
            member = await self._team.get_member(to)
            if not member:
                return ToolOutput(success=False, error=f"Member '{to}' not found")
        await self._auto_start_members()
        msg_id = await self.message_manager.send_message(content=content, to_member_name=to)
        if not msg_id:
            return ToolOutput(success=False, error=f"Failed to send message to '{to}'")
        return ToolOutput(
            success=True,
            data={
                "type": "message",
                "from": self.message_manager.member_name,
                "to": to,
                "summary": summary or None,
            },
        )

    async def _multicast(
        self,
        targets: list[str],
        content: str,
        summary: str,
    ) -> ToolOutput:
        """Send identical content to multiple members as independent point-to-point messages.

        Strict success: only returns success=True when every target receives the message.
        On any failure the data still carries delivered/failed lists so callers can
        avoid resending to members who already got the message.
        """
        # Strip + drop blanks while preserving order, then de-duplicate.
        stripped = [item.strip() if isinstance(item, str) else "" for item in targets]
        cleaned = [item for item in stripped if item]
        deduped = list(dict.fromkeys(cleaned))

        if not deduped:
            return ToolOutput(
                success=False,
                error="'to' list must contain at least one member name",
            )
        if "*" in deduped:
            return ToolOutput(
                success=False,
                error="Cannot mix broadcast '*' with member names; use to='*' for broadcast",
            )
        if "user" in deduped:
            return ToolOutput(
                success=False,
                error="'user' cannot be combined in multicast; send to user separately",
            )

        # A multicast covering every other team member is just a more
        # expensive broadcast — reject it and force the caller onto the
        # broadcast path. list_members() already excludes the caller, so
        # an exact set match means the targets are the whole roster.
        if self._team:
            roster = {member.member_name for member in await self._team.list_members()}
            if roster and set(deduped) == roster:
                return ToolOutput(
                    success=False,
                    error=(
                        "Multicast targets cover every other team member; "
                        "use to='*' to broadcast instead — same delivery, lower cost."
                    ),
                )

        await self._auto_start_members()

        delivered: list[str] = []
        failed: list[dict[str, str]] = []
        for name in deduped:
            if self._team:
                member = await self._team.get_member(name)
                if not member:
                    failed.append({"to": name, "reason": f"Member '{name}' not found"})
                    continue
            msg_id = await self.message_manager.send_message(
                content=content,
                to_member_name=name,
            )
            if not msg_id:
                failed.append({"to": name, "reason": f"Failed to send message to '{name}'"})
                continue
            delivered.append(name)

        total = len(deduped)
        ok = not failed
        return ToolOutput(
            success=ok,
            error=(None if ok else f"Multicast partially failed: {len(failed)}/{total} target(s) failed"),
            data={
                "type": "multicast",
                "from": self.message_manager.member_name,
                "delivered": delivered,
                "failed": failed,
                "summary": summary or None,
            },
        )

    async def _auto_start_members(self) -> None:
        """Auto-start unstarted members if leader with startup callback."""
        if self._team and self._on_teammate_created and self._team.is_leader:
            started = await self._team.startup(on_created=self._on_teammate_created)
            if started:
                team_logger.info(f"Auto-started members: {started}")

    def map_result(self, output: ToolOutput) -> str:
        d = output.data
        if not output.success:
            base = output.error or "Failed to send message"
            if isinstance(d, dict) and d.get("type") == "multicast":
                return self._format_multicast_text(base, d)
            return base
        if d["type"] == "broadcast":
            return f"Broadcast sent from {d['from']}"
        if d["type"] == "multicast":
            return self._format_multicast_text(None, d)
        return f"Message sent from {d['from']} to {d['to']}"

    @staticmethod
    def _format_multicast_text(error: str | None, d: Dict[str, Any]) -> str:
        """Render multicast outcome including delivered/failed lists when present."""
        delivered: list[str] = d.get("delivered", []) or []
        failed: list[dict[str, str]] = d.get("failed", []) or []
        sender = d.get("from", "")
        parts: list[str] = []
        if error:
            parts.append(error)
        else:
            head = f"Multicast sent from {sender}"
            if delivered:
                head += f" to: {', '.join(delivered)}"
            head += f" ({len(delivered)} delivered)"
            parts.append(head)
        if error and delivered:
            parts.append(f"delivered: {', '.join(delivered)}")
        if failed:
            failed_text = "; ".join(f"{item['to']} — {item['reason']}" for item in failed)
            parts.append(f"failed: {failed_text}")
        return "; ".join(parts)


# ========== Tool Factory ==========


def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    teammate_mode: str = "build_mode",
    lifecycle: str = "temporary",
    on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
    model_config_allocator: Optional[Callable[[Optional[str]], Optional["Allocation"]]] = None,
    exclude_tools: Optional[Set[str]] = None,
    lang: str = "cn",
) -> List[Tool]:
    """Create role-appropriate tool instances filtered by permission sets.

    Args:
        role: "leader" or "teammate".
        agent_team: AgentTeam instance providing task/message/db/messager.
        teammate_mode: Execution mode for teammates — "build_mode" or
            "plan_mode". Leader's approval tools (approve_plan / approve_tool)
            are only wired when teammate_mode == "plan_mode", since that's the
            only mode where teammates submit plans and tool calls can be held
            for leader sign-off.
        lifecycle: Team lifecycle — "temporary" or "persistent". The
            ``clean_team`` tool is only wired for temporary teams; persistent
            teams are torn down through operator-level SDK facades
            (``delete_agent_team`` etc.), so exposing a leader-callable
            tear-down tool inside a round would race the pool invariants.
        on_teammate_created: Callback invoked when a teammate is created.
        model_config_allocator: Callback that returns the next
            ``Allocation`` for teammate allocation. Receives an
            optional ``model_name`` hint forwarded from the spawn site;
            ``RoundRobinModelAllocator`` ignores the hint while
            ``ByModelNameAllocator`` requires it.
        exclude_tools: Tool names to exclude from the allowed set.
        lang: Locale code ("cn" or "en") for tool descriptions.
    """
    from openjiuwen.agent_teams.tools.locales import make_translator

    t = make_translator(lang)
    task_mgr = agent_team.task_manager
    msg_mgr = agent_team.message_manager

    all_tools = {
        # Team management
        "build_team": BuildTeamTool(agent_team, t),
        "clean_team": CleanTeamTool(agent_team, t),
        # Member management
        "spawn_member": SpawnMemberTool(agent_team, t, model_config_allocator=model_config_allocator),
        "shutdown_member": ShutdownMemberTool(agent_team, t),
        "approve_plan": ApprovePlanTool(agent_team, t),
        "approve_tool": ApproveToolCallTool(agent_team, t),
        "list_members": ListMembersTool(agent_team, t),
        # Task management
        "create_task": TaskCreateTool(agent_team, t),
        "update_task": UpdateTaskTool(agent_team, t),
        "view_task": ViewTaskToolV2(task_mgr, t),
        "claim_task": ClaimTaskTool(task_mgr, t),
        "submit_plan": SubmitPlanTool(task_mgr, t),
        "member_complete_task": MemberCompleteTaskTool(task_mgr, t),
        # Messaging
        "send_message": SendMessageTool(
            msg_mgr,
            t,
            team=agent_team,
            on_teammate_created=on_teammate_created,
        ),
    }

    if role == "human_agent":
        allowed = HUMAN_AGENT_TOOLS
    elif role == "leader":
        allowed = LEADER_TOOLS
    else:
        allowed = MEMBER_TOOLS
    # Plan tools only make sense in plan_mode.
    if teammate_mode != "plan_mode":
        allowed = allowed - {
            "approve_plan",
            "approve_tool",
            "submit_plan",
        }
    # clean_team is a temporary-team primitive only. Persistent teams are
    # torn down by the operator through SDK facades (delete_agent_team etc.);
    # letting the leader LLM call clean_team mid-round would race the runtime
    # pool invariants, so the tool is simply not wired in that lifecycle.
    if lifecycle == "persistent":
        allowed = allowed - {"clean_team"}
    if exclude_tools:
        allowed = allowed - exclude_tools
    tools = [tool for name, tool in all_tools.items() if name in allowed]

    for tool in tools:
        _wrap_invoke_with_logging(tool)

    return tools


def _wrap_invoke_with_logging(tool: Tool) -> None:
    """Wrap a tool's invoke method with debug logging and result mapping.

    For TeamTool instances, the wrapper also calls map_result() to produce
    a MappedToolOutput whose __str__ returns model-optimized text.
    """
    original_invoke = tool.invoke
    tool_name = tool.card.name
    is_team_tool = isinstance(tool, TeamTool)

    @wraps(original_invoke)
    async def logged_invoke(inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        team_logger.debug(f"[{tool_name}] invoke start, inputs={inputs}")
        result = await original_invoke(inputs, **kwargs)
        team_logger.debug(f"[{tool_name}] invoke end, output={result}")
        if is_team_tool:
            mapped = tool.map_result(result)  # type: ignore[union-attr]
            return MappedToolOutput.from_output(result, mapped)
        return result

    tool.invoke = logged_invoke
