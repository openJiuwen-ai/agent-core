# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member tool that posts into the public group chat."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.group_chat.handler import post_message
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class GroupSendMessageTool(TeamTool):
    """Post one public message as the bound member."""

    def __init__(self, team: TeamBackend, t: Translator):
        super().__init__(
            ToolCard(
                id="team.group_send_message",
                name="group_send_message",
                description=t("group_send_message"),
            )
        )
        self.team = team
        self.card.input_params = {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": t("group_send_message", "content")},
                "client_message_id": {
                    "type": "string",
                    "description": t("group_send_message", "client_message_id"),
                },
                "mentions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": t("group_send_message", "mentions"),
                },
            },
            "required": ["content", "client_message_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            result = await post_message(
                self.team,
                sender=self.team.member_name,
                sender_name=self.team.member_name,
                content=inputs.get("content", ""),
                client_message_id=inputs.get("client_message_id", ""),
                mentions=inputs.get("mentions"),
            )
        except ValueError as exc:
            return ToolOutput(success=False, error=f"invalid_group_chat: {exc}")
        except (OSError, RuntimeError):
            return ToolOutput(success=False, error="group_chat_delivery_failed")
        return ToolOutput(success=True, data=result.model_dump())
