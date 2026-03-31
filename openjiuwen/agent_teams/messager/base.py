# coding: utf-8
"""Messager transport abstractions and adapters."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager.messager import Messager


class MessagerPeerConfig(BaseModel):
    """Static peer metadata used to bootstrap a messager transport."""

    agent_id: str
    peer_id: Optional[str] = None
    addrs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessagerTransportConfig(BaseModel):
    """JSON-safe configuration for messager transports."""

    backend: str = "team_runtime"
    team_id: str = "default"
    node_id: Optional[str] = None
    direct_addr: Optional[str] = None
    pubsub_publish_addr: Optional[str] = None
    pubsub_subscribe_addr: Optional[str] = None
    listen_addrs: list[str] = Field(default_factory=list)
    bootstrap_peers: list[MessagerPeerConfig] = Field(default_factory=list)
    known_peers: list[MessagerPeerConfig] = Field(default_factory=list)
    request_timeout: float = 10.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def broadcast_topic(self) -> str:
        return f"team:{self.team_id}:broadcast"


class SubscriptionHandle(BaseModel):
    """Opaque handle for transport subscriptions."""

    subscription_id: str
    topic: str
    agent_id: Optional[str] = None
    backend_metadata: dict[str, Any] = Field(default_factory=dict)


def create_messager(
    config: MessagerTransportConfig,
    *,
    runtime=None,
) -> "Messager":
    """Build one messager transport from JSON-safe config."""

    if config.backend == "team_runtime":
        from openjiuwen.agent_teams.messager.team_runtime import TeamRuntimeMessager

        if runtime is None:
            raise ValueError("Team runtime backend requires a runtime instance.")
        return TeamRuntimeMessager(runtime=runtime, config=config)
    if config.backend == "pyzmq":
        from openjiuwen.agent_teams.messager.pyzmq_backend import PyZmqMessager

        return PyZmqMessager(config=config)
    raise ValueError(f"Unsupported messager backend: {config.backend}")


__all__ = [
    "MessagerPeerConfig",
    "MessagerTransportConfig",
    "SubscriptionHandle",
    "create_messager",
]
