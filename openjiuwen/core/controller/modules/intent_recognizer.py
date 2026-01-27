# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Intent recognition module.

This module implements event handling based on intent recognition, including:

- IntentRecognizer: recognizes user intent from input events.
- EventHandlerWithIntentRecognition: event handler that routes logic by
  recognized intent.

Workflow:
    1. Receive an input event.
    2. Use ``IntentRecognizer`` to recognize intent.
    3. Call the corresponding handler method based on intent type.

Supported intent types (see ``IntentType`` for details):
- CREATE_TASK
- PAUSE_TASK
- RESUME_TASK
- CONTINUE_TASK
- SUPPLEMENT_TASK
- CANCEL_TASK
- MODIFY_TASK
- SWITCH_TASK
- UNKNOWN_TASK
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.base import ControllerConfig
from openjiuwen.core.controller.modules.event_handler import EventHandler, EventHandlerInput
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.controller.schema import Intent
from openjiuwen.core.controller.schema.event import Event
from openjiuwen.core.session import Session

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.agent import AbilityManager


class IntentRecognizer:
    """Intent recognizer.

    Responsible for recognizing user intent from input events and converting
    them into ``Intent`` objects.
    """
    
    def __init__(
            self,
            config: ControllerConfig,
            task_manager: TaskManager,
            ability_manager: 'AbilityManager',
            context_engine: ContextEngine
    ):
        """Initialize the intent recognizer.

        Args:
            config: Controller configuration.
            task_manager: Task manager.
            ability_manager: Ability manager.
            context_engine: Context engine.
        """
        self._config = config
        self._task_manager = task_manager
        self._context_engine = context_engine
        self._ability_manager = ability_manager

    async def recognize(self, event: Event, session: Session) -> Intent:
        """Recognize intent from an event.

        Args:
            event: Input event.
            session: Session object.

        Returns:
            Intent: Recognized intent object.
        """
        ...


class EventHandlerWithIntentRecognition(EventHandler):
    """Event handler with intent recognition.

    Extends ``EventHandler`` by adding an intent recognition step and routing
    to specific handlers based on the recognized intent.
    """
    def __init__(self):
        super().__init__()
        self.recognizer = IntentRecognizer(
            self._config,
            self.task_manager,
            self.ability_manager,
            self.context_engine
        )

    async def handle_input(self, inputs: EventHandlerInput):
        """Handle input events.

        Recognizes intent from the input and dispatches to the corresponding
        handler. Subclasses may override this for custom behavior.

        Args:
            inputs: Wrapper containing event and session.
        """
        ...

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        """Handle task interaction events.

        Default behavior is to surface the interaction directly to the user.
        Subclasses may override this.

        Args:
            inputs: Wrapper containing event and session.
        """
        ...

    async def handle_task_completion(self, inputs: EventHandlerInput):
        """Handle task completion events.

        Default behavior is to surface completion information to the user.
        Subclasses may override this.

        Args:
            inputs: Wrapper containing event and session.
        """
        ...

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure events.

        Default behavior is to surface error information to the user.
        Subclasses may override this.

        Args:
            inputs: Wrapper containing event and session.
        """
        ...

    @abstractmethod
    async def _process_create_task_intent(self, inputs: EventHandlerInput):
        """Process CREATE_TASK intent.

        Subclasses should implement custom logic to execute a new task.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_pause_task_intent(self, inputs: EventHandlerInput):
        """Process PAUSE_TASK intent.

        Typically calls ``task_scheduler.pause_task`` to interrupt a target
        task. Subclasses may customize.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_resume_task_intent(self, inputs: EventHandlerInput):
        """Process RESUME_TASK intent.

        Typically sets the target task status back to ``submitted`` so it can
        be picked up by the scheduler.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_continue_task_intent(self, inputs: EventHandlerInput):
        """Process CONTINUE_TASK intent.

        Typically uses context from a dependent task and delegates to
        ``_process_create_task_intent`` for the new task.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_supplement_task_intent(self, inputs: EventHandlerInput):
        """Process SUPPLEMENT_TASK intent.

        Typically uses additional information supplied by the user and calls
        ``_process_create_task_intent`` to continue the target task.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_cancel_task_intent(self, inputs: EventHandlerInput):
        """Process CANCEL_TASK intent.

        Typically calls ``task_scheduler.cancel_task`` to cancel the target
        task.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_modify_task_intent(self, inputs: EventHandlerInput):
        """Process MODIFY_TASK intent.

        Typically updates the target task parameters or configuration and
        then sets its status to ``submitted`` to re-run it.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_switch_task_intent(self, inputs: EventHandlerInput):
        """Process SWITCH_TASK intent.

        Typically interrupts all currently running tasks and then calls
        ``_process_create_task_intent`` for the target task.

        Args:
            inputs: Event handler input.
        """
        ...

    async def _process_unknown_task_intent(self, event: Event):
        """Process UNKNOWN_TASK intent.

        Typically returns the ``clarification_prompt`` field of the
        corresponding ``Intent`` to ask the user for more details.

        Args:
            event: Input event.
        """
        ...

