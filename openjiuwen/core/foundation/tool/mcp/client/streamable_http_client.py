# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, List, Optional

import httpx

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
    mark_reconnect_applied,
    record_reconnect_success,
    with_reconnect,
)
from openjiuwen.core.runner.callback.events import ToolCallEvents


class StreamableHttpClient(McpClient):
    """Streamable HTTP transport based MCP client.

    Streamable HTTP binds requests to a server-issued ``Mcp-Session-Id``.
    Restarting the MCP server invalidates that id: the SDK maps the resulting
    HTTP 404 to JSON-RPC 32600 ("Session terminated"), so every later call
    fails until the transport is rebuilt. ``@with_reconnect`` rebuilds it once
    and retries the failed call.

    Lifecycle operations run on a dedicated owner task because anyio async
    context managers must be exited by the task that entered them.
    """
    __client_name__ = ["streamable-http", "streamable_http"]

    def __init__(
        self,
        config: McpServerConfig | str,
        name: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
        auth_query_params: Optional[Dict[str, str]] = None,
    ):
        resolved_config = self._normalize_config(
            config,
            name=name,
            auth_headers=auth_headers,
            auth_query_params=auth_query_params,
        )
        super().__init__(resolved_config)
        self._name = resolved_config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        self._is_disconnected = False
        self._auth_headers = resolved_config.auth_headers
        self._auth_query_params = resolved_config.auth_query_params
        self._server_id = resolved_config.server_id
        self._auth_provider = None

        # Owner-task plumbing for serialized lifecycle operations.
        self._cmd_queue: Optional[asyncio.Queue] = None
        self._owner_task: Optional[asyncio.Task] = None
        self._stopping: bool = False
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._reconnect_future: Optional[asyncio.Future] = None
        # Tool calls currently waiting on the shared session. A reconnect must
        # not leave them blocked on the transport being torn down.
        self._active_calls: dict[asyncio.Task, object] = {}
        self._reconnect_cancelled_calls: dict[asyncio.Task, object] = {}
        self._reconnect_in_progress: bool = False
        # One reconnect plus one retry; more retries turn a dead server into
        # a request storm.
        self._reconnect_attempts: int = 2
        # True once a successful connect allows lazy recovery; explicit
        # disconnect() clears it so a user-initiated close stays closed.
        self._lazy_reconnect: bool = False
        # Bumped on every successful connect; lets @with_reconnect detect that
        # a concurrent caller already rebuilt the transport.
        self._generation: int = 0

    @staticmethod
    def _normalize_config(
        config: McpServerConfig | str,
        *,
        name: Optional[str],
        auth_headers: Optional[Dict[str, str]],
        auth_query_params: Optional[Dict[str, str]],
    ) -> McpServerConfig:
        if isinstance(config, McpServerConfig):
            return config
        resolved_name = (name or "").strip() or "streamable-http"
        return McpServerConfig(
            server_id=resolved_name,
            server_name=resolved_name,
            server_path=config,
            client_type="streamable-http",
            auth_headers=auth_headers or {},
            auth_query_params=auth_query_params or {},
        )

    @property
    def server_path(self) -> str:
        return self._server_path

    @property
    def name(self) -> str:
        return self._name

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

    @staticmethod
    def _resolve_timeout(timeout: float) -> float:
        """Resolve the connect/initialize deadline; ``NO_TIMEOUT`` becomes 60s."""
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        return 60.0

    @staticmethod
    async def _await_with_optional_timeout(awaitable: Any, timeout: float) -> Any:
        """Bound a call only when the caller passed an explicit timeout."""
        if timeout == NO_TIMEOUT or timeout <= 0:
            return await awaitable
        # asyncio.timeout cancels in this task; wait_for would move the MCP
        # session call into another task and break anyio scope ownership.
        async with asyncio.timeout(timeout):
            return await awaitable

    def _cancel_pending_commands(self) -> None:
        """Release waiters whose commands cannot run on the owner task."""
        queue = self._cmd_queue
        if queue is None:
            return
        while True:
            try:
                _, fut = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not fut.done():
                fut.cancel()

    async def _owner_loop(self) -> None:
        logger.debug("[StreamableHttpClient] Owner loop started")
        try:
            while not self._stopping:
                try:
                    cmd, fut = await self._cmd_queue.get()
                except asyncio.CancelledError:
                    logger.debug("[StreamableHttpClient] Owner loop cancelled")
                    break
                try:
                    result = await cmd()
                    if not fut.done():
                        fut.set_result(result)
                except asyncio.CancelledError:
                    if not fut.done():
                        fut.cancel()
                    logger.debug("[StreamableHttpClient] Command cancelled in owner loop")
                    break
                except BaseException as exc:
                    if isinstance(exc, BaseExceptionGroup):
                        cancelled, _ = exc.split(asyncio.CancelledError)
                        current = asyncio.current_task()
                        if cancelled is not None and current is not None and current.cancelling() > 0:
                            if not fut.done():
                                fut.cancel()
                            logger.debug("[StreamableHttpClient] Command cancelled in owner loop")
                            break
                    logger.debug("[StreamableHttpClient] Command failed in owner loop: %r", exc)
                    if not fut.done():
                        fut.set_exception(self._coerce_owner_error(exc))
                    if self._is_fatal_owner_error(exc):
                        raise
        finally:
            self._cancel_pending_commands()
            reconnect_future = self._reconnect_future
            if reconnect_future is not None and not reconnect_future.done():
                reconnect_future.set_result(False)
            self._reconnect_future = None
            self._reconnect_in_progress = False
            logger.debug("[StreamableHttpClient] Owner loop stopped")

    async def _submit(self, cmd: Callable[[], Any]) -> Any:
        queue = self._cmd_queue
        if self._owner_task is None or self._owner_task.done() or queue is None:
            if queue is not None:
                self._cancel_pending_commands()
            logger.debug("[StreamableHttpClient] Creating new owner task")
            queue = asyncio.Queue()
            self._cmd_queue = queue
            self._stopping = False
            self._owner_task = asyncio.create_task(self._owner_loop())
        fut = asyncio.get_running_loop().create_future()
        queue.put_nowait((cmd, fut))
        return await fut

    async def _close_stale_stack(self) -> None:
        """Best-effort teardown of resources left by a previous attempt."""
        try:
            await self._exit_stack.aclose()
        except BaseExceptionGroup as exc:
            cancelled, _ = exc.split(asyncio.CancelledError)
            if cancelled is not None:
                raise
            logger.debug("[StreamableHttpClient] stale stack teardown raised %r (ignored)", exc)
        except Exception as exc:
            logger.debug("[StreamableHttpClient] stale stack teardown raised %r (ignored)", exc)

    @staticmethod
    def _coerce_connect_error(error: BaseException, name: str) -> BaseException:
        """Unwrap the SDK's BaseExceptionGroup into a reportable error."""
        real_err: BaseException = error
        if isinstance(error, BaseExceptionGroup):
            exc_sub, _ = error.split(Exception)
            if exc_sub is not None:
                subs = list(exc_sub.exceptions)
                real_err = subs[0] if len(subs) == 1 else exc_sub
            else:
                real_err = RuntimeError(f"MCP server '{name}' connect failed: {error}")
        if not isinstance(real_err, Exception):
            real_err = RuntimeError(
                f"MCP server '{name}' connect failed: {type(error).__name__}: {error}"
            )
        return real_err

    @staticmethod
    def _coerce_owner_error(error: BaseException) -> Exception:
        """Convert an owner-command failure into a caller-facing exception."""
        if isinstance(error, BaseExceptionGroup):
            _, without_cancel = error.split(asyncio.CancelledError)
            if without_cancel is None:
                return RuntimeError("MCP transport command was cancelled during cleanup")
            error = without_cancel
        if isinstance(error, BaseExceptionGroup):
            exception_group, _ = error.split(Exception)
            if exception_group is not None:
                exceptions = list(exception_group.exceptions)
                error = exceptions[0] if len(exceptions) == 1 else exception_group
        if isinstance(error, Exception):
            return error
        return RuntimeError(f"MCP transport command failed: {type(error).__name__}: {error}")

    @classmethod
    def _is_fatal_owner_error(cls, error: BaseException) -> bool:
        """Return whether an owner-command error must stop the owner loop."""
        if isinstance(error, BaseExceptionGroup):
            return any(cls._is_fatal_owner_error(item) for item in error.exceptions)
        return not isinstance(error, (Exception, asyncio.CancelledError))

    async def _do_connect(self, *, timeout: float) -> bool:
        """Establish the transport (runs inside the owner task)."""
        from mcp import ClientSession
        from mcp.client import streamable_http as streamable_http_module

        actual_timeout = self._resolve_timeout(timeout)
        await self._close_stale_stack()
        self._exit_stack = AsyncExitStack()

        try:
            from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType
            from openjiuwen.core.runner import Runner

            # Authentication may wait for user input; keep it outside the
            # transport deadline.
            auth_result = await Runner.callback_framework.trigger(
                ToolCallEvents.TOOL_AUTH,
                auth_config=ToolAuthConfig(
                    auth_type=AuthType.HEADER_AND_QUERY,
                    config={
                        "auth_headers": self._auth_headers,
                        "auth_query_params": self._auth_query_params,
                    },
                    tool_type=self._name,
                    tool_id=self._server_id,
                ),
            )
            auth_provider = self._extract_auth_provider(auth_result)
            if auth_provider is not None:
                self._auth_provider = auth_provider

            # Bound transport startup and initialize(); a dead peer otherwise
            # blocks the owner task before the SDK's own timeouts apply.
            async with asyncio.timeout(actual_timeout):
                legacy_client = getattr(streamable_http_module, "streamablehttp_client", None)
                if legacy_client is not None:
                    self._client = legacy_client(
                        self._server_path,
                        timeout=actual_timeout,
                        auth=self._auth_provider,
                    )
                else:
                    http_client = await self._exit_stack.enter_async_context(
                        httpx.AsyncClient(
                            timeout=httpx.Timeout(actual_timeout, read=300.0),
                            auth=self._auth_provider,
                            follow_redirects=True,
                        )
                    )
                    self._client = streamable_http_module.streamable_http_client(
                        self._server_path,
                        http_client=http_client,
                    )
                client_tuple = await self._exit_stack.enter_async_context(self._client)
                self._read, self._write, *_ = client_tuple
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(self._read, self._write, sampling_callback=None)
                )
                await self._session.initialize()
                self._is_disconnected = False
                self._lazy_reconnect = True
                self._generation += 1
                logger.info("[StreamableHttpClient] connected to %s", self._server_path)
                return True
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)) \
                and not isinstance(e, BaseExceptionGroup):
                raise
            logger.error("[StreamableHttpClient] connection failed to %s: %r", self._server_path, e)
            self._last_connect_error = self._coerce_connect_error(e, self._name)
            try:
                await self._do_disconnect(timeout=min(actual_timeout, 3.0))
            except BaseException as disc_exc:  # noqa: BLE001
                logger.debug(
                    "[StreamableHttpClient] cleanup after connect failure failed: %r",
                    disc_exc,
                )
            return False

    async def _do_disconnect(self, *, timeout: float) -> bool:
        """Tear down the transport (runs inside the owner task)."""
        logger.debug("[StreamableHttpClient] Disconnecting from %s", self._server_path)
        if self._reconnect_in_progress:
            current = asyncio.current_task()
            for task, token in tuple(self._active_calls.items()):
                if task is not current:
                    self._reconnect_cancelled_calls[task] = token
                    task.cancel()
        actual_timeout = timeout if timeout != NO_TIMEOUT else 10.0
        owner_task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        watchdog_fired = False

        def _cancel_owner(task: "asyncio.Task[Any]") -> None:
            nonlocal watchdog_fired
            if not task.done():
                watchdog_fired = True
                logger.warning(
                    "[StreamableHttpClient] disconnect watchdog fired after %ss; "
                    "cancelling owner task",
                    actual_timeout,
                )
                task.cancel()

        watchdog = loop.call_later(actual_timeout, _cancel_owner, owner_task)
        try:
            try:
                await self._exit_stack.aclose()
            except BaseExceptionGroup as exc:
                cancelled, _ = exc.split(asyncio.CancelledError)
                if cancelled is not None:
                    raise
                logger.debug("[StreamableHttpClient] teardown raised %r (ignored)", exc)
                return False
            except Exception as exc:
                logger.debug("[StreamableHttpClient] teardown raised %r (ignored)", exc)
                return False
            logger.info("[StreamableHttpClient] disconnected from %s", self._server_path)
            return True
        except asyncio.CancelledError:
            cancelling = getattr(owner_task, "cancelling", None)
            if not watchdog_fired or (cancelling is not None and cancelling() > 1):
                raise
            uncancel = getattr(owner_task, "uncancel", None)
            if uncancel is not None:
                uncancel()
            logger.warning(
                "[StreamableHttpClient] disconnect aborted by watchdog after %ss; "
                "transport scopes may not have exited cleanly",
                actual_timeout,
            )
            return False
        finally:
            watchdog.cancel()
            self._session = None
            self._client = None
            self._read = None
            self._write = None
            self._exit_stack = AsyncExitStack()
            self._is_disconnected = True

    async def _do_reconnect(self, *, timeout: float) -> bool:
        logger.debug("[StreamableHttpClient] Executing reconnect sequence")
        actual_timeout = self._resolve_timeout(timeout)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            # Auto reconnect has no caller deadline; keep a dead peer bounded.
            actual_timeout = min(actual_timeout, 10.0)
        deadline = asyncio.get_running_loop().time() + actual_timeout
        result = False
        try:
            await self._do_disconnect(timeout=min(actual_timeout, 3.0))
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                result = await self._do_connect(timeout=remaining)
            return result
        finally:
            # The owner task may outlive a cancelled caller; keep concurrent
            # reconnect waiters on one result and clear the guard only here.
            fut = self._reconnect_future
            if fut is not None and not fut.done():
                fut.set_result(result)
            self._reconnect_in_progress = False
            self._reconnect_future = None

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Establish the transport on the owner task."""
        logger.info("[StreamableHttpClient] Connecting to %s", self._server_path)
        return await self._submit(lambda: self._do_connect(timeout=timeout))

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Final teardown: stop the owner task after closing the transport."""
        logger.debug("[StreamableHttpClient] Requesting disconnect")
        async with self._lifecycle_lock:
            result = await self._submit(lambda: self._do_disconnect(timeout=timeout))
            self._lazy_reconnect = False
            self._stopping = True
            if self._owner_task is not None and not self._owner_task.done():
                self._owner_task.cancel()
                try:
                    await self._owner_task
                except asyncio.CancelledError:
                    logger.debug("[StreamableHttpClient] Owner task cancelled during disconnect")
                except Exception as exc:
                    logger.debug(
                        "[StreamableHttpClient] owner task ended with %r during disconnect",
                        exc,
                    )
                self._owner_task = None
        return result

    async def reconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Rebuild the transport, de-duplicating concurrent callers."""
        logger.info("[StreamableHttpClient] Reconnecting to %s", self._server_path)
        async with self._lifecycle_lock:
            if self._reconnect_future is not None:
                fut = self._reconnect_future
                owns_reconnect = False
                logger.debug("[StreamableHttpClient] Waiting for in-flight reconnect")
            else:
                self._stopping = False
                self._lazy_reconnect = True
                self._reconnect_in_progress = True
                fut = asyncio.get_running_loop().create_future()
                self._reconnect_future = fut
                owns_reconnect = True

        if not owns_reconnect:
            return await fut

        try:
            result = await self._submit(lambda: self._do_reconnect(timeout=timeout))
            logger.info(
                "[StreamableHttpClient] Reconnect %s",
                "succeeded" if result else "failed",
            )
            if result:
                record_reconnect_success(self)
            if not fut.done():
                fut.set_result(result)
                async with self._lifecycle_lock:
                    if self._reconnect_future is fut:
                        self._reconnect_future = None
                        self._reconnect_in_progress = False
            return result
        except BaseException:
            # The owner command normally records its own result; the owner-loop
            # finalizer covers cancellation before the command can run.
            if self._owner_task is None or self._owner_task.done():
                self._reconnect_in_progress = False
                self._reconnect_future = None
            raise

    @with_reconnect
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

        try:
            tools_response = await self._await_with_optional_timeout(
                self._session.list_tools(), timeout
            )
            tools_list = [
                McpToolCard(
                    name=tool.name,
                    server_name=self._name,
                    description=getattr(tool, "description", ""),
                    input_params=getattr(tool, "inputSchema", {}),
                )
                for tool in tools_response.tools
            ]
            logger.info("Retrieved %d tools from streamable-http server", len(tools_list))
            return tools_list
        except Exception as e:
            logger.error("Failed to list tools via streamable-http: %s", e)
            raise

    @with_reconnect
    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call a tool via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

        try:
            logger.info(
                "Calling tool '%s' via streamable-http with arguments: %s",
                tool_name,
                arguments,
            )
            tool_result = await self._await_with_optional_timeout(
                self._session.call_tool(tool_name, arguments=arguments), timeout
            )
            result_content = extract_mcp_tool_result_content(
                tool_result,
                include_image_content=self._include_image_content,
                tool_name=tool_name,
            )
            logger.info("Tool '%s' call completed via streamable-http", tool_name)
            return result_content
        except Exception as e:
            logger.error("Tool call failed via streamable-http: %s", e)
            raise

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via streamable-http."""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug("Found tool info for '%s' via streamable-http", tool_name)
                return tool
        logger.warning("Tool '%s' not found via streamable-http", tool_name)
        return None

    @with_reconnect
    async def list_resources(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available resources via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")
        try:
            response = await self._await_with_optional_timeout(
                self._session.list_resources(), timeout
            )
            return response.resources
        except Exception as e:
            logger.error("Failed to list resources via streamable-http: %s", e)
            raise

    @with_reconnect
    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        """Read a resource by URI via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")
        try:
            response = await self._await_with_optional_timeout(
                self._session.read_resource(uri), timeout
            )
            return response.contents
        except Exception as e:
            logger.error("Failed to read resource '%s' via streamable-http: %s", uri, e)
            raise


mark_reconnect_applied(StreamableHttpClient)
