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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from openjiuwen.agent_teams.constants import USER_PSEUDO_MEMBER_NAME
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.message_manager import TeamMessageManager


class UserInbox:
    """Stateless helper hooking user input into the team's message bus."""

    def __init__(self, message_manager: "TeamMessageManager"):
        self._mm = message_manager

    async def direct(self, target: str, body: str) -> Optional[str]:
        """Route ``@target body`` — point-to-point message from ``user``.

        Returns the message id on success, ``None`` on failure (mirrors
        ``TeamMessageManager.send_message``). Caller is responsible for
        validating that ``target`` exists in the roster.
        """
        return await self._mm.send_message(
            content=body,
            to_member_name=target,
            from_member_name=USER_PSEUDO_MEMBER_NAME,
        )

    async def broadcast(self, body: str) -> Optional[str]:
        """Team-wide announcement from the user.

        Overrides ``from_member_name`` to the "user" pseudo-member so
        recipients can distinguish external directives from leader
        broadcasts.
        """
        return await self._mm.broadcast_message(
            content=body,
            from_member_name=USER_PSEUDO_MEMBER_NAME,
        )

    @staticmethod
    async def deliver_to_leader(deliver_input, body: str) -> None:
        """Preserve the historical default — hand input to the leader.

        Accepts the host's ``deliver_input`` coroutine (typed as callable
        to avoid a TeamAgent import cycle) and forwards verbatim.
        """
        team_logger.debug("UserInbox: delivering input to leader DeepAgent")
        await deliver_input(body)


__all__ = ["UserInbox"]
