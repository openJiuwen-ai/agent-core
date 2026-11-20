# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event Scheduler Schema Definitions

This module defines data models for the event-driven task scheduler, including:
- ScheduleType: Schedule type enumeration (immediate, delayed, cron)
- RetryPolicy: Retry policy for failed tasks with exponential backoff
- EventChainRule: Rule for chaining tasks based on completion events
- ScheduledTaskMixin: Scheduling metadata stored in Task.extensions
- EventSchedulerConfig: Configuration for the event scheduler

These models integrate with the existing Task model through the Task.extensions
field, avoiding any modifications to the core Task schema.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class ScheduleType(str, Enum):
    """Schedule Type Enumeration

    Defines how a task should be scheduled for execution:
    - IMMEDIATE: Execute as soon as possible (default behavior)
    - DELAYED: Execute after a specified delay in seconds
    - CRON: Execute on a recurring schedule using cron expressions
    """
    IMMEDIATE = "immediate"
    DELAYED = "delayed"
    CRON = "cron"


class RetryPolicy(BaseModel):
    """Retry Policy for Failed Tasks

    Defines how failed tasks should be retried, supporting exponential
    backoff with configurable limits.

    Attributes:
        max_retries: Maximum number of retry attempts before giving up
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds (caps exponential growth)
        backoff_multiplier: Multiplier applied to delay after each retry
    """
    max_retries: int = Field(
        default=3,
        ge=0,
        description="Maximum number of retry attempts. 0 means no retries."
    )
    base_delay: float = Field(
        default=1.0,
        gt=0,
        description="Initial delay between retries in seconds."
    )
    max_delay: float = Field(
        default=60.0,
        gt=0,
        description="Maximum delay between retries in seconds."
    )
    backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        description="Multiplier applied to delay after each retry attempt."
    )

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt using exponential backoff

        Args:
            attempt: The current retry attempt number (0-indexed)

        Returns:
            float: Delay in seconds before the next retry
        """
        delay = self.base_delay * (self.backoff_multiplier ** attempt)
        return min(delay, self.max_delay)

    @model_validator(mode='after')
    def validate_delay_bounds(self):
        """Validate that base_delay does not exceed max_delay

        Returns:
            RetryPolicy: The validated retry policy

        Raises:
            ValueError: If base_delay exceeds max_delay
        """
        if self.base_delay > self.max_delay:
            raise ValueError("base_delay cannot exceed max_delay")
        return self


class EventChainRule(BaseModel):
    """Event Chain Rule

    Defines a rule for automatically triggering a downstream task when a
    source task completes. This enables declarative task chaining without
    requiring custom event handler logic.

    Attributes:
        rule_id: Unique identifier for this chain rule
        source_task_type: Task type that triggers the chain when completed
        target_task_type: Task type to create when the source completes
        target_description: Description for the auto-created target task
        condition: Optional condition expression evaluated against source task metadata
        target_metadata: Optional metadata to pass to the target task
    """
    rule_id: str
    source_task_type: str
    target_task_type: str
    target_description: Optional[str] = None
    condition: Optional[str] = Field(
        default=None,
        description="Optional condition evaluated against source task metadata. "
                    "Supports simple key-value checks in format 'key=value'."
    )
    target_metadata: Optional[Dict[str, Any]] = None

    @field_validator('rule_id', 'source_task_type', 'target_task_type')
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """Validate that required string fields are not empty

        Args:
            v: The field value to validate

        Returns:
            str: The validated value

        Raises:
            ValueError: If the value is empty or None
        """
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()

    def evaluate_condition(self, metadata: Optional[Dict[str, Any]]) -> bool:
        """Evaluate the chain condition against task metadata

        If no condition is set, the rule always matches. Conditions are
        simple key=value expressions checked against the metadata dict.

        Args:
            metadata: Source task metadata dictionary

        Returns:
            bool: True if the condition is met or no condition is set
        """
        if not self.condition:
            return True
        if not metadata:
            return False

        if '=' in self.condition:
            key, value = self.condition.split('=', 1)
            return str(metadata.get(key.strip(), '')) == value.strip()

        return self.condition in metadata


class ScheduledTaskMixin(BaseModel):
    """Scheduling Metadata for Tasks

    Stored in the Task.extensions field under the 'event_scheduler' key.
    This approach avoids modifying the core Task schema while adding
    scheduling capabilities.

    Attributes:
        schedule_type: How the task should be scheduled
        delay_seconds: Delay before execution (for DELAYED type)
        cron_expression: Cron expression for recurring execution (for CRON type)
        retry_policy: Retry policy for failed tasks
        retry_count: Current retry attempt count
        scheduled_at: ISO timestamp when the task was scheduled
        execute_after: ISO timestamp after which the task should execute
        chain_source_task_id: ID of the task that triggered this task via chaining
    """
    schedule_type: ScheduleType = ScheduleType.IMMEDIATE
    delay_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Delay before execution in seconds. Required for DELAYED type."
    )
    cron_expression: Optional[str] = Field(
        default=None,
        description="Cron expression for recurring tasks. Required for CRON type."
    )
    retry_policy: Optional[RetryPolicy] = None
    retry_count: int = Field(default=0, ge=0)
    scheduled_at: Optional[str] = None
    execute_after: Optional[str] = None
    chain_source_task_id: Optional[str] = None

    @model_validator(mode='after')
    def validate_schedule_params(self):
        """Validate schedule type specific parameters

        Ensures that DELAYED type has delay_seconds and CRON type
        has cron_expression.

        Returns:
            ScheduledTaskMixin: The validated instance

        Raises:
            ValueError: If required parameters are missing for the schedule type
        """
        if self.schedule_type == ScheduleType.DELAYED and self.delay_seconds is None:
            raise ValueError("delay_seconds is required for DELAYED schedule type")
        if self.schedule_type == ScheduleType.CRON and not self.cron_expression:
            raise ValueError("cron_expression is required for CRON schedule type")
        return self


class EventSchedulerConfig(BaseModel):
    """Event Scheduler Configuration

    Configuration parameters for the event-driven scheduler extension.

    Attributes:
        enable_delayed_scheduling: Whether to enable delayed task execution
        enable_event_chaining: Whether to enable automatic task chaining
        enable_retry: Whether to enable automatic retry of failed tasks
        chain_rules: List of event chain rules for automatic task creation
        default_retry_policy: Default retry policy applied to all tasks
        scheduler_poll_interval: Interval in seconds between scheduler polls
        max_scheduled_tasks: Maximum number of scheduled tasks to track
    """
    enable_delayed_scheduling: bool = Field(
        default=True,
        description="Enable delayed task execution based on schedule_type."
    )
    enable_event_chaining: bool = Field(
        default=True,
        description="Enable automatic task chaining on completion events."
    )
    enable_retry: bool = Field(
        default=True,
        description="Enable automatic retry of failed tasks."
    )
    chain_rules: List[EventChainRule] = Field(
        default_factory=list,
        description="List of event chain rules for automatic task creation."
    )
    default_retry_policy: Optional[RetryPolicy] = None
    scheduler_poll_interval: float = Field(
        default=0.5,
        ge=0.1,
        description="Interval in seconds between scheduler polls for delayed tasks."
    )
    max_scheduled_tasks: int = Field(
        default=1000,
        ge=1,
        description="Maximum number of scheduled tasks to track."
    )
