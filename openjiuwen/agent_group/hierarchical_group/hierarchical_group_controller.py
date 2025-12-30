# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HierarchicalGroup Controller - Leader-Worker message routing controller"""

from typing import TYPE_CHECKING, Any

from openjiuwen.core.agent.controller.group_controller import BaseGroupController
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode

if TYPE_CHECKING:
    from openjiuwen.core.agent_group.agent_group import AgentGroupRuntime


class HierarchicalGroupController(BaseGroupController):
    """HierarchicalGroup Controller - Simple Leader-Worker routing
    
    Design philosophy (Linus style):
    - Zero special cases: Leader is just another agent in the dict
    - Simple 3-line routing logic
    - Support both default routing (to leader) and subscription-based routing
    
    Routing logic:
    1. If receiver_id specified → Send to that agent (point-to-point)
    2. If message_type has subscribers → Publish to subscribers (broadcast)
    3. Otherwise → Send to leader agent (default behavior)
    
    This design:
    - Preserves HierarchicalGroup's default behavior (route to leader)
    - Enables flexible subscription-based routing when needed
    - No complexity, no magic
    """

    def __init__(self, leader_agent_id: str, agent_group=None):
        """Initialize HierarchicalGroupController
        
        Args:
            leader_agent_id: Leader agent ID (required)
            agent_group: Associated AgentGroup (optional, injected via setup)
        """
        super().__init__(agent_group)
        self.leader_agent_id = leader_agent_id
        logger.info(
            f"HierarchicalGroupController initialized with "
            f"leader_agent_id={leader_agent_id}"
        )

    async def handle_message(
        self,
        message: Message,
        runtime: 'AgentGroupRuntime'
    ) -> Any:
        """Handle message - Route based on simple rules
        
        3-line routing logic:
        1. Explicit receiver → Send to that agent
        2. Message type with subscribers → Publish to subscribers
        3. Default → Send to leader
        
        Args:
            message: Message object
            runtime: Runtime context
        
        Returns:
            Processing result (single result for 1 subscriber, list for multiple)
        """
        # Rule 1: Explicit receiver_id (highest priority)
        if message.receiver_id:
            logger.info(
                f"HierarchicalGroupController: Routing to explicit "
                f"receiver_id={message.receiver_id}"
            )
            return await self.send_to_agent(message, message.receiver_id, runtime)

        # Rule 2: Message type with subscribers
        if message.message_type:
            subscribers = self.get_subscribers(message.message_type)
            if subscribers:
                logger.info(
                    f"HierarchicalGroupController: Publishing to "
                    f"{len(subscribers)} subscribers "
                    f"for message_type={message.message_type}"
                )
                results = await self.publish(message, runtime)
                
                # Return single result for single subscriber
                # Return list for multiple subscribers (explicit broadcast)
                return results[0] if len(subscribers) == 1 else results

        # Rule 3: Default - route to leader
        leader = self.agent_group.agents.get(self.leader_agent_id)
        if not leader:
            raise JiuWenBaseException(
                StatusCode.AGENT_GROUP_CREATE_FAILED.code,
                StatusCode.AGENT_GROUP_CREATE_FAILED.errmsg.format(
                    reason=f"Leader agent '{self.leader_agent_id}' not found in group. "
                           f"Available agents: {list(self.agent_group.agents.keys())}"
                )
            )

        logger.info(
            f"HierarchicalGroupController: Routing to leader (default), "
            f"leader_agent_id={self.leader_agent_id}"
        )
        return await self.send_to_agent(message, self.leader_agent_id, runtime)

