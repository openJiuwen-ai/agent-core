"""Single LSP server instance lifecycle management."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

from openjiuwen.core.common.logging import tool_logger as logger

from openjiuwen.harness.lsp.core.client import LSPClient, LSPError
from openjiuwen.harness.lsp.core.types import LspServerState, ScopedLspServerConfig
from openjiuwen.harness.lsp.core.utils.constants import (
    LSP_ERROR_CONTENT_MODIFIED,
    MAX_CRASH_RECOVERY_ATTEMPTS,
    MAX_RETRIES_FOR_CONTENT_MODIFIED,
    RETRY_BASE_DELAY_MS,
)


class LSPServerInstance:
    """
    Manages the lifecycle of a single LSP server instance.

    Uses an explicit state machine (LspServerState) instead of a simple boolean
    flag. Supports crash recovery with automatic restart (up to
    MAX_CRASH_RECOVERY_ATTEMPTS), exponential back-off for ContentModified
    (-32801) errors, and graceful shutdown.

    State transitions::

        STOPPED ──start()──▶ STARTING ──success──▶ RUNNING
           ^                      │                    │
           │                      └──timeout/error──▶ ERROR ──┤
           │                              [crash]             │
           │                                           start()│ (crash recovery)
           │                                                  │
      stop <──────────────────────────────────────────────────┘

    Notes:
    - ERROR → STARTING (crash recovery): occurs when start() is called again
      and crash_count < MAX_CRASH_RECOVERY_ATTEMPTS.
    - RUNNING → ERROR: triggered internally when _read_loop detects subprocess
      exit (server crash), not by an explicit transition.
    """

    def __init__(
        self,
        config: ScopedLspServerConfig,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._config = config
        self._on_error = on_error
        self._state: LspServerState = LspServerState.STOPPED
        self._crash_count: int = 0
        self._last_error: Exception | None = None
        self._client: LSPClient | None = None

    @property
    def name(self) -> str:
        return self._config.server_id

    @property
    def config(self) -> ScopedLspServerConfig:
        return self._config

    @property
    def state(self) -> LspServerState:
        """Current lifecycle state of the server."""
        return self._state

    @property
    def running(self) -> bool:
        """Whether the server is currently running."""
        return self._state == LspServerState.RUNNING

    @property
    def crash_count(self) -> int:
        """Number of crash recovery attempts made so far."""
        return self._crash_count

    @property
    def last_error(self) -> Exception | None:
        """Last error encountered by the server."""
        return self._last_error

    async def start(self) -> None:
        """
        Start the LSP server synchronously.

        Idempotent: if the server is already STARTING or RUNNING, returns immediately.
        If the server is in ERROR state but crash_count < MAX_CRASH_RECOVERY_ATTEMPTS,
        it will attempt to restart (crash recovery).
        If crash_count >= MAX_CRASH_RECOVERY_ATTEMPTS, raises RuntimeError.
        """
        if self._state in {LspServerState.STARTING, LspServerState.RUNNING}:
            return

        if self._state == LspServerState.ERROR:
            if self._crash_count >= MAX_CRASH_RECOVERY_ATTEMPTS:
                raise RuntimeError(
                    f"Server '{self._config.server_id}' exceeded max crash recovery "
                    f"attempts ({MAX_CRASH_RECOVERY_ATTEMPTS}); last error: {self._last_error}"
                )
            logger.info(
                "[LSPServerInstance] '%s' restarting (crash #%d, max %d)",
                self._config.server_id,
                self._crash_count,
                MAX_CRASH_RECOVERY_ATTEMPTS,
            )

        self._state = LspServerState.STARTING

        self._client = None

        try:
            process = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                env=({**os.environ, **self._config.env} if self._config.env else None),
                cwd=self._config.workspace_folder,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self._client = LSPClient(
                config=self._config,
                process=process,
                on_exit=lambda code: self._handle_crash(code),
            )

            await self._client.initialize()
            self._state = LspServerState.RUNNING
            self._crash_count = 0
            self._last_error = None
            logger.info(
                "[LSPServerInstance] '%s' started successfully (root=%s)",
                self._config.server_id,
                self._config.workspace_folder,
            )

        except Exception as error:
            self._state = LspServerState.ERROR
            self._last_error = error
            if self._client:
                await self._client.stop()
                self._client = None
            raise

    async def stop(self) -> None:
        """
        Stop the server gracefully.

        Sends LSP shutdown/exit to the server, terminates the subprocess,
        and resets the state to STOPPED.
        """
        if self._state == LspServerState.STOPPED:
            return

        self._state = LspServerState.STOPPING

        if self._client:
            await self._client.stop()
            self._client = None

        self._state = LspServerState.STOPPED
        logger.info("[LSPServerInstance] '%s' stopped", self._config.server_id)

    async def send_request(self, method: str, params: Any) -> Any:
        """
        Send an LSP request with automatic ContentModified retry and crash detection.

        Raises:
            RuntimeError: if the server is not in RUNNING state.
        """
        if not self.running or not self._client:
            raise RuntimeError(
                f"Server '{self._config.server_id}' not running (state={self._state.value})"
            )

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES_FOR_CONTENT_MODIFIED + 1):
            try:
                return await self._client.send_request(method, params)
            except Exception as error:
                if (
                    isinstance(error, LSPError)
                    and error.code == LSP_ERROR_CONTENT_MODIFIED
                    and attempt < MAX_RETRIES_FOR_CONTENT_MODIFIED
                ):
                    delay_s = RETRY_BASE_DELAY_MS * (2**attempt) / 1000
                    await asyncio.sleep(delay_s)
                    last_error = error
                    continue
                raise error from last_error

    async def send_notification(self, method: str, params: Any) -> None:
        """Send an LSP notification (fire-and-forget)."""
        if not self.running or not self._client:
            return
        await self._client.send_notification(method, params)

    def add_notification_handler(
        self, method: str, handler: Callable[[Any], None]
    ) -> None:
        """Register *handler* for server-push notifications with the given *method*.

        Delegates to the underlying :class:`LSPClient`.  Safe to call before
        the server has started — the handler will be installed as soon as the
        client is created during :meth:`start`.

        If the client is not yet available the call is silently ignored;
        callers should register handlers after :meth:`start` returns or use
        :meth:`LSPServerManager._ensure_diagnostic_handler` which is called
        from :meth:`LSPServerManager.open_file` and
        :meth:`LSPServerManager.change_file` right before the relevant
        notification is sent.
        """
        if self._client is not None:
            self._client.add_notification_handler(method, handler)

    async def is_healthy(self) -> bool:
        """
        Check whether the server is healthy (running and process still alive).

        Returns True only if the state is RUNNING and the underlying subprocess
        has not exited.
        """
        if self._state != LspServerState.RUNNING:
            return False
        if self._client and self._client.is_alive:
            return True
        return False

    def _handle_crash(self, code: int | None) -> None:
        """
        Called when the subprocess exits unexpectedly (non-zero exit code).

        Increments crash_count, sets state to ERROR, and invokes the on_error
        callback if provided. If crash_count is below the threshold, the instance
        remains eligible for automatic restart on the next start() call.
        """
        if self._state == LspServerState.STOPPING:
            return

        self._state = LspServerState.ERROR
        self._crash_count += 1
        self._last_error = RuntimeError(
            f"Server '{self._config.server_id}' exited with code {code}"
        )

        logger.warning(
            "[LSPServerInstance] '%s' crashed (crash #%d, state=%s)",
            self._config.server_id,
            self._crash_count,
            self._state.value,
        )

        if self._on_error:
            self._on_error(self._last_error)
