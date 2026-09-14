# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level Code Graph service cache with single-flight builds."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot

_manager: CodeGraphManager | None = None
_manager_init_lock = threading.Lock()


class CodeGraphManager:
    """LRU of ``CodeGraphService`` keyed by ``(repo_root, snapshot, config_hash)``."""

    def __init__(self, *, max_cached_repos: int = 4) -> None:
        self.max_cached_repos = max(1, max_cached_repos)
        self._services: OrderedDict[str, CodeGraphService] = OrderedDict()
        self._flights: dict[str, asyncio.Future[CodeGraphService]] = {}
        self._pins: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._sessions: dict[str, CodeGraphService] = {}
        self._session_lock = asyncio.Lock()

    async def get_service(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        ensure: bool = False,
    ) -> CodeGraphService:
        """Return a shared service for ``repo_root``. Does not build unless ``ensure``."""
        cfg = config or CodeGraphConfig()
        root = str(Path(repo_root).resolve())
        snapshot = compute_snapshot(root)
        key = f"{root}|{snapshot}|{cfg.config_hash()}"
        async with self._lock:
            self._pins[key] = self._pins.get(key, 0) + 1
            existing = self._services.get(key)
            if existing is not None:
                self._services.move_to_end(key)
                flight: asyncio.Future[CodeGraphService] | None = None
            else:
                flight = self._flights.get(key)
                if flight is None:
                    loop = asyncio.get_running_loop()
                    flight = loop.create_future()
                    self._flights[key] = flight
                    loop.create_task(self._create_service(key, root, cfg, flight))
        try:
            service = existing if existing is not None else await asyncio.shield(flight)
            if ensure:
                await service.ensure_ready()
            return service
        finally:
            async with self._lock:
                remaining = self._pins.get(key, 1) - 1
                if remaining <= 0:
                    self._pins.pop(key, None)
                else:
                    self._pins[key] = remaining

    async def _create_service(
        self,
        key: str,
        root: str,
        cfg: CodeGraphConfig,
        flight: asyncio.Future[CodeGraphService],
    ) -> None:
        try:
            service = CodeGraphService(root, cfg)
            async with self._lock:
                self._services[key] = service
                self._services.move_to_end(key)
                self._evict_overflow()
            if not flight.done():
                flight.set_result(service)
        except Exception as exc:
            if not flight.done():
                flight.set_exception(exc)
        finally:
            async with self._lock:
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)

    def _evict_overflow(self) -> None:
        """Drop idle cached services only. Never evict in-flight or pinned keys."""
        while len(self._services) > self.max_cached_repos:
            victim = next(
                (
                    key
                    for key in self._services
                    if key not in self._flights and self._pins.get(key, 0) == 0
                ),
                None,
            )
            if victim is None:
                return
            self._services.pop(victim, None)
            logger.info("code_graph evicted cached repo %s", victim)

    async def get_session_service(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        session_id: str,
    ) -> CodeGraphService:
        """Return a service pinned to one repair session.

        Keyed without the repo snapshot on purpose: a session edits the code it
        is querying, so a snapshot-keyed lookup would hand back a new service
        after every edit and rebuild the whole index instead of refreshing the
        files that changed.
        """
        cfg = config or CodeGraphConfig()
        root = str(Path(repo_root).resolve())
        key = f"{root}|session:{session_id}|{cfg.config_hash()}"
        async with self._session_lock:
            existing = self._sessions.get(key)
            if existing is not None:
                return existing
        base = await self.get_service(root, cfg, ensure=True)
        service = base.fork_session()
        async with self._session_lock:
            return self._sessions.setdefault(key, service)

    def drop_session(self, session_id: str) -> None:
        """Release one session's index so its overlay is not kept forever."""
        marker = f"|session:{session_id}|"
        for key in [item for item in self._sessions if marker in item]:
            self._sessions.pop(key, None)

    def drop(self, repo_root: str | Path | None = None) -> None:
        """Drop cached services (all, or one repo)."""
        if repo_root is None:
            self._services.clear()
            self._sessions.clear()
            return
        prefix = str(Path(repo_root).resolve()) + "|"
        for key in [item for item in self._services if item.startswith(prefix)]:
            self._services.pop(key, None)
        for key in [item for item in self._sessions if item.startswith(prefix)]:
            self._sessions.pop(key, None)


def get_code_graph_manager() -> CodeGraphManager:
    """Process-global manager. Created on first use."""
    global _manager
    if _manager is None:
        with _manager_init_lock:
            if _manager is None:
                _manager = CodeGraphManager()
    return _manager


def reset_code_graph_manager() -> None:
    """Test helper: drop the process-global manager."""
    global _manager
    if _manager is not None:
        _manager.drop()
    _manager = None
