# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod
from typing import Any, List, Optional

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig, NO_TIMEOUT
from openjiuwen.core.common.clients import BaseClient


class McpClient(BaseClient):
    __client_type__ = "mcp"

    def __init__(self, config: McpServerConfig):
        super().__init__()
        self._server_path = config.server_path
        self._include_image_content = bool(getattr(config, "include_image_content", False))
        self._last_connect_error: Optional[BaseException] = None
        # Declared connect budget from the server config (issue #1975):
        # used as the fallback when ``connect`` runs without an explicit
        # call-level timeout, so slow-initializing servers can be configured
        # on the registration path. ``None`` keeps the previous 60s default.
        self._declared_connect_timeout: Optional[float] = getattr(config, "connect_timeout", None)

    def _resolve_connect_timeout(self, timeout: float) -> float:
        """Resolve the connect deadline: explicit timeout > declared config.

        A positive explicit timeout passes through. ``NO_TIMEOUT`` (or a
        non-positive value) falls back to a *positive* declared
        ``McpServerConfig.connect_timeout`` when the config declared one
        (issue #1975); non-positive declared values are rejected so all
        transports behave consistently (a declared 0/-5 would otherwise be
        silently ignored by stdio/streamable but passed through by SSE).
        Otherwise the sentinel is returned unchanged and the transport
        client applies its own default (60s via ``_resolve_timeout``).
        """
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        if isinstance(self._declared_connect_timeout, (int, float)) \
                and self._declared_connect_timeout > 0:
            return float(self._declared_connect_timeout)
        return timeout

    @abstractmethod
    async def connect(self, *, retry_times: int = 1, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        pass

    @abstractmethod
    async def call_tool(self, tool_name, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        pass

    @abstractmethod
    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        pass

    @abstractmethod
    async def list_resources(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        pass

    @abstractmethod
    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        pass

    async def close(self) -> bool:
        return await self.disconnect()
