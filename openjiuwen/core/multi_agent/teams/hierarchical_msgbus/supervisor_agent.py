# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""SupervisorAgent -- default built-in supervisor for HierarchicalTeam."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from openjiuwen.core.runner.resources_manager.base import AgentProvider

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
from openjiuwen.core.multi_agent.teams.hierarchical_msgbus.p2p_ability_manager import P2PAbilityManager
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class SupervisorAgent(CommunicableAgent, ReActAgent):
    """Default LLM-driven supervisor for :class:`HierarchicalTeam`.

    Combines :class:`CommunicableAgent` (P2P send/publish) and
    :class:`ReActAgent` (ReAct loop).  AgentCard tool calls are routed via
    :class:`P2PAbilityManager`; all other ability types execute normally.
    """

    def __init__(
        self,
        card: AgentCard,
        config: Optional[ReActAgentConfig] = None,
        max_parallel_sub_agents: int = 10,
    ) -> None:
        super().__init__(card=card)
        if config is not None:
            ReActAgent.configure(self, config)

        self._ability_manager = P2PAbilityManager(
            supervisor=self,
            max_parallel_sub_agents=max_parallel_sub_agents,
        )

    @classmethod
    def create(
        cls,
        agents: List[AgentCard],
        *,
        model_client_config,
        model_request_config,
        agent_card: AgentCard,
        system_prompt: str,
        max_iterations: int = 5,
        max_parallel_sub_agents: int = 10,
    ) -> "tuple[AgentCard, AgentProvider]":
        """Create a :class:`SupervisorAgent` pre-loaded with sub-agent cards.

        Returns ``(agent_card, provider)`` compatible with :meth:`HierarchicalTeam.add_agent`.

        Args:
            agents:                  Sub-agent cards visible to this supervisor.
            model_client_config:     LLM client configuration.
            model_request_config:    LLM model/request configuration.
            agent_card:              AgentCard for this supervisor.
            system_prompt:           Supervisor system prompt.
            max_iterations:          Max ReAct iterations (default 5).
            max_parallel_sub_agents: Max parallel AgentCard dispatches (default 10).

        Returns:
            ``(agent_card, provider)`` where *provider* lazily constructs the supervisor.

        Raises:
            ExecutionError: If ``agents`` is empty or contains non-AgentCard entries.
        """
        if not agents:
            raise build_error(
                StatusCode.AGENT_TEAM_CREATE_RUNTIME_ERROR,
                error_msg="[SupervisorAgent.create] agents list must not be empty",
            )

        for card in agents:
            if not isinstance(card, AgentCard):
                raise build_error(
                    StatusCode.AGENT_TEAM_CREATE_RUNTIME_ERROR,
                    error_msg=(
                        "[SupervisorAgent.create] each agents entry must be AgentCard, "
                        f"got {type(card)}"
                    ),
                )

        def _provider() -> "SupervisorAgent":
            cfg = ReActAgentConfig()
            cfg.model_client_config = model_client_config
            cfg.model_config_obj = model_request_config
            cfg.configure_max_iterations(max_iterations)
            cfg.configure_prompt_template([
                {"role": "system", "content": system_prompt}
            ])

            cfg.model_provider = str(model_client_config.client_provider)
            cfg.api_key = model_client_config.api_key
            cfg.api_base = model_client_config.api_base
            if cfg.model_config_obj.model_name:
                cfg.model_name = cfg.model_config_obj.model_name

            supervisor = cls(
                card=agent_card,
                config=cfg,
                max_parallel_sub_agents=max_parallel_sub_agents,
            )

            for card in agents:
                supervisor.register_sub_agent_card(card)
                logger.debug(
                    f"[SupervisorAgent.create] registered sub-agent card id={card.id}"
                )

            logger.info(
                f"[SupervisorAgent.create] supervisor id={agent_card.id} "
                f"sub_agents={[c.id for c in agents]} "
                f"max_parallel_sub_agents={max_parallel_sub_agents}"
            )
            return supervisor

        return agent_card, _provider

    # ------------------------------------------------------------------
    # Sub-agent registration
    # ------------------------------------------------------------------

    def register_sub_agent_card(self, card: AgentCard) -> None:
        """Expose a sub-agent card to the LLM as a callable tool.

        Args:
            card: AgentCard of the sub-agent.
        """
        self._ability_manager.add(card)
        logger.debug(
            f"[{self.__class__.__name__}] registered sub-agent "
            f"'{card.name}' (id={card.id}) as LLM tool"
        )

    # ------------------------------------------------------------------
    # configure override
    # ------------------------------------------------------------------

    def configure(self, config) -> "SupervisorAgent":
        """Apply a :class:`ReActAgentConfig`; no-op for other config types."""

        if isinstance(config, ReActAgentConfig):
            ReActAgent.configure(self, config)
        return self


__all__ = ["SupervisorAgent"]
