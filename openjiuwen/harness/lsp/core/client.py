# -*- coding: UTF-8 -*-
"""JSON-RPC communication layer for LSP servers — single reader coroutine pattern."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
        self._on_exit = on_exit
        self._capabilities: dict[str, Any] | None = None
        self._is_initialized = False
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, tuple[asyncio.Future[Any], str]] = {}

    @property
    def capabilities(self) -> dict[str, Any] | None:
        return self._capabilities

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

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
        if self._config.initialization_options:
            await self._rpc_notification(
                "workspace/didChangeConfiguration",
                {"settings": self._config.initialization_options},
            )

        self._is_initialized = True
        return result

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
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        try:
            await self._rpc_request("shutdown", {})
        except Exception as exc:
            logger.debug("LSP shutdown request failed (ignored during stop): %s", exc)
        try:
            await self._rpc_notification("exit", {})
        except Exception as exc:
            logger.debug("LSP exit notification failed (ignored during stop): %s", exc)

        self._process.stdin.close()
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
        reader = self._process.stdout

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
            logger.error("LSP reader loop crashed: %s", exc)

    def _dispatch(self, message: dict) -> None:
        msg_id = message.get("id")

        if msg_id is not None:
            msg_id_str = str(msg_id)
            entry = self._pending.pop(msg_id_str, None)
            if entry is None:
                return
            future, _method = entry
            if future.done():
                return
            if "error" in message:
                err = message["error"]
                future.set_exception(
                    LSPError(err.get("code", -1), err.get("message", "Unknown error"))
                )
            else:
                future.set_result(message.get("result"))

    async def _rpc_request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and wait for response."""
        msg_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
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
        """Called when the subprocess exits unexpectedly."""
        self._is_initialized = False
        for future, _method in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(f"LSP server crashed with code {code}"))
        self._pending.clear()
        if self._on_exit:
            self._on_exit(code)
