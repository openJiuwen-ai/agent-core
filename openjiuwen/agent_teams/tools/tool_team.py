# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team lifecycle tools: build_team and clean_team."""

from typing import Any

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import CapabilityOverrides, TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


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
        self._t = t
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
                "enable_task_verification": {
                    "type": "boolean",
                    "description": t("build_team", "enable_task_verification"),
                },
            },
            "required": ["display_name", "team_desc", "leader_display_name", "leader_desc"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        display_name = inputs.get("display_name")
        leader_display_name = inputs["leader_display_name"]
        # 缺键 KeyError 必须在 try 之外：参数缺失是 LLM 可修正的用法错误，
        # 由工具边界消化成可见错误即可；进 try 会被当成基础设施失败计入
        # 熔断并置 latch——两次漏参即永久熔断+误导性 degraded 告警。
        leader_desc = inputs["leader_desc"]
        # None when LLM omits a field — backend.build_team inherits the
        # spec ceiling. Explicit values set the runtime instance flag
        # (subject to the spec ceiling check).
        enable_hitt_arg = inputs.get("enable_hitt")
        enable_task_verification_arg = inputs.get("enable_task_verification")
        # Circuit open: refuse fast (no DB hit) and steer the leader to
        # degrade to solo completion. The DB layer already retries each real
        # attempt 3x with backoff; past the threshold another attempt is
        # near-certain to fail and only burns ~12s per call.
        if self.team.build_circuit_open:
            return ToolOutput(
                success=False,
                error=self._t("build_team", "circuit_open", reason=self.team.last_build_error),
            )
        try:
            await self.team.build_team(
                display_name=display_name,
                desc=inputs.get("team_desc"),
                leader_display_name=leader_display_name,
                leader_desc=leader_desc,
                overrides=CapabilityOverrides(
                    enable_hitt=enable_hitt_arg,
                    enable_task_verification=enable_task_verification_arg,
                ),
            )
        except Exception as e:
            # The tool boundary digests failures into an LLM-facing tool
            # message, and the round may then end "normally" for a team that
            # does not exist. Latch the failure first so the round-end
            # settle path can surface it as a stream-level warning.
            err_text = f"{type(e).__name__}: {e}"
            # 配置/参数类错误（能力天花板校验等）不是
            # 基础设施失败：不计熔断、不 latch——LLM 修正参数即可成功，
            # "团队不可用"告警在此语义错误，且两次同类参数错会把熔断毒化
            # 成永久。
            if getattr(e, "status", None) == StatusCode.AGENT_TEAM_CONFIG_INVALID:
                return ToolOutput(success=False, error=str(e))
            # 半建成区分：build_team 的失败面不止
            # "团队没建成"——create_team 落行后的步骤（成员注册等）也可能失败。
            # 行已在 = 团队已建立：不计熔断、不 latch 告警（团队能力可用），
            # 引导 leader 用 spawn_member 补齐成员。读不出（锁库等）按未建成
            # 保守进熔断。
            try:
                existing = await self.db.team.get_team(self.team.team_name)
            except Exception:
                existing = None
            if existing is not None:
                # 半建成：清掉前轮"未建成"遗留的 latch（否则收尾仍推
                # "团队不可用"告警，与本分支"团队已存在可继续"自相矛盾）。
                await self.team.notify_build_recovered()
                return ToolOutput(
                    success=False,
                    error=self._t("build_team", "partial_built", reason=err_text),
                )
            self.team.note_build_failure(err_text)
            await self.team.notify_build_failed(err_text)
            # Return (not raise) so the LLM-facing message is ours: the first
            # failure invites one retry; once the circuit opens, the message
            # explicitly directs degrade-to-solo.
            key = "circuit_open" if self.team.build_circuit_open else "retry_once"
            return ToolOutput(success=False, error=self._t("build_team", key, reason=err_text))
        return ToolOutput(
            success=True,
            data={
                "team_name": self.team.team_name,
                "display_name": display_name,
                "leader_member_name": self.team.member_name,
                "leader_display_name": leader_display_name,
                "enable_hitt": self.team.hitt_enabled(),
                "enable_task_verification": self.team.task_verification_enabled(),
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
            f"hitt_enabled={d.get('enable_hitt')} "
            f"task_verification={d.get('enable_task_verification')}"
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
