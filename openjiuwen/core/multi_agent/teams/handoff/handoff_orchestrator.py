# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffOrchestrator -- per-session runtime coordinator for HandoffTeam."""
from __future__ import annotations
import asyncio
from typing import Dict, List, Optional, Set
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.teams.handoff.handoff_config import HandoffRoute

COORDINATOR_STATE_KEY = "__handoff_coordinator__"
HANDOFF_HISTORY_KEY = "__handoff_history__"


class HandoffOrchestrator:
    """Per-session coordinator that tracks handoff state and routing decisions.

    Created and owned by :class:`~HandoffTeam` for each invocation.
    Use :meth:`restore_from_session` to resume an interrupted session.
    """

    def __init__(self, start_agent_id, registered_agents, config=None):
        routes = config.routes if config is not None else None
        max_handoffs = config.max_handoffs if config is not None else 10
        termination_condition = config.termination_condition if config is not None else None
        self._max_handoffs = max_handoffs
        self._termination_condition = termination_condition
        self._handoff_count = 0
        self._current_agent_id = start_agent_id
        self._done_future: Optional[asyncio.Future] = None  # created lazily on first access inside a running loop
        self._route_graph = self.build_route_graph(registered_agents, routes or [])
        logger.debug(f"[{self.__class__.__name__}] created start={start_agent_id!r} max_handoffs={max_handoffs}")

    @staticmethod
    def build_route_graph(agents: List[str], routes: List[HandoffRoute]) -> Dict[str, Set[str]]:
        """Build an adjacency graph of allowed handoff routes.

        Args:
            agents: List of all agent IDs in the team.
            routes: Explicit routing rules.  Empty list means full-mesh.

        Returns:
            Dict mapping each agent ID to the set of agent IDs it may hand off to.
        """
        graph = {a: set() for a in agents}
        if routes:
            for r in routes:
                graph.setdefault(r.source, set()).add(r.target)
        else:
            for src in agents:
                for tgt in agents:
                    if src != tgt:
                        graph[src].add(tgt)
        return graph

    async def request_handoff(self, target_id, reason=None):
        """Attempt to approve a handoff to ``target_id``.

        Args:
            target_id: ID of the agent to hand off to.
            reason:    Optional reason string for logging.

        Returns:
            ``True`` if the handoff is approved and state is updated;
            ``False`` if rejected (limit reached, condition triggered, or route not allowed).
        """
        if self._handoff_count >= self._max_handoffs:
            logger.debug(f"[{self.__class__.__name__}] max_handoffs reached, "
                         f"rejecting -> {target_id!r}")
            return False
        if self._termination_condition is not None:
            result = self._termination_condition(self)
            if asyncio.iscoroutine(result):
                result = await result
            if result:
                logger.debug(f"[{self.__class__.__name__}] termination_condition=True, "
                             f"rejecting -> {target_id!r}")
                return False
        allowed = self._route_graph.get(self._current_agent_id, set())
        if target_id not in allowed:
            logger.warning(f"[{self.__class__.__name__}] route {self._current_agent_id!r} -> "
                           f"{target_id!r} not allowed")
            return False
        self._handoff_count += 1
        self._current_agent_id = target_id
        logger.debug(f"[{self.__class__.__name__}] handoff approved -> "
                     f"{target_id!r} count={self._handoff_count}")
        return True

    async def complete(self, result):
        """Resolve the done future with *result*, ending the handoff chain.

        Args:
            result: Final result to return to the caller.
        """
        if not self.done_future.done():
            self.done_future.set_result(result)

    async def error(self, exception):
        """Reject the done future with *exception*, propagating the error to the caller.

        Args:
            exception: Exception to raise at the await site.
        """
        if not self.done_future.done():
            self.done_future.set_exception(exception)

    def save_to_session(self, session):
        """Persist coordinator state to *session* for interrupt/resume support.

        Args:
            session: Team session to write state into.
        """
        session.update_state({
            COORDINATOR_STATE_KEY: {
                "current_agent_id": self._current_agent_id,
                "handoff_count": self._handoff_count,
            }
        })

    @classmethod
    def restore_from_session(cls, session, start_agent_id, registered_agents, config=None):
        """Create an orchestrator, restoring state from a previous interrupted session.

        Args:
            session:           Team session to read state from.
            start_agent_id:    ID of the first agent to run (used when no prior state exists).
            registered_agents: List of all agent IDs in the team.
            config:            :class:`~HandoffConfig` carrying routes, max_handoffs and
                               termination_condition.  ``None`` uses defaults.

        Returns:
            A :class:`HandoffOrchestrator` initialised from session state if available,
            or a fresh one starting at ``start_agent_id``.
        """
        coord = cls(
            start_agent_id=start_agent_id,
            registered_agents=registered_agents,
            config=config,
        )
        snapshot = session.get_state(COORDINATOR_STATE_KEY)
        if snapshot:
            coord._current_agent_id = snapshot["current_agent_id"]
            coord._handoff_count = snapshot["handoff_count"]
        return coord

    @property
    def done_future(self) -> asyncio.Future:
        """Completion future for the handoff chain; created lazily inside the running event loop."""
        if self._done_future is None:
            self._done_future = asyncio.get_running_loop().create_future()
        return self._done_future

    @property
    def handoff_count(self) -> int:
        """Number of handoff transfers completed so far in this session."""
        return self._handoff_count

    @property
    def current_agent_id(self) -> str:
        """ID of the agent that will execute the next hop."""
        return self._current_agent_id
