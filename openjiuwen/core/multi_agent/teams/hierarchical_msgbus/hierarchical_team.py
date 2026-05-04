# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HierarchicalTeam -- messagebus-driven hierarchical multi-agent team."""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus import (
    HierarchicalTeamConfig,
)
from openjiuwen.core.multi_agent.teams.utils import (
    standalone_invoke_context,
    standalone_stream_context,
)
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

if TYPE_CHECKING:
    from openjiuwen.core.runner.resources_manager.base import AgentProvider


class HierarchicalTeam(BaseTeam):
    """Hierarchical multi-agent team driven by a supervisor agent."""

    def __init__(
        self,
        card: TeamCard,
        config: HierarchicalTeamConfig,
    ):
        super().__init__(card=card, config=config)
        self._supervisor_id: Optional[str] = self.config.supervisor_agent.id
        self._supervisor_instance: Optional[Any] = None

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def add_agent(
        self,
        card: AgentCard,
        provider: "AgentProvider",
    ) -> "HierarchicalTeam":
        """Register an agent (supervisor or sub-agent) into the team runtime.

        Args:
            card:     AgentCard for the agent.
            provider: Callable that returns a BaseAgent instance.

        Returns:
            self (supports method chaining)
        """
        super().add_agent(card, provider)
        if card.id == self._supervisor_id:
            logger.info(
                f"[{self.__class__.__name__}] Registered supervisor '{card.id}' "
                f"in team '{self.team_id}'"
            )
            if self.config.timeout is not None:
                self.runtime.set_p2p_timeout(self.config.timeout)
        return self

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_ready(self) -> None:
        """Raise if the team is not properly configured."""
        if self._supervisor_id is None:
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="No supervisor configured in HierarchicalTeamConfig.",
            )
        if not self.runtime.has_agent(self._supervisor_id):
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=(
                    f"Supervisor '{self._supervisor_id}' is not registered in runtime. "
                    "Call add_agent(supervisor_card, supervisor_provider) before invoke()/stream()."
                ),
            )

    async def invoke(
        self,
        message: Any,
        session: Optional[Session] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """Run the supervisor and return the final result.

        Args:
            message: User input (dict or str).
            session: Session from Runner, or ``None`` to create a fresh one.
            timeout: Per-call P2P timeout in seconds. Overrides the
                ``HierarchicalTeamConfig.timeout`` / ``RuntimeConfig.p2p_timeout``
                defaults for this invocation only. Defaults to ``None``
                (uses the configured timeout).

        Returns:
            Final result returned by the supervisor agent.
        """
        self._assert_ready()
        # Use config timeout if not provided
        if timeout is None:
            timeout = self.config.timeout
        async with standalone_invoke_context(
            self.runtime, self.card, message, session
        ) as (team_session, session_id):
            logger.debug(
                f"[{self.__class__.__name__}] invoke start "
                f"session_id={session_id} supervisor={self._supervisor_id}"
            )
            result = await self.runtime.send(
                message=message,
                recipient=self._supervisor_id,
                sender=self.card.id,
                session_id=session_id,
                timeout=timeout,
            )
            logger.debug(
                f"[{self.__class__.__name__}] invoke end session_id={session_id}"
            )
            return result

    async def stream(
        self,
        message: Any,
        session: Optional[Session] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Run the supervisor and stream output chunks.

        Args:
            message: User input (dict or str).
            session: Session from Runner, or ``None`` to create a fresh one.
            timeout: Per-call P2P timeout in seconds. Overrides the
                ``HierarchicalTeamConfig.timeout`` / ``RuntimeConfig.p2p_timeout``
                defaults for this invocation only. Defaults to ``None``
                (uses the configured timeout).

        Yields:
            Chunks emitted by the supervisor or sub-agents.
        """
        self._assert_ready()
        # Use config timeout if not provided
        if timeout is None:
            timeout = self.config.timeout
        logger.debug(
            f"[{self.__class__.__name__}] stream start supervisor={self._supervisor_id}"
        )

        async def _run(team_session: Session, session_id: str) -> None:
            result = await self.runtime.send(
                message=message,
                recipient=self._supervisor_id,
                sender=self.card.id,
                session_id=session_id,
                timeout=timeout,
            )
            if result is not None:
                try:
                    await team_session.write_stream({"output": result})
                except Exception as write_exc:
                    logger.warning(
                        f"[{self.__class__.__name__}] failed to write final "
                        f"result to stream: {write_exc}"
                    )
            logger.debug(
                f"[{self.__class__.__name__}] stream end "
                f"session_id={session_id}"
            )

        async for chunk in standalone_stream_context(
            self.runtime, self.card, message, _run, session
        ):
            yield chunk
