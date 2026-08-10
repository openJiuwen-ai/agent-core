# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-layer streaming schema extensions.

Subclassing :class:`OutputSchema` keeps the core stream layer free of
team-specific fields while letting team-layer consumers attribute each
chunk to the member that produced it (leader or in-process teammate).

Non-team producers (single agent, harness direct streaming) continue to
yield plain ``OutputSchema`` instances.
"""

from __future__ import annotations

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.session.stream.base import OutputSchema


class TeamOutputSchema(OutputSchema):
    """OutputSchema extended with the source-member identity and role.

    ``source_member`` carries the ``member_name`` of the team member
    that produced this chunk; ``role`` is that member's ``TeamRole``.
    Both default to ``None`` for non-team producers (e.g. plain
    ``OutputSchema`` upstream from single agent / harness paths).
    """

    source_member: str | None = None
    role: TeamRole | None = None

    @classmethod
    def from_output(
        cls,
        base: OutputSchema,
        *,
        source_member: str | None,
        role: TeamRole | None = None,
    ) -> "TeamOutputSchema":
        """Build a tagged team chunk from a plain OutputSchema instance.

        Returns a new instance; the original ``base`` is not mutated so
        DeepAgent internals retain their object identity.
        """
        return cls(
            type=base.type,
            index=base.index,
            payload=base.payload,
            source_member=source_member,
            role=role,
        )


def is_team_event_marker(chunk: object) -> bool:
    """Whether a chunk is a framework-emitted team event rather than agent output.

    Team markers (``team.idle`` / ``team.completed`` / ``team.interact.failed``)
    ride the same stream as model output so a streaming consumer sees them in
    order, but they carry no agent content. Non-streaming callers that reduce a
    stream to "the last thing produced" use this to skip them.

    Args:
        chunk: Any object taken off a member stream queue.

    Returns:
        True when the chunk is a team marker.
    """
    if not isinstance(chunk, TeamOutputSchema):
        return False
    payload = chunk.payload
    if not isinstance(payload, dict):
        return False
    event_type = payload.get("event_type")
    return isinstance(event_type, str) and event_type.startswith("team.")


__all__ = ["TeamOutputSchema", "is_team_event_marker"]
