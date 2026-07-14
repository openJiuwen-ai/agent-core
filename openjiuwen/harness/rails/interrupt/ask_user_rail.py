# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import Any, Dict, Iterable, List, Mapping, Optional
from pydantic import BaseModel, Field
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt import InterruptRequest
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.tools.ask_user import AskUserTool
from openjiuwen.harness.rails.interrupt.interrupt_base import BaseInterruptRail, InterruptDecision


class AskUserPayload(BaseModel):
    """Payload for user input response."""
    answers: Dict[str, str] = Field(default_factory=dict, description="Question text to answer mapping")

    @classmethod
    def to_schema(cls) -> dict:
        return cls.model_json_schema()


class AskUserRequest(InterruptRequest):
    """Ask-user request configuration, extends InterruptRequest with questions."""
    questions: List[dict] = Field(default_factory=list, description="Questions to present to the user")


class AskUserRail(BaseInterruptRail):
    """Ask-user rail: returns user input without executing tool.

    Supports multi-question mode with structured selection.

    Usage:
        rail = AskUserRail()
        await agent.register_rail(rail)
    """

    def __init__(
            self,
            tool_names: Optional[Iterable[str]] = None,
    ):
        self.tools = None
        if tool_names is None:
            tool_names = ["ask_user"]
        super().__init__(tool_names=tool_names)

    def init(self, agent):
        """Initialize the ask_user tool."""
        language = resolve_language()
        agent_id = agent.card.id
        self.tools = [
            AskUserTool(language=language, agent_id=agent_id),
        ]
        from openjiuwen.core.runner.runner import Runner
        Runner.resource_mgr.add_tool(self.tools)

        for tool in self.tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent):
        if self.tools:
            for tool in self.tools:
                name = tool.card.name
                agent.ability_manager.remove(name)

                tool_id = tool.card.id
                from openjiuwen.core.runner.runner import Runner
                Runner.resource_mgr.remove_tool(tool_id)

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
            auto_confirm_config: Optional[dict] = None,
    ) -> InterruptDecision:
        if user_input is None:
            return self.interrupt(self._build_ask_request(tool_call))

        try:
            if isinstance(user_input, AskUserPayload):
                payload = user_input
            elif isinstance(user_input, dict):
                payload = self._parse_user_input_dict(user_input, tool_call)
            elif isinstance(user_input, str):
                if not user_input:
                    return self.interrupt(self._build_ask_request(tool_call))
                args = self._parse_tool_args(tool_call)
                questions = args.get("questions", [])
                if questions:
                    first_question = questions[0].get("question", "")
                    payload = AskUserPayload(answers={first_question: user_input})
                else:
                    return self.interrupt(self._build_ask_request(tool_call))
            else:
                return self.interrupt(self._build_ask_request(tool_call))
        except Exception:
            return self.interrupt(self._build_ask_request(tool_call))

        if not payload.answers:
            return self.interrupt(self._build_ask_request(tool_call))

        tool_result = self._format_tool_result(tool_call, payload)
        return self.reject(tool_result=tool_result)

    def _parse_user_input_dict(self, user_input: dict, tool_call: Optional[ToolCall]) -> AskUserPayload:
        """Parse user input dict to AskUserPayload."""
        if "answers" in user_input and isinstance(user_input["answers"], dict):
            return AskUserPayload(answers=user_input["answers"])
        args = self._parse_tool_args(tool_call)
        questions = args.get("questions", [])
        if questions and len(questions) == 1:
            first_question = questions[0].get("question", "")
            if "answer" in user_input:
                return AskUserPayload(answers={first_question: user_input["answer"]})
        return AskUserPayload()

    def _format_tool_result(self, tool_call: Optional[ToolCall], payload: AskUserPayload) -> str:
        """Format tool result in Claude Code style."""
        args = self._parse_tool_args(tool_call)
        questions = args.get("questions", [])

        if not questions:
            return str(payload.answers)

        answer_parts = []
        for q in questions:
            question_text = q.get("question", "")
            answer_value = payload.answers.get(question_text, "")
            answer_parts.append(f'"{question_text}"="{answer_value}"')
        answers_text = ", ".join(answer_parts)

        return (
            f"User has answered your questions: {answers_text}. "
            f"You can now continue with the user's answers in mind."
        )

    def _build_ask_request(self, tool_call: Optional[ToolCall]) -> AskUserRequest:
        """Build AskUserRequest with questions from tool call arguments."""
        args = self._parse_tool_args(tool_call)
        return AskUserRequest(
            message="",
            payload_schema=AskUserPayload.to_schema(),
            questions=args.get("questions", []),
        )

    @staticmethod
    def _parse_tool_args(tool_call: Optional[ToolCall]) -> dict:
        """Parse tool_call.arguments to dict."""
        if tool_call is None:
            return {}
        args = tool_call.arguments
        if isinstance(args, str):
            import json
            try:
                parsed = json.loads(args)
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                return {}
        if isinstance(args, Mapping):
            return dict(args)
        return {}
