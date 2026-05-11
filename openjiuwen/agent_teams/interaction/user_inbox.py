# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""User-side inbox: route external input into the team runtime.

The external caller ("user") has three ways into a team:

* **deliver_to_leader** — plain text addressed to the leader's
  DeepAgent. Preserves the historical ``TeamAgent.invoke`` semantics
  so existing integrations keep working.
* **direct** — ``@member_name body`` becomes a point-to-point message
  written to the team's message bus with ``from_member_name="user"``.
* **broadcast** — explicit team-wide announcement from the user.

All three paths go through ``TeamMessageManager`` so the message ends
up in the same store as teammate-to-teammate traffic (searchable,
persisted, observable). No direct database writes here.

Every entry point returns a :class:`DeliverResult` so callers can react
to delivery failure (``ok=False``) without inspecting raw return codes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.agent_teams.interaction.payload import DeliverResult
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager


class UserInbox:
    """Stateless helper hooking user input into the team's message bus."""

    def __init__(self, message_manager: "TeamMessageManager"):
        self._mm = message_manager

    async def direct(self, target: str, body: str) -> DeliverResult:
        """Route ``@target body`` — point-to-point message from ``user``.

        Returns ``DeliverResult.success(msg_id)`` when the message was
        accepted by the bus, or ``DeliverResult.failure(reason)`` when
        delivery failed (e.g. unknown target). Caller is responsible
        for validating that ``target`` exists in the roster.
        """
        msg_id = await self._mm.send_message(
            content=body,
            to_member_name=target,
            from_member_name=USER_PSEUDO_MEMBER_NAME,
        )
        if msg_id is None:
            return DeliverResult.failure(f"send_failed:{target}")
        return DeliverResult.success(msg_id)

    async def broadcast(self, body: str) -> DeliverResult:
        """Team-wide announcement from the user.

        Overrides ``from_member_name`` to the "user" pseudo-member so
        recipients can distinguish external directives from leader
        broadcasts.
        """
        msg_id = await self._mm.broadcast_message(
            content=body,
            from_member_name=USER_PSEUDO_MEMBER_NAME,
        )
        if msg_id is None:
            return DeliverResult.failure("broadcast_failed")
        return DeliverResult.success(msg_id)

    @staticmethod
    async def deliver_to_leader(deliver_input, body: str) -> DeliverResult:
        """Preserve the historical default — hand input to the leader.

        Accepts the host's ``deliver_input`` coroutine (typed as callable
        to avoid a TeamAgent import cycle) and forwards verbatim. Returns
        a success result with ``message_id=None`` because this channel
        does not produce a bus message id.
        """
        team_logger.debug("UserInbox: delivering input to leader DeepAgent")
        try:
            await deliver_input(body)
        except Exception as e:
            return DeliverResult.failure(f"deliver_to_leader_failed:{e}")
        return DeliverResult.success(message_id=None)


__all__ = ["UserInbox"]
