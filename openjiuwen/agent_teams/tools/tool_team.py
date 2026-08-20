# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team lifecycle tools: build_team and clean_team."""

from typing import Any

from openjiuwen.agent_teams.prompts import build_leader_policy_disclosure
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import CapabilityOverrides, TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


# ========== Team Management ==========


class BuildTeamTool(TeamTool):
    """Create a new team, and disclose the leader's collaboration policy.

    The leader's system prompt carries no collaboration policy (F_76): it holds
    only the bootstrap section that routes to this tool. Everything else — role
    policy, workflow, dispatch conventions, lifecycle wrap-up, the HITT
    contract, the inbound-tag notice — is rendered into this tool's result, so
    the leader reads exactly the variant its own ``build_team`` call selected
    and never the conventions of a mode its team does not run.

    The assembly parameters that decide those variants are static team
    configuration and arrive at construction time; the two runtime flags
    (``enable_hitt`` / dispatch-relevant capability state) are read back off the
    backend *after* ``build_team`` resolves them, so the disclosed text matches
    what the team actually got rather than what the spec allowed.

    The verify gate is a dispatch-gated capability, exactly like
    ``update_task``'s ``reviewer``: under autonomous dispatch the
    ``enable_task_verification`` property is absent from the schema *and* the
    section documenting it is dropped from the description, both off the one
    signal. Only a scheduled-dispatch leader owns a ``TeamScheduler``, and that
    scheduler is the only thing that summons reviewers — so under autonomous
    dispatch there is no gate for the flag to switch, on or off.
    """

    #: The verify-gate property and the description slot documenting it. Schema
    #: and prose are gated together — the model must never read about an
    #: argument it has no way to pass.
    _VERIFY_PARAM = "enable_task_verification"
    _VERIFY_SLOT = "build_team_verify_gate"

    def __init__(
        self,
        team: TeamBackend,
        t: Translator,
        *,
        language: str = "cn",
        lifecycle: str = "temporary",
        teammate_mode: str = "build_mode",
        team_mode: str = "default",
        dispatch_mode: str = "autonomous",
    ):
        verify_gate_enabled = dispatch_mode == "scheduled"
        super().__init__(
            ToolCard(
                id="team.build_team",
                name="build_team",
                description=t(
                    "build_team",
                    omit=None if verify_gate_enabled else frozenset({self._VERIFY_SLOT}),
                ),
            )
        )
        self.team = team
        self.db = team.db
        self.messager = team.messager
        self._language = language
        self._lifecycle = lifecycle
        self._teammate_mode = teammate_mode
        self._team_mode = team_mode
        self._dispatch_mode = dispatch_mode
        self._verify_gate_enabled = verify_gate_enabled
        properties: dict[str, Any] = {
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
        }
        if verify_gate_enabled:
            properties[self._VERIFY_PARAM] = {
                "type": "boolean",
                "description": t("build_team", self._VERIFY_PARAM),
            }
        self.card.input_params = {
            "type": "object",
            "properties": properties,
            "required": ["display_name", "team_desc", "leader_display_name", "leader_desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        # A cold recovery continues the same session, so the leader's history
        # came back carrying the original build_team result -- and the policy
        # with it, which compaction never drops. Calling this again would buy
        # nothing and cost a round, so it is refused outright rather than
        # served idempotently (F_76).
        if await self.team.rejects_rebuild():
            return ToolOutput(
                success=False,
                error=(
                    "This team is already yours and this conversation already holds its "
                    "collaboration policy — do not call build_team again. Read back through "
                    "your own history for the policy, and use list_members and view_task to "
                    "see where the work stands."
                ),
            )

        display_name = inputs.get("display_name")
        leader_display_name = inputs["leader_display_name"]
        # Second-layer enforcement of the verify gate, for arguments coming from
        # an MCP client, which calls ``invoke`` directly without validating
        # against ``input_params``. Rejected loudly rather than stripped: a
        # silently dropped flag would let the leader build its whole task plan
        # around a gate that was never going to exist.
        if not self._verify_gate_enabled and inputs.get(self._VERIFY_PARAM) is not None:
            return ToolOutput(
                success=False,
                error=(
                    f"Cannot use {self._VERIFY_PARAM}: the verify gate does not exist under "
                    "autonomous dispatch — there is no scheduling runtime to summon reviewers, "
                    "so a task pushed into 'in_review' would stall there forever. Write the "
                    "acceptance criteria into the task content and review the results yourself, "
                    "or run the team in scheduled dispatch mode."
                ),
            )

        # None when LLM omits a field — backend.build_team inherits the
        # spec ceiling. Explicit values set the runtime instance flag
        # (subject to the spec ceiling check).
        enable_hitt_arg = inputs.get("enable_hitt")
        verification_arg = inputs.get(self._VERIFY_PARAM) if self._verify_gate_enabled else None
        await self.team.build_team(
            display_name=display_name,
            desc=inputs.get("team_desc"),
            leader_display_name=leader_display_name,
            leader_desc=inputs["leader_desc"],
            overrides=CapabilityOverrides(
                enable_hitt=enable_hitt_arg,
                enable_task_verification=verification_arg,
            ),
        )
        data: dict[str, Any] = {
            "team_name": self.team.team_name,
            "display_name": display_name,
            "leader_member_name": self.team.member_name,
            "leader_display_name": leader_display_name,
            "enable_hitt": self.team.hitt_enabled(),
            "taken_over": self.team.team_taken_over(),
        }
        # Reported only where the gate exists, and read back off the backend
        # rather than echoed: the spec ceiling may have narrowed what the leader
        # asked for, and that is precisely the value it has to plan against.
        if self._verify_gate_enabled:
            data[self._VERIFY_PARAM] = self.team.task_verification_enabled()
        return ToolOutput(success=True, data=data)

    def map_result(self, output: ToolOutput) -> str:
        """Render the outcome, then disclose the collaboration policy.

        The policy is appended only on success — a failed ``build_team`` leaves
        the leader on the bootstrap path, where re-reading the routing guide is
        what it needs, not a rulebook for a team that does not exist.

        The lead line distinguishes creating a team from taking over one that
        already existed. A leader that inherited a running team must not go on
        to spawn the members already on its roster, and the policy that follows
        is identical either way — so the difference has to be said here or the
        leader cannot see it at all.
        """
        if not output.success:
            return output.error or "Failed to build team"
        d = output.data or {}
        lead = "Existing team taken over" if d.get("taken_over") else "Team created"
        facts = (
            f"{lead}: team_name={d.get('team_name')} "
            f"display_name={d.get('display_name')} "
            f"leader_member_name={d.get('leader_member_name')} "
            f"leader_display_name={d.get('leader_display_name')} "
            f"hitt_enabled={d.get('enable_hitt')}"
        )
        if d.get("taken_over"):
            facts += (
                "\nThis team already existed: its members are on the roster and were restarted "
                "for you. Call list_members and view_task to see where the work stands before "
                "planning anything — do not re-spawn members that are already there."
            )
        if self._VERIFY_PARAM in d:
            facts += f" task_verification={d[self._VERIFY_PARAM]}"
        policy = build_leader_policy_disclosure(
            lifecycle=self._lifecycle,
            teammate_mode=self._teammate_mode,
            team_mode=self._team_mode,
            dispatch_mode=self._dispatch_mode,
            language=self._language,
            hitt_enabled=bool(d.get("enable_hitt")),
        )
        if not policy:
            return facts
        return f"{facts}\n\n{policy}"


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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
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
