# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Event queue module.

This module implements the event queue responsible for publishing and
subscribing events:

- EventQueue: event queue that publishes and subscribes controller events.

Workflow:
    1. Event subscription: subscribe to specific event types via ``subscribe``.
    2. Event publishing: publish events into the message queue via
       ``publish_event``.
    3. Event handling: subscribed events are handled by the appropriate
       ``EventHandler`` methods.
    4. Event unsubscription: cancel subscriptions via ``unsubscribe``.

Supported event types:
- INPUT: user input events.
- TASK_INTERACTION: task interaction events (when execution requires user
  interaction).
- TASK_COMPLETION: task completion events.
- TASK_FAILED: task failure events.
"""
from typing import Optional, Callable, Awaitable

from openjiuwen.core.controller.schema.event import Event, EventType
from openjiuwen.core.controller.modules.event_handler import EventHandler, EventHandlerInput


class EventQueue:
    """Event queue for the controller.

    Responsible for publishing and subscribing events, and dispatching them
    to the appropriate event handler methods.

    Implemented on top of a message queue abstraction and supports:
        - Publishing events (``publish_event``).
        - Subscribing to events (``subscribe``).
        - Unsubscribing from events (``unsubscribe`` / ``unsubscribe_all``).
        - Handling multiple event types.

    Event topic format: ``{agent_id}_{session_id}_{event_type}``.
    """
    
    def __init__(
            self,
            config,
    ):
        """Initialize the event queue.

        Args:
            config: Controller configuration.
        """
        self._config = config
        self._queue = None
        self._event_handler: Optional[EventHandler] = None

    def set_event_handler(self, event_handler: EventHandler):
        """Attach an event handler.

        Args:
            event_handler: Event handler instance.
        """
        self._event_handler = event_handler

    def _subscribe_event(
            self,
            topic: str,
            event_handle_func: Callable[[EventHandlerInput], Awaitable[...]]
    ):
        """Subscribe to a single event topic.

        Args:
            topic: Event topic string.
            event_handle_func: Event handler coroutine for the topic.

        Returns:
            str: The topic string.
        """
        # Create subscription
        subscription = self._queue.subscribe(topic)

        # Set message handler (passing topic to access cached sessions if needed)
        subscription.set_message_handler(event_handle_func)

        # Activate subscription
        subscription.activate()

        # Return topic for bookkeeping
        return topic

    async def subscribe(
            self,
            agent_id: str,
            session_id: str
    ) -> (dict[str, str], dict[str, str]):
        """Subscribe to all event types for a given agent/session.

        Args:
            agent_id: Agent identifier.
            session_id: Session identifier.

        Returns:
            Tuple[dict, dict]: (subscriptions, topics) where:
                - subscriptions maps ``EventType`` to subscription handles.
                - topics maps ``EventType`` to topic strings.
        """
        topics = {}
        subscriptions = {}
        # Subscribe to input events
        topic = self._build_topic(agent_id, session_id, EventType.INPUT)
        sub = self._subscribe_event(topic, self._event_handler.handle_input)
        subscriptions[EventType.INPUT] = sub
        topics[EventType.INPUT] = topic

        # Subscribe to task interaction events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_INTERACTION)
        sub = self._subscribe_event(topic, self._event_handler.handle_task_interaction)
        subscriptions[EventType.TASK_INTERACTION] = sub
        topics[EventType.TASK_INTERACTION] = topic

        # Subscribe to task completion events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_COMPLETION)
        sub = self._subscribe_event(topic, self._event_handler.handle_task_completion)
        subscriptions[EventType.TASK_COMPLETION] = sub
        topics[EventType.TASK_COMPLETION] = topic

        # Subscribe to task failure events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_FAILED)
        sub = self._subscribe_event(topic, self._event_handler.handle_task_failed)
        subscriptions[EventType.TASK_FAILED] = sub
        topics[EventType.TASK_FAILED] = topic

        return subscriptions, topics

    async def _unsubscribe_event(
            self,
            topic: str
    ):
        """Unsubscribe from a single event topic.

        Args:
            topic: Event topic string.

        Returns:
            bool: Whether the operation succeeded.
        """
        # Cancel subscription
        await self._queue.unsubscribe(topic)
        return True

    async def unsubscribe(
            self,
            agent_id: str,
            session_id: str,
    ):
        """Unsubscribe from all event types for a given agent/session.

        Args:
            agent_id: Agent identifier.
            session_id: Session identifier.

        Returns:
            dict: Topic dictionary (for compatibility; currently empty).
        """
        topics = {}
        # Unsubscribe from input events
        topic = self._build_topic(agent_id, session_id, EventType.INPUT)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task interaction events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_INTERACTION)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task completion events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_COMPLETION)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task failure events
        topic = self._build_topic(agent_id, session_id, EventType.TASK_FAILED)
        await self._unsubscribe_event(topic)

        return topics
    
    async def publish_event(
        self,
        agent_id: str,
        session_id: str,
        event: Event
    ) -> None:
        """Publish an event into the queue.

        Args:
            agent_id: Agent identifier.
            session_id: Session identifier.
            event: Event to be published.
        """
        topic = self._build_topic(agent_id, session_id, event.event_type)
        # 发布消息
        await self._queue.produce_message(topic, event.model_dump())

    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all topics (if supported by the underlying queue)."""
        ...

    @staticmethod
    def _build_topic(agent_id: str, session_id: str, event_type: str) -> str:
        """Build an event topic string.

        Args:
            agent_id: Agent identifier.
            session_id: Session identifier.
            event_type: Event type string.

        Returns:
            str: Fully qualified topic string.
        """
        return f"{agent_id}_{session_id}_{event_type}"


