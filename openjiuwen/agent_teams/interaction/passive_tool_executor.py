# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run a whitelisted team tool as a passive human, without using the leader's identity."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import MappedToolOutput
from openjiuwen.agent_teams.tools.tool_factory import _SEND_MESSAGE_CLASS, create_team_tools
from openjiuwen.agent_teams.tools.tool_permissions import HUMAN_AGENT_TOOLS
from openjiuwen.agent_teams.tools.tool_task import ClaimTaskTool
from openjiuwen.harness.tools.base_tool import ToolOutput


class PassiveToolExecutor:
    """Execute one passive-human tool call against managers bound to that sender."""

    def __init__(self, backend: TeamBackend, *, language: str = "cn") -> None:
        self._backend = backend
        self._language = language or "cn"
        self._tools: dict[str, dict[str, Any]] = {}

    async def execute(self, sender: str, tool_name: str, tool_args: dict[str, Any] | None) -> ToolOutput:
        """Run ``tool_name`` as ``sender``. Unknown names stay inside the whitelist error."""
        allowed = set(HUMAN_AGENT_TOOLS)
        if self._backend.dispatch_mode == "autonomous":
            allowed.add("claim_task")
        if tool_name not in allowed:
            return ToolOutput(success=False, error=f"tool_not_permitted:{tool_name}")
        tool = self._tool(sender, tool_name)
        if tool is None:
            return ToolOutput(success=False, error=f"tool_not_permitted:{tool_name}")
        result = await tool.invoke(dict(tool_args or {}))
        if result.success and not isinstance(result, MappedToolOutput):
            return MappedToolOutput.from_output(result, tool.map_result(result))
        return result

    def _tool(self, sender: str, tool_name: str) -> Any:
        cached = self._tools.get(sender)
        if cached is None:
            cached = self._build(sender)
            self._tools[sender] = cached
        return cached.get(tool_name)

    def _build(self, sender: str) -> dict[str, Any]:
        backend = self._sender_backend(sender)
        translator = make_translator(self._language)
        tools = {
            tool.card.name: tool
            for tool in create_team_tools(
                role="human_agent",
                agent_team=backend,
                dispatch_mode=self._backend.dispatch_mode,
                lang=self._language,
            )
        }
        if self._backend.dispatch_mode == "autonomous":
            tools["claim_task"] = ClaimTaskTool(backend.task_manager, translator)
        else:
            tools["send_message"] = _SEND_MESSAGE_CLASS[("scheduled", "member")](
                backend.message_manager,
                translator,
                team=backend,
            )
        return tools

    def _sender_backend(self, sender: str) -> TeamBackend:
        leader = self._backend
        backend = TeamBackend(
            team_name=leader.team_name,
            member_name=sender,
            is_leader=False,
            db=leader.db,
            messager=leader.messager,
            dispatch_mode=leader.dispatch_mode,
            enable_hitt=leader.hitt_enabled(),
            leader_member_name=leader.leader_member_name,
        )
        backend.task_manager = TeamTaskManager(
            leader.team_name,
            sender,
            leader.db,
            leader.messager,
            leader_member_name=leader.leader_member_name,
            dispatch_mode=leader.dispatch_mode,
        )
        backend.message_manager = backend.message_manager.__class__(
            leader.team_name,
            sender,
            leader.db,
            leader.messager,
        )
        return backend
