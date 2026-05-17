# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

from a2a.client.client_factory import TransportProtocol
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard as A2AAgentCard

from fastapi import FastAPI
import uvicorn

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a.a2a_agent_executor import A2AAgentExecutor
from openjiuwen.extensions.a2a.a2a_agentcard_adapter import A2AAgentCardAdapter

A2AInvokeHandler = Callable[[dict[str, Any]], Awaitable[AgentResult]]
A2AStreamHandler = Callable[[dict[str, Any]], AsyncIterator[AgentResult]]


def _normalize_jsonrpc_route_path(rpc_url: str) -> str:
    """Use a trailing slash on the JSON-RPC mount path.

    ``A2ARemoteClient`` and a2a-sdk agent cards use ``.../a2a/jsonrpc/``. If the Starlette route is
    registered without that slash, POSTs to the card URL get HTTP 307; streaming clients often do
    not follow redirects.
    """
    if not rpc_url.startswith("/"):
        rpc_url = "/" + rpc_url
    return rpc_url.rstrip("/") + "/"


def _normalize_jsonrpc_interface_url(interface_url: str | None) -> str | None:
    """Align a published JSON-RPC ``interface_url`` with the trailing-slash mount path."""
    if not interface_url:
        return None
    parts = urlparse(interface_url)
    path = parts.path or ""
    if "jsonrpc" not in path.lower():
        return interface_url
    norm_path = path.rstrip("/") + "/"
    return urlunparse((parts.scheme, parts.netloc, norm_path, parts.params, parts.query, parts.fragment))


class A2AServer:
    """Minimal A2A server wrapper for openjiuwen.

    start() launches the server runtime and stop() shuts it down.
    """

    def __init__(
        self,
        *,
        agent_card: AgentCard,
        adapter_id: str = "openjiuwen-a2a-agent",
        invoke_handler: A2AInvokeHandler | None = None,
        stream_handler: A2AStreamHandler | None = None,
        interface_url: str | None = None,
        protocol_binding: str = "JSONRPC",
        rpc_url: str = "/a2a/jsonrpc/",
        rest_url: str = "/a2a/rest",
    ) -> None:
        self.adapter_id = adapter_id
        if protocol_binding.upper() == TransportProtocol.GRPC.value:
            raise ValueError("gRPC transport is not supported.")
        resolved_interface_url = (
            interface_url
            if interface_url is not None
            else getattr(agent_card, "interface_url", None)
        )
        norm_interface_url = _normalize_jsonrpc_interface_url(resolved_interface_url)
        self._a2a_agent_card = self._build_a2a_agent_card(
            agent_card,
            interface_url=norm_interface_url,
            protocol_binding=protocol_binding,
        )
        self._transport_protocols = self._resolve_transport_protocols()
        if TransportProtocol.JSONRPC in self._transport_protocols:
            self._rpc_url = _normalize_jsonrpc_route_path(rpc_url)
        else:
            self._rpc_url = rpc_url if rpc_url.startswith("/") else "/" + rpc_url
        self._rest_url = rest_url
        self._task_store = InMemoryTaskStore()
        self._executor = A2AAgentExecutor(
            invoke_handler=invoke_handler,
            stream_handler=stream_handler,
        )
        self._request_handler = DefaultRequestHandler(
            agent_executor=self._executor,
            task_store=self._task_store,
            agent_card=self._a2a_agent_card,
        )
        self.app: FastAPI | None = None
        self.rest_app: FastAPI | None = None
        self._uvicorn_server: uvicorn.Server | None = None

    async def start(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "warning",
    ) -> None:
        """Launch the A2A server runtime."""
        await self._serve(host=host, port=port, log_level=log_level)

    async def stop(self) -> None:
        """Shut down the running A2A server runtime."""
        if self._uvicorn_server is None:
            return
        self._uvicorn_server.should_exit = True

    def build_app(self) -> FastAPI:
        if self.app is None:
            app = FastAPI()
            app.router.routes.extend(
                create_agent_card_routes(
                    agent_card=self._a2a_agent_card,
                    card_url="/.well-known/agent-card.json",
                )
            )
            if TransportProtocol.JSONRPC in self._transport_protocols:
                app.router.routes.extend(
                    create_jsonrpc_routes(
                        request_handler=self._request_handler,
                        rpc_url=self._rpc_url,
                        enable_v0_3_compat=True,
                    )
                )
            if TransportProtocol.HTTP_JSON in self._transport_protocols:
                if self.rest_app is None:
                    self.rest_app = FastAPI()
                    self.rest_app.router.routes.extend(
                        create_rest_routes(
                            request_handler=self._request_handler,
                            enable_v0_3_compat=True,
                            path_prefix="",
                        )
                    )
                app.mount(self._rest_url, self.rest_app)
            self.app = app
        return self.app

    async def _serve(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        log_level: str = "warning",
    ) -> None:
        app = self.build_app()
        self._uvicorn_server = uvicorn.Server(
            uvicorn.Config(
                app=app,
                host=host,
                port=port,
                log_level=log_level,
                loop="asyncio",
            )
        )
        try:
            await self._uvicorn_server.serve()
        finally:
            self._uvicorn_server = None

    def _resolve_transport_protocols(self) -> set[TransportProtocol]:
        transports: set[TransportProtocol] = set()
        for interface in getattr(self._a2a_agent_card, "supported_interfaces", []) or []:
            binding = getattr(interface, "protocol_binding", None)
            if not binding:
                continue
            try:
                transport = TransportProtocol(str(binding))
            except ValueError:
                continue
            if transport == TransportProtocol.GRPC:
                raise ValueError("gRPC transport is not supported.")
            transports.add(transport)

        if not transports:
            transports.add(TransportProtocol.JSONRPC)
        return transports

    @classmethod
    def _build_a2a_agent_card(
        cls,
        agent_card: AgentCard,
        *,
        interface_url: str | None,
        protocol_binding: str,
    ) -> A2AAgentCard:
        a2a_card = A2AAgentCardAdapter.to_a2a_agent_card(
            agent_card,
            interface_url=interface_url,
            protocol_binding=protocol_binding,
        )
        if a2a_card is not None:
            return a2a_card

        return A2AAgentCard(
            name=agent_card.name or agent_card.id,
            description=agent_card.description or "",
            capabilities=AgentCapabilities(streaming=True, push_notifications=False),
            default_input_modes=A2AAgentCardAdapter.DEFAULT_INPUT_MODES,
            default_output_modes=A2AAgentCardAdapter.DEFAULT_OUTPUT_MODES,
        )
