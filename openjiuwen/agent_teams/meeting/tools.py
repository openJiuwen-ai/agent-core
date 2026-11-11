# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit member-facing meeting controls; text admission stays with Team messaging."""

from dataclasses import asdict
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from openjiuwen.agent_teams.meeting.member import MeetingMemberClient
from openjiuwen.agent_teams.tools.locales import Translator, make_translator
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class _MeetingTool(TeamTool):
    def __init__(self, client: MeetingMemberClient, t: Translator, name: str, schema: dict[str, Any]) -> None:
        super().__init__(ToolCard(id=f"team.{name}", name=name, description=t(name), input_params=schema))
        self._client = client

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> ToolOutput:
        try:
            schema = self.card.input_params
            if not isinstance(inputs, dict) or set(inputs) - set(schema["properties"]):
                raise ValueError("Only the declared meeting tool arguments are supported")
            if set(schema.get("required", ())) - set(inputs):
                raise ValueError("Required meeting tool arguments are missing")
            return ToolOutput(success=True, data=await self._run(inputs))
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))
        except (BaseError, RuntimeError, OSError, SQLAlchemyError) as exc:
            team_logger.warning("Meeting tool %s failed: %s", self.card.name, exc)
            return ToolOutput(success=False, error="meeting_operation_failed")

    async def _run(self, inputs: dict[str, Any]) -> Any:
        raise NotImplementedError


class MeetingGetSpeakerTool(_MeetingTool):
    def __init__(self, client: MeetingMemberClient, t: Translator) -> None:
        super().__init__(
            client, t, "meeting_get_speaker",
            {"type": "object", "properties": {}, "additionalProperties": False},
        )

    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any] | None:
        request = await self._client.get_speaker()
        return asdict(request) if request is not None else None


class MeetingInterruptSpeechTool(_MeetingTool):
    def __init__(self, client: MeetingMemberClient, t: Translator) -> None:
        super().__init__(
            client, t, "meeting_interrupt_speech",
            {
                "type": "object",
                "properties": {"expected_token": {"type": "string", "minLength": 1}},
                "required": ["expected_token"],
                "additionalProperties": False,
            },
        )

    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any] | None:
        token = inputs["expected_token"]
        if not isinstance(token, str) or not token.strip():
            raise ValueError("expected_token must be a nonempty string")
        interruption = await self._client.interrupt_speech(expected_token=token)
        return asdict(interruption) if interruption is not None else None


async def create_meeting_tools(
    client: MeetingMemberClient, *, language: str = "en",
) -> dict[str, TeamTool]:
    """Build member-scoped observation and Leader interruption tools on host opt-in."""
    state = await client.get_state()
    if state is None or state.closed:
        raise ValueError("An open meeting is required before constructing member tools")
    t = make_translator(language)
    tools = [MeetingGetSpeakerTool(client, t)]
    if client.member_name == state.leader_member_name:
        tools.append(MeetingInterruptSpeechTool(client, t))
    return {tool.card.name: tool for tool in tools}


async def register_meeting_tools(
    client: MeetingMemberClient, harness: Any, *, language: str = "en",
) -> dict[str, TeamTool]:
    """Mount the explicit Meeting controls on a native TeamHarness."""
    add_tool = getattr(harness, "add_tool", None)
    if not callable(add_tool):
        raise TypeError("Meeting tool registration requires a native harness with add_tool")
    tools = await create_meeting_tools(client, language=language)
    from openjiuwen.agent_teams.rails.team_tool_rail import declare_team_tool_direct

    for tool in tools.values():
        declare_team_tool_direct(tool.card)
        add_tool(tool)
    return tools
