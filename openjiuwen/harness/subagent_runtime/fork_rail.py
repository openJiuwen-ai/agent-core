# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Place a fork's task after the inherited seed, once."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail


class ForkInstructionRail(DeepAgentRail):
    """Own the fork task and the one-time seed plant.

    The seed is written into the fork session as ``background_messages`` before
    the first stream, which inserts it ahead of the user query. The query is
    ``task_description`` and is not part of the seed. Resume builds this rail
    with an empty seed because the fork checkpoint already has both.
    """

    inherit_to_subagents = False

    def __init__(self, task_description: str, seed: list[Any] | None = None) -> None:
        super().__init__()
        self.task_description = task_description
        self.pending_seed = list(seed or [])

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        session = getattr(ctx, "session", None)
        update_state = getattr(session, "update_state", None)
        if callable(update_state):
            update_state({"background_messages": None})
