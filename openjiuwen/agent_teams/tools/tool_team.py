# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team lifecycle tools: build_team and clean_team."""

import asyncio
from typing import Any

from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import (
    CapabilityOverrides,
    MemberReconcileReport,
    TeamBackend,
)
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Half-built recovery: after a build failure whose team row already exists,
# reconcile the predefined roster before answering. Bounded so a wedged DB
# cannot hold one tool call open for a full retry cycle per missing member.
_BUILD_RECONCILE_TIMEOUT_SEC = 30.0


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
            # 熔断期的快速拒绝同样代表"本轮没有可用团队"：置 round-end
            # latch，与未知态出口同口径——否则熔断后每一轮的拒绝都静默，
            # 用户看到"正常完成"而团队实际不可用（latch 收尾读取即清，
            # 重复置位幂等）。
            await self.team.notify_build_failed(self.team.last_build_error)
            return ToolOutput(
                success=False,
                error=self._t("build_team", "circuit_open", reason=self.team.last_build_error),
            )
        # Unknown-state brake（与失败熔断分离）：判定读连续失败时状态不可确知，
        # 不计数失败（避免假熔断），但也不能无限重试——每次重试都再打一次
        # DB（create_team + 探针重试）。达阈值后快速拒绝（零 DB 访问），
        # 引导降级单干。
        if self.team.build_state_unknown_exhausted and not self.team.build_unknown_cooldown_elapsed:
            # 快速拒绝也是"本轮没有可用团队"：同样置 round-end latch，
            # 避免冷却期的装配轮次被当成干净轮次收尾（收尾读取后清空，
            # 链内后续成功/半建成亦会清，不会误报）。
            await self.team.notify_build_failed(self.team.last_unknown_error)
            return ToolOutput(
                success=False,
                error=self._t(
                    "build_team", "state_unknown_exhausted",
                    reason=self.team.last_unknown_error,
                    cooldown=int(self.team.build_unknown_cooldown_seconds),
                ),
            )
        # 冷却到点后放行一次半开探测：确定结果清零（恢复），再失败则
        # 重新计冷却——避免两次瞬时读失败把 build_team 会话级永久锁死。
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
            # 行已在 = 团队已建立：不计熔断（团队能力可用）；但初始化不完整
            # 必须收尾可见——partial_built 文案要求"必要时单人兜底并如实
            # 说明"，且 leader 侧没有任何 spawn 工具可补齐，不能静默。
            #
            # 读不出（判定读本身超时/锁失败）≠ 未建成：DB 压力下把
            # "不知道"当"没建成"计数，会让两次瞬时读失败把团队永久熔断成
            # 降级单干（高并发装配下 30s 超时的常见形态）。
            # 未知态：不计熔断，但每次出口都置 round-end latch。
            try:
                existing = await self.db.team.get_team(self.team.team_name)
            except Exception as probe_exc:
                team_logger.warning(
                    "[BuildTeamTool] build_team failure state unreadable for team %s (%s: %s); "
                    "not counting the circuit breaker (unknown-state streak=%d)",
                    self.team.team_name,
                    type(probe_exc).__name__,
                    probe_exc,
                    self.team.consecutive_build_unknown + 1,
                )
                self.team.note_build_unknown(err_text)
                # 与"确定未建成"分支对齐：任何未知态尝试都必须置 round-end
                # latch——本次 build_team 确实失败过、本轮也确实没有可用团队，
                # 若模型遵守"本轮不要重试"直接单人收尾，不 latch 会让用户看到
                # 一个"正常完成"的轮次（单轮会话下再无补偿机会）。链内后续
                # 构建成功（_mark_team_built）会清 latch；半建成则先清后以
                # 当轮半建成原因重新置位，不会留下前轮误报。
                await self.team.notify_build_failed(err_text)
                exhausted = self.team.build_state_unknown_exhausted
                key = "state_unknown_exhausted" if exhausted else "state_unknown"
                return ToolOutput(
                    success=False,
                    error=self._t(
                        "build_team", key, reason=err_text,
                        cooldown=int(self.team.build_unknown_cooldown_seconds),
                    ),
                )
            if existing is not None:
                # 半建成：状态已确定，清未知态streak。先 recovered 清掉前轮
                # "未建成"遗留的 latch，再以**本轮**半建成原因重新置位：
                # partial_built 文案已改为"初始化未完整、必要时单人兜底并
                # 如实说明"，收尾若仍静默按正常完成呈现，与文案和其余
                # 失败出口口径分叉。
                self.team.reset_build_unknown()
                await self.team.notify_build_recovered()
                # 行已落库但成员注册中断：恢复责任
                # 在运行时。这里幂等补齐缺失的预定义
                # 成员；补齐成功即团队可用，按成功返回。
                report = await self._reconcile_after_partial_build(existing)
                if report is not None and report.complete:
                    team_logger.warning(
                        "[BuildTeamTool] build_team: partial failure recovered "
                        "after reconciling team %s (%d member(s) registered): %s",
                        self.team.team_name,
                        len(report.created),
                        err_text,
                    )
                    return ToolOutput(
                        success=True,
                        data={
                            "team_name": self.team.team_name,
                            "display_name": display_name,
                            "leader_member_name": self.team.member_name,
                            "leader_display_name": leader_display_name,
                            "enable_hitt": self.team.hitt_enabled(),
                            "enable_task_verification": self.team.task_verification_enabled(),
                            "recovered_from": err_text,
                            "reconciled_members": list(report.created),
                        },
                    )
                # 补齐仍不完整（或超时）：结构化失败，且不再推荐当前工具集
                # 里不存在的恢复动作。团队不可用——禁止建任务/广播，避免
                # 零成员 pending 僵尸形态。同时置 round-end latch：不完整
                # 就代表本轮没有可用团队，收尾不得静默按"正常完成"呈现
                # （与其余失败出口同口径；补齐成功路径已由 recovered 清除）。
                await self.team.notify_build_failed(err_text)
                if report is None:
                    expected: Any = self.team.expected_member_count()
                    present: Any = "unknown"
                    details = f"自动补齐超时（{_BUILD_RECONCILE_TIMEOUT_SEC:g}s）"
                else:
                    expected = report.expected
                    present = report.present
                    details = report.failure_summary() or "unknown"
                return ToolOutput(
                    success=False,
                    error=self._t(
                        "build_team",
                        "partial_built",
                        reason=err_text,
                        expected=expected,
                        present=present,
                        details=details,
                    ),
                )
            self.team.reset_build_unknown()  # 确定未建成：状态已可判定
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

    async def _reconcile_after_partial_build(
            self,
            team: Any | None = None,
    ) -> MemberReconcileReport | None:
        """Reconcile the predefined roster after a half-built failure.

        ``team`` is the ``team_info`` row already fetched to classify the
        half-built state; handing it to the backend avoids a second read on
        the recovery path.

        Returns the report, or ``None`` when the attempt itself timed out or
        raised (the team remains incomplete either way).
        """
        try:
            return await asyncio.wait_for(
                self.team.reconcile_predefined_members(team=team),
                timeout=_BUILD_RECONCILE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            team_logger.warning(
                "[BuildTeamTool] build_team: reconcile after partial build "
                "timed out after %ss for team %s",
                _BUILD_RECONCILE_TIMEOUT_SEC,
                self.team.team_name,
            )
            return None
        except Exception as e:  # noqa: BLE001 - surfaced to the LLM below
            team_logger.warning(
                "[BuildTeamTool] build_team: reconcile after partial build "
                "failed for team %s: %s",
                self.team.team_name,
                e,
            )
            return None

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
