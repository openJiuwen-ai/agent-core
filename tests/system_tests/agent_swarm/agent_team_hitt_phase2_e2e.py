# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HITT Phase-2 walkthrough — runtime API surface only, no live LLM.

Demonstrates the new ``HumanAgentInbox`` routing and the
``register_human_agent_inbound`` callback wiring without spinning up a
real model client. Use it as a copy-paste template for SDK integrations.

Run directly:
    python examples/agent_teams/agent_team_hitt_phase2_e2e.py

What it shows:
    1. Build a HITT-enabled team in inprocess spawn mode (no LLM cost).
    2. Send user input through the inbox using ``@<member>`` mention
       routing — message lands on the team bus from the human agent.
    3. Send user input *without* a mention — falls through to the
       avatar's DeepAgent ``deliver_input`` (logged here; the avatar
       does nothing without a real LLM but we exercise the path).
    4. When another teammate sends to the human agent, an
       ``on_inbound`` callback registered via ``TeamBackend`` fires so
       the SDK can deliver the message to the external user.

The script uses an in-memory database, in-process messager, and skips
the real LLM call by stubbing the avatar's ``deliver_input``. Replace
the stubs with a real model client to drive the agent in production.
The end-to-end Runner activation flow is omitted intentionally — this
example focuses on the new APIs themselves.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from openjiuwen.agent_teams.constants import HUMAN_AGENT_MEMBER_NAME
from openjiuwen.agent_teams.tools.team import CapabilityOverrides
from openjiuwen.agent_teams.interaction import (
    HumanAgentInboundEvent,
    HumanAgentInbox,
)
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    StorageSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
)
from openjiuwen.core.common.logging import team_logger

TEAM_NAME = "hitt-phase2-demo"


def build_spec() -> TeamAgentSpec:
    """Construct a HITT team spec without a real LLM model.

    HITT now requires the human roster to be declared explicitly — the
    framework no longer injects a default ``human_agent`` for free. This
    demo declares a single default human member to mirror the legacy
    convenience while exercising the new explicit contract.
    """
    return TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name=TEAM_NAME,
        spawn_mode="inprocess",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name=HUMAN_AGENT_MEMBER_NAME,
                display_name="Human Operator",
                role_type=TeamRole.HUMAN_AGENT,
                persona="External user proxy for the phase-2 inbox demo",
            ),
        ],
        storage=StorageSpec(type="memory"),
    )


def make_inbound_handler(label: str):
    """Return an ``on_inbound`` callback that prints what the user would see."""

    async def _handler(evt: HumanAgentInboundEvent) -> None:
        kind = "broadcast" if evt.broadcast else "direct"
        team_logger.info(
            "[%s] inbound %s from %s → %s: %s",
            label,
            kind,
            evt.sender,
            evt.member_name,
            evt.body,
        )

    return _handler


async def main() -> None:
    spec = build_spec()
    leader = spec.build()
    backend = leader.team_backend
    assert backend is not None

    # Materialize the team row + register the human agent so the
    # message manager has something to work with. In a real flow this
    # is what ``BuildTeamTool`` (or ``leader.team_backend.build_team``)
    # does, then the leader's startup sweep launches the avatar's
    # DeepAgent automatically.
    await backend.build_team(
        display_name="HITT Phase 2 Demo",
        desc="Showcasing the inbox routing API",
        leader_display_name="Leader",
        leader_desc="Coordinator",
        overrides=CapabilityOverrides(enable_hitt=True),
    )

    # Stub the avatar runtime so the LLM-driven inbox path is observable
    # without an API key. Replace with the real spawn path in production.
    avatar = AsyncMock()
    inbox = HumanAgentInbox(
        backend,
        backend.message_manager,
        agent_lookup=lambda name: avatar if name == HUMAN_AGENT_MEMBER_NAME else None,
    )

    # Register the team→user notification callback. In a real SDK this
    # would push to a websocket / queue / UI rendering layer.
    backend.register_human_agent_inbound(
        HUMAN_AGENT_MEMBER_NAME,
        make_inbound_handler("user-ui"),
    )

    # === Path 1: user → @member mention → team bus passthrough ===
    team_logger.info("=== Path 1: @<member> direct from human agent ===")
    res = await inbox.send(
        f"@{leader.member_name} ping you for a status update",
    )
    team_logger.info("Path 1 result: %s", res)

    # === Path 2: user → no mention → avatar's LLM deliver_input ===
    team_logger.info("=== Path 2: no mention → avatar LLM ===")
    res = await inbox.send(
        "Please summarise design.md and mark task #3 completed",
    )
    team_logger.info("Path 2 result: %s", res)
    assert avatar.deliver_input.await_count == 1, "expected the avatar to receive the LLM input"

    # === Path 3: leader → human agent → on_inbound notification ===
    team_logger.info("=== Path 3: leader → human agent (on_inbound fires) ===")
    msg_id = await backend.message_manager.send_message(
        content="leader poking the user",
        to_member_name=HUMAN_AGENT_MEMBER_NAME,
    )
    team_logger.info("Direct message id: %s", msg_id)

    # The leader-side dispatcher would normally consume the MESSAGE
    # event and call the registered on_inbound. We exercise the same
    # path manually here so the demo runs without a coordination loop.
    from openjiuwen.agent_teams.interaction.payload import HumanAgentInboundEvent

    callback = backend.get_human_agent_inbound(HUMAN_AGENT_MEMBER_NAME)
    assert callback is not None
    await callback(
        HumanAgentInboundEvent(
            member_name=HUMAN_AGENT_MEMBER_NAME,
            sender=leader.member_name,
            body="leader poking the user",
            broadcast=False,
            message_id=msg_id or "",
            timestamp=0,
        )
    )

    team_logger.info("Done. The 'user-ui' line above is the user-facing notification.")


if __name__ == "__main__":
    asyncio.run(main())
