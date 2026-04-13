"""Single LSP server instance lifecycle management."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

from openjiuwen.harness.lsp.core.client import LSPClient, LSPError
from openjiuwen.harness.lsp.core.types import ScopedLspServerConfig
from openjiuwen.harness.lsp.core.utils.constants import (
    LSP_ERROR_CONTENT_MODIFIED,
    MAX_RETRIES_FOR_CONTENT_MODIFIED,
    RETRY_BASE_DELAY_MS,
)


class LSPServerInstance:
    """
    Manages the lifecycle of a single LSP server instance.

    Uses a boolean `_running` flag for state tracking (no state machine).
    Does NOT auto-restart after crashes. Retains ContentModified (-32801)
    exponential back-off retry logic.
    """

    def __init__(
        self,
        config: ScopedLspServerConfig,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._config = config
        self._on_error = on_error
        self._running: bool = False
        self._client: LSPClient | None = None

    @property
    def name(self) -> str:
        return self._config.server_id

    @property
    def config(self) -> ScopedLspServerConfig:
        return self._config

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the server synchronously."""
        if self._running:
            return

        self._client = None

        try:
            process = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                env=({**os.environ, **self._config.env} if self._config.env else None),
                cwd=self._config.workspace_folder,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            self._client = LSPClient(
                config=self._config,
                process=process,
                on_exit=lambda code: self._on_error(
                    RuntimeError(f"Server '{self._config.server_id}' exited with code {code}")
                ) if self._on_error else None,
            )

            await self._client.initialize()
            self._running = True

        except Exception as error:
            self._running = False
            if self._client:
                await self._client.stop()
                self._client = None
            raise

    async def stop(self) -> None:
        """Stop the server gracefully."""
        if not self._running:
            return

        if self._client:
            await self._client.stop()
            self._client = None
        self._running = False

    async def send_request(self, method: str, params: Any) -> Any:
        """
        Send an LSP request with automatic ContentModified retry.
        """
        if not self._running or not self._client:
            raise RuntimeError(f"Server '{self._config.server_id}' not running")

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES_FOR_CONTENT_MODIFIED + 1):
            try:
                return await self._client.send_request(method, params)
            except Exception as error:
                if (isinstance(error, LSPError) and error.code == LSP_ERROR_CONTENT_MODIFIED
                        and attempt < MAX_RETRIES_FOR_CONTENT_MODIFIED):
                    delay_s = RETRY_BASE_DELAY_MS * (2 ** attempt) / 1000
                    await asyncio.sleep(delay_s)
                    last_error = error
                    continue
                raise error from last_error

    async def send_notification(self, method: str, params: Any) -> None:
        """Send an LSP notification."""
        if not self._running or not self._client:
            return
        await self._client.send_notification(method, params)
