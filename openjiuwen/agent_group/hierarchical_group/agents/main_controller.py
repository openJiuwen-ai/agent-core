# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HierarchicalMainController - Intelligent leader controller for HierarchicalGroup"""

import time
from typing import Optional

from openjiuwen.core.agent.controller.controller import BaseController
from openjiuwen.core.agent.controller.config.reasoner_config import IntentDetectionConfig
from openjiuwen.core.agent.controller.reasoner.agent_reasoner import AgentReasoner
from openjiuwen.core.agent.controller.reasoner.intent_detection import IntentDetection
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.common.constants import constant as const
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class HierarchicalMainController(BaseController):
    """Intelligent leader controller for HierarchicalGroup
    
    Capabilities:
    1. Auto-discover other agents in the group
    2. LLM-based intent detection (using agent description)
    3. State-based interruption recovery
    4. Task dispatch via BaseController.send_to_agent
    
    Usage:
        hierarchical_group = HierarchicalGroup(config)
        leader = ControllerAgent(config, HierarchicalMainController())
        hierarchical_group.add_agent("leader", leader)
        hierarchical_group.add_agent("agent_a", agent_a)
        result = await hierarchical_group.invoke(message, runtime)
    """
    
    def __init__(self):
        super().__init__()
        self.reasoner = None
        self._desc_to_agent_id = {}  # description -> agent_id 映射
    
    def _get_other_agents(self) -> dict:
        """Get all agents except self"""
        if not self._group:
            return {}
        
        result = {}
        for agent_id, agent in self._group.agents.items():
            if hasattr(agent, 'controller') and agent.controller is self:
                continue
            result[agent_id] = agent
        return result
    
    def _ensure_reasoner_initialized(self, runtime):
        """Initialize or update reasoner for intent detection
        
        Like WorkflowController._ensure_intent_detection_initialized:
        - If reasoner exists, update its intent_detection_module.runtime
        - Otherwise create new reasoner and IntentDetection
        """
        if not self._group:
            logger.warning("HierarchicalMainController: Not attached to a group")
            return
        
        agents = self._get_other_agents()
        if not agents:
            logger.warning("HierarchicalMainController: No other agents found")
            return
        
        # If already initialized, just update runtime
        if self.reasoner is not None:
            has_intent_module = (
                hasattr(self.reasoner, 'intent_detection_module')
                and self.reasoner.intent_detection_module
            )
            if has_intent_module:
                self.reasoner.intent_detection_module.runtime = runtime
                return
        
        # Create new reasoner and IntentDetection
        # Use description as category for better LLM understanding
        category_descriptions = []
        self._desc_to_agent_id = {}
        
        for agent_id, agent in agents.items():
            desc = None
            if hasattr(agent, 'config') and hasattr(agent.config, 'description'):
                desc = agent.config.description
            elif hasattr(agent, 'agent_config'):
                desc = getattr(agent.agent_config, 'description', None)
            
            # Fallback: use agent_id if no description
            if not desc:
                desc = agent_id
                logger.warning(
                    f"HierarchicalMainController: Agent {agent_id} has no "
                    f"description, using id as category"
                )
            
            # Handle duplicate descriptions
            if desc in self._desc_to_agent_id:
                original_desc = desc
                desc = f"{desc} ({agent_id})"
                logger.warning(
                    f"HierarchicalMainController: Duplicate description "
                    f"'{original_desc}', using '{desc}'"
                )
            
            category_descriptions.append(desc)
            self._desc_to_agent_id[desc] = agent_id
        
        category_info = "\n".join(
            f"- {desc}" for desc in category_descriptions
        )
        logger.info(
            f"HierarchicalMainController: Init reasoner, "
            f"categories={category_descriptions}"
        )
        
        try:
            intent_config = IntentDetectionConfig(
                category_list=category_descriptions,
                category_info=category_info,
                enable_history=True,
                enable_input=True
            )
            
            self.reasoner = AgentReasoner(
                config=self._config,
                context_engine=self._context_engine,
                runtime=runtime
            )
            
            intent_detection = IntentDetection(
                intent_config=intent_config,
                agent_config=self._config,
                context_engine=self._context_engine,
                runtime=runtime
            )
            
            self.reasoner.set_intent_detection(intent_detection)
            logger.info(
                f"HierarchicalMainController: Reasoner ready, "
                f"{len(category_descriptions)} agents"
            )
        except Exception as e:
            logger.error(f"HierarchicalMainController: Reasoner init failed: {e}")
            self.reasoner = None
    
    async def handle_message(self, message: Message, runtime) -> dict:
        """Process message: intent detection -> interruption check -> dispatch
        
        Logic:
        1. If message content is InteractiveInput, skip intent detection and resume last interrupted agent
        2. Otherwise, detect intent first
        3. If intent matches an interrupted agent, resume it
        4. If intent points to a different agent, route to that agent
        """
        self._ensure_reasoner_initialized(runtime)
        
        # Check if message content is InteractiveInput
        is_interactive_input = (
            hasattr(message.content, 'interactive_input') 
            and message.content.interactive_input is not None
        )
        
        if is_interactive_input:
            # Skip intent detection for InteractiveInput, directly resume last interrupted agent
            target_id = self._get_last_interrupted_agent(runtime)
            if target_id:
                logger.info(
                    f"HierarchicalMainController: InteractiveInput detected, "
                    f"resume last interrupted agent -> {target_id}"
                )
                return await self._dispatch(target_id, message, runtime)
            else:
                logger.warning(
                    "HierarchicalMainController: InteractiveInput detected but no "
                    "interrupted agent found, falling back to intent detection"
                )
        
        # Normal flow: detect intent first
        target_id = await self._detect_intent(message)
        logger.info(f"HierarchicalMainController: Intent -> {target_id}")
        
        return await self._dispatch(target_id, message, runtime)
    
    async def _dispatch(self, agent_id: str, message: Message, runtime) -> dict:
        """Dispatch task to target agent"""
        logger.info(f"HierarchicalMainController: Dispatch to {agent_id}")
        result = await self.send_to_agent(agent_id, message, runtime)
        self._update_interruption_state(agent_id, result, runtime)
        return result
    
    async def _detect_intent(self, message: Message) -> str:
        """Detect intent via reasoner
        
        Returns agent_id by mapping from detected description.
        """
        agents = self._get_other_agents()
        
        if not self.reasoner:
            if agents:
                fallback = list(agents.keys())[0]
                logger.warning(
                    f"HierarchicalMainController: No reasoner, "
                    f"fallback to {fallback}"
                )
                return fallback
            raise JiuWenBaseException(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR.code,
                StatusCode.AGENT_GROUP_EXECUTION_ERROR.errmsg.format(
                    reason="HierarchicalMainController: No agents available"
                )
            )
        
        try:
            tasks = await self.reasoner.use_intent_detection(message)
            if tasks and len(tasks) > 0:
                detected_desc = tasks[0].input.target_name
                # Map description back to agent_id
                agent_id = self._desc_to_agent_id.get(detected_desc)
                if agent_id:
                    logger.info(
                        f"HierarchicalMainController: Mapped '{detected_desc}' "
                        f"-> {agent_id}"
                    )
                    return agent_id
                # Fallback if mapping not found
                logger.warning(
                    f"HierarchicalMainController: No mapping for "
                    f"'{detected_desc}', trying direct match"
                )
                if detected_desc in agents:
                    return detected_desc
            
            fallback = list(agents.keys())[0]
            logger.warning(
                f"HierarchicalMainController: No intent result, "
                f"fallback to {fallback}"
            )
            return fallback
        except Exception as e:
            logger.error(
                f"HierarchicalMainController: Intent detection failed: {e}"
            )
            if agents:
                return list(agents.keys())[0]
            raise
    
    def _get_state_key(self) -> str:
        return "hierarchical_main_controller"
    
    def _get_last_interrupted_agent(self, runtime) -> Optional[str]:
        """Get most recently interrupted agent"""
        state = runtime.get_state(self._get_state_key()) or {}
        interrupted = state.get("interrupted_agents", {})
        
        if not interrupted:
            return None
        
        sorted_items = sorted(
            interrupted.items(),
            key=lambda x: x[1].get("interrupt_time", 0),
            reverse=True
        )
        return sorted_items[0][0]
    
    def _update_interruption_state(self, agent_id: str, result, runtime):
        """Update interruption state based on result
        
        Result formats:
        1. Interrupted: list containing OutputSchema(type='__interaction__', ...)
        2. Completed: dict with {'result_type': 'answer', 'output': WorkflowOutput}
        """
        state_key = self._get_state_key()
        state = runtime.get_state(state_key) or {}
        
        if "interrupted_agents" not in state:
            state["interrupted_agents"] = {}
        
        # Case 1: list with __interaction__ -> interrupted
        if isinstance(result, list) and len(result) > 0:
            first_item = result[0]
            # Check if it's an interaction (interrupt)
            is_interaction = (
                hasattr(first_item, 'type')
                and first_item.type == const.INTERACTION
            )
            if is_interaction:
                state["interrupted_agents"][agent_id] = {
                    "interrupt_time": time.time()
                }
                runtime.update_state({state_key: state})
                logger.info(
                    f"HierarchicalMainController: Recorded interruption: {agent_id}"
                )
                return
        
        # Case 2: dict with result_type='answer' -> completed
        if isinstance(result, dict):
            result_type = result.get("result_type")
            output = result.get("output")
            
            # Check if workflow completed
            is_completed = False
            if result_type == "answer" and output is not None:
                if hasattr(output, 'state'):
                    is_completed = output.state.value == "COMPLETED"
            
            if is_completed:
                if agent_id in state["interrupted_agents"]:
                    del state["interrupted_agents"][agent_id]
                    runtime.update_state({state_key: state})
                    logger.info(
                        f"HierarchicalMainController: Cleared interruption: "
                        f"{agent_id}"
                    )

