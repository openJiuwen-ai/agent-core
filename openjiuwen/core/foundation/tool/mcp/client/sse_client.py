# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from contextlib import AsyncExitStack
from typing import Any, Callable, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig, NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
from openjiuwen.core.foundation.tool.mcp.client.reconnect import with_reconnect, mark_reconnect_applied
from openjiuwen.core.runner.callback.events import ToolCallEvents


class SseClient(McpClient):
    """SSE (Server-Sent Events) transport based MCP client.

    Lifecycle note: ``connect`` / ``disconnect`` / ``reconnect`` run on a
    dedicated *owner task* via an internal command queue.  This satisfies
    anyio's cancel-scope invariant (async context managers must be exited on
    the same task they were entered) and prevents cross-task ``RuntimeError``
    when a timeout monitor or decorator calls ``disconnect`` from a different
    task.
    """
    __client_name__ = "sse"

    def __init__(self, config: McpServerConfig):
        super().__init__(config)
        self._name = config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        self._is_disconnected: bool = False
        self._auth_headers = config.auth_headers
        self._auth_query_params = config.auth_query_params
        self._server_id = config.server_id
        self._auth_provider = None

        # Owner-task plumbing for serialized lifecycle ops.
        self._cmd_queue: Optional[Any] = None
        self._owner_task: Optional[Any] = None
        self._stopping: bool = False

        # Concurrent-reconnect serialization (used by reconnect() itself).
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()
        self._reconnect_event: Optional[asyncio.Event] = None

    @staticmethod
    def _extract_auth_provider(auth_result: Any) -> Any:
        if auth_result is None:
            return None
        if isinstance(auth_result, (list, tuple)):
            auth_items = list(auth_result)
        else:
            auth_items = [auth_result]

        for item in reversed(auth_items):
            if item is None:
                continue
            if isinstance(item, ToolAuthResult) and not item.success:
                continue
            auth_data = getattr(item, "auth_data", None)
            if isinstance(auth_data, dict) and "auth_provider" in auth_data:
                return auth_data.get("auth_provider")
        return None

    async def _owner_loop(self) -> None:
        """Owner-task command queue"""
        logger.debug("[SseClient] Owner loop started")
        while not self._stopping:
            try:
                cmd, fut = await self._cmd_queue.get()
            except asyncio.CancelledError:
                logger.debug("[SseClient] Owner loop cancelled")
                break
            try:
                result = await cmd()
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.cancel()
                logger.debug("[SseClient] Command cancelled in owner loop")
                break
            except Exception as exc:
                logger.debug("[SseClient] Command failed in owner loop: %s", exc)
                if not fut.done():
                    fut.set_exception(exc)
        logger.debug("[SseClient] Owner loop stopped")

    async def _submit(self, cmd: Callable[[], Any]) -> Any:
        if self._owner_task is None or self._owner_task.done():
            logger.debug("[SseClient] Creating new owner task")
            self._cmd_queue = asyncio.Queue()
            self._stopping = False
            self._owner_task = asyncio.create_task(self._owner_loop())
        fut = asyncio.get_event_loop().create_future()
        await self._cmd_queue.put((cmd, fut))
        return await fut

    async def _do_connect(self, *, timeout: float) -> bool:
        """Lifecycle implementation (runs inside the owner task)"""
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        # Ensure a fresh exit stack for every new connection attempt.
        self._exit_stack = AsyncExitStack()

        try:
            from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType
            from openjiuwen.core.runner import Runner
            framework = Runner.callback_framework
            auth_result = await framework.trigger(
                ToolCallEvents.TOOL_AUTH,
                auth_config=ToolAuthConfig(
                    auth_type=AuthType.HEADER_AND_QUERY,
                    config={
                        "auth_headers": self._auth_headers,
                        "auth_query_params": self._auth_query_params,
                    },
                    tool_type=self._name,
                    tool_id=self._server_id
                ),
            )
            self._auth_provider = self._extract_auth_provider(auth_result)
            actual_timeout = timeout if timeout != NO_TIMEOUT else 60.0
            self._client = sse_client(self._server_path, timeout=actual_timeout, auth=self._auth_provider)
            self._read, self._write = await self._exit_stack.enter_async_context(self._client)
            self._session = await self._exit_stack.enter_async_context(ClientSession(
                self._read, self._write, sampling_callback=None
            ))
            # Defensive timeout: the SDK's initialize() uses anyio.fail_after
            # with default session_read_timeout_seconds=None, which means NO
            # timeout by default.  If the server is dead, this hangs forever
            # and kills the owner loop.
            await asyncio.wait_for(self._session.initialize(), timeout=actual_timeout)
            self._is_disconnected = False
            logger.info("[SseClient] SSE client connected successfully to %s", self._server_path)
            return True

        except Exception as e:
            logger.error("[SseClient] SSE connection failed to %s: %s: %r", self._server_path,
                         type(e).__name__, e)
            # Clean up whatever partial state we have, but don't let cleanup
            # exceptions mask the original connection error.
            try:
                await self._do_disconnect(timeout=NO_TIMEOUT)
            except Exception as cleanup_exc:
                logger.debug("[SseClient] SSE connect cleanup failed: %r (ignored)", cleanup_exc)
            return False

    async def _do_disconnect(self, *, timeout: float) -> bool:
        logger.debug("[SseClient] Disconnecting from %s", self._server_path)
        try:
            if self._session is not None:
                try:
                    await asyncio.wait_for(
                        self._session.__aexit__(None, None, None),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[SseClient] session __aexit__ timed out after "
                        "10s; forcing cleanup. The receive_loop may be hung "
                        "on a dead TCP connection after RST.",
                    )
                except Exception as e:
                    logger.debug("[SseClient] session __aexit__ raised %r (ignored)", e)
            if self._client is not None:
                try:
                    await asyncio.wait_for(
                        self._client.__aexit__(None, None, None),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[SseClient] sse_client generator __aexit__ "
                        "timed out after 10s; forcing cleanup.",
                    )
                except Exception as e:
                    logger.debug("[SseClient] sse_client generator __aexit__ raised %r (ignored)", e)
            self._session = None
            self._client = None
            self._read = None
            self._write = None
            self._is_disconnected = True
            logger.info("[SseClient] SSE client disconnected successfully")
            return True
        except Exception as e:
            logger.error("[SseClient] SSE disconnection failed: %s: %r", type(e).__name__, e)
            return False

    async def _do_reconnect(self, *, timeout: float) -> bool:
        logger.debug("[SseClient] Executing reconnect sequence")
        await self._do_disconnect(timeout=NO_TIMEOUT)
        return await self._do_connect(timeout=timeout)

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Public API (submits to owner task where required)"""
        logger.info("[SseClient] Connecting to %s", self._server_path)
        return await self._submit(lambda: self._do_connect(timeout=timeout))

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        logger.debug("[SseClient] Requesting disconnect")
        result = await self._submit(lambda: self._do_disconnect(timeout=timeout))
        # A public disconnect is a final teardown: stop the owner task so it
        # doesn't leak.
        self._stopping = True
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()
            try:
                await self._owner_task
            except asyncio.CancelledError:
                logger.debug("[SseClient] Owner task cancelled during disconnect")
            except Exception as e:
                logger.debug(
                    "[SseClient] Error while waiting for owner task to finish during disconnect: %s",
                    e
                )
            self._owner_task = None
        return result

    async def reconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        logger.info("[SseClient] Reconnecting to %s", self._server_path)
        event: Optional[asyncio.Event] = None
        async with self._reconnect_lock:
            if self._reconnect_event is not None:
                event = self._reconnect_event
                logger.debug("[SseClient] Waiting for in-flight reconnect")
            else:
                self._stopping = False
                self._reconnect_event = asyncio.Event()

        if event is not None:
            await event.wait()
            return True

        try:
            result = await self._submit(lambda: self._do_reconnect(timeout=timeout))
            logger.info("[SseClient] Reconnect %s", "succeeded" if result else "failed")
            return result
        finally:
            async with self._reconnect_lock:
                evt = self._reconnect_event
                self._reconnect_event = None
            if evt is not None:
                evt.set()

    @with_reconnect
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")

        try:
            tools_response = await self._session.list_tools()
            tools_list = [
                McpToolCard(
                    name=tool.name,
                    server_name=self._name,
                    description=getattr(tool, "description", ""),
                    input_params=getattr(tool, "inputSchema", {}),
                )
                for tool in tools_response.tools
            ]
            logger.info("[SseClient] Retrieved %d tools from SSE server", len(tools_list))
            return tools_list
        except Exception as e:
            logger.error("[SseClient] Failed to list tools via SSE: %s", e)
            raise

    @with_reconnect
    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")

        try:
            logger.info(
                "[SseClient] Calling tool '%s' via SSE with arguments: %s",
                tool_name, arguments
            )
            tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(tool_result)
            logger.info("[SseClient] Tool '%s' call completed via SSE", tool_name)
            return result_content
        except Exception as e:
            logger.error("[SseClient] Tool '%s' call failed via SSE: %s", tool_name, e)
            raise

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via SSE"""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug("[SseClient] Found tool info for '%s' via SSE", tool_name)
                return tool
        logger.warning("[SseClient] Tool '%s' not found via SSE", tool_name)
        return None

    @with_reconnect
    async def list_resources(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available resources via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")
        try:
            response = await self._session.list_resources()
            logger.info(
                "[SseClient] Retrieved %d resources from SSE server",
                len(response.resources)
            )
            return response.resources
        except Exception as e:
            logger.error("[SseClient] Failed to list resources via SSE: %s", e)
            raise

    @with_reconnect
    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        """Read a resource by URI via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")
        try:
            response = await self._session.read_resource(uri)
            logger.info("[SseClient] Read resource '%s' via SSE (%d contents)", uri,
                        len(response.contents))
            return response.contents
        except Exception as e:
            logger.error("[SseClient] Failed to read resource '%s' via SSE: %s", uri, e)
            raise


# Mark that @with_reconnect is applied, so external monkeypatches can detect and skip.
mark_reconnect_applied(SseClient)
