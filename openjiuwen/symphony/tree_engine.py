"""Runtime-owned taxonomy building and session-local discovery."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from pathlib import Path

from openjiuwen.symphony.config import RetrievalConfig
from openjiuwen.symphony.discovery import CapabilityCandidate, InstalledSkillsDirectoryToolkit, SkillFS, read_fingerprints
from openjiuwen.symphony.discovery.skillfs import SkillIndexSnapshot, capture_index_snapshot, project_index_snapshot
from openjiuwen.symphony.inventory import DirectoryInventory, expert_manifest_path
from openjiuwen.symphony.runtime_storage import RuntimeStorage
from openjiuwen.symphony.shared.fingerprint import FingerprintService

_UNPINNED = object()


class DiscoverySession(InstalledSkillsDirectoryToolkit):
    """A directory tool and prompt view sharing one live visibility projection."""

    def __init__(self, environment, *, records_provider, session_scope, max_notice_chars,
                 search_catalog=None, expert_candidates=None, expert_snapshot=None, pinned_expert_snapshot=None):
        super().__init__(environment, session_scope=session_scope, incremental_notice_max_chars=max_notice_chars,
                         search_catalog=search_catalog, expert_candidates=expert_candidates,
                         expert_snapshot=expert_snapshot)
        self._records_provider = records_provider
        self.pinned_expert_index_snapshot = pinned_expert_snapshot

    def records(self):
        return self._records_provider()


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
        self._sessions: list[DiscoverySession] = []

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

        self._inventory.validate_readers()
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

        current = _record_hashes(toolkit._current_records())
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
        committed = toolkit._read_committed_index_state()
        if self._storage.scoped:
            status["indexed_skill_count"] = sum(
                identifier.startswith("skill:") for identifier in committed.get("record_hashes", {})
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

    def create_session(self, *, session_scope="default", capability_directories=None,
                       visible_capability_ids=None, disabled_capability_ids=None, source_by_id=None,
                       config: RetrievalConfig | None = None, pin_index_revision=False, search_catalog=None,
                       expert_candidates: Callable[[], Awaitable[Sequence[CapabilityCandidate]]] | None = None,
                       pinned_expert_index_snapshot=_UNPINNED, **snapshot):
        self._check()
        self._inventory.validate_readers()
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
                        "pinned_index_snapshot" not in snapshot or pinned_expert_index_snapshot is _UNPINNED
                    ) else None
                )
                snapshot.setdefault("pinned_index_snapshot", project_index_snapshot(shared, "skill"))
                if pinned_expert_index_snapshot is _UNPINNED:
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

        async def current_experts():
            authorized = await expert_candidates() if expert_candidates is not None else None
            if not has_expert_sources:
                return authorized or ()
            if expert_candidates is not None and not authorized:
                return ()
            if authorized is None and search_catalog is not None:
                return ()
            return await asyncio.to_thread(self._expert_view, authorized)

        environment = SkillFS(
            records, settings=settings, visible_skill_names=visible_capability_ids,
            artifact_root=self.artifact_root, index_root=self.artifact_root,
            fingerprint_service=self._fingerprints.get("skill"),
            fingerprint_source_roots=tuple(str(source.root) for source in self._inventory.sources("skill")),
            index_snapshot_loader=load_skill_index,
            pin_index_revision=pin_index_revision, **snapshot,
        )
        session = DiscoverySession(
            environment, records_provider=records, session_scope=session_scope,
            max_notice_chars=settings.output.incremental_notice_max_chars,
            search_catalog=search_catalog,
            expert_candidates=current_experts if (
                has_expert_sources or expert_candidates is not None or search_catalog is None
            ) else None,
            expert_snapshot=load_expert_index, pinned_expert_snapshot=pinned_expert,
        )
        self._sessions.append(session)
        return session

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
            for session in self._sessions:
                await session.aclose()
            self._sessions.clear()

