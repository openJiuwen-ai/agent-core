#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved

from typing import List
from openjiuwen.core.common.logging import logger
from openjiuwen.core.agent.message.message import Message
from openjiuwen.core.agent.task.task import Task
from openjiuwen.agent.common.enum import TaskType, TaskStatus


class Planner:
    """Planner - Plans and decomposes complex tasks"""

    def __init__(self, config, context_engine, runtime):
        """
        Initialize Planner
        
        Args:
            config: Planner config
            context_engine: Context engine
            runtime: Runtime environment
        """
        self.config = config
        self.context_engine = context_engine
        self.runtime = runtime

    @staticmethod
    def _create_default_task(message: Message) -> Task:
        # Temporary: return default task
        return Task(
            task_type=TaskType.UNDEFINED,
            description=f"Planner task for message: {message.content.get_query() if message.content else 'No content'}",
            status=TaskStatus.PENDING,
            metadata={
                "original_message_id": message.msg_id,
                "task_source": "planner"
            }
        )

    async def process_message(self, message: Message) -> List[Task]:
        """
        Process message, plan tasks and generate task list
        
        Args:
            message: Input message
            
        Returns:
            List[Task]: Generated task list
        """
        # Implement task planning logic:
        # 1. Analyze message, identify complex tasks
        # 2. Create execution plan, decompose into subtasks
        # 3. Generate task list, set task dependencies
        # 4. Return task list

        logger.debug(f"Processing message {message.msg_id} with Planner")
        return [self._create_default_task(message)]
