# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-teammate ceiling for debate-oriented ``send_message`` calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.base_tool import ToolOutput

_OPEN_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.PLANNING.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.IN_REVIEW.value,
    },
)
_ELIGIBILITY_KEY_PREFIX = "_team_debate_round_cap_eligible"


class DebateRoundCapRail(DeepAgentRail):
    """Cap successful teammate-to-teammate debate messages."""

    priority = 55

    def __init__(
        self,
        *,
        max_debate_rounds: int,
        team_backend: TeamBackend,
        message_manager: TeamMessageManager,
        member_name: str,
        language: str = "cn",
    ) -> None:
        super().__init__()
        if max_debate_rounds < 1:
            raise ValueError(f"max_debate_rounds must be >= 1, got {max_debate_rounds}")
        self._max = max_debate_rounds
        self._team = team_backend
        self._messages = message_manager
        self._member_name = member_name
        self._language = (language or "cn").lower()
        self._count = 0
        self._count_lock = asyncio.Lock()
        self._leader_notified = False
        self._leader_notify_in_flight = False
        self._notify_lock = asyncio.Lock()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Reject debate sends after the successful-call counter reaches the cap."""
        if ctx.extra.get("_skip_tool") or not self._is_send_message(ctx.inputs.tool_name):
            return
        eligibility_key = self._eligibility_key(ctx)
        eligible = await self._should_apply(ctx.inputs.tool_args)
        ctx.extra[eligibility_key] = eligible
        if not eligible:
            return

        if self._count >= self._max:
            self._reject_tool(ctx, self._limit_error_text())
            await self._notify_leader_once()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Count a successful debate send and notify the Leader at the cap."""
        if ctx.extra.get("_skip_tool") or not self._is_send_message(ctx.inputs.tool_name):
            return
        eligible = ctx.extra.pop(self._eligibility_key(ctx), None)
        if eligible is None:
            eligible = await self._should_apply(ctx.inputs.tool_args)
        if not eligible or not self._tool_succeeded(ctx):
            return

        async with self._count_lock:
            if self._count >= self._max:
                return
            self._count += 1
            reached_cap = self._count >= self._max

        if reached_cap:
            await self._notify_leader_once()

    async def _should_apply(self, tool_args: Any) -> bool:
        args = self._parse_args(tool_args)
        if not await self._is_debate_target(args.get("to")):
            return False
        open_tasks = await self._has_open_tasks()
        # None means the board could not be queried. Fail open for the tool:
        # do not consume budget and do not reject the call.
        return open_tasks is False

    async def _has_open_tasks(self) -> bool | None:
        try:
            tasks = await self._team.task_manager.list_tasks()
        except Exception as exc:  # noqa: BLE001 - a guard rail must not block work on DB failure
            team_logger.warning("[DebateRoundCap] list_tasks failed; skipping cap check: {}", exc)
            return None
        return any(
            getattr(task, "status", None) in _OPEN_TASK_STATUSES
            for task in tasks or []
        )

    async def _is_debate_target(self, raw_target: Any) -> bool:
        if isinstance(raw_target, str):
            targets = [raw_target.strip()]
        elif isinstance(raw_target, list):
            targets = [item.strip() for item in raw_target if isinstance(item, str)]
        else:
            return False
        targets = [target for target in targets if target]
        if not targets:
            return False
        if "*" in targets:
            return True
        leader = await self._leader_name()
        # Scheduled send_message uses the role placeholder "leader" while
        # autonomous mode normally uses the concrete leader member name.
        excluded = {USER_PSEUDO_MEMBER_NAME, "leader", self._member_name, leader}
        return any(target not in excluded for target in targets)

    async def _leader_name(self) -> str:
        configured = str(getattr(self._team, "leader_member_name", "") or "").strip()
        if configured:
            return configured
        try:
            return str(await self._team.resolve_leader_member_name() or "").strip()
        except Exception:  # noqa: BLE001 - preserve report path if metadata lookup fails
            return "team_leader"

    async def _notify_leader_once(self) -> None:
        async with self._notify_lock:
            if self._leader_notified or self._leader_notify_in_flight:
                return
            self._leader_notify_in_flight = True
        try:
            message_id = await self._messages.send_message(
                content=self._leader_notify_text(),
                to_member_name=await self._leader_name(),
            )
            if message_id:
                async with self._notify_lock:
                    self._leader_notified = True
            else:
                team_logger.error("[DebateRoundCap] failed to persist leader notification")
        except Exception as exc:  # noqa: BLE001 - the completed user tool call stays successful
            team_logger.error("[DebateRoundCap] failed to notify leader: {}", exc)
        finally:
            async with self._notify_lock:
                self._leader_notify_in_flight = False

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

    def _leader_notify_text(self) -> str:
        if self._language.startswith("zh") or self._language == "cn":
            return (
                f"[思辨轮数限制] 成员 `{self._member_name}` 已达到 {self._max} 次互发上限。"
                "请基于现有讨论收束，不要再要求成员继续互论或重复总结。"
            )
        return (
            f"[Debate round cap] Teammate `{self._member_name}` reached the {self._max}-message cap. "
            "Close from the existing discussion; do not request more peer debate or duplicate summaries."
        )

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
    def _reject_tool(ctx: AgentCallbackContext, error: str) -> None:
        tool_call_id = getattr(ctx.inputs.tool_call, "id", "")
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": error}
        ctx.inputs.tool_msg = ToolMessage(content=error, tool_call_id=tool_call_id)


__all__ = ["DebateRoundCapRail"]
