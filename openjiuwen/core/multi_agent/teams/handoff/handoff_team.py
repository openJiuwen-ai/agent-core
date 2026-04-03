# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HandoffTeam -- event-driven handoff multi-agent team."""
from __future__ import annotations
import asyncio
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.teams.handoff.handoff_config import HandoffTeamConfig
from openjiuwen.core.multi_agent.teams.handoff.container_agent import ContainerAgent
from openjiuwen.core.multi_agent.teams.handoff.handoff_orchestrator import (
    HandoffOrchestrator, COORDINATOR_STATE_KEY, HANDOFF_HISTORY_KEY,
)
from openjiuwen.core.multi_agent.teams.handoff.handoff_request import HandoffRequest
from openjiuwen.core.multi_agent.teams.utils import (
    standalone_invoke_context,
    standalone_stream_context,
)
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class HandoffTeam(BaseTeam):
    """Event-driven handoff multi-agent team.

    Agents collaborate via sequential handoffs driven by a pub/sub message bus.
    The LLM in each agent decides whether to complete the task or transfer
    control to another agent by calling an injected ``transfer_to_{agent}`` tool.

    Args:
        card:   TeamCard describing this team.
        config: :class:`HandoffTeamConfig` instance.  Uses defaults when omitted.
    """

    def __init__(self, card, config=None):
        super().__init__(card=card, config=config or HandoffTeamConfig())
        self._agent_providers = {}
        self._internal_agents_ready = False
        self._coordinator_registry: dict = {}
        self._init_lock = asyncio.Lock()

    def _lookup_coordinator(self, session_id: str):
        return self._coordinator_registry.get(session_id)

    def add_agent(self, card, provider):
        """Register an agent into the team.

        Args:
            card:     AgentCard for the agent.
            provider: Callable that returns a BaseAgent instance.

        Returns:
            self (supports method chaining)
        """
        if self.runtime.has_agent(card.id):
            return self  # 重复注册时跳过，与父类行为保持一致
        super().add_agent(card, provider)
        self._agent_providers[card.id] = provider
        self._internal_agents_ready = False
        return self

    def _get_start_agent_id(self):
        cfg = self.config.handoff
        if cfg.start_agent is not None:
            return cfg.start_agent.id
        return self.card.agent_cards[0].id

    async def _ensure_internal_agents(self):
        if self._internal_agents_ready:
            return
        async with self._init_lock:
            if self._internal_agents_ready:
                return
            cfg = self.config.handoff
            agent_ids = [c.id for c in self.card.agent_cards]
            route_graph = HandoffOrchestrator.build_route_graph(agent_ids, cfg.routes or [])
            logger.info(
                f"[{self.__class__.__name__}:{self.card.id}] initializing internal agents "
                f"agents={agent_ids} routes={cfg.routes or 'full-mesh'}"
            )
            for agent_id in agent_ids:
                card = self.runtime.get_agent_card(agent_id)
                allowed_targets = list(route_graph.get(agent_id, set()))
                endpoint_id = f"__handoff_ep_{self.card.id}_{agent_id}"
                endpoint_card = AgentCard(id=endpoint_id, name=endpoint_id)
                provider_type = type(self._agent_providers[agent_id]).__name__
                container_provider = self._make_container_provider(
                    card=card,
                    agent_id=agent_id,
                    allowed_targets=allowed_targets,
                )
                self.runtime.register_agent(endpoint_card, container_provider)
                await self.runtime.subscribe(endpoint_id, f"container_{agent_id}")
                logger.debug(
                    f"[{self.__class__.__name__}:{self.card.id}] endpoint registered "
                    f"endpoint_id={endpoint_id!r} agent_id={agent_id!r} "
                    f"targets={allowed_targets} provider_type={provider_type}"
                )
            logger.info(
                f"[{self.__class__.__name__}:{self.card.id}] internal agents ready "
                f"count={len(agent_ids)}"
            )
            self._internal_agents_ready = True

    def _make_container_provider(self, card, agent_id, allowed_targets):
        coordinator_lookup = self._lookup_coordinator

        def provider():
            return ContainerAgent(
                target_card=card,
                target_provider=self._agent_providers[agent_id],
                allowed_targets=allowed_targets,
                coordinator_lookup=coordinator_lookup,
            )
        return provider

    async def _run_chain(self, message, session):
        session_id = session.get_session_id()
        await self._ensure_internal_agents()
        cfg = self.config.handoff
        coordinator = HandoffOrchestrator.restore_from_session(
            session=session,
            start_agent_id=self._get_start_agent_id(),
            registered_agents=[c.id for c in self.card.agent_cards],
            config=cfg,
        )
        history = session.get_state(HANDOFF_HISTORY_KEY) or []
        is_resume = bool(session.get_state(COORDINATOR_STATE_KEY))
        if is_resume:
            filtered = []
            for h in history:
                output = h.get("output")
                is_interrupt = isinstance(output, dict) and output.get("result_type") == "interrupt"
                if not is_interrupt:
                    filtered.append(h)
            history = filtered
        self._coordinator_registry[session_id] = coordinator
        logger.info(
            f"[{self.__class__.__name__}:{self.card.id}] run_chain start "
            f"session_id={session_id!r} start_agent={coordinator.current_agent_id!r} "
            f"resume={is_resume} history_hops={len(history)}"
        )
        await self.runtime.publish(
            message=HandoffRequest(
                input_message=message,
                history=history,
                session=session,
            ),
            topic_id=f"container_{coordinator.current_agent_id}",
            sender=self.card.id, session_id=session_id,
        )
        timeout = self.config.message_timeout or None
        try:
            result = await (
                asyncio.wait_for(coordinator.done_future, timeout=timeout)
                if timeout else coordinator.done_future
            )
        except asyncio.TimeoutError as exc:
            error_msg = f"handoff chain timeout after {timeout}s, team={self.card.id!r}"
            logger.error(f"[{self.__class__.__name__}:{self.card.id}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=error_msg,
            ) from exc
        finally:
            self._coordinator_registry.pop(session_id, None)
            await self.runtime.cleanup_session(session_id)
        logger.info(
            f"[{self.__class__.__name__}:{self.card.id}] run_chain done "
            f"session_id={session_id!r} hops={coordinator.handoff_count}"
        )
        return result

    async def invoke(self, message, session=None):
        """Run the handoff chain and return the final result.

        Args:
            message: User input (dict or str).
            session: Session from Runner, or ``None`` to create a fresh one.

        Returns:
            Final result produced by the last agent in the chain.
        """
        async with standalone_invoke_context(
            self.runtime, self.card, message, session
        ) as (team_session, _):
            return await self._run_chain(message, team_session)

    async def stream(self, message, session=None):
        """Run the handoff chain and stream output chunks in real time.

        Args:
            message: User input (dict or str).
            session: Session from Runner, or ``None`` to create a fresh one.

        Yields:
            Chunks emitted by agents during the handoff chain.
        """
        async def _run(team_session: Session, sid: str) -> None:
            await self._run_chain(message, team_session)

        async for chunk in standalone_stream_context(
            self.runtime, self.card, message, _run, session
        ):
            yield chunk
