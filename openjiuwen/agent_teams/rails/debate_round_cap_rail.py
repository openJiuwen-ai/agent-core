# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Autonomous debate enrollment and per-teammate message ceiling."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.debate import (
    DebateMessageRole,
    DebateRunState,
    make_debate_invocation_meta,
)
from openjiuwen.agent_teams.schema.status import ExecutionStatus, MemberStatus, TaskStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.llm_stability_rail import FORCE_SKIP_ALL_KEY, SKIP_KEY
from openjiuwen.harness.tools.base_tool import ToolOutput

_DEBATE_META_ARG = "_team_debate_meta"
_OPEN_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.PLANNING.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.IN_REVIEW.value,
    }
)
_ELIGIBILITY_KEY_PREFIX = "_team_debate_round_cap_eligible"


class DebateRoundCapRail(DeepAgentRail):
    """Coordinate Leader invitations and cap teammate peer messages."""

    priority = 55

    def __init__(
        self,
        *,
        max_debate_rounds: int,
        team_backend: TeamBackend,
        member_name: str,
        role: TeamRole | str = TeamRole.TEAMMATE,
        language: str = "cn",
    ) -> None:
        super().__init__()
        if max_debate_rounds < 1:
            raise ValueError(f"max_debate_rounds must be >= 1, got {max_debate_rounds}")
        self._max = max_debate_rounds
        self._team = team_backend
        self._member_name = member_name
        self._role = TeamRole(role)
        self._language = (language or "cn").lower()
        debate_state = getattr(team_backend, "debate_state", None)
        if not isinstance(debate_state, DebateRunState):
            debate_state = DebateRunState(language=self._language)
            team_backend.debate_state = debate_state
        debate_state.language = self._language
        self._debate = debate_state
        if self._role == TeamRole.LEADER:
            self._debate.reset_leader_round()
        else:
            self._debate.reset_participant_round()
        self._count = 0
        self._count_lock = asyncio.Lock()
        self._count_round_id: str | None = None

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Enroll valid Leader invitations emitted by one model response."""
        if self._role != TeamRole.LEADER or ctx.extra.get(FORCE_SKIP_ALL_KEY):
            return
        if self._debate.round_id and not self._debate.finalized:
            return
        response = getattr(ctx.inputs, "response", None)
        if not isinstance(response, AssistantMessage) or not response.tool_calls:
            return
        if await self._has_open_tasks() is not False:
            return
        participant_snapshot = await self._trackable_participants()
        if participant_snapshot is None:
            return
        trackable_participants, initially_failed = participant_snapshot
        if not trackable_participants:
            return

        skipped = ctx.extra.get(SKIP_KEY, {})
        invitation_calls: dict[str, set[str]] = {}
        for call in response.tool_calls:
            call_id = str(getattr(call, "id", "") or "")
            tool_name = getattr(call, "name", "")
            if not call_id or call_id in skipped or not self._is_send_message(tool_name):
                continue
            args = self._parse_args(getattr(call, "arguments", None))
            targets = self._invitation_targets(
                args.get("to"),
                trackable_participants,
            )
            if targets:
                invitation_calls[call_id] = targets
        if invitation_calls:
            await self._debate.begin_round(invitation_calls)
            invited = set().union(*invitation_calls.values())
            for member_name in initially_failed & invited:
                await self._debate.mark_failed(member_name)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Tag debate messages and reject teammate peer sends at the cap."""
        if ctx.extra.get("_skip_tool") or not self._is_send_message(ctx.inputs.tool_name):
            return
        if self._role == TeamRole.LEADER:
            call_id = str(getattr(ctx.inputs.tool_call, "id", "") or "")
            meta = await self._debate.invitation_meta(call_id)
            if meta is not None:
                self._inject_meta(ctx, meta)
            return

        self._sync_participant_round()
        round_id = self._debate.participant_round_id
        if not round_id:
            return
        args = self._parse_args(ctx.inputs.tool_args)
        if await self._is_leader_target(args.get("to")):
            self._inject_meta(
                ctx,
                make_debate_invocation_meta(round_id, DebateMessageRole.FINAL_REPORT),
            )
            return

        eligibility_key = self._eligibility_key(ctx)
        eligible = await self._should_apply(args)
        ctx.extra[eligibility_key] = eligible
        if eligible and self._count >= self._max:
            self._reject_tool(ctx, self._limit_error_text())

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Settle Leader invitations or count a successful teammate peer send."""
        if not self._is_send_message(ctx.inputs.tool_name):
            return
        if self._role == TeamRole.LEADER:
            call_id = str(getattr(ctx.inputs.tool_call, "id", "") or "")
            succeeded = not ctx.extra.get("_skip_tool") and self._tool_succeeded(ctx)
            delivered = (
                set()
                if ctx.extra.get("_skip_tool")
                else self._multicast_delivered_participants(ctx.inputs.tool_result)
            )
            await self._debate.settle_invitation(
                call_id,
                succeeded=succeeded,
                delivered_participants=delivered,
            )
            return
        if ctx.extra.get("_skip_tool"):
            return

        self._sync_participant_round()
        round_id = self._debate.participant_round_id
        if not round_id:
            return
        if await self._is_leader_target(self._parse_args(ctx.inputs.tool_args).get("to")):
            if self._tool_succeeded(ctx):
                await self._debate.complete_participant(round_id)
            return
        eligible = ctx.extra.pop(self._eligibility_key(ctx), None)
        if eligible is None:
            eligible = await self._should_apply(ctx.inputs.tool_args)
        if not eligible or not self._tool_succeeded(ctx):
            return

        async with self._count_lock:
            if self._count < self._max:
                self._count += 1

    async def _should_apply(self, tool_args: Any) -> bool:
        args = self._parse_args(tool_args)
        if not await self._is_debate_target(args.get("to")):
            return False
        open_tasks = await self._has_open_tasks()
        return open_tasks is False

    async def _has_open_tasks(self) -> bool | None:
        try:
            tasks = await self._team.task_manager.list_tasks()
        except Exception as exc:  # noqa: BLE001 - a guard rail must not block work on DB failure
            team_logger.warning("[DebateRoundCap] list_tasks failed; skipping debate check: {}", exc)
            return None
        return any(
            getattr(task, "status", None) in _OPEN_TASK_STATUSES
            for task in tasks or []
        )

    async def _is_debate_target(self, raw_target: Any) -> bool:
        targets = self._target_list(raw_target)
        if not targets:
            return False
        if "*" in targets:
            return True
        leader = await self._leader_name()
        excluded = {USER_PSEUDO_MEMBER_NAME, "leader", self._member_name, leader}
        return any(target not in excluded for target in targets)

    async def _is_leader_target(self, raw_target: Any) -> bool:
        if not isinstance(raw_target, str):
            return False
        return raw_target.strip() in {"leader", await self._leader_name()}

    async def _trackable_participants(self) -> tuple[set[str], set[str]] | None:
        try:
            members = await self._team.list_members()
        except Exception as exc:  # noqa: BLE001 - fail open when roster roles cannot be read
            team_logger.warning("[DebateRoundCap] list_members failed: {}", exc)
            return None
        trackable: set[str] = set()
        failed: set[str] = set()
        for member in members:
            member_name = str(getattr(member, "member_name", "") or "")
            if (
                not member_name
                or getattr(member, "role", TeamRole.TEAMMATE.value)
                != TeamRole.TEAMMATE.value
                or self._team.is_external_cli_agent(member_name)
            ):
                continue
            trackable.add(member_name)
            if (
                getattr(member, "status", None) == MemberStatus.ERROR.value
                or getattr(member, "execution_status", None)
                == ExecutionStatus.FAILED.value
            ):
                failed.add(member_name)
        return trackable, failed

    def _invitation_targets(
        self,
        raw_target: Any,
        trackable_participants: set[str],
    ) -> set[str]:
        targets = self._target_list(raw_target)
        if not targets:
            return set()
        if "*" in targets:
            return set(trackable_participants)
        return set(targets) & trackable_participants

    async def _leader_name(self) -> str:
        configured = str(getattr(self._team, "leader_member_name", "") or "").strip()
        if configured:
            return configured
        try:
            return str(await self._team.resolve_leader_member_name() or "").strip()
        except Exception:  # noqa: BLE001 - preserve final-report path on lookup failure
            return "team_leader"

    def _limit_error_text(self) -> str:
        if self._language.startswith("zh") or self._language == "cn":
            return (
                f"已达到思辨互发上限（本成员 {self._count}/{self._max} 次）。"
                "请停止向其他成员或全体继续互发，并仅在尚未汇报时向 Leader 发送一次最终要点。"
            )
        return (
            f"Debate send_message limit reached ({self._count}/{self._max}). "
            "Stop messaging peers or broadcasting; send one final report to the Leader only if needed."
        )

    def _sync_participant_round(self) -> None:
        round_id = self._debate.participant_round_id
        if round_id != self._count_round_id:
            self._count_round_id = round_id
            self._count = 0

    @staticmethod
    def _inject_meta(ctx: AgentCallbackContext, meta: Any) -> None:
        args = DebateRoundCapRail._parse_args(ctx.inputs.tool_args)
        if not args:
            return
        args[_DEBATE_META_ARG] = meta
        ctx.inputs.tool_args = args

    @staticmethod
    def _target_list(raw_target: Any) -> list[str]:
        if isinstance(raw_target, str):
            return [raw_target.strip()] if raw_target.strip() else []
        if isinstance(raw_target, list):
            return [
                item.strip()
                for item in raw_target
                if isinstance(item, str) and item.strip()
            ]
        return []

    @staticmethod
    def _is_send_message(tool_name: Any) -> bool:
        return isinstance(tool_name, str) and tool_name.rsplit(".", 1)[-1] == "send_message"

    @staticmethod
    def _eligibility_key(ctx: AgentCallbackContext) -> str:
        return f"{_ELIGIBILITY_KEY_PREFIX}:{id(ctx)}"

    @staticmethod
    def _parse_args(tool_args: Any) -> dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _tool_succeeded(ctx: AgentCallbackContext) -> bool:
        result = ctx.inputs.tool_result
        if isinstance(result, ToolOutput):
            return result.success
        if isinstance(result, dict) and "success" in result:
            return bool(result["success"])
        return result is not None and not (
            isinstance(result, dict) and bool(result.get("error"))
        )

    @staticmethod
    def _multicast_delivered_participants(result: Any) -> set[str] | None:
        data = result.data if isinstance(result, ToolOutput) else None
        if isinstance(result, dict):
            data = result.get("data")
        if not isinstance(data, dict) or data.get("type") != "multicast":
            return None
        delivered = data.get("delivered")
        if not isinstance(delivered, list):
            return set()
        return {
            member.strip()
            for member in delivered
            if isinstance(member, str) and member.strip()
        }

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, error: str) -> None:
        tool_call_id = getattr(ctx.inputs.tool_call, "id", "")
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": error}
        ctx.inputs.tool_msg = ToolMessage(content=error, tool_call_id=tool_call_id)


__all__ = ["DebateRoundCapRail"]
