# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MoA startup and owned-resource lifecycle at the DeepAgent boundary."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentRail, init_rail
from openjiuwen.harness.rails.moa_rail import MoARail


class MoAIntegration:
    """Keep MoA-specific startup policy out of the host's rail registration loop."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.rails = list({id(rail): rail for rail in agent.find_rails_by_type((MoARail,))}.values())

    def prepare(self) -> None:
        """Select the native loader after every factory rail has been queued."""
        for rail in self.rails:
            rail.prepare_skill_loading(self.agent)

    async def finalize(self, initialized_rails: list[AgentRail]) -> None:
        """Refresh bindings on retained rails and validate before the first request."""
        for rail in self.rails:
            if rail not in initialized_rails:
                init_rail(rail, self.agent)
            await rail.validate_skill_loading(refresh=True)

    async def rollback(self, initializing_rail: AgentRail | None) -> None:
        """Withdraw MoA resources while preserving successfully registered user rails."""
        agent = self.agent
        if not self.rails:
            return
        owned = [item for rail in self.rails for item in (rail, rail.owned_skill_rail)]
        if initializing_rail is not None:
            await agent.unregister_rail(initializing_rail)
            if initializing_rail not in owned:
                agent.add_rail(initializing_rail)
        for rail in self.rails:
            await agent.unregister_rail(rail)
            agent.add_rail(rail)
        # Successful user callbacks are already live; replaying their pending
        # entries on retry would register the same callback a second time.
        for rail in agent.configured_rails():
            if agent.is_registered_rail(rail):
                agent.remove_pending_rail(rail)

    @staticmethod
    async def retire_companions(agent: Any, rail: AgentRail) -> None:
        """Retire only the native loader created by this MoA instance."""
        if isinstance(rail, MoARail) and rail.owned_skill_rail is not None:
            await agent.unregister_rail(rail.owned_skill_rail)
