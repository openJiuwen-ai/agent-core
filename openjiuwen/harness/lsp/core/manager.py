"""Global singleton LSP server manager with lazy-loading hybrid mode."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.harness.lsp.core.instance import LSPServerInstance
from openjiuwen.harness.lsp.core.types import LspServerState, LspServerStatus, ScopedLspServerConfig
from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerInstanceKey:
    """
    Unique identifier for a server instance cache entry.

    Composed of server_id and project root to support the same language
    running under multiple project roots in a monorepo.
    """

    server_id: str
    root: str


class LSPServerManager:
    """
    Multi-server coordinator and global singleton orchestrator.
    """

    _instance: LSPServerManager | None = None
    _lock: asyncio.Lock | None = None  # 延迟创建，避免绑定到创建时的 event loop

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """获取或创建 Lock，确保在同一 event loop 中创建"""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def initialize(
        cls, options: Any | None = None
    ) -> Any:  # noqa: ANN401
        """
        Global singleton initialization (idempotent, fast return).

        Pre-builds all (server_id, root) config mappings and establishes
        extension indices. Servers are NOT started immediately; they
        lazy-load on first LSP request.
        """
        from openjiuwen.harness.lsp.types import InitializeOptions, InitializeResult
        from openjiuwen.harness.lsp.servers.registry import build_configs_async

        opts = options or InitializeOptions()

        if cls._instance is not None:
            return InitializeResult(
                success=True,
                servers_loaded=len(cls._instance.get_status()),
            )

        async with cls._get_lock():
            if cls._instance is not None:
                return InitializeResult(success=True, servers_loaded=0)

            cwd = opts.cwd if opts.cwd else os.getcwd()
            try:
                cwd = str(Path(cwd).resolve())
            except Exception:
                cwd = os.getcwd()
            configs = await build_configs_async(opts, cwd)

            if not configs:
                cls._instance = None
                return InitializeResult(success=True, servers_loaded=0)

            # Build extension -> server_id index
            extension_map: dict[str, list[str]] = {}
            for config in configs:
                for ext in config.extension_to_language.keys():
                    normalized = ext.lower()
                    if normalized not in extension_map:
                        extension_map[normalized] = []
                    if config.server_id not in extension_map[normalized]:
                        extension_map[normalized].append(config.server_id)

            manager = cls()
            server_configs_dict: dict[str, list[ScopedLspServerConfig]] = {}
            for config in configs:
                if config.server_id not in server_configs_dict:
                    server_configs_dict[config.server_id] = []
                server_configs_dict[config.server_id].append(config)
            manager._configs = server_configs_dict
            manager._instances = {}
            manager._spawning = {}
            manager._extension_map = extension_map
            manager._workspace_root = cwd

            cls._instance = manager

            return InitializeResult(
                success=True,
                servers_loaded=len(configs),
                duration_ms=0.0,
            )

    @classmethod
    async def shutdown(cls) -> None:
        """Shutdown all servers managed by the global singleton."""
        if cls._instance:
            await cls._instance.stop_all()
            cls._instance = None
        cls._lock = None  # 清理 Lock 以便下次在不同 loop 中使用

    async def stop_all(self) -> None:
        """Cancel all pending spawn tasks and stop all running server instances."""
        spawning_tasks = list(self._spawning.values())
        for task in spawning_tasks:
            task.cancel()

        if spawning_tasks:
            gathered = asyncio.gather(*spawning_tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(gathered, timeout=5.0)
            except asyncio.TimeoutError:
                pass

        self._spawning.clear()

        if self._instances:
            stops = [inst.stop() for inst in list(self._instances.values())]
            await asyncio.gather(*stops, return_exceptions=True)
        self._instances.clear()

    @classmethod
    def get_instance(cls) -> LSPServerManager | None:
        """Get the global singleton instance."""
        return cls._instance

    @classmethod
    def get_status(cls) -> Any:  # noqa: ANN401
        """Get the current LSP subsystem status."""
        from openjiuwen.harness.lsp.types import LspStatus

        servers = cls._instance.get_status() if cls._instance else []
        return LspStatus(
            initialized=cls._instance is not None,
            servers=servers,
        )

    def __init__(self) -> None:
        self._workspace_root: str = ""
        self._configs: dict[str, list[ScopedLspServerConfig]] = {}
        self._instances: dict[ServerInstanceKey, LSPServerInstance] = {}
        self._spawning: dict[ServerInstanceKey, asyncio.Task[None]] = {}
        self._extension_map: dict[str, list[str]] = {}
        # Set of object-ids for LSPServerInstance objects that already have the
        # publishDiagnostics handler registered.  Prevents double-registration
        # when open_file() or change_file() is called multiple times for the
        # same server instance.
        self._diag_handler_instances: set[int] = set()
        # file URI → current document version (incremented on each didChange)
        self._doc_versions: dict[str, int] = {}

    def get_workspace_root(self) -> str:
        """Get the workspace root directory."""
        return self._workspace_root

    async def get_or_start_server(self, file_path: str) -> LSPServerInstance | None:
        """
        Get or start a server for the given file (supports monorepos).

        Flow:
        1. Match file extension to server_id list
        2. Dynamically find the file's root via find_root
        3. Check cache (ServerInstanceKey):
           - Running & healthy -> return directly
           - Starting -> await result
           - ERROR with remaining crash attempts -> restart
           - Not started -> _start_server waits for full startup, then return
        """
        from openjiuwen.harness.lsp.servers.registry import BUILTIN_SERVERS

        ext = Path(file_path).suffix.lower()
        server_ids = self._extension_map.get(ext, [])

        for server_id in server_ids:
            server_def = BUILTIN_SERVERS.get(server_id)
            if not server_def:
                continue

            root_result = server_def.find_root(file_path)
            if asyncio.iscoroutine(root_result):
                root = await root_result
            else:
                root = root_result

            if root is None:
                continue

            configs = self._configs.get(server_id, [])

            for config in configs:
                key = ServerInstanceKey(server_id=server_id, root=root)

                if key in self._instances:
                    instance = self._instances[key]
                    if instance.running:
                        if await instance.is_healthy():
                            return instance
                        # running=True but zombie (crashed) — fall through to restart
                        logger.info(
                            "[LSPServerManager] zombie instance for %s, restarting",
                            key,
                        )
                        self._instances.pop(key, None)
                    elif instance.state == LspServerState.ERROR:
                        # Crash recovery: if attempts remain, restart; else give up
                        self._instances.pop(key, None)
                    elif key in self._spawning:
                        await self._spawning[key]
                        if key in self._instances and self._instances[key].running:
                            return self._instances[key]

                # _start_server waits for full startup (LSP handshake complete) before returning
                instance = await self._start_server(key, config, root)
                if instance and instance.running:
                    return instance

        return None

    async def _start_server(
        self,
        key: ServerInstanceKey,
        config: ScopedLspServerConfig,
        root: str | None = None,
    ) -> LSPServerInstance | None:
        """Start a server with the given config (supports dynamic root)."""
        if key in self._instances and self._instances[key].running:
            return self._instances[key]

        if key in self._spawning:
            task = self._spawning[key]
            await task
            return self._instances.get(key)

        if root and root != config.workspace_folder:
            active_config = ScopedLspServerConfig(
                server_id=config.server_id,
                command=config.command,
                args=config.args,
                env=config.env,
                workspace_folder=root,
                initialization_options=config.initialization_options,
                startup_timeout=config.startup_timeout,
                extension_to_language=config.extension_to_language,
            )
        else:
            active_config = config

        instance = LSPServerInstance(
            config=active_config,
            on_error=lambda e: self._log_server_error(active_config.server_id, e),
        )

        async def start_and_cache() -> None:
            try:
                await instance.start()
            except Exception as e:
                self._log_server_error(active_config.server_id, e)
            finally:
                self._spawning.pop(key, None)

        self._instances[key] = instance
        task = asyncio.create_task(start_and_cache())
        self._spawning[key] = task

        # Wait for the server to fully start (LSP handshake complete) before returning
        try:
            await asyncio.wait_for(task, timeout=active_config.startup_timeout / 1000)
        except asyncio.TimeoutError:
            task.cancel()
            self._log_server_error(
                active_config.server_id,
                RuntimeError(f"Server start timeout after {active_config.startup_timeout}ms"),
            )
            self._spawning.pop(key, None)
            self._instances.pop(key, None)  # Clean up failed entry to prevent zombie cache
            return None
        except Exception as exc:
            self._log_server_error(active_config.server_id, exc)
            self._instances.pop(key, None)  # Clean up failed entry
            return None

        return instance if instance.running else None

    @staticmethod
    def _path_belongs_to_root(file_path: str, root: str) -> bool:
        """Check whether a file path belongs to a given root directory."""
        try:
            abs_file = Path(file_path).resolve()
            abs_root = Path(root).resolve()
            abs_file.relative_to(abs_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _log_server_error(server_id: str, error: Exception) -> None:
        """Log a server error."""
        logger.warning(f"[LSP] Server '{server_id}' failed: {error}")

    def _ensure_diagnostic_handler(self, server: LSPServerInstance) -> None:
        """Register the ``textDocument/publishDiagnostics`` handler on *server*.

        Idempotent: the handler is registered at most once per server instance
        (guarded by ``id(server)``).  The handler writes incoming diagnostic
        notifications into the process-global :class:`LspDiagnosticRegistry`.

        Called by :meth:`open_file` and :meth:`change_file` just before
        sending the respective LSP notifications so that any diagnostics
        emitted in response are captured.
        """
        from openjiuwen.harness.lsp.core.diagnostic_registry import LspDiagnosticRegistry

        instance_id = id(server)
        if instance_id in self._diag_handler_instances:
            return

        registry = LspDiagnosticRegistry.get_instance()
        server_name = server.config.server_id

        def _publish_diagnostics_handler(params: Any) -> None:
            if not params or not isinstance(params, dict):
                return
            uri = params.get("uri", "")
            raw_diags = params.get("diagnostics", [])
            if not uri or not isinstance(raw_diags, list):
                return
            registry.register(server_name, uri, raw_diags)

        server.add_notification_handler(
            "textDocument/publishDiagnostics",
            _publish_diagnostics_handler,
        )
        self._diag_handler_instances.add(instance_id)
        logger.debug(
            "[LSPServerManager] publishDiagnostics handler registered for server '%s'",
            server_name,
        )

    @classmethod
    def get_pending_diagnostics(
        cls,
        max_per_file: int = 10,
        max_total: int = 30,
    ) -> list[Any]:
        """Return pending LSP diagnostics from the global registry and clear the queue.

        Diagnostics are deduplicated, capped, and sorted by severity
        (Error=1 first).  See :class:`LspDiagnosticRegistry` for full semantics.

        Args:
            max_per_file: Maximum diagnostics per file URI (default 10).
            max_total:    Maximum diagnostics across all files (default 30).

        Returns:
            A list of :class:`LspDiagnosticFile` objects.  Empty when nothing pending.
        """
        from openjiuwen.harness.lsp.core.diagnostic_registry import LspDiagnosticRegistry

        return LspDiagnosticRegistry.get_instance().get_and_clear(max_per_file, max_total)

    def is_file_open(self, uri: str) -> bool:
        """Return ``True`` if *uri* has been opened via :meth:`open_file`."""
        return uri in self._doc_versions

    async def open_file(self, file_path: str, language_id: str) -> None:
        """Open a file: send ``textDocument/didOpen``.

        Before sending, registers the ``publishDiagnostics`` handler on the
        server (once per server instance) so that the full-file diagnostic
        scan triggered by ``didOpen`` is captured in the global
        :class:`LspDiagnosticRegistry`.

        The document version is initialised to ``0`` and the next
        :meth:`change_file` call will increment it to ``1``.
        """
        server = await self.get_or_start_server(file_path)
        if not server:
            return

        self._ensure_diagnostic_handler(server)

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Could not read file for didOpen notification: %s", exc)
            text = ""

        uri = path_to_file_uri(file_path)
        self._doc_versions[uri] = 0
        await server.send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 0,
                    "text": text,
                }
            },
        )

    async def change_file(
        self,
        file_path: str,
        language_id: str,
        content: str | None = None,
    ) -> None:
        """Notify the LSP server that a file's content has changed.

        Sends ``textDocument/didChange`` using a full-file content replacement
        (no diff — the entire text is sent as a single content-change event
        without a ``range`` field, which corresponds to LSP *full* sync mode).

        Before sending, registers the ``publishDiagnostics`` handler on the
        server (once per server instance) so that any diagnostics emitted in
        response to the change are captured in the global
        :class:`LspDiagnosticRegistry`.

        The document version is incremented on each call.  If ``open_file``
        has not been called previously for this URI the version starts at 1.

        Args:
            file_path:   Absolute path to the changed file.
            language_id: LSP language identifier (e.g. ``"python"``).
            content:     New full text of the file.  When ``None`` the file is
                         read from disk.  Pass an explicit string when the
                         in-memory content differs from the on-disk content
                         (e.g. unsaved editor buffer).
        """
        server = await self.get_or_start_server(file_path)
        if not server:
            return

        self._ensure_diagnostic_handler(server)

        if content is None:
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except OSError as exc:
                logger.debug("Could not read file for didChange notification: %s", exc)
                content = ""

        uri = path_to_file_uri(file_path)
        version = self._doc_versions.get(uri, 0) + 1
        self._doc_versions[uri] = version

        await server.send_notification(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": uri,
                    "version": version,
                },
                "contentChanges": [{"text": content}],
            },
        )

    async def send_request(
        self,
        file_path: str,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        """Send an LSP request to the appropriate server."""
        server = await self.get_or_start_server(file_path)
        if not server:
            raise RuntimeError(f"No LSP server for file: {file_path}")
        return await server.send_request(method, params)

    def get_status(self) -> list[LspServerStatus]:
        """Get status of all started servers."""
        return [
            LspServerStatus(
                server_id=inst.config.server_id,
                name=inst.config.server_id,
                running=inst.running,
                state=inst.state,
                root=inst.config.workspace_folder,
                crash_count=inst.crash_count,
                last_error=str(inst.last_error) if inst.last_error else None,
            )
            for inst in self._instances.values()
        ]
