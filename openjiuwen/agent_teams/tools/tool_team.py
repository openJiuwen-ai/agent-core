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
    """

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
        self._language = language
        self._lifecycle = lifecycle
        self._teammate_mode = teammate_mode
        self._team_mode = team_mode
        self._dispatch_mode = dispatch_mode
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

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        display_name = inputs.get("display_name")
        leader_display_name = inputs["leader_display_name"]
        # None when LLM omits a field — backend.build_team inherits the
        # spec ceiling. Explicit values set the runtime instance flag
        # (subject to the spec ceiling check).
        enable_hitt_arg = inputs.get("enable_hitt")
        await self.team.build_team(
            display_name=display_name,
            desc=inputs.get("team_desc"),
            leader_display_name=leader_display_name,
            leader_desc=inputs["leader_desc"],
            overrides=CapabilityOverrides(
                enable_hitt=enable_hitt_arg,
            ),
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
        """Render the creation facts, then disclose the collaboration policy.

        The policy is appended only on success — a failed ``build_team`` leaves
        the leader on the bootstrap path, where re-reading the routing guide is
        what it needs, not a rulebook for a team that does not exist.
        """
        if not output.success:
            return output.error or "Failed to build team"
        d = output.data or {}
        facts = (
            f"Team created: team_name={d.get('team_name')} "
            f"display_name={d.get('display_name')} "
            f"leader_member_name={d.get('leader_member_name')} "
            f"leader_display_name={d.get('leader_display_name')} "
            f"hitt_enabled={d.get('enable_hitt')}"
        )
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
