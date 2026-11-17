"""Runtime-owned taxonomy building and session-local discovery."""

from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import TimeoutError as FutureTimeout

from openjiuwen.symphony.config import RetrievalConfig
from openjiuwen.symphony.discovery import InstalledSkillsDirectoryToolkit, SkillFS
from openjiuwen.symphony.inventory import DirectoryInventory
from openjiuwen.symphony.runtime_storage import RuntimeStorage


class DiscoverySession(InstalledSkillsDirectoryToolkit):
    """A directory tool and prompt view sharing one live visibility projection."""

    def __init__(self, environment, *, records_provider, session_scope, max_notice_chars):
        super().__init__(environment, session_scope=session_scope, incremental_notice_max_chars=max_notice_chars)
        self._records_provider = records_provider

    def records(self):
        return self._records_provider()


class TreeEngine:
    """Keep build workers, model calls and discovery snapshots inside Core."""

    def __init__(self, model, config: RetrievalConfig, storage: RuntimeStorage, inventory: DirectoryInventory):
        self.model = model
        self.config = config
        self._storage = storage
        self._inventory = inventory
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

    def _records(self, capability_directories=None, disabled_capability_ids=None, source_by_id=None):
        def value(item, default):
            return item() if callable(item) else item if item is not None else default

        return self._inventory.records(
            value(capability_directories, None),
            disabled=value(disabled_capability_ids, ()),
            sources=value(source_by_id, {}),
        )

    def _toolkit(self, *, records=(), model=None):
        from openjiuwen.symphony.agent.retrieval_toolkit import AgenticSkillRetrievalToolkit, SkillRecord

        indexed = tuple(SkillRecord(
            name=item.name, description=item.description, worker_id=item.worker_id,
            skill_md_path=item.skill_file, content_hash=item.content_hash,
            metadata={"source": item.source, "version": item.version, "author": item.author},
        ) for item in records)
        return AgenticSkillRetrievalToolkit(self.artifact_root, skills=indexed, config=self.config.index_build,
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
            latest = self._records(capability_directories, disabled_capability_ids, source_by_id)
            if latest != records:
                raise RuntimeError("Capability inventory changed during the taxonomy build; previous version preserved")

        worker = None
        try:
            records = await asyncio.to_thread(self._records, capability_directories, disabled_capability_ids, source_by_id)
            if not records:
                raise ValueError("Cannot build a taxonomy from an empty capability inventory")
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
        records = self._records(capability_directories, disabled_capability_ids, source_by_id)
        return self._status(self._toolkit(records=records), build_id=build_id)

    def read(self):
        self._check()
        if not (self.artifact_root / "index").is_dir():
            raise FileNotFoundError("No published Symphony taxonomy is available")
        toolkit = self._toolkit()
        committed = toolkit._read_committed_index_state()
        result = toolkit.load_index_tree(validate_fresh=False)
        return {**result, "version": str(committed.get("build_id") or ""),
                "build_id": str(committed.get("build_id") or ""), "diagnostics": []}

    async def cancel_build(self, *, build_id=None):
        self._check()
        if build_id and self._status(self._toolkit())["build_id"] != build_id:
            return self.status(build_id=build_id)
        self._cancel.set()
        if self._task is not None and self._task is not asyncio.current_task():
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                pass
        return self.status(build_id=build_id)

    def create_session(self, *, session_scope="default", capability_directories=None,
                       visible_capability_ids=None, disabled_capability_ids=None, source_by_id=None,
                       config: RetrievalConfig | None = None, pin_index_revision=False, **snapshot):
        self._check()
        settings = (config or self.config).model_copy(deep=True)
        records = lambda: self._records(capability_directories, disabled_capability_ids, source_by_id)
        environment = SkillFS(
            records, settings=settings, visible_skill_names=visible_capability_ids,
            artifact_root=self.artifact_root, index_root=self.artifact_root,
            pin_index_revision=pin_index_revision, **snapshot,
        )
        session = DiscoverySession(environment, records_provider=records, session_scope=session_scope,
                                   max_notice_chars=settings.output.incremental_notice_max_chars)
        self._sessions.append(session)
        return session

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
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

