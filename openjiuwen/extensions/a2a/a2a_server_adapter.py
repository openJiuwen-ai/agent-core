# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from threading import Thread
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult

try:
    from openjiuwen.extensions.a2a.a2a_server import A2AServer
except ModuleNotFoundError:
    A2AServer = None

A2AInvokeHandler = Callable[[dict[str, Any]], Awaitable[AgentResult]]
A2AStreamHandler = Callable[[dict[str, Any]], AsyncIterator[AgentResult]]


class A2AServerAdapter:
    """Adapter that boots an openjiuwen A2A server.

    start() triggers server launch, stop() shuts it down.
    """

    def __init__(
        self,
        adapter_id: str,
        *,
        version: str = "",
        agent_card: AgentCard,
        invoke_handler: A2AInvokeHandler | None = None,
        stream_handler: A2AStreamHandler | None = None,
        interface_url: str | None = None,
        rpc_url: str = "/a2a/jsonrpc/",
        rest_url: str = "/a2a/rest",
    ) -> None:
        if agent_card is None:
            raise ValueError("agent_card is required for A2AServerAdapter")
        self.adapter_id = adapter_id
        self.version = version
        self.agent_card = agent_card
        resolved_interface_url = (
            interface_url
            if interface_url is not None
            else getattr(agent_card, "interface_url", None)
        )
        self._protocol_binding = self._infer_protocol_binding(resolved_interface_url)
        if A2AServer is None:
            raise ModuleNotFoundError("A2A server dependencies are not available")
        self.server = A2AServer(
            agent_card=self.agent_card,
            adapter_id=adapter_id,
            invoke_handler=invoke_handler,
            stream_handler=stream_handler,
            interface_url=interface_url,
            protocol_binding=self._protocol_binding,
            rpc_url=rpc_url,
            rest_url=rest_url,
        )
        self.app: FastAPI | None = None
        self.rest_app: FastAPI | None = None
        self.active = False
        self._serve_thread: Thread | None = None
        self._serve_host, self._serve_port = self._parse_interface_url(resolved_interface_url)

    def start(self) -> None:
        """Launch the A2A server adapter and, when configured, its runtime thread."""
        if not self.active:
            self.app = self.server.build_app()
            self.rest_app = self.server.rest_app
            self.active = True
            if self._serve_thread is None:
                self._serve_thread = Thread(
                    target=self._run_server_in_thread,
                    daemon=True,
                    name=f"a2a-server-{self.adapter_id}",
                )
                self._serve_thread.start()

    async def stop(self) -> None:
        """Shut down the A2A server adapter and its runtime thread."""
        if not self.active:
            return
        await self.server.stop()
        if self._serve_thread is not None:
            await asyncio.to_thread(self._serve_thread.join, 5)
            self._serve_thread = None
        self.active = False

    def _run_server_in_thread(self) -> None:
        if self._serve_host is None or self._serve_port is None:
            return
        asyncio.run(self.server.start(host=self._serve_host, port=self._serve_port))

    @staticmethod
    def _parse_interface_url(interface_url: str | None) -> tuple[str | None, int | None]:
        if not interface_url:
            return None, None

        parsed = urlparse(interface_url)
        if not parsed.hostname:
            return None, None

        port = parsed.port or 8000
        return parsed.hostname, port

    @staticmethod
    def _infer_protocol_binding(interface_url: str | None) -> str:
        if not interface_url:
            return "JSONRPC"

        parsed = urlparse(interface_url)
        path = (parsed.path or "").lower()
        if "grpc" in path:
            raise ValueError("gRPC transport is not supported.")
        if "rest" in path:
            return "HTTP+JSON"
        return "JSONRPC"
