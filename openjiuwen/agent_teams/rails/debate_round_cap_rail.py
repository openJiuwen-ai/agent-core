# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-teammate debate ``send_message`` ceiling for interactive debate.

Mounted only on teammates (not the Leader). After a successful debate-oriented
``send_message``, increments a per-member counter; when the counter reaches
``max_debate_rounds``, notifies the Leader once to close the debate. Subsequent
debate sends are rejected in ``before_tool_call`` (no double-counting).

Debate targets: peer member(s) and broadcast ``*`` (each call counts as 1).
Sends to the Leader / ``user`` are not counted so wrap-up reports stay open.

Task-collaboration traffic is excluded while any non-terminal board task
exists (same tool name, different purpose — see ``_has_open_tasks``).
"""

from __future__ import annotations

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
    }
)

_SEND_MESSAGE_NAMES = frozenset({"send_message"})


class DebateRoundCapRail(DeepAgentRail):
    """Reject teammate peer ``send_message`` after ``max_debate_rounds`` successes."""

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
        self._leader_notified = False

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Reject further debate sends once the after-call counter has hit the cap.

        Does **not** increment ``_count`` — counting only happens in
        ``after_tool_call`` after a successful send.
        """
        if ctx.extra.get("_skip_tool"):
            return
        if not self._is_send_message(ctx.inputs.tool_name):
            return

        tool_args = self._parse_args(ctx.inputs.tool_args)
        if not await self._should_count(tool_args):
            return

        if self._count >= self._max:
            error = self._limit_error_text()
            team_logger.info(
                "[DebateRoundCap] rejecting debate send_message for {} ({}/{})",
                self._member_name,
                self._count,
                self._max,
            )
            self._reject_tool(ctx, error)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Count a successful debate send; notify Leader when the cap is reached."""
        if ctx.extra.get("_skip_tool"):
            return
        if not self._is_send_message(ctx.inputs.tool_name):
            return

        tool_args = self._parse_args(ctx.inputs.tool_args)
        if not await self._should_count(tool_args):
            return
        if not self._tool_succeeded(ctx):
            return

        self._count += 1
        team_logger.debug(
            "[DebateRoundCap] {} debate send_message count={}/{}",
            self._member_name,
            self._count,
            self._max,
        )
        if self._count >= self._max:
            await self._notify_leader_once()

    async def _should_count(self, tool_args: dict[str, Any]) -> bool:
        """True when this call is a debate send under an active debate window."""
        if await self._has_open_tasks():
            return False
        return self._is_debate_target(tool_args.get("to"))

    async def _has_open_tasks(self) -> bool:
        """Return whether the team board has any non-terminal task.

        Orthogonal to ``_SEND_MESSAGE_NAMES``: task collaboration also uses
        ``send_message``. While open tasks exist, treat traffic as task P2P
        and leave the debate budget untouched.
        """
        try:
            tasks = await self._team.task_manager.list_tasks()
        except Exception as exc:  # noqa: BLE001 — fail open to avoid blocking work
            team_logger.warning("[DebateRoundCap] list_tasks failed (treating as no open tasks): {}", exc)
            return False
        return any(getattr(task, "status", None) in _OPEN_TASK_STATUSES for task in tasks or [])

    def _is_debate_target(self, to_raw: Any) -> bool:
        """Whether ``to`` is debate traffic: peer(s) and/or broadcast ``*``.

        Leader / ``user`` / empty / self-only are not counted so reports and
        user-facing replies stay available after the cap.
        """
        if to_raw is None:
            return False
        if isinstance(to_raw, str):
            target = to_raw.strip()
            if not target:
                return False
            if target == "*":
                return True
            if target == USER_PSEUDO_MEMBER_NAME:
                return False
            if target == self._leader_name_sync():
                return False
            return target != self._member_name
        if isinstance(to_raw, list):
            names = [item.strip() for item in to_raw if isinstance(item, str) and item.strip()]
            if not names:
                return False
            if "*" in names:
                return True
            leader = self._leader_name_sync()
            return any(name not in {USER_PSEUDO_MEMBER_NAME, leader, self._member_name} for name in names)
        return False

    def _leader_name_sync(self) -> str:
        """Best-effort leader name without awaiting (for target filtering)."""
        name = getattr(self._team, "leader_member_name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        cache = getattr(self._team, "_leader_name_cache", None)
        if isinstance(cache, str) and cache.strip():
            return cache.strip()
        return "team-leader"

    async def _leader_name(self) -> str:
        try:
            return await self._team.resolve_leader_member_name()
        except Exception:  # noqa: BLE001
            return self._leader_name_sync()

    async def _notify_leader_once(self) -> None:
        if self._leader_notified:
            return
        self._leader_notified = True
        leader = await self._leader_name()
        content = self._leader_notify_text()
        try:
            await self._messages.send_message(content=content, to_member_name=leader)
            team_logger.info(
                "[DebateRoundCap] notified leader {} that debate budget is exhausted for {}",
                leader,
                self._member_name,
            )
        except Exception as exc:  # noqa: BLE001
            team_logger.error("[DebateRoundCap] failed to notify leader: {}", exc)

    def _limit_error_text(self) -> str:
        if self._language.startswith("zh") or self._language == "cn":
            return (
                f"已达到思辨互发上限（本成员 {self._count}/{self._max} 次辩论 send_message）。"
                f"禁止继续向其他成员/全体互发。"
                f"若尚未向 Leader 汇报过本场要点：现在用一条消息合并发送停止说明与要点即可；"
                f"若已汇报过则直接停止，收束阶段不要再发第二份总结。"
            )
        return (
            f"Interactive-debate send_message limit reached "
            f"({self._count}/{self._max} for this teammate). "
            f"Do not message other members or broadcast further. "
            f"If you have not yet reported key points to the Leader, send one message that merges "
            f"the stop note and the summary; if you already reported, stop — do not send a second "
            f"wrap-up summary."
        )

    def _leader_notify_text(self) -> str:
        if self._language.startswith("zh") or self._language == "cn":
            return (
                f"[思辨轮数限制] 成员 `{self._member_name}` 的辩论 `send_message` "
                f"已达上限（{self._max}）。辩论应结束：请直接依据已有互论内容与成员要点"
                f"（若有）向用户呈现共识/分歧；**不要**再要求成员「再总结/再汇报」，"
                f"也不要再转发或催促成员继续互论。"
            )
        return (
            f"[Debate round cap] Teammate `{self._member_name}` has exhausted their debate "
            f"`send_message` budget ({self._max}). Close to the user from existing debate "
            f"content and any member key-points already received; **do not** ask members to "
            f"summarize/report again, and do not relay or nudge further peer debate."
        )

    @staticmethod
    def _is_send_message(tool_name: Any) -> bool:
        if not isinstance(tool_name, str) or not tool_name:
            return False
        base = tool_name.rsplit(".", 1)[-1]
        return base in _SEND_MESSAGE_NAMES or tool_name in _SEND_MESSAGE_NAMES

    @staticmethod
    def _parse_args(tool_args: Any) -> dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except Exception:  # noqa: BLE001
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _tool_succeeded(ctx: AgentCallbackContext) -> bool:
        result = ctx.inputs.tool_result
        if isinstance(result, ToolOutput):
            return bool(result.success)
        if isinstance(result, dict) and "error" in result and result.get("error"):
            return False
        if isinstance(result, dict) and "success" in result:
            return bool(result.get("success"))
        return result is not None and not (isinstance(result, dict) and result.get("error"))

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, error_msg: str) -> None:
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        msg = ToolMessage(content=error_msg, tool_call_id=tool_call_id)
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = {"error": error_msg}
        ctx.inputs.tool_msg = msg


__all__ = ["DebateRoundCapRail"]
