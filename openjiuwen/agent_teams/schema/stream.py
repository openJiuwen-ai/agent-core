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

from openjiuwen.core.session.stream.base import OutputSchema


class TeamOutputSchema(OutputSchema):
    """OutputSchema extended with the source-member identity.

    ``source_member`` carries the ``member_name`` of the team member
    that produced this chunk. ``None`` indicates the producer is not
    associated with a team (e.g. plain ``OutputSchema`` upstream).
    """

    source_member: str | None = None

    @classmethod
    def from_output(cls, base: OutputSchema, *, source_member: str | None) -> "TeamOutputSchema":
        """Build a tagged team chunk from a plain OutputSchema instance.

        Returns a new instance; the original ``base`` is not mutated so
        DeepAgent internals retain their object identity.
        """
        return cls(**base.model_dump(), source_member=source_member)


__all__ = ["TeamOutputSchema"]
