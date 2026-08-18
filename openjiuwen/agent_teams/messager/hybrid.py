# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WebSocket publisher used by external team clients."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol
import uuid

import aiohttp

from openjiuwen.agent_teams.interaction.payload import ExternalTeamEvent
from openjiuwen.agent_teams.messager.messager import Messager, MessagerHandler
from openjiuwen.agent_teams.schema.events import EventMessage, TeamTopic


class EventPublisher(Protocol):
    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        ...


class WebSocketEventPublisher:
    """Publish standard team events through the configured Gateway relay."""

    def __init__(
        self,
        *,
        url: str,
        session_id: str,
        team_name: str,
        request_timeout: float,
    ) -> None:
        self._url = url.strip()
        self._session_id = session_id
        self._team_name = team_name
        self._request_timeout = request_timeout
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._url.startswith(("ws://", "wss://")):
            raise ValueError("external_publish_url must use ws:// or wss://")
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        if self._ws is None or self._ws.closed:
            self._ws = await self._session.ws_connect(self._url, heartbeat=30.0)

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _build_request(self, topic_id: str, message: EventMessage, request_id: str) -> dict:
        try:
            topic = TeamTopic(topic_id.rsplit(":", 1)[-1])
        except ValueError as exc:
            raise ValueError(f"unsupported team topic: {topic_id}") from exc
        if topic_id != topic.build(self._session_id, self._team_name):
            raise ValueError(f"team topic does not match the external client identity: {topic_id}")
        return {
            "type": "req",
            "id": request_id,
            "method": "team.mq.publish",
            "params": {
                "session_id": self._session_id,
                "payload": ExternalTeamEvent(topic=topic, event=message).to_wire(),
            },
            "is_stream": False,
        }

    @staticmethod
    def _validate_response(data: dict) -> None:
        """Validate the unary acknowledgement for a published event."""
        if data.get("type") == "res" and data.get("ok") is True:
            return

        error = data.get("error")
        if not error:
            payload = data.get("payload")
            if isinstance(payload, dict):
                error = payload.get("error")
        raise RuntimeError(str(error or "team event publish failed"))

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        request_id = f"team-event-{uuid.uuid4()}"
        request = self._build_request(topic_id, message, request_id)
        async with self._lock:
            if self._session is None or self._session.closed:
                raise RuntimeError("WebSocket event publisher is not started")
            if self._ws is None or self._ws.closed:
                await self.start()
            ws = self._ws
            if ws is None or ws.closed:
                raise RuntimeError("WebSocket event publisher is not started")
            try:
                await ws.send_str(json.dumps(request, ensure_ascii=False))
                async with asyncio.timeout(self._request_timeout):
                    while True:
                        frame = await ws.receive()
                        if frame.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(frame.data)
                            if data.get("type") == "event" and data.get("event") == "connection.ack":
                                continue
                            if data.get("id") != request_id:
                                continue
                            self._validate_response(data)
                            return
                        if frame.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            raise ConnectionError("team event WebSocket closed before acknowledgement")
            except Exception:
                if self._ws is not None:
                    await self._ws.close()
                    self._ws = None
                raise


class HybridMessager(Messager):
    """Publish external team events over WebSocket."""

    def __init__(self, *, publisher: EventPublisher, sender_id: str) -> None:
        self._publisher = publisher
        self._sender_id = sender_id

    async def start(self) -> None:
        await self._publisher.start()

    async def stop(self) -> None:
        await self._publisher.stop()

    async def publish(self, topic_id: str, message: EventMessage) -> None:
        if not message.sender_id:
            message = message.model_copy(update={"sender_id": self._sender_id})
        await self._publisher.publish(topic_id, message)

    async def subscribe(self, topic_id: str, handler: MessagerHandler) -> None:
        raise NotImplementedError("HybridMessager only supports publishing")

    async def unsubscribe(self, topic_id: str) -> None:
        raise NotImplementedError("HybridMessager only supports publishing")

    async def send(self, agent_id: str, message: EventMessage) -> None:
        raise NotImplementedError("HybridMessager only supports publishing")

    async def register_direct_message_handler(self, handler: MessagerHandler) -> None:
        raise NotImplementedError("HybridMessager only supports publishing")

    async def unregister_direct_message_handler(self) -> None:
        raise NotImplementedError("HybridMessager only supports publishing")


__all__ = ["HybridMessager", "WebSocketEventPublisher"]
