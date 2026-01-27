# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""HierarchicalMainController - Intelligent leader controller for HierarchicalGroup"""

import time
from typing import Optional

from openjiuwen.core.controller.legacy.controller import BaseController
from openjiuwen.core.controller.legacy import IntentDetectionConfig, IntentDetector, Event
from openjiuwen.core.common.constants import constant as const
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class HierarchicalMainController(BaseController):
    """Intelligent leader controller for HierarchicalGroup
    
    Capabilities:
    1. Auto-discover other agents in the group
    2. LLM-based intent detection (using single_agent description)
    3. State-based interruption recovery
    4. Task dispatch via BaseController.send_to_agent
    
    Usage:
        hierarchical_group = HierarchicalGroup(config)
        leader = ControllerAgent(config, HierarchicalMainController())
        hierarchical_group.add_agent("leader", leader)
        hierarchical_group.add_agent("agent_a", agent_a)
        result = await hierarchical_group.invoke(event, session)
    """
    
    def __init__(self):
        super().__init__()
        self._intent_detector = None
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
    
    def _ensure_intent_detection_initialized(self, session):
        """Initialize or update intent detection module
        
        Like WorkflowController._ensure_intent_detection_initialized:
        - If intent_detection exists, update its session
        - Otherwise create new IntentDetection instance
        """
        if not self._group:
            logger.warning("HierarchicalMainController: Not attached to a group")
            return
        
        agents = self._get_other_agents()
        if not agents:
            logger.warning("HierarchicalMainController: No other agents found")
            return
        
        # If already initialized, just update session
        if self._intent_detector is not None:
            self._intent_detector.session = session
            logger.debug("HierarchicalMainController: Updated intent detection session")
            return
        
        # Create new IntentDetection
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
            f"HierarchicalMainController: Init intent detection, "
            f"categories={category_descriptions}"
        )
        
        try:
            intent_config = IntentDetectionConfig(
                category_list=category_descriptions,
                category_info=category_info,
                enable_history=True,
                enable_input=True
            )
            
            self._intent_detector = IntentDetector(
                intent_config=intent_config,
                agent_config=self._config,
                context_engine=self._context_engine,
                session=session
            )

            logger.info(
                f"HierarchicalMainController: Intent detection ready, "
                f"{len(category_descriptions)} agents"
            )
        except Exception as e:
            logger.error(f"HierarchicalMainController: Intent detection init failed: {e}")
            self._intent_detector = None
    
    async def handle_event(self, event: Event, session) -> dict:
        """Process message: intent detection -> interruption check -> dispatch
        
        Logic:
        1. If message content is InteractiveInput, skip intent detection and resume last interrupted single_agent
        2. Otherwise, detect intent first
        3. If intent matches an interrupted single_agent, resume it
        4. If intent points to a different single_agent, route to that single_agent
        """
        self._ensure_intent_detection_initialized(session)
        
        # Check if message content is InteractiveInput
        is_interactive_input = (
            hasattr(event.content, 'interactive_input') 
            and event.content.interactive_input is not None
        )
        
        if is_interactive_input:
            # Skip intent detection for InteractiveInput, directly resume last interrupted single_agent
            target_id = self._get_last_interrupted_agent(session)
            if target_id:
                logger.info(
                    f"HierarchicalMainController: InteractiveInput detected, "
                    f"resume last interrupted single_agent -> {target_id}"
                )
                return await self._dispatch(target_id, event, session)
            else:
                logger.warning(
                    "HierarchicalMainController: InteractiveInput detected but no "
                    "interrupted single_agent found, falling back to intent detection"
                )
        
        # Normal flow: detect intent first
        target_id = await self._detect_intent(event)
        logger.info(f"HierarchicalMainController: Intent -> {target_id}")
        
        return await self._dispatch(target_id, event, session)
    
    async def _dispatch(self, agent_id: str, event: Event, session) -> dict:
        """Dispatch task to target single_agent"""
        logger.info(f"HierarchicalMainController: Dispatch to {agent_id}")
        result = await self.send_to_agent(agent_id, event, session)
        self._update_interruption_state(agent_id, result, session)
        return result
    
    async def _detect_intent(self, event: Event) -> str:
        """Detect intent via intent detection module
        
        Returns agent_id by mapping from detected description.
        """
        agents = self._get_other_agents()
        
        if not self._intent_detector:
            if agents:
                fallback = list(agents.keys())[0]
                logger.warning(
                    f"HierarchicalMainController: No intent detection, "
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
            tasks = await self._intent_detector.process_message(event)
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
    
    def _get_last_interrupted_agent(self, session) -> Optional[str]:
        """Get most recently interrupted single_agent"""
        state = session.get_state(self._get_state_key()) or {}
        interrupted = state.get("interrupted_agents", {})
        
        if not interrupted:
            return None
        
        sorted_items = sorted(
            interrupted.items(),
            key=lambda x: x[1].get("interrupt_time", 0),
            reverse=True
        )
        return sorted_items[0][0]
    
    def _update_interruption_state(self, agent_id: str, result, session):
        """Update interruption state based on result
        
        Result formats:
        1. Interrupted: list containing OutputSchema(type='__interaction__', ...)
        2. Completed: dict with {'result_type': 'answer', 'output': WorkflowOutput}
        """
        state_key = self._get_state_key()
        state = session.get_state(state_key) or {}
        
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
                session.update_state({state_key: state})
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
                    session.update_state({state_key: state})
                    logger.info(
                        f"HierarchicalMainController: Cleared interruption: "
                        f"{agent_id}"
                    )

