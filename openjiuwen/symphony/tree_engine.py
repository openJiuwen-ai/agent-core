"""Runtime-owned taxonomy building and session-local discovery."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import WeakSet

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.symphony.config import RetrievalConfig
from openjiuwen.symphony.discovery import CapabilityCandidate, SkillDCICommandResult, SkillFS, read_fingerprints
from openjiuwen.symphony.discovery.directory_toolkit import _CapabilityDirectory
from openjiuwen.symphony.discovery.skillfs import (
    _UNSET_SNAPSHOT, SkillIndexSnapshot, VisibleSkillNames, capture_index_snapshot, project_index_snapshot,
)
from openjiuwen.symphony.inventory import DirectoryInventory, RuntimeStorage, expert_manifest_path
from openjiuwen.symphony.shared.fingerprint import FingerprintService


class TreeEngine:
    """Keep build workers, model calls and discovery snapshots inside Core."""

    def __init__(self, model, config: RetrievalConfig, storage: RuntimeStorage, inventory: DirectoryInventory,
                 fingerprint_services: dict[str, FingerprintService]):
        self.model = model
        self.config = config
        self._storage = storage
        self._inventory = inventory
        self._fingerprints = fingerprint_services
        self._fingerprint_task: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self._cancel = threading.Event()
        self._closed = False
        self._directory: _CapabilityDirectory | None = None
        # Old tools and in-flight searches can outlive a rebind. Drain those still in use on shutdown.
        self._directories: WeakSet[_CapabilityDirectory] = WeakSet()

    @property
    def artifact_root(self):
        return self._storage.root / "tree"

    def _check(self, *, write: bool = False):
        if self._closed:
            raise RuntimeError("Symphony runtime is closed")
        if not self.config.enabled:
            raise RuntimeError("Symphony retrieval is disabled")
        self._storage.check(write=write)

    def _records(
        self, capability_directories=None, disabled_capability_ids=None, source_by_id=None, *, allow_partial=False,
    ):
        def value(item, default):
            return item() if callable(item) else item if item is not None else default

        return self._inventory.records(
            value(capability_directories, None),
            disabled=value(disabled_capability_ids, ()),
            sources=value(source_by_id, {}),
            allow_partial=allow_partial,
        )

    def _build_records(self, capability_directories, disabled_capability_ids, source_by_id, *, allow_partial=False):
        from openjiuwen.symphony.agent.retrieval_toolkit import SkillRecord

        skills, errors = self._records(
            capability_directories, disabled_capability_ids, source_by_id, allow_partial=allow_partial,
        )
        indexed = [SkillRecord(
            name=item.name, description=item.description,
            worker_id=f"skill:{item.worker_id}" if self._storage.scoped else item.worker_id,
            skill_md_path=item.skill_file, content_hash=item.content_hash,
            metadata={"source": item.source, "version": item.version, "author": item.author},
        ) for item in skills]
        if self._inventory.sources("expert"):
            experts = self._inventory.experts(allow_partial=allow_partial)
            indexed.extend(SkillRecord(
                name=item.name, description=item.description, worker_id=f"expert:{item.capability_id}",
                content_hash=item.content_hash, metadata={"capability_type": "expert"},
            ) for item in experts.capabilities if item.available)
            errors += tuple(item for item in experts.diagnostics if item.severity == "error")
        return tuple(indexed), errors

    def _toolkit(self, *, records=(), model=None):
        from openjiuwen.symphony.agent.retrieval_toolkit import AgenticSkillRetrievalToolkit

        return AgenticSkillRetrievalToolkit(self.artifact_root, skills=records, config=self.config.index_build,
                                           model=self.model if model is None else model)

    @staticmethod
    def _changes(toolkit, committed):
        from openjiuwen.symphony.agent.retrieval_toolkit import _record_hashes

        current = _record_hashes(toolkit.current_records())
        previous = dict(committed.get("record_hashes") or {})
        return {
            "capability_count": len(current),
            "added_count": len(current.keys() - previous.keys()),
            "removed_count": len(previous.keys() - current.keys()),
            "changed_count": sum(current[key] != previous[key] for key in current.keys() & previous.keys()),
            "reused_count": sum(current[key] == previous[key] for key in current.keys() & previous.keys()),
        }

    def _status(self, toolkit, *, build_id=None):
        if not self.artifact_root.exists():
            return {
                "enabled": True, "status": "idle", "stage": "", "build_id": "", "version": "",
                "index_exists": False, "fresh": False, "indexed_count": 0,
                "counts": self._changes(toolkit, {}), "diagnostics": [],
            }
        status = toolkit.load_index_status(build_id=build_id, include_logs=False)
        committed = toolkit.read_committed_index_state()
        if self._storage.scoped:
            status["indexed_skill_count"] = sum(
                identifier.startswith("skill:") for identifier in (committed.get("record_hashes") or {})
            )
        return {
            **status, "enabled": True, "version": str(committed.get("build_id") or ""),
            "counts": self._changes(toolkit, committed),
            "diagnostics": ([{"severity": "error", "message": status["error"]}] if status.get("error") else []),
        }

    async def build(self, *, force=False, progress=None, prepare_artifact=None,
                    capability_directories=None, disabled_capability_ids=None, source_by_id=None):
        self._check(write=True)
        if self._task is not None and not self._task.done():
            raise RuntimeError("A Symphony tree build is already running")
        from openjiuwen.symphony.model_bridge import _bind_model_to_loop

        current = asyncio.current_task()
        self._task = current
        self._cancel.clear()
        loop = asyncio.get_running_loop()

        def dispatch(coroutine):
            from openjiuwen.symphony.agent.retrieval_toolkit import SkillIndexBuildCancelled

            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            try:
                while True:
                    if self._cancel.is_set():
                        raise SkillIndexBuildCancelled("Tree build cancelled during application callback")
                    try:
                        return future.result(timeout=0.05)
                    except FutureTimeout:
                        if future.done():
                            return future.result()
            finally:
                if not future.done():
                    future.cancel()

        def report(payload):
            if progress is None:
                return

            async def deliver():
                result = progress(payload)
                if inspect.isawaitable(result):
                    await result

            dispatch(deliver())

        def prepare(candidate_dir):
            if prepare_artifact is not None:
                async def deliver():
                    result = prepare_artifact(candidate_dir)
                    if inspect.isawaitable(result):
                        await result

                dispatch(deliver())
            latest, _ = self._build_records(capability_directories, disabled_capability_ids, source_by_id)
            if latest != records:
                raise RuntimeError("Capability inventory changed during the taxonomy build; previous version preserved")

        worker = None
        try:
            records, _ = await asyncio.to_thread(
                self._build_records, capability_directories, disabled_capability_ids, source_by_id,
            )
            if not records:
                raise ValueError("Cannot build a taxonomy from an empty capability inventory")
            if self._cancel.is_set():
                raise asyncio.CancelledError
            # The shared service always fingerprints the configured collection. Directory
            # overrides may include session-only Skills and belong to the taxonomy alone.
            for service in self._fingerprints.values():
                if self._cancel.is_set():
                    raise asyncio.CancelledError
                self._fingerprint_task = asyncio.create_task(service.build(force=force))
                try:
                    await self._fingerprint_task
                finally:
                    self._fingerprint_task = None
            toolkit = self._toolkit(records=records, model=_bind_model_to_loop(self.model, loop))
            before = self._status(toolkit)["counts"]
            worker = asyncio.create_task(asyncio.to_thread(
                toolkit.build_index, force=force, progress_callback=report,
                cancel_token=self._cancel, prepare_artifact=prepare,
            ))
            result = await asyncio.shield(worker)
            status = self._status(toolkit)
            operation = (result.get("data", {}).get("build") or {}).get("operation")
            if operation == "build" or not result.get("success"):
                before["reused_count"] = 0
            return {**result, "build_id": status["build_id"], "version": status["version"],
                    "counts": before, "diagnostics": status["diagnostics"]}
        except asyncio.CancelledError:
            self._cancel.set()
            if worker is not None:
                await asyncio.shield(worker)
            raise
        finally:
            if self._task is current:
                self._task = None

    def status(self, *, build_id=None, capability_directories=None, disabled_capability_ids=None, source_by_id=None):
        if not self.config.enabled:
            return {"enabled": False, "status": "disabled", "build_id": "", "version": "",
                    "index_exists": False, "counts": {}, "diagnostics": []}
        self._check()
        records, errors = self._build_records(
            capability_directories, disabled_capability_ids, source_by_id, allow_partial=True,
        )
        status = self._status(self._toolkit(records=records), build_id=build_id)
        if errors:
            status["fresh"] = False
            status["diagnostics"].extend(error.to_dict() for error in errors)
        return status

    def read(self):
        self._check()
        if not self.artifact_root.is_dir():
            raise FileNotFoundError("No published Symphony taxonomy is available")
        toolkit = self._toolkit()
        try:
            result = toolkit.load_index_tree(validate_fresh=False)
        except FileNotFoundError as exc:
            raise FileNotFoundError("No published Symphony taxonomy is available") from exc
        if self._storage.scoped:
            nodes = list(result["data"]["tree"])
            while nodes:
                node = nodes.pop()
                if node["type"] == "leaf":
                    kind, _, identifier = node["worker_id"].partition(":")
                    node.update(capability_type=kind, capability_id=identifier)
                nodes.extend(node["children"])
        return {**result, "build_id": result["version"], "diagnostics": []}

    async def cancel_build(self, *, build_id=None):
        self._check()
        if build_id and self._status(self._toolkit())["build_id"] != build_id:
            return self.status(build_id=build_id)
        self._cancel.set()
        if self._fingerprint_task is not None:
            self._fingerprint_task.cancel()
        if self._task is not None and self._task is not asyncio.current_task():
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                pass
        return self.status(build_id=build_id)

    def bind_search(
        self, *,
        session_scope: str | None = None,
        capability_directories: Sequence[str | Path] | Callable[[], Sequence[str | Path]] | None = None,
        visible_capability_ids: VisibleSkillNames = None,
        disabled_capability_ids: Iterable[str] | Callable[[], Iterable[str]] | None = None,
        source_by_id: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
        config: RetrievalConfig | None = None,
        pin_index_revision: bool = False,
        catalog_candidates: Callable[[str], Awaitable[Sequence[CapabilityCandidate]]] | None = None,
        pinned_index_snapshot: SkillIndexSnapshot | Mapping[str, Any] | None | object = _UNSET_SNAPSHOT,
        pinned_expert_index_snapshot: SkillIndexSnapshot | Mapping[str, Any] | None | object = _UNSET_SNAPSHOT,
    ) -> SkillFS:
        """Bind this Runtime's search and tools to a live host view and return its prompt directory."""
        self._check()
        settings = (config or self.config).model_copy(deep=True)

        def records():
            visible_records, _ = self._records(
                capability_directories, disabled_capability_ids, source_by_id, allow_partial=True,
            )
            return visible_records

        load_skill_index = None
        load_expert_index = None
        pinned_expert = None
        if self._storage.scoped:
            def load(kind: str) -> SkillIndexSnapshot | None:
                return project_index_snapshot(capture_index_snapshot(self.artifact_root), kind)

            def load_skill_index():
                return load("skill")

            if settings.use_existing_index:
                def load_expert_index():
                    return load("expert")

            if pin_index_revision:
                shared = (
                    capture_index_snapshot(self.artifact_root) if settings.use_existing_index and (
                        pinned_index_snapshot is _UNSET_SNAPSHOT or pinned_expert_index_snapshot is _UNSET_SNAPSHOT
                    ) else None
                )
                if pinned_index_snapshot is _UNSET_SNAPSHOT:
                    pinned_index_snapshot = project_index_snapshot(shared, "skill")
                if pinned_expert_index_snapshot is _UNSET_SNAPSHOT:
                    pinned_expert = project_index_snapshot(shared, "expert")
                elif isinstance(pinned_expert_index_snapshot, Mapping):
                    pinned_expert = SkillIndexSnapshot.from_dict(pinned_expert_index_snapshot)
                else:
                    pinned_expert = pinned_expert_index_snapshot
                if pinned_expert is not None and not isinstance(pinned_expert, SkillIndexSnapshot):
                    raise TypeError("pinned_expert_index_snapshot must be SkillIndexSnapshot, mapping, or None")

                def load_expert_index():
                    return pinned_expert

        has_expert_sources = bool(self._inventory.sources("expert"))

        async def current_candidates(kind: str):
            authorized = await catalog_candidates(kind) if catalog_candidates is not None else None
            if kind != "expert" or not has_expert_sources:
                return authorized or ()
            if catalog_candidates is not None and not authorized:
                return ()
            return await asyncio.to_thread(self._expert_view, authorized)

        environment = SkillFS(
            records, settings=settings, visible_skill_names=visible_capability_ids,
            artifact_root=self.artifact_root, index_root=self.artifact_root,
            fingerprint_service=self._fingerprints.get("skill"),
            fingerprint_source_roots=tuple(str(source.root) for source in self._inventory.sources("skill")),
            index_snapshot_loader=load_skill_index,
            pin_index_revision=pin_index_revision, pinned_index_snapshot=pinned_index_snapshot,
            pinned_expert_index_snapshot=pinned_expert,
        )
        directory = _CapabilityDirectory(
            environment, session_scope=uuid4().hex if session_scope is None else session_scope,
            incremental_notice_max_chars=settings.output.incremental_notice_max_chars,
            catalog_candidates=current_candidates,
            expert_snapshot=load_expert_index,
        )
        self.close_search()
        self._directory = directory
        self._directories.add(directory)
        return environment

    def _search_directory(self) -> _CapabilityDirectory:
        if self._closed:
            raise RuntimeError("Symphony runtime is closed")
        if self._directory is None:
            self.bind_search()
        assert self._directory is not None
        return self._directory

    async def search(
        self, query: str | list[str] | None = None, *, type: str | list[str] | tuple[str, ...] | None = None,
        operation: str | None = None, category: str | None = None, cursor: str | None = None, top_k: int | None = None,
    ) -> SkillDCICommandResult | dict[str, Any]:
        """Search or browse any combination of capability types. New requests default to all three."""
        return await self._search_directory().search(
            query=query, type=type, operation=operation, category=category, cursor=cursor, top_k=top_k,
        )

    def get_tools(self) -> list[Tool]:
        """Return the model-facing capability search tool bound to the same directory as search()."""
        return self._search_directory().get_tools()

    def close_search(self) -> None:
        """Immediately revoke tools and cursors issued for the current binding."""
        if self._directory is not None:
            self._directory.close()

    def _expert_view(self, authorized: Sequence[CapabilityCandidate] | None) -> tuple[CapabilityCandidate, ...]:
        allowed = {}
        for candidate in authorized or ():
            if (candidate.manifest_path is None or not candidate.descriptor.available
                    or candidate.descriptor.capability_type != "expert"):
                continue
            try:
                path = candidate.manifest_path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            allowed[path] = candidate
        descriptors = self._inventory.experts(
            allow_partial=True, manifest_paths=set(allowed) if authorized is not None else None,
        ).capabilities
        fingerprints = read_fingerprints(self._fingerprints.get("expert")) if descriptors else {}
        candidates = []
        for descriptor in descriptors:
            path = expert_manifest_path(descriptor)
            candidate = allowed.get(path, CapabilityCandidate(descriptor))
            candidates.append(replace(
                candidate, descriptor=descriptor, manifest_path=path, aliases=(),
                fingerprint=fingerprints.get(("expert", descriptor.capability_id)),
            ))
        return tuple(candidates)

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        self.close_search()
        self._cancel.set()
        if self._fingerprint_task is not None:
            self._fingerprint_task.cancel()
        try:
            if self._task is not None and self._task is not asyncio.current_task():
                try:
                    await asyncio.shield(self._task)
                except asyncio.CancelledError:
                    pass
        finally:
            for directory in tuple(self._directories):
                await directory.aclose()
            self._directory = None

