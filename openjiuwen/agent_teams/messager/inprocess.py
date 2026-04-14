# coding: utf-8
"""In-process messager — pure handler-based pub-sub and P2P.

All ``InProcessMessager`` instances share a process-global ``_Bus``.
Messages are delivered by direct handler invocation — no serialization,
no message queues, no Runner dependency.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.messager.messager import Messager, MessagerHandler
from openjiuwen.agent_teams.schema.events import EventMessage
from openjiuwen.core.common.logging import team_logger


class _Bus:
    """Process-global in-process message bus.

    Two data structures, both keyed by agent_id for O(1) lookup:

    - ``_topic_subs``: topic → {agent_id → handler}  (pub-sub fan-out)
    - ``_p2p``:        agent_id → handler             (point-to-point)
    """

    def __init__(self) -> None:
        self._topic_subs: dict[str, dict[str, MessagerHandler]] = {}
        self._p2p: dict[str, MessagerHandler] = {}

    # -- pub-sub --------------------------------------------------------

    def subscribe(self, agent_id: str, topic: str, handler: MessagerHandler) -> None:
        self._topic_subs.setdefault(topic, {})[agent_id] = handler

    def unsubscribe(self, agent_id: str, topic: str) -> None:
        subs = self._topic_subs.get(topic)
        if subs:
            subs.pop(agent_id, None)
            if not subs:
                del self._topic_subs[topic]

    async def publish(self, topic: str, message: EventMessage) -> None:
        subs = self._topic_subs.get(topic)
        if not subs:
            return
        for agent_id, handler in list(subs.items()):
            try:
                await handler(message)
            except Exception as exc:
                team_logger.error("[_Bus] publish to {} on topic {} failed: {}", agent_id, topic, exc)

    # -- point-to-point -------------------------------------------------

    def register_p2p(self, agent_id: str, handler: MessagerHandler) -> None:
        self._p2p[agent_id] = handler

    def unregister_p2p(self, agent_id: str) -> None:
        self._p2p.pop(agent_id, None)

    async def send(self, agent_id: str, message: EventMessage) -> None:
        handler = self._p2p.get(agent_id)
        if handler is None:
            team_logger.warning("[_Bus] no P2P handler for agent_id={}", agent_id)
            return
        await handler(message)

    # -- lifecycle ------------------------------------------------------

    def clear(self) -> None:
        self._topic_subs.clear()
        self._p2p.clear()


# ---- process-global singleton -----------------------------------------

_bus: Optional[_Bus] = None


def _get_bus() -> _Bus:
    global _bus
    if _bus is None:
        _bus = _Bus()
    return _bus


def cleanup_inprocess_bus() -> None:
    """Reset the process-global bus (e.g. between test runs)."""
    global _bus
    if _bus is not None:
        _bus.clear()
    _bus = None


# ---- Messager implementation ------------------------------------------


class InProcessMessager(Messager):
    """In-process messager using direct handler callbacks.

    All instances share a process-global ``_Bus``.  ``EventMessage``
    objects are passed directly — no serialization round-trip.
    """

    def __init__(self, *, config: Optional[MessagerTransportConfig] = None) -> None:
        self._config = config or MessagerTransportConfig()
        self._bus = _get_bus()
        self._subscribed_topics: list[str] = []

    @property
    def _agent_id(self) -> str:
        return self._config.node_id or ""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        # Stamp sender_id so subscribers can filter self-published events —
        # mirrors the pyzmq backend so _filter_self works identically in
        # inprocess mode. Without this a leader receives its own events
        # (TeamCleanedEvent in particular) and tears itself down. Tolerant
        # of messages without a ``sender_id`` field (tests use a subclass
        # of BaseEventMessage for plain pub-sub smoke checks).
        if hasattr(message, "sender_id") and not message.sender_id:
            message = message.model_copy(update={"sender_id": self._agent_id})
        await self._bus.publish(topic_id, message)

    async def subscribe(self, topic_id: str, handler: MessagerHandler) -> None:
        self._bus.subscribe(self._agent_id, topic_id, handler)
        self._subscribed_topics.append(topic_id)

    async def unsubscribe(self, topic_id: str) -> None:
        self._bus.unsubscribe(self._agent_id, topic_id)
        try:
            self._subscribed_topics.remove(topic_id)
        except ValueError:
            pass

    async def send(self, agent_id: str, message: EventMessage) -> None:
        await self._bus.send(agent_id, message)

    async def register_direct_message_handler(self, handler: MessagerHandler) -> None:
        self._bus.register_p2p(self._agent_id, handler)

    async def unregister_direct_message_handler(self) -> None:
        self._bus.unregister_p2p(self._agent_id)
