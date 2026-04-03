# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agents-as-Tools hierarchical multi-agent team."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Optional, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.teams.hierarchical_tools.hierarchical_config import (
    HierarchicalTeamConfig,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.multi_agent.teams.utils import (
    standalone_invoke_context,
    standalone_stream_context,
)
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

if TYPE_CHECKING:
    from openjiuwen.core.runner.resources_manager.base import AgentProvider


class HierarchicalTeam(BaseTeam):
    """Agents-as-Tools multi-agent team.

    Agents are composed hierarchically via each agent's ability_manager.
    The root (entry point) agent is determined by:

    ``config.root_agent`` if provided in :class:`HierarchicalTeamConfig`.
    """

    def __init__(
        self,
        card: TeamCard,
        config: HierarchicalTeamConfig,
        runtime=None
    ):
        super().__init__(card, config, runtime)
        # root_agent is required in config.
        self._root_agent_id: str = self.config.root_agent.id
        self._pending_children: dict[str, list[AgentCard]] = {}

        logger.debug(
            f"[{self.__class__.__name__}] Initialized with team_id: {self.card.id}, "
            f"root_agent_id: {self._root_agent_id}"
        )

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def add_agent(
        self,
        card: AgentCard,
        provider: "AgentProvider",
        parent_agent_id: Optional[str] = None
    ) -> "HierarchicalTeam":
        """Add an agent to the team.

        Args:
            card: AgentCard defining agent identity.
            provider: AgentProvider factory for lazy instance creation.
            parent_agent_id: Optional parent agent ID; registers card as a
                tool under the parent's ability_manager before first run.

        Returns:
            self (supports method chaining).
        """
        super().add_agent(card, provider)

        if parent_agent_id:
            self._pending_children.setdefault(parent_agent_id, []).append(card)
            logger.debug(
                f"[{self.__class__.__name__}] Queued {card.id} "
                f"as child of {parent_agent_id}"
            )

        return self

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_ready(self) -> None:
        """Raise if the root agent is not yet registered in runtime."""
        if not self.runtime.has_agent(self._root_agent_id):
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                msg=(
                    f"Root agent '{self._root_agent_id}' is not registered in runtime. "
                    "Call add_agent(root_card, root_provider) before invoke()/stream()."
                ),
            )

    async def _setup_hierarchy(self) -> None:
        if not self._pending_children:
            return
        from openjiuwen.core.runner import Runner
        for parent_id, child_cards in self._pending_children.items():
            parent_agent = await Runner.resource_mgr.get_agent(agent_id=parent_id)
            for child_card in child_cards:
                parent_agent.ability_manager.add(child_card)
                logger.debug(
                    f"[{self.__class__.__name__}] Registered {child_card.id} "
                    f"-> {parent_id}.ability_manager"
                )
        self._pending_children.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def invoke(self, inputs: Any, session=None) -> Any:
        """Run the team from the root agent.

        Args:
            inputs: Input message or dict.
            session: External AgentTeamSession, or None for standalone use.

        Returns:
            Final result from the root agent.
        """
        self._assert_ready()
        await self._setup_hierarchy()
        async with standalone_invoke_context(
            self.runtime, self.card, inputs, session
        ) as (team_session, sid):
            return await self.runtime.send(
                message=inputs,
                recipient=self._root_agent_id,
                sender=self.card.id,
                session_id=sid,
            )

    async def stream(self, inputs: Any, session=None) -> AsyncGenerator[Any, None]:
        """Run the team from the root agent with streaming output.

        Args:
            inputs: Input message or dict.
            session: External AgentTeamSession, or None for standalone use.

        Yields:
            Streaming output chunks.
        """
        self._assert_ready()
        await self._setup_hierarchy()

        async def _run(team_session: Session, sid: str) -> None:
            await self.runtime.send(
                message=inputs,
                recipient=self._root_agent_id,
                sender=self.card.id,
                session_id=sid,
            )

        async for chunk in standalone_stream_context(
            self.runtime, self.card, inputs, _run, session
        ):
            yield chunk
