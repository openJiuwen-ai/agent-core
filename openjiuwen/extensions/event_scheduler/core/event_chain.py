# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event Chain Handler Module

This module implements event-driven task chaining, including:
- EventChainHandler: Processes task completion events and creates follow-up tasks

The EventChainHandler listens for TASK_COMPLETION events and evaluates configured
chain rules to determine if downstream tasks should be automatically created.
This enables declarative workflow orchestration without custom event handler code.

Workflow:
- Register chain rules mapping source_task_type -> target_task_type
- When a task completes, evaluate matching rules
- For matching rules, create a new task with SUBMITTED status
- Optionally pass metadata from the source task to the target
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Dict, Any, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema import TaskStatus
from openjiuwen.core.controller.schema.task import Task
from openjiuwen.extensions.event_scheduler.schema import (
    EventChainRule,
    ScheduledTaskMixin,
    EventSchedulerConfig,
)

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager


EXTENSION_KEY = "event_scheduler"


class EventChainHandler:
    """Event Chain Handler for Automatic Task Creation

    Evaluates chain rules when tasks complete and creates downstream tasks
    based on configured rules. Each rule maps a source task type to a target
    task type with optional conditions.

    Attributes:
        _config: Event scheduler configuration
        _task_manager: Reference to the core task manager
        _rules_by_source: Index of chain rules by source_task_type for fast lookup
    """

    def __init__(
            self,
            config: EventSchedulerConfig,
            task_manager: 'TaskManager'
    ):
        """Initialize event chain handler

        Args:
            config: Event scheduler configuration
            task_manager: Core task manager instance
        """
        self._config = config
        self._task_manager = task_manager
        self._rules_by_source: Dict[str, List[EventChainRule]] = {}
        self._index_rules()

    def _index_rules(self):
        """Build index of chain rules by source task type for fast lookup"""
        self._rules_by_source.clear()
        for rule in self._config.chain_rules:
            if rule.source_task_type not in self._rules_by_source:
                self._rules_by_source[rule.source_task_type] = []
            self._rules_by_source[rule.source_task_type].append(rule)
        logger.info(
            f"EventChainHandler indexed {len(self._config.chain_rules)} rules "
            f"across {len(self._rules_by_source)} source task types"
        )

    def add_rule(self, rule: EventChainRule):
        """Add a chain rule dynamically

        Args:
            rule: The chain rule to add
        """
        self._config.chain_rules.append(rule)
        if rule.source_task_type not in self._rules_by_source:
            self._rules_by_source[rule.source_task_type] = []
        self._rules_by_source[rule.source_task_type].append(rule)
        logger.info(f"Added chain rule {rule.rule_id}: {rule.source_task_type} -> {rule.target_task_type}")

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a chain rule by ID

        Args:
            rule_id: The ID of the rule to remove

        Returns:
            bool: True if the rule was found and removed
        """
        for i, rule in enumerate(self._config.chain_rules):
            if rule.rule_id == rule_id:
                self._config.chain_rules.pop(i)
                self._index_rules()
                logger.info(f"Removed chain rule {rule_id}")
                return True
        return False

    async def handle_task_completion(self, completed_task: Task) -> List[str]:
        """Handle a task completion event by evaluating chain rules

        Checks all rules matching the completed task's type and creates
        downstream tasks for rules whose conditions are met.

        Args:
            completed_task: The task that just completed

        Returns:
            List[str]: List of task IDs for newly created downstream tasks
        """
        if not self._config.enable_event_chaining:
            return []

        matching_rules = self._rules_by_source.get(completed_task.task_type, [])
        if not matching_rules:
            return []

        created_task_ids = []

        for rule in matching_rules:
            if not rule.evaluate_condition(completed_task.metadata):
                logger.debug(
                    f"Chain rule {rule.rule_id} condition not met for task {completed_task.task_id}"
                )
                continue

            try:
                task_id = await self._create_chained_task(completed_task, rule)
                if task_id:
                    created_task_ids.append(task_id)
                    logger.info(
                        f"Chain rule {rule.rule_id} triggered: "
                        f"{completed_task.task_id} ({completed_task.task_type}) -> "
                        f"{task_id} ({rule.target_task_type})"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to create chained task for rule {rule.rule_id}: {e}",
                    exc_info=True
                )

        return created_task_ids

    async def _create_chained_task(
            self,
            source_task: Task,
            rule: EventChainRule
    ) -> Optional[str]:
        """Create a downstream task based on a chain rule

        Args:
            source_task: The completed source task
            rule: The chain rule that triggered this creation

        Returns:
            Optional[str]: The ID of the newly created task, or None on failure
        """
        task_id = str(uuid.uuid4())

        # Build metadata combining rule metadata and source task reference
        metadata = dict(rule.target_metadata) if rule.target_metadata else {}
        metadata["chain_source_task_id"] = source_task.task_id
        metadata["chain_rule_id"] = rule.rule_id

        # Build extensions with scheduler mixin
        scheduler_mixin = ScheduledTaskMixin(
            chain_source_task_id=source_task.task_id
        )
        extensions = {
            EXTENSION_KEY: scheduler_mixin.model_dump()
        }

        # Create the new task
        chained_task = Task(
            session_id=source_task.session_id,
            task_id=task_id,
            task_type=rule.target_task_type,
            description=rule.target_description or f"Chained from {source_task.task_type}",
            priority=source_task.priority,
            status=TaskStatus.SUBMITTED,
            parent_task_id=source_task.task_id,
            metadata=metadata,
            extensions=extensions,
        )

        await self._task_manager.add_task(chained_task)
        return task_id
