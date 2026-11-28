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
from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
    mark_reconnect_applied,
    record_reconnect_success,
    with_reconnect,
)
from openjiuwen.core.runner.callback.events import ToolCallEvents


class SseClient(McpClient):
    """SSE (Server-Sent Events) transport based MCP client.

    Lifecycle note: ``connect`` / ``disconnect`` / ``reconnect`` run on a
    dedicated *owner task* via an internal command queue.  This satisfies
    anyio's cancel-scope invariant (async context managers must be exited on
    the same task they were entered) and prevents cross-task ``RuntimeError``
    when a timeout monitor or decorator calls ``disconnect`` from a different
    task.

    ``_lazy_reconnect`` mirrors ``StreamableHttpClient``
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

        # Lifecycle serialization: guards (a) disconnect vs reconnect
        # mutual exclusion and (b) concurrent-reconnect de-duplication via
        # ``_reconnect_future``. One lock serves both to keep the critical
        # sections atomic and avoid nested-lock deadlock surface.
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._reconnect_future: Optional[asyncio.Future] = None
        # True once a successful connect allows lazy recovery; explicit
        # disconnect() clears it so a user-initiated close stays closed.
        self._lazy_reconnect: bool = False

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
        """Resolve a call-level timeout to a concrete seconds value.

        ``NO_TIMEOUT`` (-1) means "use the default 60s ceiling" so a dead
        server cannot hang the caller forever (R2). Explicit positive
        timeouts pass through unchanged.
        """
        return timeout if timeout != NO_TIMEOUT else 60.0

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

    async def _owner_loop(self) -> None:
        """Owner-task command queue.

        Must settle every submitted future — including when teardown raises a
        ``BaseExceptionGroup`` wrapping ``CancelledError`` (anyio task-group
        shape). Matching only ``Exception`` would leave ``await fut`` hanging
        while ``_lifecycle_lock`` is still held by disconnect/invalidate.
        """
        logger.debug("[SseClient] Owner loop started")
        try:
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
                except BaseException as exc:
                    if isinstance(exc, BaseExceptionGroup):
                        cancelled, _ = exc.split(asyncio.CancelledError)
                        current = asyncio.current_task()
                        if cancelled is not None and current is not None and current.cancelling() > 0:
                            if not fut.done():
                                fut.cancel()
                            logger.debug("[SseClient] Command cancelled in owner loop")
                            break
                    logger.debug("[SseClient] Command failed in owner loop: %r", exc)
                    if not fut.done():
                        fut.set_exception(self._coerce_owner_error(exc))
                    if self._is_fatal_owner_error(exc):
                        raise
        finally:
            self._cancel_pending_commands()
            logger.debug("[SseClient] Owner loop stopped")

    async def _submit(self, cmd: Callable[[], Any]) -> Any:
        queue = self._cmd_queue
        if self._owner_task is None or self._owner_task.done() or queue is None:
            if queue is not None:
                self._cancel_pending_commands()
            logger.debug("[SseClient] Creating new owner task")
            queue = asyncio.Queue()
            self._cmd_queue = queue
            self._stopping = False
            self._owner_task = asyncio.create_task(self._owner_loop())
        fut = asyncio.get_running_loop().create_future()
        queue.put_nowait((cmd, fut))
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
            # Issue #1975: a declared ``connect_timeout`` on the server config
            # raises the fallback ceiling for slow-initializing servers when
            # the caller passed no explicit timeout; an explicit call-level
            # timeout still wins.
            actual_timeout = self._resolve_timeout(self._resolve_connect_timeout(timeout))
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
            self._lazy_reconnect = True
            logger.info("[SseClient] SSE client connected successfully to %s", self._server_path)
            return True

        except Exception as e:
            logger.error("[SseClient] SSE connection failed to %s: %s: %r", self._server_path,
                         type(e).__name__, e)
            self._last_connect_error = e
            try:
                await self._do_disconnect(timeout=NO_TIMEOUT)
            except Exception as cleanup_exc:
                logger.debug("[SseClient] SSE connect cleanup failed: %r (ignored)", cleanup_exc)
            return False

    async def _do_disconnect(self, *, timeout: float) -> bool:
        """Tear down the transport (runs inside the owner task).

        Prefer ``_exit_stack.aclose()`` (same shape as StreamableHttpClient) so
        teardown still works after a call-timeout path nulled ``_session`` while
        leaving the SSE generator / ClientSession on the stack. Manual
        ``__aexit__`` gated on ``_session is not None`` would skip session cleanup
        and leave a zombie connection that poisons the next reconnect.
        """
        logger.debug("[SseClient] Disconnecting from %s", self._server_path)
        # Resolve the caller's deadline; NO_TIMEOUT falls back to a 10s
        # defensive bound so a receive_loop hung on a dead TCP connection
        # cannot block teardown forever. Shorter than connect's 60s ceiling
        # because disconnect is cleanup, not a live server wait.
        actual_timeout = timeout if timeout != NO_TIMEOUT else 10.0

        owner_task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        watchdog_fired = False

        def _cancel_owner(task: "asyncio.Task[Any]") -> None:
            nonlocal watchdog_fired
            if not task.done():
                watchdog_fired = True
                logger.warning(
                    "[SseClient] disconnect watchdog fired after %ss; "
                    "cancelling owner task (receive_loop may be hung on a "
                    "dead TCP connection after RST).",
                    actual_timeout,
                )
                task.cancel()

        watchdog = loop.call_later(actual_timeout, _cancel_owner, owner_task)
        try:
            try:
                await self._exit_stack.aclose()
            except BaseExceptionGroup as exc:
                cancelled, rest = exc.split(asyncio.CancelledError)
                if cancelled is None:
                    logger.debug("[SseClient] teardown raised %r (ignored)", exc)
                    return False
                # Re-surface pure cancellation as CancelledError so the outer
                # watchdog-swallow / caller-cancel handler still applies. A raw
                # BaseExceptionGroup would bypass ``except CancelledError``.
                if rest is None:
                    raise asyncio.CancelledError() from exc
                raise
            except Exception as exc:
                logger.debug("[SseClient] teardown raised %r (ignored)", exc)
                return False
            logger.info("[SseClient] SSE client disconnected successfully")
            return True
        except asyncio.CancelledError:
            # Watchdog-only cancel: clear refs and return False. A second
            # cancel (caller interrupt) still propagates.
            cancelling = getattr(owner_task, "cancelling", None)
            if not watchdog_fired or (cancelling is not None and cancelling() > 1):
                raise
            uncancel = getattr(owner_task, "uncancel", None)
            if uncancel is not None:
                uncancel()
            logger.warning(
                "[SseClient] disconnect aborted by watchdog after %ss; "
                "transport scopes may not have exited cleanly.",
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

    async def invalidate_for_lazy_reconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Detach a dead transport after call timeout without closing permanently.

        Unlike ``disconnect()``, this keeps ``_lazy_reconnect`` so the next
        ``call_tool`` may rebuild the SSE session. Prefer this over only
        nulling ``_session`` from outside the owner task.
        """
        async with self._lifecycle_lock:
            self._lazy_reconnect = True
            return await self._submit(lambda: self._do_disconnect(timeout=timeout))

    async def _do_reconnect(self, *, timeout: float) -> bool:
        logger.debug("[SseClient] Executing reconnect sequence")
        # Bound teardown so a hung zombie cannot consume the whole call timeout
        # budget before connect even starts.
        actual_timeout = self._resolve_timeout(timeout)
        await self._do_disconnect(timeout=min(actual_timeout, 3.0))
        return await self._do_connect(timeout=timeout)

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Public API (submits to owner task where required)"""
        logger.info("[SseClient] Connecting to %s", self._server_path)
        return await self._submit(lambda: self._do_connect(timeout=timeout))

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Public teardown: atomically submit disconnect and stop the owner.

        The whole sequence runs under ``_lifecycle_lock`` so a concurrent
        ``reconnect()`` cannot revive the owner mid-teardown (R6).
        """
        logger.debug("[SseClient] Requesting disconnect")
        async with self._lifecycle_lock:
            result = await self._submit(lambda: self._do_disconnect(timeout=timeout))
            # A public disconnect is a final teardown: stop the owner task so it
            # doesn't leak, and clear lazy reconnect so Not connected stays final.
            self._lazy_reconnect = False
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
        """Re-establish the transport, de-duplicating concurrent callers.

        The first caller creates a future and runs the actual
        disconnect+connect; later callers await the same future and
        observe its real result/exception (R1). The lock makes this entry
        section mutually exclusive with ``disconnect()`` (R6): a teardown
        cannot race a reconnect's future setup. The actual ``_do_reconnect``
        runs outside the lock (on the owner task's serialized queue), so
        concurrent reconnect waiters are not blocked from observing the
        result, and ``disconnect()`` only contends on the cheap entry
        section.
        """
        logger.info("[SseClient] Reconnecting to %s", self._server_path)
        async with self._lifecycle_lock:
            if self._reconnect_future is not None:
                fut = self._reconnect_future
                logger.debug("[SseClient] Waiting for in-flight reconnect")
            else:
                self._stopping = False
                self._lazy_reconnect = True
                self._reconnect_future = asyncio.get_running_loop().create_future()
                fut = None

        if fut is not None:
            return await fut

        try:
            result = await self._submit(lambda: self._do_reconnect(timeout=timeout))
            logger.info("[SseClient] Reconnect %s", "succeeded" if result else "failed")
            if result:
                record_reconnect_success(self)
            self._reconnect_future.set_result(result)
            return result
        except BaseException as exc:
            # 包括 CancelledError：让等待方也收到同一异常
            if not self._reconnect_future.done():
                self._reconnect_future.set_exception(exc)
            raise
        finally:
            async with self._lifecycle_lock:
                self._reconnect_future = None

    @with_reconnect
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")

        try:
            tools_response = await asyncio.wait_for(
                self._session.list_tools(), timeout=self._resolve_timeout(timeout)
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
            tool_result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments=arguments),
                timeout=self._resolve_timeout(timeout),
            )
            result_content = extract_mcp_tool_result_content(
                tool_result,
                include_image_content=self._include_image_content,
                tool_name=tool_name,
            )
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
            response = await asyncio.wait_for(
                self._session.list_resources(), timeout=self._resolve_timeout(timeout)
            )
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
            response = await asyncio.wait_for(
                self._session.read_resource(uri), timeout=self._resolve_timeout(timeout)
            )
            logger.info("[SseClient] Read resource '%s' via SSE (%d contents)", uri,
                        len(response.contents))
            return response.contents
        except Exception as e:
            logger.error("[SseClient] Failed to read resource '%s' via SSE: %s", uri, e)
            raise


# Mark that @with_reconnect is applied, so external monkeypatches can detect and skip.
mark_reconnect_applied(SseClient)
