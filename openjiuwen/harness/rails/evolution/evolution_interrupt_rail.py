# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Active mutation approval rail for Skill evolution tools."""

from __future__ import annotations

import json
from typing import Any, Literal

from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.evolution.review.runtime import (
    EvolutionProposalSelection,
    EvolutionReviewRuntime,
)
from openjiuwen.harness.rails.interrupt.interrupt_base import (
    BaseInterruptRail,
    InterruptDecision,
    RejectResult,
)
from openjiuwen.harness.tools.base_tool import ToolOutput

_ACTIVE_EVOLUTION_TOOL_OPERATIONS: dict[str, Literal["evolve", "simplify"]] = {
    "evolve_skill_experiences": "evolve",
    "simplify_skill_experiences": "simplify",
}


class EvolutionInterruptRail(BaseInterruptRail):
    """Interrupt active Skill evolution mutation tools for user approval."""

    priority = 89

    def __init__(
        self,
        *,
        review_runtime: EvolutionReviewRuntime | None = None,
        submission_service: Any | None = None,
        auto_save: bool = False,
        language: str = "cn",
    ) -> None:
        super().__init__(tool_names=_ACTIVE_EVOLUTION_TOOL_OPERATIONS)
        self._review_runtime = review_runtime
        self._submission_service = submission_service
        self._auto_save = bool(auto_save)
        self._language = language

    def configure(
        self,
        *,
        review_runtime: EvolutionReviewRuntime,
        submission_service: Any,
        auto_save: bool,
        language: str,
    ) -> None:
        """Bind runtime dependencies supplied by SkillEvolutionRail."""
        self._review_runtime = review_runtime
        self._submission_service = submission_service
        self._auto_save = bool(auto_save)
        self._language = language

    @property
    def auto_save(self) -> bool:
        """Whether active evolution tool calls bypass user approval."""
        return self._auto_save

    @auto_save.setter
    def auto_save(self, value: bool) -> None:
        self._auto_save = bool(value)

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: ToolCall | None,
        user_input: Any | None,
        auto_confirm_config: dict | None = None,
    ) -> InterruptDecision:
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        operation = _ACTIVE_EVOLUTION_TOOL_OPERATIONS[tool_name]
        try:
            args = self._active_tool_args(ctx)
            subject = self._normalize_subject(args.get("subject")).to_payload()
            resolved = await self._preflight(operation=operation, args=args, subject=subject, ctx=ctx)
            self._replace_active_tool_args(ctx, args)
        except Exception as exc:
            return self._reject_tool(ctx, str(exc), data=self._error_data(tool_name, ctx))

        if self._auto_save:
            return self.approve()

        if user_input is not None:
            return self._resolve_user_input(ctx, tool_name, user_input)

        auto_confirm_key = self._active_auto_confirm_key(tool_name, subject)
        if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
            return self.approve()

        message = self._render_approval_message(operation=operation, args=args, subject=subject, resolved=resolved)
        return self.interrupt(
            InterruptRequest(
                message=message,
                payload_schema=self._action_payload_schema(),
                auto_confirm_key=auto_confirm_key,
                ui_options=self._approval_ui_options(),
            )
        )

    async def _preflight(
        self,
        *,
        operation: Literal["evolve", "simplify"],
        args: dict[str, Any],
        subject: dict[str, Any],
        ctx: AgentCallbackContext,
    ) -> EvolutionProposalSelection | None:
        if self._review_runtime is None or self._submission_service is None:
            raise ValueError("EvolutionInterruptRail is not configured")
        if operation == "evolve":
            try:
                return self._submission_service.prepare_evolve_submission(
                    review_runtime=self._review_runtime,
                    evolution_review_ref=str(args.get("evolution_review_ref") or ""),
                    subject=subject,
                    selected_proposal_ids=list(args.get("selected_proposal_ids") or []),
                    session_id=self._session_id(ctx),
                )
            except KeyError as exc:
                raise ValueError("unknown or expired evolution_review_ref") from exc
        await self._submission_service.prepare_simplify_submission(subject, list(args.get("actions") or []))
        return None

    def _resolve_user_input(
        self,
        ctx: AgentCallbackContext,
        tool_name: str,
        user_input: Any,
    ) -> InterruptDecision:
        payload = user_input if isinstance(user_input, dict) else {}
        action = str(payload.get("action") or "").lower()
        if action == "allow_once":
            return self.approve()
        if action == "allow_always":
            self._store_auto_confirm(ctx, tool_name)
            return self.approve()
        if action == "reject":
            return self._reject_tool(
                ctx,
                str(payload.get("feedback") or "evolution tool call rejected by user"),
                data={"tool_name": tool_name, "tool_call_id": self._active_tool_call_id(ctx)},
            )
        return self.interrupt(
            InterruptRequest(
                message="Invalid evolution approval action.",
                payload_schema=self._action_payload_schema(),
                ui_options=self._approval_ui_options(),
            )
        )

    def _reject_tool(
        self,
        ctx: AgentCallbackContext,
        error: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> RejectResult:
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "")
        tool_call_id = self._active_tool_call_id(ctx)
        result = ToolOutput(success=False, error=error, data=data or {"tool_name": tool_name})
        return RejectResult(
            tool_result=result,
            tool_message=ToolMessage(content=error, tool_call_id=tool_call_id, name=tool_name),
        )

    def _render_approval_message(
        self,
        *,
        operation: Literal["evolve", "simplify"],
        args: dict[str, Any],
        subject: dict[str, Any],
        resolved: EvolutionProposalSelection | None,
    ) -> str:
        subject_name = str(subject.get("name") or "unknown")
        subject_kind = str(subject.get("kind") or "skill")
        if operation == "evolve":
            proposals = list(resolved.proposals if resolved is not None else ())
            count = len(proposals)
            if self._is_english():
                lines = [
                    f"Approve {count} skill evolution experience(s) for `{subject_name}` ({subject_kind})?",
                ]
            else:
                lines = [
                    f"是否批准 `{subject_name}` ({subject_kind}) 的 {count} 条 Skill 演进经验？",
                ]
            if proposals:
                lines.append("")
            for proposal in resolved.proposals if resolved is not None else ():
                lines.append(f"- {self._text_or(proposal.summary, proposal.proposal_id)}")
                for label, value in (
                    ("target", proposal.target),
                    ("section", proposal.section),
                    ("reason", proposal.reason),
                    ("content", proposal.content),
                ):
                    text = self._preview(value)
                    if text:
                        lines.append(f"  - {label}: {text}")
            return "\n".join(lines)

        actions = [item for item in args.get("actions", []) if isinstance(item, dict)]
        count = len(actions)
        if self._is_english():
            lines = [
                f"Approve {count} skill experience simplification action(s) for `{subject_name}` ({subject_kind})?"
            ]
        else:
            lines = [f"是否批准 `{subject_name}` ({subject_kind}) 的 {count} 项 Skill 经验精简操作？"]
        if actions:
            lines.append("")
        for action in actions:
            title = self._text_or(action.get("action"), action.get("kind"), action.get("record_id"), "action")
            lines.append(f"- {title}")
            for label, value in (("record_id", action.get("record_id")), ("reason", action.get("reason"))):
                text = self._preview(value)
                if text:
                    lines.append(f"  - {label}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _text_or(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _preview(value: Any, *, limit: int = 240) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _approval_ui_options(self) -> list[dict[str, str]]:
        if self._is_english():
            return [
                {"label": "Allow Once", "value": "allow_once", "description": "Allow this skill evolution change"},
                {
                    "label": "Always Allow",
                    "value": "allow_always",
                    "description": "Automatically allow future matching skill evolution changes",
                },
                {"label": "Reject", "value": "reject", "description": "Skip this skill evolution change"},
            ]
        return [
            {"label": "本次允许", "value": "allow_once", "description": "允许本次技能演进变更执行"},
            {"label": "总是允许", "value": "allow_always", "description": "自动允许后续匹配的技能演进变更"},
            {"label": "拒绝", "value": "reject", "description": "跳过本次技能演进变更"},
        ]

    @staticmethod
    def _action_payload_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["allow_once", "allow_always", "reject"]},
                "feedback": {"type": "string"},
            },
            "required": ["action"],
        }

    @staticmethod
    def _active_tool_args(ctx: AgentCallbackContext) -> dict[str, Any]:
        raw_args = getattr(ctx.inputs, "tool_args", {}) or {}
        return json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

    @staticmethod
    def _replace_active_tool_args(ctx: AgentCallbackContext, args: dict[str, Any]) -> None:
        serialized = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
        ctx.inputs.tool_args = serialized
        tool_call = getattr(ctx.inputs, "tool_call", None)
        if isinstance(tool_call, ToolCall):
            tool_call.arguments = serialized

    @staticmethod
    def _active_tool_call_id(ctx: AgentCallbackContext) -> str:
        inputs = ctx.inputs
        for attr in ("tool_call_id", "call_id"):
            value = getattr(inputs, attr, None)
            if value:
                return str(value)
        tool_call = getattr(inputs, "tool_call", None)
        value = getattr(tool_call, "id", None)
        return str(value or getattr(inputs, "tool_name", ""))

    @staticmethod
    def _active_auto_confirm_key(tool_name: str, subject: dict[str, Any]) -> str:
        return f"evolution:{tool_name}:{subject['kind']}:{subject['name']}"

    @staticmethod
    def _is_auto_confirmed(auto_confirm_config: dict | None, key: str) -> bool:
        if isinstance(auto_confirm_config, dict):
            return auto_confirm_config.get(key) is True
        return False

    def _store_auto_confirm(self, ctx: AgentCallbackContext, tool_name: str) -> None:
        session = getattr(ctx, "session", None)
        if session is None or not hasattr(session, "get_state") or not hasattr(session, "update_state"):
            return
        subject = self._normalize_subject(self._active_tool_args(ctx).get("subject")).to_payload()
        config = session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
        config[self._active_auto_confirm_key(tool_name, subject)] = True
        session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})

    @staticmethod
    def _session_id(ctx: AgentCallbackContext) -> str:
        return str(getattr(ctx.inputs, "conversation_id", "") or "")

    def _is_english(self) -> bool:
        return str(self._language).lower() == "en"

    def _normalize_subject(self, subject: Any):
        if self._review_runtime is None or self._submission_service is None:
            raise ValueError("EvolutionInterruptRail is not configured")
        return normalize_subject(subject)

    def _error_data(self, tool_name: str, ctx: AgentCallbackContext) -> dict[str, Any]:
        args = self._active_tool_args(ctx)
        return {
            "tool_name": tool_name,
            "evolution_review_ref": str(args.get("evolution_review_ref") or ""),
            "selected_proposal_ids": list(args.get("selected_proposal_ids") or []),
        }


__all__ = ["EvolutionInterruptRail"]
