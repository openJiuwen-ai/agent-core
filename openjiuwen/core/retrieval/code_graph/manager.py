# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-level Code Graph registry: one workspace path, one current graph."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from openjiuwen.core.common.logging import retrieval_logger as logger
from openjiuwen.core.retrieval.code_graph.budgets import (
    cache_dir_bytes,
    cancel_requested,
    raise_if_resource_limits,
    raise_limit_exceeded,
)
from openjiuwen.core.retrieval.code_graph.errors import (
    CodeGraphBusy,
    CodeGraphLimitExceeded,
    CodeGraphStatus,
)
from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity
from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.lifecycle import (
    GraphEntry,
    GraphGeneration,
    GraphLease,
    estimate_index_bytes,
)
from openjiuwen.core.retrieval.code_graph.metrics import record_code_graph_event
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig, CodeGraphIndex
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore
from openjiuwen.core.retrieval.code_graph.workspace_token import (
    WorkspaceToken,
    compute_workspace_token,
    detect_changed_paths,
    head_changed_paths,
    incremental_limit,
)

_manager: CodeGraphManager | None = None
_manager_init_lock = threading.Lock()


class CodeGraphManager:
    """Registry of shared ``GraphEntry`` objects keyed by workspace ``repo_id``."""

    def __init__(
        self,
        *,
        max_cached_repos: int = 3,
        memory_idle_ttl_seconds: float = 1800.0,
        max_process_index_memory_mb: int = 1536,
        max_concurrent_builds: int = 1,
    ) -> None:
        self.max_cached_repos = max(1, max_cached_repos)
        self.memory_idle_ttl_seconds = max(0.0, float(memory_idle_ttl_seconds))
        self.max_process_index_bytes = max(1, int(max_process_index_memory_mb)) * 1024 * 1024
        self._entries: OrderedDict[str, GraphEntry] = OrderedDict()
        self._flights: dict[str, asyncio.Future[CodeGraphService]] = {}
        self._pins: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._build_sema = asyncio.Semaphore(max(1, int(max_concurrent_builds)))

    @property
    def _services(self) -> dict[str, CodeGraphService]:
        """Compatibility view used by older tests that inspected cache internals."""
        return {
            key: entry.service
            for key, entry in self._entries.items()
            if entry.service is not None
        }

    async def get_service(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        ensure: bool = False,
    ) -> CodeGraphService:
        """Return the shared service for ``repo_root``. Does not build unless ``ensure``."""
        cfg = config or CodeGraphConfig()
        identity = RepoIdentity.from_path(repo_root)
        key = identity.entry_key(cfg.config_hash())
        async with self._lock:
            self._pins[key] = self._pins.get(key, 0) + 1
            self.reclaim()
            entry = self._entries.get(key)
            if entry is not None and entry.service is not None:
                self._bind_config(entry, cfg)
                self._entries.move_to_end(key)
                service = entry.service
                flight: asyncio.Future[CodeGraphService] | None = None
            else:
                flight = self._flights.get(key)
                if flight is None:
                    loop = asyncio.get_running_loop()
                    flight = loop.create_future()
                    self._flights[key] = flight
                    loop.create_task(self._create_service(key, identity, cfg, flight))
                service = None
        try:
            if service is None:
                service = await asyncio.shield(flight)
            if ensure:
                await self.ensure_fresh(identity.canonical_root, cfg)
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
        identity: RepoIdentity,
        cfg: CodeGraphConfig,
        flight: asyncio.Future[CodeGraphService],
    ) -> None:
        try:
            entry = self._entries.get(key) or GraphEntry(
                identity=identity,
                config_hash=cfg.config_hash(),
            )
            service = CodeGraphService(
                identity.canonical_root,
                cfg,
                persist_index=True,
            )
            service.bind_entry(entry)
            entry.service = service
            entry.manager = self
            async with self._lock:
                self._entries[key] = entry
                self._entries.move_to_end(key)
                self.reclaim()
            if not flight.done():
                flight.set_result(service)
        except Exception as exc:
            if not flight.done():
                flight.set_exception(exc)
        finally:
            async with self._lock:
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)

    def reclaim(self) -> None:
        """Drop idle and over-quota entries. Safe to call from queries and the watcher."""
        self._evict_idle()
        self._evict_overflow()

    def _evict_idle(self) -> None:
        """Drop entries unused longer than ``memory_idle_ttl_seconds``, even under quota."""
        ttl = self.memory_idle_ttl_seconds
        if ttl <= 0:
            return
        now = time.time()
        for key in [item for item in self._entries if self._can_evict(item, now, require_idle=True)]:
            self._drop_entry(key, reason="idle")

    def _evict_overflow(self) -> None:
        """Drop cached entries when repo count or estimated bytes exceed the cap."""
        now = time.time()
        while True:
            over_count = len(self._entries) > self.max_cached_repos
            over_bytes = self._resident_bytes() > self.max_process_index_bytes
            if not over_count and not over_bytes:
                return
            victim = next(
                (key for key in self._entries if self._can_evict(key, now, require_idle=False)),
                None,
            )
            if victim is None:
                return
            self._drop_entry(victim, reason="overflow")

    def _can_evict(self, key: str, now: float, *, require_idle: bool) -> bool:
        entry = self._entries.get(key)
        if entry is None or self._windows_using(key, entry):
            return False
        if not require_idle:
            return True
        return now - entry.last_access_at >= self.memory_idle_ttl_seconds

    def _windows_using(self, key: str, entry: GraphEntry) -> bool:
        """True when another chat is reading, building, or still on this graph."""
        if key in self._flights or self._pins.get(key, 0) > 0:
            return True
        if entry.update_task is not None and not entry.update_task.done():
            return True
        if entry.active is not None and entry.active.reader_count > 0:
            return True
        if any(item.reader_count > 0 for item in entry.retired):
            return True
        return False

    def _drop_entry(self, key: str, *, reason: str, persist: bool = True) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        if persist:
            self._checkpoint_entry(entry, reason=f"evict-{reason}")
        else:
            cfg = entry.service.config if entry.service is not None else None
            store = self._store(cfg) if cfg is not None else None
            if store is not None:
                store.delete_repo(entry.identity.repo_id)
        logger.info("code_graph evicted cached repo %s reason=%s", key, reason)

    def _resident_bytes(self) -> int:
        return sum(entry.estimated_bytes() for entry in self._entries.values())

    async def get_session_service(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        session_id: str = "",
    ) -> CodeGraphService:
        """Legacy façade. Product tools share the workspace entry; they do not fork.

        ``session_id`` is accepted so old callers keep working. It is not a cache key.
        """
        del session_id
        return await self.get_service(repo_root, config, ensure=True)

    def drop(self, repo_root: str | Path | None = None) -> None:
        """Drop cached entries (all, or one repo)."""
        if repo_root is None:
            self._entries.clear()
            return
        identity = RepoIdentity.from_path(repo_root)
        self._entries.pop(identity.repo_id, None)

    def mark_dirty(
        self,
        repo_root: str | Path,
        paths: list[str],
        *,
        source: str = "tool",
        conversation_id: str | None = None,
        config: CodeGraphConfig | None = None,
    ) -> None:
        """Record precise file writes. Next latest query must refresh."""
        del source, conversation_id
        entry = self._peek_entry(repo_root, config)
        if entry is None:
            return
        entry.mark_dirty(paths)
        record_code_graph_event(
            "mark_dirty",
            0.0,
            repo_id=entry.identity.repo_id,
            path_count=len(paths),
        )

    def mark_dirty_unknown(
        self,
        repo_root: str | Path,
        reason: str = "unknown",
        *,
        config: CodeGraphConfig | None = None,
    ) -> None:
        """Record an unbounded mutation (shell, git, generator)."""
        entry = self._peek_entry(repo_root, config)
        if entry is None or entry.active is None:
            return
        entry.mark_dirty_unknown()
        record_code_graph_event(
            "mark_dirty_unknown",
            0.0,
            repo_id=entry.identity.repo_id,
            reason=reason,
        )

    def _peek_entry(self, repo_root: str | Path, config: CodeGraphConfig | None) -> GraphEntry | None:
        del config
        identity = RepoIdentity.from_path(repo_root)
        return self._entries.get(identity.repo_id)

    async def acquire(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
    ) -> GraphLease:
        """Pin the current generation after a freshness barrier."""
        cfg = config or CodeGraphConfig()
        generation = await self.ensure_fresh(repo_root, cfg)
        generation.reader_count += 1
        return GraphLease(generation)

    def release(self, lease: GraphLease) -> None:
        lease.release()

    async def ensure_fresh(
        self,
        repo_root: str | Path,
        config: CodeGraphConfig | None = None,
        *,
        consistency: str = "latest",
    ) -> GraphGeneration:
        """Return the current generation, refreshing when the workspace moved."""
        del consistency
        cfg = config or CodeGraphConfig()
        service = await self.get_service(repo_root, cfg, ensure=False)
        entry = service.lifecycle_entry
        if entry is None:
            identity = RepoIdentity.from_path(repo_root)
            entry = GraphEntry(identity=identity, config_hash=cfg.config_hash(), service=service)
            service.bind_entry(entry)
            self._entries[identity.entry_key(cfg.config_hash())] = entry
        self._bind_config(entry, cfg)
        entry.manager = self
        return await self._ensure_fresh_or_wait(entry, cfg)

    async def _ensure_fresh_or_wait(self, entry: GraphEntry, cfg: CodeGraphConfig) -> GraphGeneration:
        started = time.perf_counter()
        async with entry.update_lock:
            entry.normalize_dirty_paths()
            generation = self._fresh_if_clean(entry, cfg, started)
            if generation is not None:
                return generation
            token = compute_workspace_token(
                entry.identity.canonical_root,
                cfg,
                extra_paths=tuple(entry.dirty_paths),
                previous_dirty_paths=entry.active.token.dirty_paths if entry.active else (),
            )
            entry.last_freshness_at = time.time()
            if (
                entry.limit_error is not None
                and entry.limit_exceeded_digest == token.digest
            ):
                if not self._raised_cap_clears_limit(entry.limit_error, cfg):
                    raise entry.limit_error
                entry.limit_error = None
                entry.limit_exceeded_digest = None
            elif entry.limit_error is not None:
                entry.limit_error = None
                entry.limit_exceeded_digest = None
            if (
                entry.active is not None
                and not entry.dirty_paths
                and not entry.dirty_unknown
                and entry.active.token.digest == token.digest
            ):
                record_code_graph_event("index_memory", (time.perf_counter() - started) * 1000, cache_hit=True)
                return entry.active
            task = entry.update_task
            if task is None or task.done():
                async with self._lock:
                    self.reclaim()
                    if self._resource_blocks_build(cfg):
                        self._release_obsolete_generation(entry, cfg)
                        self._evict_unused_others(cfg, keep=entry)
                    blocked = self._resource_blocks_build(cfg)
                if blocked:
                    self._refuse_after_cleanup(entry, cfg, token.digest)
                entry.update_task = asyncio.create_task(self._update_entry(entry, cfg, token))
                task = entry.update_task
        wait = cfg.resolved_wait_seconds(first_build=entry.active is None)
        if wait is None:
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except asyncio.TimeoutError as exc:
            record_code_graph_event(
                "query_wait",
                (time.perf_counter() - started) * 1000,
                repo_id=entry.identity.repo_id,
                status="timeout",
            )
            raise CodeGraphBusy(
                CodeGraphStatus.BUILDING,
                "index is still updating; retry this tool shortly",
            ) from exc

    def _fresh_if_clean(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        started: float,
    ) -> GraphGeneration | None:
        """Reuse the published generation only inside an explicit skip window.

        Product default is 0 ms: correctness is the content-aware token on
        every query. A positive interval is opt-in debounce, not a freshness
        guarantee for unmarked Shell/IDE writes.
        """
        now = time.time()
        interval = max(0, int(cfg.freshness_check_interval_ms)) / 1000.0
        if (
            entry.active is not None
            and not entry.dirty_paths
            and not entry.dirty_unknown
            and interval
            and now - entry.last_freshness_at < interval
        ):
            record_code_graph_event("index_memory", (time.perf_counter() - started) * 1000, cache_hit=True)
            return entry.active
        return None

    async def _update_entry(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        token: WorkspaceToken,
    ) -> GraphGeneration:
        cancel = threading.Event()
        entry.cancel_event = cancel
        rss_before = _rss_bytes()
        generation: GraphGeneration | None = None
        async with self._build_sema:
            try:
                generation = await self._run_update_respecting_resources(
                    entry, cfg, token, cancel
                )
            except CodeGraphLimitExceeded as exc:
                self._abandon_entry(entry, cfg, exc, token.digest)
                raise
            finally:
                entry.cancel_event = None
        rss_after = _rss_bytes()
        if generation is not None and rss_after and rss_before:
            record_code_graph_event(
                "index_rss",
                0.0,
                repo_id=entry.identity.repo_id,
                rss_before=rss_before,
                rss_after=rss_after,
                rss_delta=max(0, rss_after - rss_before),
                estimated_bytes=generation.estimated_bytes,
                reason=generation.reason,
            )
        return generation

    async def _update_entry_body(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        token: WorkspaceToken,
        cancel: threading.Event,
    ) -> GraphGeneration:
        start_epoch = entry.change_epoch
        reason, paths = self._plan_update(entry, cfg, token)
        started = time.perf_counter()
        candidate_index = None
        if reason == "incremental" and entry.active is not None:
            candidate_index = entry.active.index.copy_for_session()
        self._release_obsolete_generation(entry, cfg)
        if reason == "incremental" and candidate_index is not None:
            result = await asyncio.to_thread(
                refresh_index_files,
                candidate_index,
                paths,
                cfg,
                cancel=cancel,
            )
            if result.cancelled or cancel_requested(cancel):
                if entry.active is not None:
                    return entry.active
                raise InterruptedError("code_graph indexing cancelled")
            if result.stale or result.failed:
                reason = "full"
                record_code_graph_event(
                    "index_refresh",
                    (time.perf_counter() - started) * 1000,
                    reason="incremental",
                    status="stale",
                    repo_id=entry.identity.repo_id,
                )
            else:
                candidate_token = compute_workspace_token(
                    entry.identity.canonical_root,
                    cfg,
                    extra_paths=tuple(entry.dirty_paths),
                    previous_dirty_paths=token.dirty_paths,
                )
                if entry.change_epoch != start_epoch or candidate_token.digest != token.digest:
                    replay = sorted(set(detect_changed_paths(
                        entry.identity.canonical_root,
                        entry.file_hashes,
                        extra_paths=candidate_token.dirty_paths,
                    )))
                    if len(replay) > incremental_limit(
                        candidate_index.file_count,
                        cfg,
                        last_full_build_seconds=entry.last_full_build_seconds,
                    ):
                        reason = "full"
                    else:
                        await asyncio.to_thread(
                            refresh_index_files,
                            candidate_index,
                            replay,
                            cfg,
                            cancel=cancel,
                        )
                        if cancel_requested(cancel):
                            if entry.active is not None:
                                return entry.active
                            raise InterruptedError("code_graph indexing cancelled")
                        candidate_token = compute_workspace_token(
                            entry.identity.canonical_root,
                            cfg,
                            extra_paths=tuple(entry.dirty_paths),
                        )
                if reason == "incremental":
                    generation = self._publish(entry, candidate_index, candidate_token, "incremental", cfg)
                    record_code_graph_event(
                        "index_refresh",
                        (time.perf_counter() - started) * 1000,
                        reason="incremental",
                        status="success",
                        repo_id=entry.identity.repo_id,
                        path_count=len(paths),
                    )
                    return generation
        if cancel_requested(cancel) and entry.active is not None:
            return entry.active
        try:
            index, from_cache = await self._load_or_build(entry, cfg)
        except InterruptedError:
            if entry.active is not None:
                return entry.active
            raise
        if cancel_requested(cancel) and entry.active is not None:
            return entry.active
        index, published_token, catch_up_full = await self._catch_up_loaded(
            entry, cfg, index, token, start_epoch, cancel=cancel
        )
        if cancel_requested(cancel) and entry.active is not None:
            return entry.active
        used_full = catch_up_full or not from_cache
        if used_full:
            entry.last_full_build_seconds = time.perf_counter() - started
        generation = self._publish(entry, index, published_token, "full" if used_full else "incremental", cfg)
        record_code_graph_event(
            "index_build" if used_full else "index_refresh",
            (time.perf_counter() - started) * 1000,
            reason="full" if used_full else "incremental",
            cache_hit=from_cache and not catch_up_full,
            repo_id=entry.identity.repo_id,
            file_count=index.file_count,
        )
        return generation

    def _publish(
        self,
        entry: GraphEntry,
        index: CodeGraphIndex,
        token: WorkspaceToken,
        reason: str,
        cfg: CodeGraphConfig,
    ) -> GraphGeneration:
        generation = self._make_generation(entry, index, token, reason)
        store = self._store(cfg)
        if store is not None:
            entry.last_token = token
            self._save_checkpoint(store, entry, cfg, index)
        entry.config_hash = cfg.config_hash()
        entry.publish(generation)
        service = entry.service
        if service is not None:
            service.adopt_index(index)
        return generation

    async def _catch_up_loaded(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        index: CodeGraphIndex,
        token: WorkspaceToken,
        start_epoch: int,
        *,
        cancel: object | None = None,
    ) -> tuple[CodeGraphIndex, WorkspaceToken, bool]:
        """Bring a checkpoint or just-built index up to the current workspace."""
        detected = detect_changed_paths(
            entry.identity.canonical_root,
            index.file_hashes,
            extra_paths=(*entry.dirty_paths, *token.dirty_paths),
        )
        replay = sorted(set(detected))
        limit = incremental_limit(
            index.file_count or len(index.file_hashes),
            cfg,
            last_full_build_seconds=entry.last_full_build_seconds,
        )
        need_full = bool(entry.dirty_unknown and not replay) or len(replay) > limit
        if need_full:
            rebuilt = await asyncio.to_thread(
                build_index, entry.identity.canonical_root, cfg, cancel=cancel
            )
            published = compute_workspace_token(
                entry.identity.canonical_root,
                cfg,
                extra_paths=tuple(entry.dirty_paths),
            )
            return rebuilt, published, True
        if replay or entry.change_epoch != start_epoch:
            if replay:
                result = await asyncio.to_thread(
                    refresh_index_files, index, replay, cfg, cancel=cancel
                )
                if result.cancelled or cancel_requested(cancel):
                    return index, token, False
                if result.stale or result.failed:
                    rebuilt = await asyncio.to_thread(
                        build_index, entry.identity.canonical_root, cfg, cancel=cancel
                    )
                    published = compute_workspace_token(
                        entry.identity.canonical_root,
                        cfg,
                        extra_paths=tuple(entry.dirty_paths),
                    )
                    return rebuilt, published, True
        published = compute_workspace_token(
            entry.identity.canonical_root,
            cfg,
            extra_paths=tuple(entry.dirty_paths),
        )
        return index, published, False

    def _plan_update(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        token: WorkspaceToken,
    ) -> tuple[str, list[str]]:
        if entry.active is None:
            return "full", []
        if entry.active.index.config_hash != cfg.config_hash():
            return "full", []
        head_paths = head_changed_paths(
            entry.identity.canonical_root,
            entry.active.token.head,
            token.head,
        )
        detected = detect_changed_paths(
            entry.identity.canonical_root,
            entry.file_hashes,
            extra_paths=(*entry.dirty_paths, *token.dirty_paths),
        )
        paths = sorted(set(head_paths) | set(detected) | set(entry.dirty_paths))
        limit = incremental_limit(
            entry.active.index.file_count,
            cfg,
            last_full_build_seconds=entry.last_full_build_seconds,
        )
        if entry.dirty_unknown and not paths:
            return "full", []
        if len(head_paths) > limit or len(paths) > limit:
            return "full", paths
        if not paths:
            return "full", []
        return "incremental", paths

    def _make_generation(
        self,
        entry: GraphEntry,
        index: CodeGraphIndex,
        token: WorkspaceToken,
        reason: str,
    ) -> GraphGeneration:
        index.snapshot = token.digest
        return GraphGeneration(
            generation_id=entry.next_generation_id,
            token=token,
            index=index,
            created_at=time.time(),
            estimated_bytes=estimate_index_bytes(index),
            reason=reason,
        )

    async def _load_or_build(self, entry: GraphEntry, cfg: CodeGraphConfig) -> tuple[CodeGraphIndex, bool]:
        store = self._store(cfg)
        if store is not None:
            store.cleanup_orphans()
            store.purge_expired(cfg.disk_ttl_days)
            cached = store.load_active(entry.identity.repo_id, cfg.config_hash())
            if cached is not None:
                self._restore_full_build_seconds(entry, store)
                record_code_graph_event("index_cache_hit", 0.0, repo_id=entry.identity.repo_id, cache_hit=True)
                return cached, True
            return await asyncio.to_thread(self._locked_build, entry, cfg, store)
        index = await asyncio.to_thread(
            build_index,
            entry.identity.canonical_root,
            cfg,
            cancel=entry.cancel_event,
        )
        return index, False

    def _locked_build(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        store: DiskIndexStore,
    ) -> tuple[CodeGraphIndex, bool]:
        lock_path = store.build_lock_path(entry.identity.repo_id)
        try:
            from filelock import FileLock

            lock: FileLock | None = FileLock(str(lock_path), timeout=cfg.index_timeout_seconds)
        except Exception:  # noqa: BLE001 — disk lock is best-effort
            lock = None
        cancel = entry.cancel_event
        if lock is None:
            index = build_index(entry.identity.canonical_root, cfg, cancel=cancel)
            self._save_checkpoint(store, entry, cfg, index)
            return index, False
        with lock:
            cached = store.load_active(entry.identity.repo_id, cfg.config_hash())
            if cached is not None:
                self._restore_full_build_seconds(entry, store)
                return cached, True
            index = build_index(entry.identity.canonical_root, cfg, cancel=cancel)
            self._save_checkpoint(store, entry, cfg, index)
            return index, False

    def _restore_full_build_seconds(self, entry: GraphEntry, store: DiskIndexStore) -> None:
        if entry.last_full_build_seconds is not None:
            return
        seconds = store.load_full_build_seconds(entry.identity.repo_id)
        if seconds is not None:
            entry.last_full_build_seconds = seconds

    def checkpoint(self, repo_root: str | Path, reason: str = "manual", config: CodeGraphConfig | None = None) -> None:
        entry = self._peek_entry(repo_root, config)
        if entry is not None:
            self._checkpoint_entry(entry, reason=reason)

    def _checkpoint_entry(self, entry: GraphEntry, *, reason: str) -> None:
        if entry.active is None or entry.service is None:
            return
        cfg = entry.service.config
        store = self._store(cfg)
        if store is None:
            return
        try:
            self._save_checkpoint(store, entry, cfg, entry.active.index)
            entry.checkpoint_dirty = False
            record_code_graph_event("index_checkpoint", 0.0, reason=reason, repo_id=entry.identity.repo_id)
        except Exception as exc:  # noqa: BLE001 — checkpoint is best effort
            logger.warning("code_graph checkpoint failed: %s", exc)

    def _save_checkpoint(
        self,
        store: DiskIndexStore,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        index: CodeGraphIndex,
    ) -> None:
        token = entry.last_token or compute_workspace_token(entry.identity.canonical_root, cfg)
        cache_key = f"{entry.identity.repo_id}/{token.digest}-{cfg.config_hash()}"
        store.save(
            cache_key,
            index,
            last_full_build_seconds=entry.last_full_build_seconds,
        )

    def _store(self, cfg: CodeGraphConfig) -> DiskIndexStore | None:
        if not cfg.cache_dir:
            return None
        return DiskIndexStore(cfg.cache_dir, max_size_mb=max(1, cfg.disk_quota_bytes() // (1024 * 1024)))

    def stats(
        self,
        repo_root: str | Path | None = None,
        config: CodeGraphConfig | None = None,
    ) -> dict[str, object]:
        if repo_root is None:
            return {
                "entries": len(self._entries),
                "resident_bytes": self._resident_bytes(),
                "active_generations": sum(1 for item in self._entries.values() if item.active is not None),
            }
        entry = self._peek_entry(repo_root, config)
        if entry is None:
            return {"present": False, "state": "absent"}
        entry.normalize_dirty_paths()
        updating = entry.update_task is not None and not entry.update_task.done()
        cfg = config
        if cfg is None and entry.service is not None:
            cfg = getattr(entry.service, "config", None)
        if cfg is not None:
            self._bind_config(entry, cfg)
        limit_error = getattr(entry, "limit_error", None)
        if limit_error is not None and cfg is not None and self._raised_cap_clears_limit(limit_error, cfg):
            entry.limit_error = None
            entry.limit_exceeded_digest = None
            limit_error = None
        dirty = sorted(entry.dirty_paths)
        live_dirty: list[str] = []
        token_stale = False
        if (
            entry.active is not None
            and entry.last_token is not None
            and cfg is not None
            and not updating
        ):
            try:
                live = compute_workspace_token(
                    entry.identity.canonical_root,
                    cfg,
                    extra_paths=tuple(entry.dirty_paths),
                )
                token_stale = live.digest != entry.last_token.digest
                live_dirty = list(live.dirty_paths)
            except Exception:  # noqa: BLE001 — status must still return
                token_stale = False
        if updating and entry.active is None:
            state = "building"
        elif updating:
            state = "refreshing"
        elif entry.dirty_paths or entry.dirty_unknown or token_stale:
            state = "stale"
        elif limit_error is not None and entry.active is None:
            state = "unavailable"
        elif entry.active is not None:
            state = "ready"
        else:
            state = "absent"
        payload = {
            "present": True,
            "state": state,
            "repo_id": entry.identity.repo_id,
            "generation_id": entry.active.generation_id if entry.active else None,
            "dirty_paths": dirty or (sorted(live_dirty) if token_stale else []),
            "dirty_unknown": entry.dirty_unknown,
            "reader_count": entry.active.reader_count if entry.active else 0,
            "estimated_bytes": entry.estimated_bytes(),
        }
        if limit_error is not None:
            payload["limit_exceeded"] = True
            payload["message"] = str(getattr(limit_error, "message", limit_error))
        return payload


    async def _run_update_respecting_resources(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        token: WorkspaceToken,
        cancel: threading.Event,
    ) -> GraphGeneration:
        """Build or refresh. Free this repo's old graph, then unused older repos."""
        if self._resource_blocks_build(cfg):
            self._release_obsolete_generation(entry, cfg)
            self._evict_unused_others(cfg, keep=entry)
        raise_if_resource_limits(cfg, rss_bytes=_current_rss_bytes())
        try:
            return await self._update_entry_body(entry, cfg, token, cancel)
        except CodeGraphLimitExceeded as exc:
            if exc.limit not in {"max_build_rss_mb", "max_cache_size_mb"}:
                raise
            self._release_obsolete_generation(entry, cfg)
            self._evict_unused_others(cfg, keep=entry)
            raise_if_resource_limits(cfg, rss_bytes=_current_rss_bytes())
            return await self._update_entry_body(entry, cfg, token, cancel)

    def _bind_config(self, entry: GraphEntry, cfg: CodeGraphConfig) -> None:
        """Keep the shared façade on the live yaml caps. One path, one entry."""
        entry.config_hash = cfg.config_hash()
        if entry.service is not None:
            entry.service.config = cfg

    def _raised_cap_clears_limit(self, exc: object, cfg: CodeGraphConfig) -> bool:
        """True when the user raised the cap that previously refused this tree."""
        limit = str(getattr(exc, "limit", "") or "")
        try:
            old_cap = int(getattr(exc, "cap", 0) or 0)
        except (TypeError, ValueError):
            return False
        if limit == "max_build_rss_mb":
            return int(cfg.max_build_rss_mb or 0) * 1024 * 1024 > old_cap
        if limit == "max_cache_size_mb":
            return cfg.disk_quota_bytes() > old_cap
        if limit == "max_files":
            return int(cfg.max_files) > old_cap
        if limit == "max_source_bytes":
            return int(cfg.max_source_bytes) > old_cap
        return False

    def _refuse_after_cleanup(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        digest: str,
    ) -> None:
        """Cleanup already ran. Clear this graph and tell the user to raise the cap."""
        if self._rss_blocks_build(cfg):
            limit = "max_build_rss_mb"
            observed = _current_rss_bytes()
            cap: int | str = int(cfg.max_build_rss_mb or 0) * 1024 * 1024
        else:
            limit = "max_cache_size_mb"
            observed = cache_dir_bytes(cfg.cache_dir)
            cap = cfg.disk_quota_bytes()
        try:
            raise_limit_exceeded(limit, observed, cap)
        except CodeGraphLimitExceeded as exc:
            self._abandon_entry(entry, cfg, exc, digest)
            raise

    def _abandon_entry(
        self,
        entry: GraphEntry,
        cfg: CodeGraphConfig,
        exc: CodeGraphLimitExceeded,
        digest: str,
    ) -> None:
        """Clear memory + disk for this workspace after a hard refuse."""
        entry.limit_error = exc
        entry.limit_exceeded_digest = digest
        entry.abandon()
        self._clear_service_index(entry)
        store = self._store(cfg)
        if store is not None:
            store.delete_repo(entry.identity.repo_id)
        logger.warning(
            "code_graph abandoned %s after %s",
            entry.identity.repo_id,
            getattr(exc, "limit", "limit"),
        )

    def _release_obsolete_generation(self, entry: GraphEntry, cfg: CodeGraphConfig) -> None:
        """Drop this workspace's previous graph. Other chats must wait for the new one."""
        del cfg
        generation = entry.active
        if generation is not None:
            if generation.reader_count > 0:
                entry.retired.append(generation)
            entry.active = None
            entry.gc_retired()
        self._clear_service_index(entry)
        # Keep the last checkpoint until publish or abandon. Restart still
        # needs it; the live process must not keep serving that generation.

    def _evict_unused_others(self, cfg: CodeGraphConfig, *, keep: GraphEntry) -> None:
        """Drop unused repos, oldest first. Skip graphs other windows still use."""
        now = time.time()
        victims = sorted(
            (
                (key, item)
                for key, item in self._entries.items()
                if item is not keep and self._can_evict(key, now, require_idle=False)
            ),
            key=lambda pair: (
                pair[1].active.created_at if pair[1].active is not None else 0.0,
                pair[1].last_access_at,
            ),
        )
        for key, _item in victims:
            if not self._resource_blocks_build(cfg):
                return
            persist = not self._disk_blocks_build(cfg)
            self._drop_entry(key, reason="resource", persist=persist)
        if self._disk_blocks_build(cfg):
            self._delete_unused_cache_repos(cfg, keep=keep)

    def _delete_unused_cache_repos(self, cfg: CodeGraphConfig, *, keep: GraphEntry) -> None:
        """Remove leftover checkpoints when disk is still over after memory eviction."""
        store = self._store(cfg)
        if store is None or not store.cache_dir.is_dir():
            return
        protected = {DiskIndexStore._safe_part(keep.identity.repo_id)}
        for key, item in self._entries.items():
            if self._windows_using(key, item):
                protected.add(DiskIndexStore._safe_part(item.identity.repo_id))
        leftovers: list[tuple[float, str]] = []
        for child in store.cache_dir.iterdir():
            if not child.is_dir() or child.name in protected:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0.0
            leftovers.append((mtime, child.name))
        leftovers.sort()
        for _mtime, repo_part in leftovers:
            if not self._disk_blocks_build(cfg):
                return
            store.delete_repo(repo_part)

    def _clear_service_index(self, entry: GraphEntry) -> None:
        service = entry.service
        if service is not None:
            service.clear_index()

    def _resource_blocks_build(self, cfg: CodeGraphConfig) -> bool:
        return self._rss_blocks_build(cfg) or self._disk_blocks_build(cfg)

    def _rss_blocks_build(self, cfg: CodeGraphConfig) -> bool:
        """True when this process is already too large to start another index build."""
        cap_mb = int(getattr(cfg, "max_build_rss_mb", 4096) or 0)
        if cap_mb <= 0:
            return False
        current = _current_rss_bytes()
        return current > 0 and current >= cap_mb * 1024 * 1024

    def _disk_blocks_build(self, cfg: CodeGraphConfig) -> bool:
        if not cfg.cache_dir:
            return False
        used = cache_dir_bytes(cfg.cache_dir)
        return used > 0 and used >= cfg.disk_quota_bytes()


def get_code_graph_manager(config: CodeGraphConfig | None = None) -> CodeGraphManager:
    """Process-global manager. Created on first use."""
    global _manager
    if _manager is None:
        with _manager_init_lock:
            if _manager is None:
                cfg = config or CodeGraphConfig()
                _manager = CodeGraphManager(
                    max_cached_repos=cfg.max_cached_repos,
                    memory_idle_ttl_seconds=cfg.memory_idle_ttl_seconds,
                    max_process_index_memory_mb=cfg.max_process_index_memory_mb,
                    max_concurrent_builds=cfg.max_concurrent_builds,
                )
    return _manager


def configure_code_graph_manager(config: CodeGraphConfig) -> CodeGraphManager:
    """Apply product resource knobs when the process manager is first created."""
    return get_code_graph_manager(config)


def reset_code_graph_manager() -> None:
    """Test helper: drop the process-global manager."""
    global _manager
    from openjiuwen.core.retrieval.code_graph.watch import stop_workspace_watch

    stop_workspace_watch()
    if _manager is not None:
        _manager.drop()
    _manager = None


def _rss_bytes() -> int:
    """Best-effort peak RSS. 0 if the platform cannot report it."""
    try:
        import resource
        import sys

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(usage)
        return int(usage) * 1024
    except Exception:  # noqa: BLE001
        return 0


def _current_rss_bytes() -> int:
    """Current resident set, not the high-water mark. 0 if unreadable."""
    from openjiuwen.core.retrieval.code_graph import budgets

    return budgets.process_rss_bytes()
