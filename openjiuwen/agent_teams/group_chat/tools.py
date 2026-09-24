# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public conversation tools with backend-bound author and team/session."""
from typing import Any

from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class GroupSendMessageTool(TeamTool):
    def __init__(self, backend, t):
        super().__init__(ToolCard(id="team.group_send_message", name="group_send_message",
                                 description=t("group_send_message")))
        self.backend = backend
        self.card.input_params = {"type": "object", "properties": {
            "content": {"type": "string"}, "client_message_id": {"type": "string"},
            "mentions": {"type": "array", "items": {"type": "string"}},
        }, "required": ["content", "client_message_id"], "additionalProperties": False}

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            if set(inputs) - {"content", "client_message_id", "mentions"}:
                raise ValueError("Only content, client_message_id and mentions are supported")
            result = await self.backend.append_group_message(
                self.backend.member_name, inputs["content"], client_message_id=inputs["client_message_id"],
                mentions=inputs.get("mentions", ()),
            )
            return ToolOutput(success=True, data=result.model_dump(mode="json"))
        except Exception as exc:
            team_logger.error("Could not post group message: {}", exc)
            return ToolOutput(success=False, error=str(exc))


def create_group_chat_tools(backend, t) -> list[TeamTool]:
    if not backend.group_chat_spec.enable_group_chat:
        return []
    return [GroupSendMessageTool(backend, t)]


def group_chat_prompt(spec) -> str:
    from openjiuwen.agent_teams.tools.locales import make_translator

    return "\n\n" + make_translator(spec.language or "cn")("group_role") if spec.enable_group_chat else ""
