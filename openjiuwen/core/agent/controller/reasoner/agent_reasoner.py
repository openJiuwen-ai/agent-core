#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
from typing import List, Optional

from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.agent.controller.reasoner.intent_detection import IntentDetection
from openjiuwen.core.agent.controller.reasoner.planner import Planner
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.agent.task.task import Task
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine.engine import ContextEngine
from openjiuwen.core.runtime.runtime import Runtime


class AgentReasoner:
    """AgentReasoner - Agent decision module for message decision and task generation"""
    
    def __init__(self, config: AgentConfig, context_engine: ContextEngine, runtime: Runtime):
        """
        Initialize AgentReasoner
        
        Args:
            config: AgentConfig
            context_engine: Context engine
            runtime: Runtime environment
        """
        self.config = config
        self.context_engine = context_engine
        self.runtime = runtime

        # Sub-modules
        self.intent_detection: Optional[IntentDetection] = None
        self.planner: Optional[Planner] = None
        
    async def process_message(self, message: Message) -> List[Task]:
        """
        Process message - unified decision entry point
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        # Currently uses intent detection by default
        tasks = await self.use_intent_detection(message)
        return tasks

    def set_intent_detection(self, intent_detection: IntentDetection) -> 'AgentReasoner':
        """
        Set intent detection module
        
        Args:
            intent_detection: Intent detection module instance
            
        Returns:
            AgentReasoner: Supports chaining
        """
        self.intent_detection = intent_detection
        logger.debug("Intent detection module set")
        return self

    def set_planner(self, planner: Planner) -> 'AgentReasoner':
        """
        Set planner module
        
        Args:
            planner: Planner module instance
            
        Returns:
            AgentReasoner: Supports chaining
        """
        self.planner = planner
        return self

    async def use_intent_detection(self, message: Message) -> List[Task]:
        """
        Process message using intent detection module
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        if not self.intent_detection:
            raise ValueError("Intent detection module not set")
        return await self.intent_detection.process_message(message)

    async def use_planner(self, message: Message) -> List[Task]:
        """
        Process message using planner module
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        if not self.planner:
            raise ValueError("Planner module not set")
        return await self.planner.process_message(message)

    @property
    def intent_detection_module(self) -> Optional[IntentDetection]:
        """Get intent detection module"""
        return self.intent_detection

    @property
    def planner_module(self) -> Optional[Planner]:
        """Get planner module"""
        return self.planner
