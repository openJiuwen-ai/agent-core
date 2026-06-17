# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member management tools: spawn, shutdown, approve, and list."""

from abc import ABC
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_permissions import _MEMBER_NAME_PATTERN
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.schema.team import MemberOpResult


# ========== Member Management ==========


class _SpawnToolBase(TeamTool, ABC):
    """Shared scaffolding for the role-specific spawn tools.

    Each concrete subclass owns exactly one ``role_type``: it declares its
    own flat ``input_params`` schema and implements a single straight-line
    ``invoke`` — no role branching anywhere. The cross-cutting concerns every
    spawn tool shares live here: ``member_name`` validation, the persona
    fallback, ToolOutput construction, and model-facing result mapping.
    """

    def __init__(self, team: TeamBackend, t: Translator, tool_name: str):
        super().__init__(
            ToolCard(
                id=f"team.{tool_name}",
                name=tool_name,
                description=t(tool_name),
            )
        )
        self.team = team

    @staticmethod
    def _validate_member_name(member_name: str | None) -> str | None:
        """Validate ``member_name`` at the tool boundary.

        Returns:
            An error message when the name is missing or malformed,
            otherwise ``None``.
        """
        if member_name and _MEMBER_NAME_PATTERN.match(member_name):
            return None
        return (
            f"Invalid member_name {member_name!r}: must start with a "
            "lowercase ASCII letter (a-z), followed by lowercase letters, "
            "digits (0-9) or hyphen (-); no uppercase, underscore, "
            "whitespace, or non-ASCII characters (including CJK) — "
            "member_name is reused as a routing token and a filesystem "
            "path segment"
        )

    @staticmethod
    def _resolve_persona(inputs: dict[str, Any]) -> str:
        """Resolve the persona surface: ``desc`` first, then ``prompt``."""
        return inputs.get("desc") or inputs.get("prompt") or ""

    @staticmethod
    def _fail(reason: str) -> ToolOutput:
        """Build a failed ToolOutput carrying a diagnostic reason."""
        return ToolOutput(success=False, error=reason)

    @staticmethod
    def _from_result(
        result: "MemberOpResult",
        *,
        member_name: str,
        display_name: str,
        role_type: str,
        **extra: Any,
    ) -> ToolOutput:
        """Wrap a backend ``MemberOpResult`` into a ToolOutput.

        Propagates ``result.reason`` into ``error`` on failure so the LLM
        can diagnose what the backend rejected.
        """
        return ToolOutput(
            success=result.ok,
            data={
                "member_name": member_name,
                "display_name": display_name,
                "role_type": role_type,
                **extra,
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
        return (
            f"Member spawned: member_name={d['member_name']} "
            f"display_name={d['display_name']} role={role}{suffix}"
        )


class SpawnTeammateTool(_SpawnToolBase):
    """Spawn an ordinary LLM teammate (``role_type='teammate'``)."""

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        model_config_allocator: Callable[[str | None], "Allocation | None"] | None = None,
    ):
        super().__init__(team, t, "spawn_teammate")
        self._allocate_model_config = model_config_allocator
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_teammate", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_teammate", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_teammate", "desc")},
                "prompt": {"type": "string", "description": t("spawn_teammate", "prompt")},
                "model_name": {
                    "type": "string",
                    "description": t("spawn_teammate", "model_name"),
                },
                "isolation": {
                    "type": "string",
                    "enum": ["worktree"],
                    "description": (
                        "Optional isolation mode. Use 'worktree' to start the "
                        "teammate in an isolated git worktree."
                    ),
                },
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.agent_teams.schema.status import MemberMode
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        desc = inputs.get("desc", "")
        allocation = (
            self._allocate_model_config(inputs.get("model_name")) if self._allocate_model_config else None
        )
        agent_card = AgentCard(
            id=f"{self.team.team_name}_{member_name}",
            name=display_name,
            description=desc,
        )
        result = await self.team.spawn_member(
            member_name=member_name,
            display_name=display_name,
            agent_card=agent_card,
            desc=desc,
            prompt=inputs.get("prompt"),
            mode=MemberMode(self.team.teammate_mode.value),
            allocation=allocation,
            isolation=inputs.get("isolation"),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="teammate",
            isolation=inputs.get("isolation"),
        )


class SpawnHumanAgentTool(_SpawnToolBase):
    """Spawn a human member driven by the real user (``role_type='human_agent'``).

    The schema deliberately omits ``model_name`` / ``prompt`` — human members
    run on the framework template, so there is no field to reject at runtime.
    The HITT capability check below is a defensive backstop; the tool is not
    even wired when HITT is disabled (see ``create_team_tools``).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_human_agent")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_human_agent", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_human_agent", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_human_agent", "desc")},
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        if not self.team.hitt_enabled():
            return self._fail(
                "Cannot spawn human agent: HITT capability is disabled "
                "(enable_hitt=False on TeamAgentSpec or build_team). "
                "Enable HITT in the team spec or use spawn_teammate instead."
            )

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        result = await self.team.spawn_human_agent(
            member_name=member_name,
            display_name=display_name,
            desc=inputs.get("desc", ""),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="human_agent",
        )


class SpawnBridgeAgentTool(_SpawnToolBase):
    """Spawn a bridge agent to a remote independent agent (``role_type='bridge_agent'``).

    A bridge agent is a full local teammate paired with a remote agent reached
    over a pure-text protocol. ``desc`` is required: it doubles as the local
    persona and the briefing the remote adopts via ``adapter.connect``. The
    Bridge capability check is a defensive backstop; the tool is not wired when
    Bridge is disabled (see ``create_team_tools``).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_bridge_agent")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_bridge_agent", "desc")},
                "mailbox_inject_mode": {
                    "type": "string",
                    "enum": ["passthrough", "rephrase"],
                    "default": "passthrough",
                    "description": t("spawn_bridge_agent", "mailbox_inject_mode"),
                },
                "protocol": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "protocol"),
                },
                "adapter_config": {
                    "type": "object",
                    "description": t("spawn_bridge_agent", "adapter_config"),
                },
                "model_name": {
                    "type": "string",
                    "description": t("spawn_bridge_agent", "model_name"),
                },
            },
            "required": ["member_name", "display_name", "desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        from openjiuwen.agent_teams.schema.team import BridgeMailboxInjectMode

        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        if not self.team.bridge_enabled():
            return self._fail(
                "Cannot spawn bridge agent: Bridge capability is disabled "
                "(enable_bridge=False on TeamAgentSpec or build_team). "
                "Enable Bridge in the team spec or use spawn_teammate instead."
            )

        persona = self._resolve_persona(inputs)
        if not persona:
            return self._fail(
                "spawn_bridge_agent requires a non-empty 'desc' — it is the "
                "persona/briefing the remote agent adopts via adapter.connect"
            )

        mode_raw = (inputs.get("mailbox_inject_mode") or "passthrough").lower()
        try:
            inject_mode = BridgeMailboxInjectMode(mode_raw)
        except ValueError:
            return self._fail(
                f"Invalid mailbox_inject_mode '{mode_raw}'; expected 'passthrough' or 'rephrase'"
            )

        adapter_config = inputs.get("adapter_config") or {}
        if not isinstance(adapter_config, dict):
            return self._fail("adapter_config must be an object/dict")

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        protocol = inputs.get("protocol") or ""
        result = await self.team.spawn_bridge_agent(
            member_name=member_name,
            display_name=display_name,
            persona=persona,
            desc=inputs.get("desc"),
            model_name=inputs.get("model_name"),
            mailbox_inject_mode=inject_mode,
            protocol=protocol,
            adapter_config=adapter_config,
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="bridge_agent",
            mailbox_inject_mode=inject_mode.value,
            protocol=protocol,
        )


class SpawnExternalCliTool(_SpawnToolBase):
    """Spawn a third-party CLI agent as a teammate (``role_type='external_cli'``).

    The teammate's brain is a CLI subprocess (claudecode / codex / ...) driven
    by an ``ExternalCliRuntime``. ``cli_agent`` names a CLI kind pre-declared in
    ``TeamAgentSpec.external_cli_agents`` — all launch knowledge lives there, so
    this call carries only the identifier. ``desc`` is the persona stored on the
    member row. The tool is not wired when no CLI kinds are declared (see
    ``create_team_tools``).
    """

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(team, t, "spawn_external_cli")
        self.card.input_params = {
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": t("spawn_external_cli", "member_name"),
                },
                "display_name": {
                    "type": "string",
                    "description": t("spawn_external_cli", "display_name"),
                },
                "desc": {"type": "string", "description": t("spawn_external_cli", "desc")},
                "cli_agent": {
                    "type": "string",
                    "description": t("spawn_external_cli", "cli_agent"),
                },
            },
            "required": ["member_name", "display_name", "desc", "cli_agent"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        err = self._validate_member_name(inputs.get("member_name"))
        if err:
            return self._fail(err)

        cli_agent = (inputs.get("cli_agent") or "").strip()
        if not cli_agent:
            return self._fail(
                "spawn_external_cli requires 'cli_agent' naming a CLI kind "
                "declared in TeamAgentSpec.external_cli_agents (e.g. 'claude' or 'codex')"
            )

        persona = self._resolve_persona(inputs)
        if not persona:
            return self._fail("spawn_external_cli requires a non-empty 'desc' (the member persona)")

        member_name = inputs["member_name"]
        display_name = inputs.get("display_name")
        result = await self.team.spawn_external_cli_agent(
            member_name=member_name,
            display_name=display_name,
            cli_agent=cli_agent,
            persona=persona,
            desc=inputs.get("desc"),
        )
        return self._from_result(
            result,
            member_name=member_name,
            display_name=display_name,
            role_type="external_cli",
            cli_agent=cli_agent,
        )


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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
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
