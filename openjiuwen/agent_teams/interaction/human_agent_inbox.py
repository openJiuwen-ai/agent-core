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
    """Raised when a caller tries to speak as a human agent on a
    team that has no human-agent member registered at all."""


class UnknownHumanAgentError(RuntimeError):
    """Raised when ``sender`` is not a registered human-agent member.

    Distinct from ``HumanAgentNotEnabledError``: the team has HITT on,
    but the specific sender the caller picked does not exist.
    """


class HumanAgentInbox:
    """Route human-agent speech onto the team's message bus.

    Holds a reference to ``TeamBackend`` so the entry points can verify
    the team has at least one human agent registered and that the
    chosen ``sender`` is one of them. Unregistered senders raise
    ``UnknownHumanAgentError`` instead of silently injecting a rogue
    identity into the message log.
    """

    def __init__(self, team: "TeamBackend", message_manager: "TeamMessageManager"):
        self._team = team
        self._mm = message_manager

    def _resolve_sender(self, sender: Optional[str]) -> str:
        """Pick a sender and verify it is a registered human-agent member.

        Defaults to the first registered human agent when ``sender`` is
        omitted so single-human teams keep the minimal call form
        ``inbox.send(body)`` working.
        """
        names = self._team.human_agent_names()
        if not names:
            raise HumanAgentNotEnabledError(
                "No human-agent member is registered on this team; "
                "create the team with enable_hitt=True or declare "
                "TeamMemberSpec(role_type=TeamRole.HUMAN_AGENT, ...) "
                "entries in predefined_members"
            )
        if sender is None:
            # Deterministic default: the reserved name if present,
            # otherwise the lexicographically first registered one.
            if HUMAN_AGENT_MEMBER_NAME in names:
                return HUMAN_AGENT_MEMBER_NAME
            return sorted(names)[0]
        if sender not in names:
            raise UnknownHumanAgentError(
                f"'{sender}' is not a registered human-agent member; "
                f"registered members: {sorted(names)}"
            )
        return sender

    async def send(
        self,
        body: str,
        to: Optional[str] = None,
        *,
        sender: Optional[str] = None,
    ) -> Optional[str]:
        """Post a message as a human-agent member.

        Args:
            body: Message content.
            to: Target member name. ``None`` broadcasts; otherwise
                writes a point-to-point message.
            sender: Member name of the human agent speaking. Optional
                on single-human teams; required when the team declares
                multiple human-agent members.
        """
        resolved_sender = self._resolve_sender(sender)
        team_logger.debug(
            "HumanAgentInbox: sending as %s, to=%s", resolved_sender, to or "*"
        )
        if to is None:
            return await self._mm.broadcast_message(
                content=body,
                from_member_name=resolved_sender,
            )
        return await self._mm.send_message(
            content=body,
            to_member_name=to,
            from_member_name=resolved_sender,
        )


__all__ = [
    "HumanAgentInbox",
    "HumanAgentNotEnabledError",
    "UnknownHumanAgentError",
]
