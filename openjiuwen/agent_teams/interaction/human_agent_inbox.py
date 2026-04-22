# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Human-agent-side inbox: let the human collaborator speak into the team.

The human operator drives this inbox when acting as the reserved
``human_agent`` team member. Every call is converted into a
``send_message`` from ``human_agent`` on the team's message bus — the
only communication channel human_agent is allowed to use, per the HITT
contract.

A separate module (and a separate error class) makes the difference
from ``UserInbox`` explicit: UserInbox posts as ``user``; this one
posts as ``human_agent`` and refuses to work when HITT is off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from openjiuwen.agent_teams.constants import HUMAN_AGENT_MEMBER_NAME
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager
    from openjiuwen.agent_teams.tools.team import TeamBackend


class HumanAgentNotEnabledError(RuntimeError):
    """Raised when someone tries to act as human_agent on a non-HITT team."""


class HumanAgentInbox:
    """Route human_agent speech onto the team's message bus.

    Holds a reference to ``TeamBackend`` so the entry points can
    check ``hitt_enabled()`` before writing — calls on a non-HITT
    team raise ``HumanAgentNotEnabledError`` instead of silently
    injecting a rogue ``human_agent`` identity.
    """

    def __init__(self, team: "TeamBackend", message_manager: "TeamMessageManager"):
        self._team = team
        self._mm = message_manager

    def _ensure_enabled(self) -> None:
        if not self._team.hitt_enabled():
            raise HumanAgentNotEnabledError(
                "human_agent is not registered on this team; "
                "create the team with enable_hitt=True or call "
                "build_team(enable_hitt=True)"
            )

    async def send(self, body: str, to: Optional[str] = None) -> Optional[str]:
        """Post a message as ``human_agent``.

        Args:
            body: Message content.
            to: Target member name. ``None`` broadcasts; otherwise
                writes a point-to-point message.
        """
        self._ensure_enabled()
        team_logger.debug(f"HumanAgentInbox: sending as human_agent, to={to or '*'}")
        if to is None:
            return await self._mm.broadcast_message(
                content=body,
                from_member_name=HUMAN_AGENT_MEMBER_NAME,
            )
        return await self._mm.send_message(
            content=body,
            to_member_name=to,
            from_member_name=HUMAN_AGENT_MEMBER_NAME,
        )


__all__ = [
    "HumanAgentInbox",
    "HumanAgentNotEnabledError",
]
