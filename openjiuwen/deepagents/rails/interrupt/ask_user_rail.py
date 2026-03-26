# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations
from typing import Any, Iterable, Mapping, Optional
from pydantic import BaseModel, Field
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt import InterruptRequest
from openjiuwen.core.single_agent.rail import AgentCallbackContext
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.prompts import resolve_language
from openjiuwen.deepagents.rails.interrupt.interrupt_base import BaseInterruptRail, InterruptDecision


class AskUserPayload(BaseModel):
    """Payload for user input response."""
    answer: str = Field(default="", description="answer")

    @classmethod
    def to_schema(cls) -> dict:
        return cls.model_json_schema()


class AskUserRequest(BaseModel):
    """Ask-user request configuration for a tool."""
    message: str = Field(default="Please input", description="The question to present to the user.")
    payload_schema: dict = Field(default_factory=lambda: AskUserPayload.to_schema())


class AskUserTool(Tool):
    def __init__(self, language: str = "cn"):
        super().__init__(
            build_tool_card(
                name="ask_user",
                tool_id="ask_user",
                language=language,
            )
        )

    async def invoke(self, query, **kwargs):
        return {}

    async def stream(self, query, **kwargs):
        yield {}


class AskUserRail(BaseInterruptRail):
    """Ask-user rail: returns user input without executing tool.
    
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
        self.request = AskUserRequest()

    def init(self, agent):
        """Initialize the ask_user tool."""
        language = resolve_language()
        self.tools = [
            AskUserTool(language=language),
        ]
        from openjiuwen.core.runner.runner import Runner
        Runner.resource_mgr.add_tool(self.tools)

        for tool in self.tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent):
        if self.tools:
            for tool in self.tools:
                name = getattr(tool.card, 'name', None)
                if name and hasattr(agent, 'ability_manager'):
                    agent.ability_manager.remove(name)

                tool_id = tool.card.id
                if tool_id:
                    from openjiuwen.core.runner.runner import Runner
                    Runner.resource_mgr.remove_tool(tool_id)

    async def resolve_interrupt(
            self,
            ctx: AgentCallbackContext,
            tool_call: Optional[ToolCall],
            user_input: Optional[Any],
    ) -> InterruptDecision:
        if user_input is None:
            return self.interrupt(self._build_ask_request(tool_call))

        try:
            if isinstance(user_input, AskUserPayload):
                payload = user_input
            elif isinstance(user_input, dict):
                payload = AskUserPayload.model_validate(user_input)
            elif isinstance(user_input, str):
                payload = AskUserPayload(answer=user_input)
            else:
                return self.interrupt(self._build_ask_request(tool_call))
        except Exception:
            return self.interrupt(self._build_ask_request(tool_call))

        return self.reject(tool_result=payload.answer)

    def _build_ask_request(self, tool_call: Optional[ToolCall]) -> InterruptRequest:
        query = ""
        if tool_call is not None:
            args = tool_call.arguments
            if isinstance(args, Mapping):
                query = str(args.get("query", ""))
        message = query or self.request.message
        payload_schema = self.request.payload_schema
        return InterruptRequest(message=message, payload_schema=payload_schema)
