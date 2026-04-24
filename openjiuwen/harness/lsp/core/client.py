# -*- coding: UTF-8 -*-
"""JSON-RPC communication layer for LSP servers — single reader coroutine pattern."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Callable

from openjiuwen.core.common.logging import tool_logger as logger

from openjiuwen.harness.lsp.core.utils.constants import DEFAULT_REQUEST_TIMEOUT_MS


class LSPError(Exception):

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"LSP Error {code}: {message}")


_CONTENT_LENGTH = "Content-Length"


class LSPClient:
    """
    JSON-RPC communication layer for a single LSP server subprocess.
    """

    def __init__(
        self,
        config: Any,
        process: asyncio.subprocess.Process,
        on_exit: Callable[[int | None], None],
    ) -> None:
        self._config = config
        self._process = process
        self._on_exit_callback: Callable[[int | None], None] | None = on_exit
        self._capabilities: dict[str, Any] | None = None
        self._is_initialized = False
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, tuple[asyncio.Future[Any], str]] = {}
        self._is_stopping = False
        # Guard flag: ensures _on_crash() callbacks (pending future resolution,
        # LSP shutdown, on_exit notification) fire at most once per LSPClient
        # instance even if the subprocess exits multiple times.
        self._crash_reported = False
        # Background task consuming stderr on Windows/Python 3.13+ to prevent
        # stdout reads from blocking when the stderr buffer fills.  Stored here
        # so that stop() can cancel it directly without relying on _read_loop's
        # finally block running first.
        self._stderr_task: asyncio.Task[None] | None = None
        # method → list of callables registered via add_notification_handler()
        self._notification_handlers: dict[str, list[Callable[[Any], None]]] = {}

    @property
    def capabilities(self) -> dict[str, Any] | None:
        return self._capabilities

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def is_alive(self) -> bool:
        """Check if the subprocess is still running (returncode is None)."""
        return self._process.returncode is None

    async def initialize(self) -> dict[str, Any]:
        """Execute full LSP handshake: start reader, send initialize request."""
        from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri

        self._reader_task = asyncio.create_task(self._read_loop(), name="lsp-reader")

        params: dict[str, Any] = {
            "processId": os.getpid(),
            "rootUri": path_to_file_uri(self._config.workspace_folder),
            "workspaceFolders": [
                {"uri": path_to_file_uri(self._config.workspace_folder), "name": "workspace"}
            ],
            "capabilities": {
                "window": {"workDoneProgress": True},
                "workspace": {
                    "applyEdit": True,
                    "workspaceEdit": {"documentChanges": True},
                    "workspaceFolders": True,
                    "configuration": True,
                },
                "textDocument": {
                    "synchronization": {
                        "didOpen": True,
                        "didChange": {"willSave": True, "willSaveWaitUntil": True, "save": True},
                    },
                    "publishDiagnostics": {"versionSupport": True},
                },
            },
        }
        if self._config.initialization_options:
            params["initializationOptions"] = self._config.initialization_options

        result = await self._rpc_request("initialize", params)
        self._capabilities = result.get("capabilities", {})

        await self._rpc_notification("initialized", {})
        # 主动发送配置，避免 pyright 等待配置请求
        await self._rpc_notification(
            "workspace/didChangeConfiguration",
            {"settings": self._config.initialization_options or {}},
        )
        # 等待 100ms，让 reader loop 处理完所有配置请求
        await asyncio.sleep(0.1)

        self._is_initialized = True
        return result

    def add_notification_handler(
        self, method: str, handler: Callable[[Any], None]
    ) -> None:
        """Register *handler* to be called when a server push notification for
        *method* arrives.

        Multiple handlers for the same method are called in registration order.
        Safe to call before :meth:`initialize`.
        """
        self._notification_handlers.setdefault(method, []).append(handler)

    async def send_request(self, method: str, params: Any) -> Any:
        """Send LSP request and wait for response."""
        if not self._is_initialized:
            raise RuntimeError("Client not initialized")
        return await self._rpc_request(method, params)

    async def send_notification(self, method: str, params: Any) -> None:
        """Send LSP notification (fire-and-forget)."""
        if not self._is_initialized:
            return
        await self._rpc_notification(method, params)

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._is_stopping = True
        # Send shutdown request first (needs reader to receive response)
        try:
            await asyncio.wait_for(self._rpc_request("shutdown", {}), timeout=2.0)
        except Exception as exc:
            logger.debug("LSP shutdown request failed (ignored during stop): %s", exc)
        try:
            await self._rpc_notification("exit", {})
        except Exception as exc:
            logger.debug("LSP exit notification failed (ignored during stop): %s", exc)

        # Now cancel the reader task and the stderr consumer.
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        # Explicitly cancel the stderr task here rather than relying on
        # _read_loop's finally block, which may not have run yet.
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass

        # Close pipes explicitly before terminating process to prevent
        # "Event loop is closed" warnings during interpreter shutdown.
        try:
            self._process.stdin.close()
        except Exception as exc:
            logger.debug("Failed to close stdin (ignored): %s", exc)
        try:
            self._process.stdout.close()
        except Exception as exc:
            logger.debug("Failed to close stdout (ignored): %s", exc)
        try:
            self._process.stderr.close()
        except Exception as exc:
            logger.debug("Failed to close stderr (ignored): %s", exc)

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

        for future, _method in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("LSP client stopped"))
        self._pending.clear()

        self._is_initialized = False
        self._capabilities = None

    async def _read_loop(self) -> None:
        """
        Read loop for JSON-RPC messages from stdout.

        On Windows with Python 3.13+, we also consume stderr to prevent
        stdout reads from blocking when stderr buffer fills up.
        """
        logger.debug("[_read_loop] Starting read loop")
        reader = self._process.stdout
        stderr_reader = self._process.stderr

        stderr_task = self._stderr_task = asyncio.create_task(
            self._consume_stderr_forever(stderr_reader), name="lsp-stderr"
        )

        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    line_str = line.decode("ascii", errors="replace")
                    if line_str == "\r\n":
                        break
                    if ":" in line_str:
                        key, _, value = line_str.partition(": ")
                        headers[key.strip()] = value.strip()

                length_str = headers.get(_CONTENT_LENGTH, "")
                try:
                    content_length = int(length_str)
                except ValueError:
                    continue

                if content_length <= 0:
                    continue

                body = await reader.readexactly(content_length)
                body_text = body.decode("utf-8", errors="replace").lstrip("\r\n")
                if not body_text.strip():
                    continue

                try:
                    message = json.loads(body_text)
                except json.JSONDecodeError:
                    continue

                self._dispatch(message)

        except (asyncio.CancelledError, Exception) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.error("[_read_loop] crashed: %s", exc)
            self._on_crash(None)  # Report crash so pending futures are resolved
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
                try:
                    await stderr_task
                except asyncio.CancelledError:
                    pass
            self._stderr_task = None

    def _dispatch(self, message: dict) -> None:
        """Dispatch an incoming JSON-RPC message to the appropriate handler."""
        msg_id = message.get("id")
        method = message.get("method")

        # Handle server-initiated requests (e.g., workspace/configuration, window/showMessageRequest)
        # These have both 'id' and 'method' and are NOT in our pending requests
        if msg_id is not None and method is not None:
            msg_id_str = str(msg_id)
            entry = self._pending.get(msg_id_str)
            if entry is not None:
                future, _method = entry
                if not future.done():
                    # Resolve the future BEFORE removing from _pending so that any
                    # done-callback that inspects _pending still sees the entry.
                    if "error" in message:
                        err = message["error"]
                        future.set_exception(
                            LSPError(err.get("code", -1), err.get("message", "Unknown error"))
                        )
                    else:
                        future.set_result(message.get("result"))
                    self._pending.pop(msg_id_str, None)
            else:
                # Server-initiated request - schedule async handling without blocking read loop
                asyncio.create_task(self._handle_server_request(method, msg_id, message.get("params")))
            return

        # Handle server-initiated notifications (e.g., publishDiagnostics, window/logMessage)
        # These have 'method' but no 'id'.
        if msg_id is None and method is not None:
            handlers = self._notification_handlers.get(method)
            if handlers:
                params = message.get("params")
                for handler in handlers:
                    try:
                        handler(params)
                    except Exception as exc:
                        logger.debug(
                            "[_dispatch] notification handler error for %s: %s", method, exc
                        )
            elif method.startswith("window/") or method.startswith("telemetry/"):
                logger.debug("[_dispatch] server notification: %s", method)
            return

        # Handle responses to our own requests
        if msg_id is not None:
            msg_id_str = str(msg_id)
            entry = self._pending.get(msg_id_str)
            if entry is None:
                return
            future, _method = entry
            if future.done():
                return
            # Resolve the future BEFORE removing from _pending (same rationale as above).
            if "error" in message:
                err = message["error"]
                future.set_exception(
                    LSPError(err.get("code", -1), err.get("message", "Unknown error"))
                )
            else:
                future.set_result(message.get("result"))
            self._pending.pop(msg_id_str, None)

    async def _consume_stderr_forever(self, stderr_reader) -> None:
        """Consume stderr to prevent stdout from blocking on Windows."""
        try:
            while True:
                chunk = await stderr_reader.read(4096)
                if not chunk:
                    break
        except Exception as e:
            logger.debug("[stderr_consumer] Error: %s", e)

    async def _handle_server_request(self, method: str, msg_id: int | str, params: Any | None) -> None:
        """Handle server-initiated requests (e.g., workspace/configuration)."""
        if method == "workspace/configuration":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": []}
            await self._send_response(response)
        elif method == "workspace/workspaceFolders":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": []}
            await self._send_response(response)
        elif method == "client/registerCapability":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": None}
            await self._send_response(response)
        elif method == "client/unregisterCapability":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": None}
            await self._send_response(response)
        else:
            logger.debug("[_handle_server_request] unhandled server request: %s (id=%s)", method, msg_id)
            response = {"jsonrpc": "2.0", "id": msg_id, "result": None}
            await self._send_response(response)

    async def _send_response(self, response: dict) -> None:
        """Send a JSON-RPC response and wait for flush."""
        try:
            body = json.dumps(response).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            self._process.stdin.write(header + body)
            await self._process.stdin.drain()
            logger.debug("[_send_response] sent and drained for id=%s", response.get("id"))
        except Exception as e:
            logger.debug("[_send_response] Failed: %s", e)

    async def _rpc_request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and wait for response, with timeout protection."""
        msg_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        # Timeout guard: reject the future after DEFAULT_REQUEST_TIMEOUT_MS
        def on_timeout() -> None:
            if not future.done():
                future.set_exception(
                    LSPError(-1, f"Request timeout after {DEFAULT_REQUEST_TIMEOUT_MS}ms: {method}")
                )

        timer = loop.call_later(DEFAULT_REQUEST_TIMEOUT_MS / 1000, on_timeout)

        def cleanup(_fut: asyncio.Future[Any]) -> None:
            timer.cancel()

        future.add_done_callback(cleanup)

        self._pending[msg_id] = (future, method)

        body = json.dumps(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        ).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

        self._process.stdin.write(header + body)
        await self._process.stdin.drain()
        return await future

    async def _rpc_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (fire-and-forget)."""
        body = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params}
        ).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    def _on_crash(self, code: int | None) -> None:
        """Called when the subprocess exits unexpectedly (once per crash)."""
        if self._crash_reported:
            return
        self._crash_reported = True
        self._is_initialized = False
        for future, _method in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(f"LSP server crashed with code {code}"))
        self._pending.clear()
        if self._on_exit_callback:
            self._on_exit_callback(code)
