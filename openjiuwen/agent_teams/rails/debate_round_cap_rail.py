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
    parse_debate_coordination_meta,
)
from openjiuwen.agent_teams.i18n import STRINGS
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
_CAP_NOTICE_KEY_PREFIX = "_team_debate_round_cap_notice"


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
            if not self._debate.finalized:
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
        if self._debate.round_id:
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
        """Tag debate messages and transform over-limit sends into notices."""
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
            if args.get("final_report") is True:
                self._inject_meta(
                    ctx,
                    make_debate_invocation_meta(
                        round_id,
                        DebateMessageRole.FINAL_REPORT,
                    ),
                )
            return

        eligibility_key = self._eligibility_key(ctx)
        eligible = await self._should_apply(args)
        ctx.extra[eligibility_key] = eligible
        if eligible:
            ctx.inputs.tool_args = args
            if self._count >= self._max:
                args["content"] = self._text(
                    "debate.cap_notice",
                    member_name=await self._display_name(),
                )
                self._inject_meta(
                    ctx,
                    make_debate_invocation_meta(
                        round_id,
                        DebateMessageRole.CAP_NOTICE,
                    ),
                )
                ctx.extra[self._cap_notice_key(ctx)] = True
                return
            inactive = await self._inactive_targets(args.get("to"))
            if inactive:
                self._reject_tool(ctx, self._inactive_target_text(inactive))
                return
            self._inject_meta(
                ctx,
                make_debate_invocation_meta(
                    round_id,
                    DebateMessageRole.PEER,
                ),
            )
            if self._count == self._max - 1:
                content = str(args.get("content", "") or "")
                suffix = self._text(
                    "debate.cap_public_suffix",
                    member_name=await self._display_name(),
                )
                args["content"] = f"{content}\n\n{suffix}"

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
        args = self._parse_args(ctx.inputs.tool_args)
        if await self._is_leader_target(args.get("to")):
            if args.get("final_report") is True and self._tool_succeeded(ctx):
                await self._debate.complete_participant(round_id)
            return
        if ctx.extra.pop(self._cap_notice_key(ctx), False):
            return
        eligible = ctx.extra.pop(self._eligibility_key(ctx), None)
        if eligible is None:
            eligible = await self._should_apply(ctx.inputs.tool_args)
        if not eligible or not self._tool_succeeded(ctx):
            return

        async with self._count_lock:
            if self._count < self._max:
                self._count += 1
                reached_cap = self._count == self._max
            else:
                reached_cap = False
        if reached_cap and await self._debate.mark_participant_capped(round_id):
            self._append_tool_instruction(
                ctx,
                self._text("debate.cap_private_instruction"),
            )

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
        excluded = {USER_PSEUDO_MEMBER_NAME, self._member_name, leader}
        return any(target not in excluded for target in targets)

    async def _is_leader_target(self, raw_target: Any) -> bool:
        if not isinstance(raw_target, str):
            return False
        return raw_target.strip() == await self._leader_name()

    async def _inactive_targets(self, raw_target: Any) -> set[str]:
        round_id = self._debate.participant_round_id
        if not round_id:
            return set()
        terminal = await self._terminal_participants(round_id)
        if terminal is None:
            return set()
        targets = self._target_list(raw_target)
        if "*" not in targets:
            return set(targets) & terminal
        snapshot = await self._trackable_participants()
        if snapshot is None:
            return set()
        participants, _ = snapshot
        round_participants = await self._round_participants(round_id, participants)
        if round_participants is None:
            return set()
        active = round_participants - terminal - {self._member_name}
        return {"*"} if not active else set()

    async def _round_participants(
        self,
        round_id: str,
        trackable_participants: set[str],
    ) -> set[str] | None:
        message_manager = self._team.message_manager
        try:
            messages = await message_manager.get_team_messages(
                message_manager.team_name,
            )
        except Exception as exc:  # noqa: BLE001 - target checks must fail open
            team_logger.warning("[DebateRoundCap] invite lookup failed: {}", exc)
            return None
        participants: set[str] = set()
        invited_all = False
        for message in messages:
            meta = parse_debate_coordination_meta(
                getattr(message, "coordination_meta", None),
            )
            if (
                not meta
                or meta["round_id"] != round_id
                or meta["message_role"] != DebateMessageRole.INVITE.value
            ):
                continue
            if getattr(message, "broadcast", False) is True:
                invited_all = True
                continue
            recipient = str(getattr(message, "to_member_name", "") or "")
            if recipient:
                participants.add(recipient)
        if invited_all:
            participants.update(trackable_participants)
        return participants & trackable_participants

    async def _terminal_participants(self, round_id: str) -> set[str] | None:
        snapshot = await self._trackable_participants()
        if snapshot is None:
            return None
        _, failed = snapshot
        terminal = set(failed)
        try:
            messages = await self._team.message_manager.get_messages(
                to_member_name=await self._leader_name(),
            )
        except Exception as exc:  # noqa: BLE001 - target checks must fail open
            team_logger.warning("[DebateRoundCap] final-report lookup failed: {}", exc)
            return None
        for message in messages:
            meta = parse_debate_coordination_meta(
                getattr(message, "coordination_meta", None),
            )
            if (
                meta
                and meta["round_id"] == round_id
                and meta["message_role"] == DebateMessageRole.FINAL_REPORT.value
            ):
                terminal.add(str(getattr(message, "from_member_name", "") or ""))
        terminal.discard("")
        return terminal

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

    def _inactive_target_text(self, inactive: set[str]) -> str:
        names = ", ".join(sorted(inactive - {"*"}))
        if self._language.startswith("zh") or self._language == "cn":
            if names:
                return (
                    f"这些成员已经结束本轮思辨：{names}。不要再向他们发送消息；"
                    "如果没有其他可讨论成员，请向 Leader 发送 final_report。"
                )
            return "本轮已经没有可继续响应的思辨成员。请停止互发并向 Leader 发送 final_report。"
        if names:
            return (
                f"These members already completed this debate round: {names}. "
                "Do not message them again; if no active peers remain, send your final report to the Leader."
            )
        return (
            "No active peers remain in this debate round. "
            "Stop peer messaging and send your final report to the Leader."
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
    def _cap_notice_key(ctx: AgentCallbackContext) -> str:
        return f"{_CAP_NOTICE_KEY_PREFIX}:{id(ctx)}"

    async def _display_name(self) -> str:
        try:
            member = await self._team.get_member(self._member_name)
        except Exception:  # noqa: BLE001 - presentation falls back to the stable routing name
            return self._member_name
        display_name = str(getattr(member, "display_name", "") or "").strip()
        return display_name or self._member_name

    def _text(self, key: str, *, member_name: str | None = None) -> str:
        language = "en" if self._language.startswith("en") else "cn"
        return STRINGS[language][key].format_map(
            {
                "member_name": member_name or self._member_name,
                "count": self._max,
                "max_count": self._max,
            },
        )

    @staticmethod
    def _append_tool_instruction(ctx: AgentCallbackContext, instruction: str) -> None:
        tool_msg = ctx.inputs.tool_msg
        if not isinstance(tool_msg, ToolMessage):
            return
        if isinstance(tool_msg.content, str):
            tool_msg.content = f"{tool_msg.content}\n\n{instruction}"
        elif isinstance(tool_msg.content, list):
            tool_msg.content.append(instruction)

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
