from __future__ import annotations

from typing import Optional

from openjiuwen.agent_teams.messager.base import (
    MessagerTransportConfig,
)
from openjiuwen.agent_teams.messager.messager import (
    Messager,
    MessagerHandler,
)
from openjiuwen.agent_teams.tools.team_events import EventMessage
from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime


class TeamRuntimeMessager(Messager):
    """Adapter that maps messager calls onto ``TeamRuntime`` messaging."""

    def __init__(
        self,
        runtime: TeamRuntime,
        *,
        config: Optional[MessagerTransportConfig] = None,
    ) -> None:
        self._runtime = runtime
        self._config = config or MessagerTransportConfig()
        self._subscribed_topics: list[str] = []

    async def start(self) -> None:
        await self._runtime.start()

    async def stop(self) -> None:
        await self._runtime.stop()

    async def send(
        self,
        agent_id: str,
        message: EventMessage,
    ) -> None:
        sender = self._config.node_id or ""
        await self._runtime.send(
            message=message.model_dump(mode="python"),
            recipient=agent_id,
            sender=sender,
        )

    async def publish(
        self,
        topic_id: str,
        message: EventMessage,
    ) -> None:
        sender = self._config.node_id or ""
        await self._runtime.publish(
            message=message.model_dump(mode="python"),
            topic_id=topic_id or self._config.broadcast_topic(),
            sender=sender,
        )

    async def subscribe(
        self,
        topic_id: str,
        handler: MessagerHandler,
    ) -> None:
        del handler
        agent_id = self._config.node_id or ""
        await self._runtime.subscribe(
            agent_id=agent_id,
            topic=topic_id,
        )
        self._subscribed_topics.append(topic_id)

    async def unsubscribe(self, topic_id: str) -> None:
        agent_id = self._config.node_id or ""
        await self._runtime.unsubscribe(
            agent_id=agent_id,
            topic=topic_id,
        )
        try:
            self._subscribed_topics.remove(topic_id)
        except ValueError:
            pass

    async def register_direct_message_handler(
        self,
        handler: MessagerHandler,
    ) -> None:
        del handler

    async def unregister_direct_message_handler(
        self,
    ) -> None:
        pass
