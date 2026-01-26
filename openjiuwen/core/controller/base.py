# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Controller core definitions.

This module defines the core configuration and runtime controller:

- ControllerConfig: configuration for controller behavior
- Controller: high-level controller that orchestrates events and task lifecycle

The controller is responsible for handling events, managing the lifecycle of
tasks, and coordinating intent recognition and processing.
"""
from typing import AsyncIterator, Optional, List, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller import InputEvent

from openjiuwen.core.controller.modules.event_queue import EventQueue, EventHandler
from openjiuwen.core.controller.schema.controller_output import ControllerOutput, ControllerOutputChunk
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.modules.task_scheduler import TaskScheduler
from openjiuwen.core.controller.modules.task_executor import TaskExecutor
from openjiuwen.core.session.session import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.agent import AbilityManager


class ControllerConfig(BaseModel):
    """Controller configuration.

    Defines configuration parameters that control controller behavior.
    Configuration items are grouped into several categories: task scheduling,
    task management, event queue, and intent recognition.

    Attributes:
        # ==================== Task scheduling config ====================
        max_concurrent_tasks: Maximum number of concurrent tasks. Controls the
            upper limit of tasks executed at the same time. Default is 5. Set
            to 0 to indicate no limit.
        schedule_interval: Task scheduling interval (seconds). The interval at
            which the scheduler periodically scans for tasks to run. Default is
            1.0 seconds. Smaller values improve responsiveness but increase CPU
            usage.
        task_timeout: Task timeout (seconds). Tasks exceeding this duration
            will be marked as failed. Default is None, meaning no timeout.

        # ==================== Task management config ====================
        default_task_priority: Default task priority used when creating a task
            without an explicit priority. Default is 1. Larger values mean
            higher priority.
        enable_task_persistence: Whether to enable task persistence. When
            enabled, task state is stored so that it can be recovered later.
            Default is False.

        # ==================== Event queue config ====================
        event_queue_size: Event queue capacity. Limits the number of events
            that can be stored in the queue. Default is None, meaning no
            limit.
        event_timeout: Event processing timeout (seconds). Events that are not
            processed within this period will be dropped. Default is None,
            meaning no timeout.

        # ==================== Intent recognition config ====================
        enable_intent_recognition: Whether to enable intent recognition.
            Default is True. When enabled, user intent is automatically
            detected and routed to the corresponding handler.
        intent_confidence_threshold: Confidence threshold for intent
            recognition. Intents below this value are treated as
            UNKNOWN_TASK. Default is 0.7, range 0.0–1.0.

    Example:
        ```python
        config = ControllerConfig(
            max_concurrent_tasks=10,
            schedule_interval=0.5,
            default_task_priority=5,
            enable_intent_recognition=True
        )
        ```
    """
    # ==================== Task scheduling config ====================
    max_concurrent_tasks: int = Field(
        default=5,
        description=(
            "Maximum number of concurrent tasks. Controls the upper limit of "
            "tasks executed at the same time. Set to 0 for no limit."
        ),
    )
    schedule_interval: float = Field(
        default=1.0,
        ge=0.1,
        description=(
            "Task scheduling interval (seconds). The scheduler periodically "
            "scans for tasks to run with this interval."
        ),
    )
    task_timeout: Optional[float] = Field(
        default=None,
        ge=600,
        description=(
            "Task timeout (seconds). Tasks exceeding this duration are marked "
            "as failed. None means no timeout."
        ),
    )

    # ==================== Task management config ====================
    default_task_priority: int = Field(
        default=1,
        description=(
            "Default task priority. Used when no priority is given at task "
            "creation. Larger values mean higher priority."
        ),
    )

    # ==================== Event queue config ====================
    event_queue_size: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Event queue capacity. Limits the number of events stored in the "
            "queue. None means no limit."
        ),
    )
    event_timeout: Optional[float] = Field(
        default=None,
        ge=600,
        description=(
            "Event processing timeout (seconds). Events not processed within "
            "this period are dropped. None means no timeout."
        ),
    )

    # ==================== Intent recognition config ====================
    intent_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence threshold for intent recognition. Intents below this "
            "value are treated as UNKNOWN_TASK. Range 0.0–1.0."
        ),
    )


class Controller:
    """High-level controller.

    Responsible for handling events and managing the lifecycle of tasks.
    This is the core component used by a ControllerAgent.
    """
    
    def __init__(self):
        """Initialize the controller."""
        super().__init__()
        self._card: Optional[str] = None
        self._ability_manager: Optional[AbilityManager] = None
        self._config: Optional[ControllerConfig] = None
        self._context_engine: Optional[ContextEngine] = None
        self._task_manager: Optional[TaskManager] = None
        self._event_queue: Optional[EventQueue] = None
        self._task_scheduler: Optional[TaskScheduler] = None
        self._event_handler: Optional[EventHandler] = None

    def init(
            self,
            card: AgentCard,
            config: ControllerConfig,
            ability_manager: AbilityManager,
            context_engine: ContextEngine
    ):
        """Initialize controller dependencies.

        Args:
            card: Agent card with identity and configuration.
            config: Controller configuration.
            ability_manager: Ability manager containing tools, workflows, etc.
            context_engine: Context engine used to manage conversation context.
        """
        self._card = card
        self._config = config
        self._ability_manager = ability_manager
        self._context_engine = context_engine
        self._task_manager = TaskManager(config=self._config)
        self._event_queue = EventQueue(config=self._config)
        self._task_scheduler = TaskScheduler(
            config=self._config,
            task_manager=self._task_manager,
            context_engine=self._context_engine,
            ability_manager=self._ability_manager,
            event_queue=self._event_queue
        )

    @property
    def event_queue(self) -> EventQueue:
        """Return the underlying event queue."""
        return self._event_queue

    @property
    def config(self) -> ControllerConfig:
        """Return controller configuration."""
        return self._config

    @property
    def context_engine(self) -> ContextEngine:
        """Return the context engine."""
        return self._context_engine

    @property
    def ability_manager(self) -> AbilityManager:
        """Return the ability manager."""
        return self._ability_manager

    @config.setter
    def config(self, config: ControllerConfig):
        """Set controller configuration."""
        self._config = config

    @context_engine.setter
    def context_engine(self, context_engine: ContextEngine):
        """Set context engine."""
        self._context_engine = context_engine

    @ability_manager.setter
    def ability_manager(self, ability_manager: AbilityManager):
        """Set ability manager."""
        self._ability_manager = ability_manager

    def set_event_handler(self, event_handler: EventHandler):
        """Bind an event handler to the controller.

        This injects controller dependencies into the event handler.

        Args:
            event_handler: Event handler instance.
        """
        self._event_handler = event_handler
        self._event_handler.config = self._config
        self._event_handler.context_engine = self._context_engine
        self._event_handler.task_scheduler = self._task_scheduler
        self._event_handler.task_manager = self._task_manager
        self._event_handler.ability_manager = self._ability_manager

    def add_task_executor(
            self,
            task_type: str,
            task_executor_builder: Callable[
                [ControllerConfig, AbilityManager, ContextEngine, TaskManager, EventQueue], TaskExecutor
            ]
    ) -> "Controller":
        """Register a task executor for a given task type.

        Args:
            task_type: Logical task type identifier.
            task_executor_builder: Factory function that builds a
                ``TaskExecutor`` using controller dependencies.

        Returns:
            ``self`` to support fluent-style chaining.
        """
        self._task_scheduler.task_executor_registry.add_task_executor(task_type, task_executor_builder)
        return self

    def remove_task_executor(self, task_type: str):
        """Unregister a task executor for the given task type.

        Args:
            task_type: Logical task type identifier.
        """
        self._task_scheduler.task_executor_registry.remove_task_executor(task_type)

    def start(self):
        """Start the controller.

        This starts the scheduler and begins processing tasks.
        """
        self._task_scheduler.start()

    def stop(self):
        """Stop the controller.

        This stops the scheduler and stops processing tasks.
        """
        self._task_scheduler.stop()

    async def invoke(
            self,
            inputs: InputEvent,
            session: Session,
            **kwargs
    ) -> ControllerOutput:
        """Execute the controller in batch mode.

        High-level helper that consumes the stream interface and aggregates
        results into a single ``ControllerOutput``.

        Args:
            inputs: Input event.
            session: Session object.
            **kwargs: Additional parameters passed through.

        Returns:
            ControllerOutput: Aggregated controller output.

        Note:
            1. Calls the ``stream`` method.
            2. Converts streamed messages into batch output.
        """
        ...

    async def stream(
            self,
            inputs: InputEvent,
            session: Session,
            stream_modes: Optional[List[StreamMode]] = None,
            **kwargs
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Execute the controller in streaming mode.

        This is the core execution entrypoint that wires events, sessions,
        scheduler, and event queue together.

        Args:
            inputs: Input event.
            session: Session object.
            stream_modes: Optional list of stream output modes.
            **kwargs: Additional parameters passed through.

        Yields:
            ControllerOutputChunk: Individual controller output chunks.

        Note:
            1. Restore controller state (including ``TaskManager`` state, etc.).
            2. Register the ``Session`` in the scheduler ``sessions`` mapping.
            3. Subscribe to events via ``self._event_queue.subscribe``.
            4. Push the input event into the event queue.
            5. Consume event handler results and stream them out.
            6. Deactivate all subscriptions.
            7. Persist controller state (including ``TaskManager`` state).
            8. Remove the ``Session`` from the scheduler ``sessions`` mapping.
        """
        ...




