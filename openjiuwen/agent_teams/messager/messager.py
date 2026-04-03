# coding: utf-8
"""Abstract Messager interface for team event publishing."""

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Awaitable,
    Callable,
)

from openjiuwen.agent_teams.schema.events import EventMessage

MessagerHandler = Callable[[EventMessage], Awaitable[None]]


class Messager(ABC):
    """Abstract interface for event publishing in the agent_teams tools layer.

    Decouples the tools layer from any specific message transport
    implementation (MessageBus, PyZmqMessagerTransport, etc.).
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the messager transport."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the messager transport."""
        ...

    @abstractmethod
    async def publish(
        self,
        topic_id: str,
        message: EventMessage,
    ) -> None:
        """Publish an event message to a topic.

        Args:
            topic_id: The topic string built via TeamTopic.build().
            message: The EventMessage wrapping a concrete event payload.
        """
        ...

    @abstractmethod
    async def subscribe(
        self,
        topic_id: str,
        handler: MessagerHandler,
    ) -> None:
        """Subscribe to a topic.

        Args:
            topic_id: The event topic / channel name.
            handler: The callback function to handle incoming messages.
        """
        ...

    @abstractmethod
    async def unsubscribe(self, topic_id: str) -> None:
        """Unsubscribe to a topic.

        Args:
            topic_id: The event topic / channel name.
        """
        ...

    @abstractmethod
    async def send(self, agent_id: str, message: EventMessage) -> None:
        """Send an event message to an agent.

        Args:
            agent_id: The agent id of the message to send.
            message: The EventMessage wrapping a concrete event payload.
        """
        ...

    @abstractmethod
    async def register_direct_message_handler(
        self,
        handler: MessagerHandler,
    ) -> None:
        """Register a message handler for a direct message transport.

        Args:
            handler: The callback function to handle incoming messages.
        """
        ...

    @abstractmethod
    async def unregister_direct_message_handler(
        self,
    ) -> None:
        """Unregister a message handler for a direct message transport. """
        ...
