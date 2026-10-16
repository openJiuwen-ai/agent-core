# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-session budget: whole-session ceilings on rounds / messages / tokens.

ISS-4993: an autonomous team whose chat never matches the debate pattern
(member→leader reports, leader broadcasts) slips past every existing cap —
``max_debate_rounds`` never counts it, ``max_iterations`` is overridden to
``sys.maxsize`` by the task loop — so a runaway discussion grows unbounded
until the gateway drops the connection and the team is torn down mid-flight.

This rail mounts a *session-level* budget on top of the per-teammate debate
cap. It counts every delivery shape with no leader/report exemption:

* **rounds** — every mailbox drain that actually delivers a message into a
  member's ReAct loop (``MessageHandler._process_unread_messages``);
* **messages** — every successful intra-team ``send_message`` call;
* **input tokens** — ``usage_metadata.input_tokens`` summed over every
  member's model responses.

On breach (``SessionBudgetState.exceeded``) the runtime switches from
"keep discussing" to "wrap up":

1. ``MessageHandler`` stops waking members for new mail — the leader gets
   exactly one wrap-up instruction, everyone else drains read-only;
2. ``SessionBudgetRail`` rejects ordinary ``send_message`` calls (a
   ``final_report=true`` send to the Leader/user stays available so the
   conclusion can still be delivered);
3. ``TeamCompletionHandler`` force-completes the team once the wrap-up is
   delivered (persistent teams auto-pause, same as a normal completion).

A fresh real ``USER_INPUT`` to the leader resets the counters — a persistent
team's next interaction starts with a full budget again.

The shared state lives on ``team_backend.session_budget_state`` (per-process,
like ``debate_state``): under the default inprocess execution mode every
member's rail sees the same counters; a future subprocess mode would need
the counters centralized, the same known limitation ``DebateRunState`` has.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.i18n import STRINGS
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.llm_stability_rail import FORCE_SKIP_ALL_KEY
from openjiuwen.harness.tools.base_tool import ToolOutput

REASON_MAX_TOTAL_MESSAGES = "max_total_messages"
REASON_MAX_TOTAL_ROUNDS = "max_total_rounds"
REASON_TOKEN_BUDGET = "token_budget"

_DIMENSIONS = {
    REASON_MAX_TOTAL_MESSAGES: ("消息数", "messages"),
    REASON_MAX_TOTAL_ROUNDS: ("轮次", "rounds"),
    REASON_TOKEN_BUDGET: ("累计输入 tokens", "input tokens"),
}


class SessionBudgetState:
    """Team-wide budget counters shared by every member rail in-process.

    Hung off ``team_backend.session_budget_state`` (first rail to construct
    wins, exactly like ``debate_state``). All mutating methods take the lock
    so two members recording concurrently cannot lose a count; reads of
    ``exceeded`` are single-attribute and safe without it.
    """

    def __init__(
        self,
        *,
        max_total_rounds: int | None = None,
        max_total_messages: int | None = None,
        token_budget: int | None = None,
        language: str = "cn",
    ) -> None:
        self.max_total_rounds = max_total_rounds
        self.max_total_messages = max_total_messages
        self.token_budget = token_budget
        self.language = (language or "cn").lower()
        self.message_count = 0
        self.turn_count = 0
        self.input_tokens = 0
        self.exceeded: str | None = None
        self.wrapup_delivered = False
        self._lock = asyncio.Lock()

    async def record_message(self) -> str | None:
        """Count one successful send; return the reason if this crossing breaches."""
        async with self._lock:
            self.message_count += 1
            if (
                self.exceeded is None
                and self.max_total_messages is not None
                and self.message_count >= self.max_total_messages
            ):
                self.exceeded = REASON_MAX_TOTAL_MESSAGES
            return self.exceeded

    async def record_turn(self) -> str | None:
        """Count one delivering mailbox drain; return the reason on breach."""
        async with self._lock:
            self.turn_count += 1
            if (
                self.exceeded is None
                and self.max_total_rounds is not None
                and self.turn_count >= self.max_total_rounds
            ):
                self.exceeded = REASON_MAX_TOTAL_ROUNDS
            return self.exceeded

    async def record_input_tokens(self, tokens: int) -> str | None:
        """Accumulate LLM input tokens; return the reason on breach."""
        if tokens <= 0:
            return self.exceeded
        async with self._lock:
            self.input_tokens += tokens
            if (
                self.exceeded is None
                and self.token_budget is not None
                and self.input_tokens >= self.token_budget
            ):
                self.exceeded = REASON_TOKEN_BUDGET
            return self.exceeded

    async def mark_wrapup_delivered(self) -> bool:
        """Try to claim the one-shot wrap-up delivery. False when already claimed."""
        async with self._lock:
            if self.wrapup_delivered:
                return False
            self.wrapup_delivered = True
            return True

    async def reset_cycle(self) -> None:
        """Reset every counter — the leader's fresh USER_INPUT starts a new cycle."""
        async with self._lock:
            self.message_count = 0
            self.turn_count = 0
            self.input_tokens = 0
            self.exceeded = None
            self.wrapup_delivered = False

    # ------------------------------------------------------------------
    # Rendered texts (i18n) — consumed by handlers, not only this rail
    # ------------------------------------------------------------------

    def wrapup_instruction(self) -> str:
        """The one-shot wrap-up instruction delivered to the leader."""
        language = "en" if self.language.startswith("en") else "cn"
        return STRINGS[language]["session_budget.exceeded_wrapup"].format_map(
            self._text_args(),
        )

    def reject_text(self) -> str:
        """The error text a rejected ordinary send_message returns."""
        language = "en" if self.language.startswith("en") else "cn"
        return STRINGS[language]["session_budget.send_rejected"].format_map(
            self._text_args(),
        )

    def _text_args(self) -> dict[str, Any]:
        language = "en" if self.language.startswith("en") else "cn"
        reason, used, limit = self._breach_stats()
        return {
            "dimension": _dimension_label(reason, language),
            "used": used,
            "limit": limit,
        }

    def _breach_stats(self) -> tuple[str, int, int]:
        """Return ``(reason, used, limit)`` for whichever dimension first breached.

        Falls back to the message dimension when ``exceeded`` is unset —
        callers only render these texts after checking ``exceeded``, so the
        fallback just keeps the text well-formed if ever hit.
        """
        reason = self.exceeded
        if reason == REASON_MAX_TOTAL_MESSAGES:
            return REASON_MAX_TOTAL_MESSAGES, self.message_count, int(self.max_total_messages or 0)
        if reason == REASON_MAX_TOTAL_ROUNDS:
            return REASON_MAX_TOTAL_ROUNDS, self.turn_count, int(self.max_total_rounds or 0)
        if reason == REASON_TOKEN_BUDGET:
            return REASON_TOKEN_BUDGET, self.input_tokens, int(self.token_budget or 0)
        # Not breached (yet): describe the message dimension as a neutral fallback.
        return (
            REASON_MAX_TOTAL_MESSAGES,
            self.message_count,
            int(self.max_total_messages or 0),
        )


def _dimension_label(reason: str, language: str) -> str:
    cn, en = _DIMENSIONS.get(reason, ("预算", "budget"))
    return en if language == "en" else cn


class SessionBudgetRail(DeepAgentRail):
    """Enforce the team-session budget from inside every member's harness."""

    priority = 56

    def __init__(
        self,
        *,
        max_total_rounds: int | None,
        max_total_messages: int | None,
        token_budget: int | None,
        team_backend: TeamBackend,
        member_name: str,
        role: TeamRole | str = TeamRole.TEAMMATE,
        language: str = "cn",
    ) -> None:
        super().__init__()
        if max_total_rounds is None and max_total_messages is None and token_budget is None:
            raise ValueError(
                "SessionBudgetRail requires at least one of "
                "max_total_rounds / max_total_messages / token_budget",
            )
        self._team = team_backend
        self._member_name = member_name
        self._role = TeamRole(role)
        state = getattr(team_backend, "session_budget_state", None)
        if not isinstance(state, SessionBudgetState):
            state = SessionBudgetState(
                max_total_rounds=max_total_rounds,
                max_total_messages=max_total_messages,
                token_budget=token_budget,
                language=(language or "cn").lower(),
            )
            team_backend.session_budget_state = state
        # The ceilings come from the team spec, identical for every member;
        # refresh them so a rebuilt member never runs on a stale limit.
        state.max_total_rounds = max_total_rounds
        state.max_total_messages = max_total_messages
        state.token_budget = token_budget
        state.language = (language or "cn").lower()
        self._state = state

    @property
    def state(self) -> SessionBudgetState:
        """Shared session-budget state (exposed for handler-side checks/tests)."""
        return self._state

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Accumulate this response's input tokens against the team budget."""
        if ctx.extra.get(FORCE_SKIP_ALL_KEY):
            return
        response = getattr(ctx.inputs, "response", None)
        if not isinstance(response, AssistantMessage):
            return
        usage = getattr(response, "usage_metadata", None)
        tokens = int(getattr(usage, "input_tokens", 0) or 0)
        if tokens <= 0:
            return
        reason = await self._state.record_input_tokens(tokens)
        if reason:
            team_logger.warning(
                "[{}] session budget exhausted via input tokens: {} tokens (limit {})",
                self._member_name,
                self._state.input_tokens,
                self._state.token_budget,
            )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Reject ordinary send_message calls once the budget is exhausted.

        A wrap-up send stays available: ``final_report=true`` or any send
        addressed to the ``user`` pseudo-member must still go through, or
        the leader could never deliver its concluding summary.
        """
        if ctx.extra.get("_skip_tool") or not self._is_send_message(ctx.inputs.tool_name):
            return
        reason = self._state.exceeded
        if not reason:
            return
        args = self._parse_args(ctx.inputs.tool_args)
        if self._is_wrapup_send(args):
            return
        self._reject_tool(ctx, self._state.reject_text())

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Count one successful send_message — session level, no exemptions.

        Unlike the debate cap there is no leader/report carve-out here: the
        runaway mode in ISS-4993 was *precisely* the shape the debate cap
        exempted (member→leader reports and leader broadcasts).
        """
        if not self._is_send_message(ctx.inputs.tool_name):
            return
        if ctx.extra.get("_skip_tool") or not self._tool_succeeded(ctx):
            return
        reason = await self._state.record_message()
        if reason:
            team_logger.warning(
                "[{}] session budget exhausted via message count: {} messages (limit {})",
                self._member_name,
                self._state.message_count,
                self._state.max_total_messages,
            )

    @staticmethod
    def _is_wrapup_send(args: dict[str, Any]) -> bool:
        """Whether this send_message is a wrap-up (final report / user reply)."""
        if args.get("final_report") is True:
            return True
        raw_to = args.get("to")
        targets = [raw_to] if isinstance(raw_to, str) else raw_to if isinstance(raw_to, list) else []
        return any(
            isinstance(target, str) and target.strip() == USER_PSEUDO_MEMBER_NAME
            for target in targets
        )

    @staticmethod
    def _is_send_message(tool_name: Any) -> bool:
        return isinstance(tool_name, str) and tool_name.rsplit(".", 1)[-1] == "send_message"

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


__all__ = [
    "REASON_MAX_TOTAL_MESSAGES",
    "REASON_MAX_TOTAL_ROUNDS",
    "REASON_TOKEN_BUDGET",
    "SessionBudgetRail",
    "SessionBudgetState",
]
