# coding: utf-8
"""Optional pyzmq-backed messager transport."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Optional

from openjiuwen.agent_teams.messager.base import (
    MessagerPeerConfig,
    MessagerTransportConfig,
    SubscriptionHandle,
)
from openjiuwen.agent_teams.messager.messager import (
    Messager,
    MessagerHandler,
)
from openjiuwen.agent_teams.tools.team_events import EventMessage
from openjiuwen.core.common.logging import team_logger

try:
    import zmq
    import zmq.asyncio
except ImportError:  # pragma: no cover
    zmq = None


# ----------------------------------------------------------
# P2P layer: ROUTER/DEALER direct messaging
# ----------------------------------------------------------


class _P2PLayer:
    """ROUTER/DEALER direct messaging layer."""

    def __init__(
        self,
        config: MessagerTransportConfig,
    ) -> None:
        self._config = config
        self._ctx: Optional[zmq.asyncio.Context] = None
        self._router: Optional[zmq.asyncio.Socket] = None
        self._router_task: Optional[asyncio.Task] = None
        self._running = False
        self._peer_book: dict[str, MessagerPeerConfig] = {
            p.agent_id: p for p in config.bootstrap_peers + config.known_peers
        }
        self._handlers: dict[str, MessagerHandler] = {}

    def register_peer(
        self,
        peer: MessagerPeerConfig,
    ) -> None:
        self._peer_book[peer.agent_id] = peer

    async def start(
        self,
        ctx: zmq.asyncio.Context,
    ) -> None:
        if self._running:
            return
        self._ctx = ctx
        self._router = ctx.socket(zmq.ROUTER)
        if self._config.direct_addr:
            self._router.bind(self._config.direct_addr)
        self._router_task = asyncio.create_task(
            self._recv_loop(),
        )
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._router_task is not None:
            self._router_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._router_task
        if self._router is not None:
            self._router.close(linger=0)
        self._router_task = None
        self._router = None

    async def send(
        self,
        agent_id: str,
        payload_bytes: bytes,
    ) -> None:
        """Send raw bytes to *agent_id* via DEALER, wait for ACK."""
        peer = self._resolve_peer(agent_id)
        node_id = self._config.node_id or str(uuid.uuid4())
        team_logger.debug("P2P sending {} bytes to agent {} via {}", len(payload_bytes), agent_id, peer.addrs[0])
        dealer = self._ctx.socket(zmq.DEALER)
        dealer.setsockopt(
            zmq.IDENTITY,
            node_id.encode("utf-8"),
        )
        dealer.connect(peer.addrs[0])
        try:
            await dealer.send(payload_bytes)
            await asyncio.wait_for(
                dealer.recv(),
                timeout=self._config.request_timeout,
            )
            team_logger.debug("P2P send to agent {} acknowledged", agent_id)
        finally:
            dealer.close(linger=0)

    def register_handler(
        self,
        agent_id: str,
        handler: MessagerHandler,
    ) -> None:
        self._handlers[agent_id] = handler

    def unregister_handler(self, agent_id: str) -> None:
        self._handlers.pop(agent_id, None)

    # -- internals --

    async def _recv_loop(self) -> None:
        while self._running and self._router is not None:
            try:
                frames = await self._router.recv_multipart()
            except Exception:
                if not self._running:
                    break
                continue
            if len(frames) < 2:
                continue
            identity, payload = frames[0], frames[-1]
            await self._handle_request(identity, payload)

    async def _handle_request(
        self,
        identity: bytes,
        payload: bytes,
    ) -> None:
        try:
            data = json.loads(payload)
            recipient_id = data.get("_recipient_id", "")
            handler = self._handlers.get(recipient_id)
            if handler is not None:
                message = EventMessage.model_validate(data)
                team_logger.debug(
                    "P2P received message for {}: event_type={}",
                    recipient_id, message.event_type,
                )
                with contextlib.suppress(Exception):
                    await handler(message)
            else:
                team_logger.debug("P2P no handler for recipient {}, ignoring", recipient_id)
        except Exception:
            team_logger.warning("P2P failed to parse incoming request")
        # Always send ACK
        await self._router.send_multipart(
            [identity, b"ok"],
        )

    def _resolve_peer(
        self,
        agent_id: str,
    ) -> MessagerPeerConfig:
        peer = self._peer_book.get(agent_id)
        if peer is not None and peer.addrs:
            return peer
        raise RuntimeError(
            f"Unknown zmq route for recipient '{agent_id}'. "
            "Provide a known peer entry."
        )

    @staticmethod
    async def _invoke(
        handler: MessagerHandler,
        message: EventMessage,
    ) -> None:
        await handler(message)


# ----------------------------------------------------------
# PubSub layer: PUB/SUB + optional XPUB/XSUB proxy
# ----------------------------------------------------------


class _PubSubLayer:
    """PUB/SUB messaging layer with optional broker proxy."""

    def __init__(
        self,
        config: MessagerTransportConfig,
    ) -> None:
        self._config = config
        self._pub: Optional[zmq.asyncio.Socket] = None
        self._sub: Optional[zmq.asyncio.Socket] = None
        self._xpub: Optional[zmq.asyncio.Socket] = None
        self._xsub: Optional[zmq.asyncio.Socket] = None
        self._proxy_task: Optional[asyncio.Task] = None
        self._sub_task: Optional[asyncio.Task] = None
        self._running = False
        self._subscriptions: dict[str, SubscriptionHandle] = {}
        self._handlers: dict[str, MessagerHandler] = {}
        self._seen_ids: set[str] = set()

    async def start(
        self,
        ctx: zmq.asyncio.Context,
    ) -> None:
        if self._running:
            return
        pub_addr = self._require_publish_addr()
        sub_addr = self._require_subscribe_addr()

        if self._config.metadata.get("pubsub_bind", False):
            self._xpub = ctx.socket(zmq.XPUB)
            self._xsub = ctx.socket(zmq.XSUB)
            self._xpub.bind(sub_addr)
            self._xsub.bind(pub_addr)
            self._proxy_task = asyncio.create_task(
                self._run_proxy(),
            )

        self._pub = ctx.socket(zmq.PUB)
        self._pub.connect(pub_addr)
        self._sub = ctx.socket(zmq.SUB)
        self._sub.connect(sub_addr)
        self._sub_task = asyncio.create_task(
            self._recv_loop(),
        )
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in (self._sub_task, self._proxy_task):
            if task is not None:
                task.cancel()
        for task in (self._sub_task, self._proxy_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for sock in (
                self._pub,
                self._sub,
                self._xpub,
                self._xsub,
        ):
            if sock is not None:
                sock.close(linger=0)
        self._sub_task = None
        self._proxy_task = None
        self._pub = None
        self._sub = None
        self._xpub = None
        self._xsub = None

    async def publish(
        self,
        topic: str,
        payload_bytes: bytes,
    ) -> None:
        await self._pub.send_multipart(
            [
                topic.encode("utf-8"),
                payload_bytes,
            ],
        )

    async def subscribe(
        self,
        topic: str,
        handler: MessagerHandler,
    ) -> SubscriptionHandle:
        first_local = topic not in self._handlers
        self._handlers[topic] = handler
        if first_local and self._sub is not None:
            self._sub.setsockopt(
                zmq.SUBSCRIBE,
                topic.encode("utf-8"),
            )
        handle = SubscriptionHandle(
            subscription_id=str(uuid.uuid4()),
            topic=topic,
            backend_metadata={"backend": "pyzmq"},
        )
        self._subscriptions[handle.subscription_id] = handle
        return handle

    async def unsubscribe(
        self,
        handle: SubscriptionHandle,
    ) -> None:
        self._handlers.pop(handle.topic, None)
        if self._sub is not None:
            self._sub.setsockopt(
                zmq.UNSUBSCRIBE,
                handle.topic.encode("utf-8"),
            )
        self._subscriptions.pop(
            handle.subscription_id,
            None,
        )

    def find_handle_by_topic(self, topic: str) -> Optional[SubscriptionHandle]:
        """Find a subscription handle by topic."""
        for handle in self._subscriptions.values():
            if handle.topic == topic:
                return handle
        return None

    # -- internals --

    async def _recv_loop(self) -> None:
        while self._running and self._sub is not None:
            try:
                topic_frame, payload_frame = await self._sub.recv_multipart()
            except Exception:
                if not self._running:
                    break
                continue
            topic = topic_frame.decode("utf-8")
            try:
                event_message = EventMessage.deserialize(payload_frame)
            except Exception:
                team_logger.warning("failed to deserialize pubsub message on topic {}", topic)
                continue
            handler = self._handlers.get(topic)
            if handler is not None:
                team_logger.debug(
                    "received pubsub message on topic {}: event_type={}, sender={}",
                    topic, event_message.event_type, event_message.sender_id,
                )
                with contextlib.suppress(Exception):
                    await handler(event_message)
            else:
                team_logger.debug("no handler for pubsub topic {}, ignoring", topic)

    async def _run_proxy(self) -> None:
        poller = zmq.asyncio.Poller()
        poller.register(self._xsub, zmq.POLLIN)
        poller.register(self._xpub, zmq.POLLIN)
        while self._running:
            events = dict(await poller.poll(timeout=100))
            if self._xsub in events:
                frames = await self._xsub.recv_multipart()
                await self._xpub.send_multipart(frames)
            if self._xpub in events:
                frames = await self._xpub.recv_multipart()
                await self._xsub.send_multipart(frames)

    def _require_publish_addr(self) -> str:
        if not self._config.pubsub_publish_addr:
            raise RuntimeError("pubsub_publish_addr is required for pyzmq messager transport.")
        return self._config.pubsub_publish_addr

    def _require_subscribe_addr(self) -> str:
        if not self._config.pubsub_subscribe_addr:
            raise RuntimeError("pubsub_subscribe_addr is required for pyzmq messager transport.")
        return self._config.pubsub_subscribe_addr


# ----------------------------------------------------------
# Public composer
# ----------------------------------------------------------


class PyZmqMessager(Messager):
    """Messager transport using pyzmq — composes P2P + PubSub."""

    def __init__(
        self,
        config: MessagerTransportConfig,
        *,
        default_sender_id: str = "",
    ) -> None:
        self._config = config
        self._default_sender_id = default_sender_id
        self._context: Optional[zmq.asyncio.Context] = None
        self._p2p = _P2PLayer(config)
        self._pubsub = _PubSubLayer(config)
        self._running = False
        self._topic_handles: dict[str, SubscriptionHandle] = {}

    @property
    def local_peer(self) -> MessagerPeerConfig:
        return MessagerPeerConfig(
            agent_id=self._config.node_id or "",
            addrs=([self._config.direct_addr] if self._config.direct_addr else []),
        )

    def register_peer(
        self,
        peer: MessagerPeerConfig,
    ) -> None:
        self._p2p.register_peer(peer)

    async def start(self) -> None:
        if self._running:
            return
        _ensure_zmq()
        self._context = zmq.asyncio.Context()
        await self._p2p.start(self._context)
        await self._pubsub.start(self._context)
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._p2p.stop()
        await self._pubsub.stop()
        if self._context is not None:
            self._context.term()
        self._context = None

    async def publish(
        self,
        topic_id: str,
        message: EventMessage,
    ) -> None:
        if not self._running:
            await self.start()
        if not message.sender_id:
            message = message.model_copy(update={"sender_id": self._config.node_id or ""})
        team_logger.debug(
            "publishing message on topic {}: event_type={}, sender={}",
            topic_id, message.event_type, message.sender_id,
        )
        payload = message.serialize()
        await self._pubsub.publish(topic_id, payload)

    async def subscribe(
        self,
        topic_id: str,
        handler: MessagerHandler,
    ) -> None:
        if not self._running:
            await self.start()
        team_logger.debug("subscribing to topic {}", topic_id)
        handle = await self._pubsub.subscribe(topic_id, handler)
        self._topic_handles[topic_id] = handle

    async def unsubscribe(self, topic_id: str) -> None:
        handle = self._topic_handles.pop(topic_id, None)
        if handle is not None:
            await self._pubsub.unsubscribe(handle)

    async def send(
        self,
        agent_id: str,
        message: EventMessage,
    ) -> None:
        if not self._running:
            await self.start()
        team_logger.debug("sending message to {}: {}", agent_id, message)
        data = message.model_dump()
        data["_recipient_id"] = agent_id
        payload = json.dumps(data).encode("utf-8")
        await self._p2p.send(agent_id, payload)

    async def register_direct_message_handler(
        self,
        handler: MessagerHandler,
    ) -> None:
        if not self._running:
            await self.start()
        agent_id = self._config.node_id or ""
        self._p2p.register_handler(agent_id, handler)

    async def unregister_direct_message_handler(
        self,
    ) -> None:
        agent_id = self._config.node_id or ""
        self._p2p.unregister_handler(agent_id)


def _ensure_zmq() -> None:
    if zmq is None:
        raise RuntimeError("PyZmqMessagerTransport requires optional dependency 'pyzmq'.")


__all__ = ["PyZmqMessager"]
