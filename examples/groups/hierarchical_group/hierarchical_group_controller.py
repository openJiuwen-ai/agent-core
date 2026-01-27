# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HierarchicalGroup Controller - Leader-Worker message routing controller"""

from typing import TYPE_CHECKING, Any

from openjiuwen.core.multi_agent.legacy.group_controller import (
    BaseGroupController
)
from openjiuwen.core.controller.legacy import Event
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode

from openjiuwen.core.session.agent import Session


class HierarchicalGroupController(BaseGroupController):
    """HierarchicalGroup Controller - Simple Leader-Worker routing
    
    Design philosophy (Linus style):
    - Zero special cases: Leader is just another single_agent in the dict
    - Simple 3-line routing logic
    - Support both default routing (to leader) and subscription-based routing
    
    Routing logic:
    1. If receiver_id specified → Send to that single_agent (point-to-point)
    2. If message_type has subscribers → Publish to subscribers (broadcast)
    3. Otherwise → Send to leader single_agent (default behavior)
    
    This design:
    - Preserves HierarchicalGroup's default behavior (route to leader)
    - Enables flexible subscription-based routing when needed
    - No complexity, no magic
    """

    def __init__(self, leader_agent_id: str, agent_group=None):
        """Initialize HierarchicalGroupController
        
        Args:
            leader_agent_id: Leader single_agent ID (required)
            agent_group: Associated AgentGroup (optional, injected via setup)
        """
        super().__init__(agent_group)
        self.leader_agent_id = leader_agent_id
        logger.info(
            f"HierarchicalGroupController initialized with "
            f"leader_agent_id={leader_agent_id}"
        )

    async def handle_event(
        self,
        event: Event,
        session: Session
    ) -> Any:
        """Handle message - Route based on simple rules
        
        3-line routing logic:
        1. Explicit receiver → Send to that single_agent
        2. Message type with subscribers → Publish to subscribers
        3. Default → Send to leader
        
        Args:
            event: Event object
            session: Session context
        
        Returns:
            Processing result (single result for 1 subscriber, list for multiple)
        """
        # Rule 1: Explicit receiver_id (highest priority)
        if event.receiver_id:
            logger.info(
                f"HierarchicalGroupController: Routing to explicit "
                f"receiver_id={event.receiver_id}"
            )
            return await self.send_to_agent(event, event.receiver_id, session)

        # Rule 2: Message type with subscribers
        if event.custom_event_type:
            subscribers = self.get_subscribers(event.custom_event_type)
            if subscribers:
                logger.info(
                    f"HierarchicalGroupController: Publishing to "
                    f"{len(subscribers)} subscribers "
                    f"for message_type={event.custom_event_type}"
                )
                results = await self.publish(event, session)
                
                # Return single result for single subscriber
                # Return list for multiple subscribers (explicit broadcast)
                return results[0] if len(subscribers) == 1 else results

        # Rule 3: Default - route to leader
        leader = self.agent_group.agents.get(self.leader_agent_id)
        if not leader:
            raise JiuWenBaseException(
                StatusCode.AGENT_GROUP_CREATE_FAILED.code,
                StatusCode.AGENT_GROUP_CREATE_FAILED.errmsg.format(
                    reason=f"Leader single_agent '{self.leader_agent_id}' not found in group. "
                           f"Available agents: {list(self.agent_group.agents.keys())}"
                )
            )

        logger.info(
            f"HierarchicalGroupController: Routing to leader (default), "
            f"leader_agent_id={self.leader_agent_id}"
        )
        return await self.send_to_agent(event, self.leader_agent_id, session)

