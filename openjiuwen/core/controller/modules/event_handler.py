# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Event handler module.

This module defines classes related to event handling:

- EventHandlerInput: data model for event handler inputs.
- EventHandler: abstract base class for concrete event handlers.

Event handlers are responsible for processing different event types:
- INPUT: user input events.
- TASK_INTERACTION: task interaction events (when execution requires user
  interaction).
- TASK_COMPLETION: task completion events.
- TASK_FAILED: task failure events.
"""
from abc import abstractmethod, ABC
from typing import TYPE_CHECKING

from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.schema import Event
from openjiuwen.core.session import Session
from openjiuwen.core.single_agent.agent import AbilityManager

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager
    from openjiuwen.core.controller.modules.task_scheduler import TaskScheduler


class EventHandlerInput(BaseModel):
    """Input payload for event handlers.

    Encapsulates both the event and the associated session so they can be
    passed together to handler methods.

    Attributes:
        event: Event object.
        session: Session object.
    """
    model_config = {"arbitrary_types_allowed": True}


    event: Event
    session: Session


class EventHandler(ABC):
    """Abstract base class for event handlers.

    Defines the interface for handling different types of events. Concrete
    controllers should implement their own event handlers.

    Responsibilities:
        - Handle input events (``handle_input``).
        - Handle task interaction events (``handle_task_interaction``).
        - Handle task completion events (``handle_task_completion``).
        - Handle task failure events (``handle_task_failed``).
    """
    def __init__(self):
        """Initialize the event handler.

        All dependencies are initialized as ``None`` and should be injected
        later via property setters.
        """
        self._config = None
        self._context_engine = None
        self._ability_manager = None
        self._task_manager = None
        self._task_scheduler = None

    @property
    def config(self) -> ControllerConfig:
        return self._config

    @property
    def context_engine(self) -> ContextEngine:
        return self._context_engine

    @property
    def task_manager(self) -> "TaskManager":
        return self._task_manager

    @property
    def ability_manager(self) -> AbilityManager:
        return self._ability_manager

    @property
    def task_scheduler(self) -> "TaskScheduler":
        return self._task_scheduler

    @config.setter
    def config(self, config: ControllerConfig):
        self._config = config

    @context_engine.setter
    def context_engine(self, context_engine: ContextEngine):
        self._context_engine = context_engine

    @task_manager.setter
    def task_manager(self, task_manager: "TaskManager"):
        self._task_manager = task_manager

    @ability_manager.setter
    def ability_manager(self, ability_manager: AbilityManager):
        self._ability_manager = ability_manager

    @task_scheduler.setter
    def task_scheduler(self, task_scheduler: "TaskScheduler"):
        self._task_scheduler = task_scheduler

    @abstractmethod
    async def handle_input(self, inputs: EventHandlerInput):
        """Handle user input events.

        Args:
            inputs: Event handler input containing event and session.
        """
        ...

    @abstractmethod
    async def handle_task_interaction(self, inputs: EventHandlerInput):
        """Handle task interaction events.

        Triggered when a running task requires user interaction.

        Args:
            inputs: Event handler input containing event and session.
        """
        ...

    @abstractmethod
    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion events.

        Triggered when a task finishes successfully.

        Args:
            inputs: Event handler input containing event and session.
        """
        ...

    @abstractmethod
    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure events.

        Triggered when a task fails during execution.

        Args:
            inputs: Event handler input containing event and session.
        """
        ...
