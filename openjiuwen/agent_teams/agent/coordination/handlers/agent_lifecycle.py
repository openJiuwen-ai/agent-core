# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Agent-lifecycle coordination events.

Owns the inner USER_INPUT bootstrap path plus the TEAM_STANDBY /
TEAM_CLEANED / TOOL_APPROVAL_RESULT signals that pause / tear down /
resume the local agent. Stateless — defers to ``DispatcherHost`` for
all behavior.
"""

from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    InnerEventMessage,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.schema.events import EventMessage, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class AgentLifecycleHandler(BaseCoordinationHandler):
    """Handle USER_INPUT / STANDBY / CLEANED / TOOL_APPROVAL_RESULT.

    These events drive the local agent's lifecycle directly: bootstrap
    user input, pause polling on team standby, tear down on team
    cleanup (non-leader only), resume HITL interrupt on tool-approval
    result.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        # Inner events (InnerEventType is a str-Enum; use .value for pure str)
        InnerEventType.USER_INPUT.value: "on_user_input",
        # Lifecycle (TeamEvent members are bare str constants, no .value)
        TeamEvent.STANDBY: "on_standby",
        TeamEvent.CLEANED: "on_cleaned",
        # Tool approval
        TeamEvent.TOOL_APPROVAL_RESULT: "on_tool_approval_result",
    }

    async def on_user_input(self, event: InnerEventMessage) -> None:
        """Forward ``coordination bootstrap`` user input to the agent.

        Routing decisions (``@<member> body`` etc.) happen at the
        runtime dispatch boundary in
        ``TeamRuntimeManager._dispatch_god_view`` — by the time input
        reaches the inner event bus it is already aimed at this agent.
        """
        content = event.payload.get("content", "")
        team_logger.info("user_input → deliver_input")
        await self._round.deliver_input(content)

    async def on_standby(self, event: EventMessage) -> None:
        """Pause periodic polling on TEAM_STANDBY."""
        member_name = self._blueprint.member_name
        team_logger.info("[{}] received TEAM_STANDBY, pausing polls", member_name)
        await self._poll.pause_polls()

    async def on_cleaned(self, event: EventMessage) -> None:
        """Tear down on TEAM_CLEANED for non-leader members.

        The leader must NEVER ``shutdown_self`` from its own CLEANED
        event: persistent leaders have to survive ``clean_team`` to
        accept the next interaction, and the teardown for temporary
        leaders is handled by the natural ``_finalize_round`` path
        instead. Skipping the leader branch is defense in depth on top
        of the sender_id self-filter at transport level. Teammates and
        human-agent avatars must abandon their loop here so they don't
        spin forever waiting for events on a dead team.
        """
        member_name = self._blueprint.member_name
        if self._blueprint.role == TeamRole.LEADER:
            team_logger.debug("[{}] ignoring TEAM_CLEANED on leader path", member_name)
            return
        team_logger.info("[{}] received TEAM_CLEANED, shutting down coordination", member_name)
        await self._lifecycle.shutdown_self()

    async def on_tool_approval_result(self, event: EventMessage) -> None:
        """Resume a teammate HITL interrupt from a structured approval event."""
        member_name = self._blueprint.member_name
        payload = event.get_payload()
        target_id = payload.member_name

        if target_id is None or target_id != member_name:
            return

        from openjiuwen.core.session import InteractiveInput

        interactive_input = InteractiveInput()
        interactive_input.update(
            payload.tool_call_id,
            {
                "approved": payload.approved,
                "feedback": payload.feedback,
                "auto_confirm": payload.auto_confirm,
            },
        )
        team_logger.debug(
            "[{}] received tool approval result for tool_call_id={}, approved={}",
            member_name,
            payload.tool_call_id,
            payload.approved,
        )
        await self._round.resume_interrupt(interactive_input)
