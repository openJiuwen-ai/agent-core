# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Hierarchical Group - Leader-Worker pattern implementation"""

from openjiuwen.core.agent_group.agent_group import ControllerGroup
from openjiuwen.agent_group.hierarchical_group.config import HierarchicalGroupConfig
from openjiuwen.agent_group.hierarchical_group.hierarchical_group_controller import HierarchicalGroupController
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class HierarchicalGroup(ControllerGroup):
    """Hierarchical Group - Leader-Worker pattern for multi-agent coordination
    
    Architecture:
    - One leader agent: receives external messages, coordinates workers
    - Multiple worker agents: execute tasks assigned by leader
    - Message flow: External → Leader → Workers → Leader → External
    
    Design principles (Linus style):
    - Leader is just an agent in agents dict, no special status
    - Simple routing: external messages → leader, leader decides rest
    - Zero complexity: HierarchicalGroupController handles all routing
    
    Usage:
        # 1. Create config
        config = HierarchicalGroupConfig(
            group_id="my_hierarchical_group",
            leader_agent_id="leader_001",
            max_agents=10
        )
        
        # 2. Create group
        hierarchical_group = HierarchicalGroup(config)
        
        # 3. Add leader agent
        hierarchical_group.add_agent("leader_001", leader_agent)
        
        # 4. Add worker agents
        hierarchical_group.add_agent("worker_001", worker_agent_1)
        hierarchical_group.add_agent("worker_002", worker_agent_2)
        
        # 5. Process messages
        result = await hierarchical_group.invoke(message, runtime)
    """
    
    def __init__(self, config: HierarchicalGroupConfig):
        """Initialize HierarchicalGroup
        
        Args:
            config: HierarchicalGroupConfig with leader_agent_id
        
        Raises:
            ValueError: If leader_agent_id not provided in config
        """
        if not isinstance(config, HierarchicalGroupConfig):
            raise JiuWenBaseException(
                StatusCode.AGENT_GROUP_CREATE_FAILED.code,
                StatusCode.AGENT_GROUP_CREATE_FAILED.errmsg.format(
                    reason=f"HierarchicalGroup requires HierarchicalGroupConfig, "
                           f"got {type(config)}"
                )
            )
        
        # Create HierarchicalGroupController with leader_agent_id
        hierarchical_controller = HierarchicalGroupController(
            leader_agent_id=config.leader_agent_id
        )
        
        # Initialize ControllerGroup with controller
        super().__init__(config=config, group_controller=hierarchical_controller)
        
        self.leader_agent_id = config.leader_agent_id
        
        logger.info(
            f"HierarchicalGroup initialized: group_id={config.group_id}, "
            f"leader_agent_id={self.leader_agent_id}"
        )
    
    def add_agent(self, agent_id: str, agent) -> None:
        """Add agent to group
        
        Args:
            agent_id: Agent identifier
            agent: Agent instance
        
        Note:
            Leader agent must be added first before processing messages.
            Leader is treated the same as any other agent in the group.
        """
        super().add_agent(agent_id, agent)
        
        # Log if this is the leader
        if agent_id == self.leader_agent_id:
            logger.info(
                f"HierarchicalGroup: Leader agent added (agent_id={agent_id})"
            )
    
    def get_leader_agent(self):
        """Get leader agent instance
        
        Returns:
            Leader agent instance or None if not found
        """
        return self.agents.get(self.leader_agent_id)
    
    def get_agents(self, exclude_leader: bool = False):
        """Get agents in this group
        
        Args:
            exclude_leader: If True, exclude leader agent from result
        
        Returns:
            Dict of agents {agent_id: agent}
        """
        if exclude_leader:
            return {
                agent_id: agent
                for agent_id, agent in self.agents.items()
                if agent_id != self.leader_agent_id
            }
        return dict(self.agents)

    def get_worker_agents(self):
        """Get all worker agents (excluding leader)
        
        Returns:
            Dict of worker agents {agent_id: agent}
        """
        return self.get_agents(exclude_leader=True)


