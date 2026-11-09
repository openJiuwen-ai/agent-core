from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Sequence

import portalocker

from openjiuwen.symphony.retrieval.build.io import load_tree_preset
from openjiuwen.symphony.retrieval.build.scanners.common import (
    find_skill_file,
    first_paragraph,
    read_skill_file,
    sha256_file,
)
from openjiuwen.symphony.config import RetrievalBuildConfig
from openjiuwen.symphony.model_bridge import _BuildInterrupted, _bind_model_to_loop, _model_identity, _model_scope, _validate_model_binding
from openjiuwen.symphony.retrieval.build.tree.root_categories import resolve_tree_root_categories
from openjiuwen.symphony.retrieval.build.workflows.artifacts import resolve_build_config
from openjiuwen.symphony.retrieval.build.workflows.index_builder import IndexBuilder
from openjiuwen.symphony.retrieval.search.artifacts.loading import CatalogRecord, load_retriever_index
from openjiuwen.symphony.shared.fingerprint._io import atomic_write_json

TREE_INDEX_FILENAME = "tree_index.yaml"
CATALOG_FILENAME = "catalog.jsonl"
MANIFEST_FILENAME = "manifest.json"
STATE_FILENAME = "state.json"
COMMIT_FILENAME = "commit.json"
_INDEX_PUBLISH_LOCK_FILENAME = ".skill-index.publish.lock"
_INDEX_IDENTITY_FIELDS = (
    "fingerprint", "build_policy_fingerprint", "build_settings_fingerprint", "indexed_count",
    "record_hashes", "source_file_hashes",
)

_TASKS: dict[str, "_BuildTask"] = {}
_TASKS_LOCK = threading.RLock()


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str = ""
    worker_id: str = ""
    skill_md_path: str = ""
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    content_hash: str = ""

    @property
    def resolved_worker_id(self) -> str:
        return str(self.worker_id or self.name).strip()


@dataclass
class _BuildTask:
    thread: threading.Thread
    cancel_event: threading.Event
    build_id: str


@dataclass(frozen=True)
class _IndexBuildPlan:
    operation: str
    records: tuple[SkillRecord, ...]
    response_worker_ids: tuple[str, ...] = ()


class SkillIndexBuildCancelled(_BuildInterrupted):
    """Raised when a cooperative index build cancellation is requested."""


class SkillIndexBuildTimeout(_BuildInterrupted):
    """Raised when an index build exceeds the configured total timeout."""


class AgenticSkillRetrievalToolkit:
    """Build, update, and inspect skill indexes consumed by Discovery."""

    def __init__(
        self,
        index_root: str | Path,
        *,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        skills_dir: str | Path | None = None,
        config: RetrievalBuildConfig,
        model: Any = None,
    ) -> None:
        self.index_root = Path(index_root).expanduser().resolve()
        self.index_dir = self.index_root / "index"
        self._config = config.model_copy(deep=True)
        self._model = model
        self._skills = _coerce_skill_records(skills)
        self._skills_dir = Path(skills_dir).expanduser().resolve() if skills_dir else None

    def build_index(
        self,
        *,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        skills_dir: str | Path | None = None,
        force: bool = False,
        config: RetrievalBuildConfig | None = None,
        model: Any = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        prepare_artifact: Callable[[Path], None] | None = None,
        cancel_token: Callable[[], bool] | threading.Event | None = None,
        _build_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_model = model if model is not None else self._model
        _validate_model_binding(resolved_model)
        records = [
            _snapshot_record_source(record) for record in self._resolve_records(skills=skills, skills_dir=skills_dir)
        ]
        if skills_dir is not None:
            self._skills_dir = Path(skills_dir).expanduser()
        self._skills = list(records)
        self._config = (config or self._config).model_copy(deep=True)
        build_cfg = resolve_build_config(config=self._config)
        self._model = resolved_model
        started = time.monotonic()
        build_id = str(_build_id or _new_build_id())
        record_hashes = _record_hashes(records)
        source_file_hashes = _source_file_hashes(records)
        policy_fingerprint = _build_policy_fingerprint(build_cfg, model=resolved_model)
        settings_fingerprint = _build_settings_fingerprint(build_cfg)
        fingerprint = _index_fingerprint(
            records,
            build_cfg,
            model=resolved_model,
            policy_fingerprint=policy_fingerprint,
            record_hashes=record_hashes,
            source_file_hashes=source_file_hashes,
        )
        previous_root_state = self._read_state()
        previous_index_state = self._read_committed_index_state(
            fallback=previous_root_state,
        )
        previous_index_available = _is_complete_index(self.index_dir)
        self.index_root.mkdir(parents=True, exist_ok=True)

        if not records:
            if not build_cfg.task.preserve_previous_index_on_failure:
                _cleanup_index(self.index_dir)
            state = _build_state(
                status="failed",
                stage="scan",
                message="No enabled skills were provided.",
                error="No enabled skills were provided.",
                progress=1.0,
                build_id=build_id,
                force=force,
                indexed_count=0,
                fingerprint=fingerprint,
                finished=True,
            )
            _set_failed_index_state(
                state,
                previous_state=previous_index_state,
                previous_index_available=previous_index_available,
                previous_index_preserved=build_cfg.task.preserve_previous_index_on_failure,
                attempted_fingerprint=fingerprint,
            )
            self._write_state(state, expected_build_id=build_id, claim=True)
            return _result(False, "Skill index build failed: no enabled skills were provided.", data=state)

        if not force and self._is_fresh(fingerprint):
            capability_category_paths: list[dict[str, Any]] = []
            state = _build_state(
                status="success",
                stage="reuse",
                message="Existing skill index is fresh; reused without rebuilding.",
                progress=1.0,
                build_id=build_id,
                force=force,
                indexed_count=len(records),
                fingerprint=fingerprint,
                record_hashes=record_hashes,
                source_file_hashes=source_file_hashes,
                finished=True,
                elapsed_seconds=time.monotonic() - started,
            )
            state["build_policy_fingerprint"] = policy_fingerprint
            state["build_settings_fingerprint"] = settings_fingerprint
            state["build"]["operation"] = "reuse"
            _set_capability_category_paths(state, capability_category_paths)
            self._write_state(state, expected_build_id=build_id, claim=True)
            return _result(
                True,
                f"Skill index is fresh. {len(records)} skills indexed.",
                data={**state, "capability_category_paths": capability_category_paths},
            )

        cancel_check = _cancel_check(cancel_token)
        build_check = _build_check(
            cancel_check=cancel_check,
            started=started,
            total_timeout_seconds=build_cfg.task.total_timeout_seconds,
        )
        self._set_running_state(
            build_id=build_id,
            stage="prepare",
            message="Preparing skill index build.",
            progress=0.05,
            force=force,
            indexed_count=len(records),
            fingerprint=fingerprint,
        )
        _emit(progress_callback, self.load_index_status())
        try:
            build_check("prepare")
        except SkillIndexBuildCancelled:
            return self._cancelled_result(build_id=build_id, started=started, fingerprint=fingerprint)
        except SkillIndexBuildTimeout as exc:
            return self._failed_build_result(
                error=_normalize_error(exc),
                build_id=build_id,
                started=started,
                fingerprint=fingerprint,
                force=force,
                preserve_previous_index=build_cfg.task.preserve_previous_index_on_failure,
                previous_state=previous_index_state,
                previous_index_available=previous_index_available,
            )

        if not build_cfg.task.allow_fallback_tree and resolved_model is None:
            error = "Skill index build requires an injected model when fallback is disabled."
            if not build_cfg.task.preserve_previous_index_on_failure:
                _cleanup_index(self.index_dir)
            state = _build_state(
                status="failed",
                stage="model",
                message="Build LLM configuration is missing.",
                error=error,
                progress=1.0,
                build_id=build_id,
                force=force,
                indexed_count=0,
                fingerprint=fingerprint,
                finished=True,
                elapsed_seconds=time.monotonic() - started,
            )
            _set_failed_index_state(
                state,
                previous_state=previous_index_state,
                previous_index_available=previous_index_available,
                previous_index_preserved=build_cfg.task.preserve_previous_index_on_failure,
                attempted_fingerprint=fingerprint,
            )
            self._write_state(state, expected_build_id=build_id)
            return _result(
                False,
                f"Skill index build failed: {error}",
                data={
                    **state,
                    "index_updated": False,
                    "previous_index_available": previous_index_available,
                    "previous_index_preserved": (
                        previous_index_available and build_cfg.task.preserve_previous_index_on_failure
                    ),
                },
                error={"code": "model_missing", "message": error},
            )

        with TemporaryDirectory(prefix="symphony-skill-index-") as tmp, _model_scope(
            resolved_model, check=build_check,
        ) as build_model:
            tmp_root = Path(tmp)
            item_jsonl = tmp_root / "skills.jsonl"
            base_index_dir = tmp_root / "base-index"
            base_state = {} if force else self._snapshot_index_base(base_index_dir)
            plan = self._select_build_plan(
                records=records,
                force=force,
                base_index_dir=base_index_dir,
                committed_state=base_state,
                policy_fingerprint=policy_fingerprint,
            )
            _write_records_jsonl(plan.records, item_jsonl)
            output_dir = tmp_root / "index"
            operation = plan.operation
            published = False
            committed_state: dict[str, Any] = {}
            try:
                self._set_running_state(
                    build_id=build_id,
                    stage="build",
                    message="Building skill tree index.",
                    progress=0.2,
                    force=force,
                    indexed_count=len(records),
                    fingerprint=fingerprint,
                )
                _emit(progress_callback, self.load_index_status())
                try:
                    build_check("build")
                except SkillIndexBuildCancelled:
                    return self._cancelled_result(build_id=build_id, started=started, fingerprint=fingerprint)
                config = build_cfg.model_copy(deep=True)
                if previous_index_available and build_cfg.task.preserve_previous_index_on_failure:
                    # A successful flat fallback must not replace a usable taxonomy after an LLM failure.
                    config = config.model_copy(update={"task": config.task.model_copy(update={"allow_fallback_tree": False})})
                if plan.operation == "build":
                    _run_index_builder(
                        operation=plan.operation,
                        item_jsonl_path=item_jsonl,
                        output_dir=output_dir,
                        base_index_dir=base_index_dir,
                        config=config,
                        model=build_model,
                        check=build_check,
                    )
                else:
                    try:
                        _run_index_builder(
                            operation=plan.operation,
                            item_jsonl_path=item_jsonl,
                            output_dir=output_dir,
                            base_index_dir=base_index_dir,
                            config=config,
                            model=build_model,
                            check=build_check,
                        )
                    except _BuildInterrupted:
                        raise
                    except Exception:
                        operation = "build"
                        self._set_running_state(
                            build_id=build_id,
                            stage="build",
                            message="Incremental skill index build failed; rebuilding the full index.",
                            progress=0.35,
                            force=force,
                            indexed_count=len(records),
                            fingerprint=fingerprint,
                        )
                        shutil.rmtree(output_dir, ignore_errors=True)
                        _write_records_jsonl(records, item_jsonl)
                        _run_index_builder(
                            operation="build",
                            item_jsonl_path=item_jsonl,
                            output_dir=output_dir,
                            base_index_dir=base_index_dir,
                            config=config,
                            model=build_model,
                            check=build_check,
                        )
                if prepare_artifact is not None:
                    build_check("prepare_artifact")
                    prepare_artifact(output_dir)
                try:
                    build_check("publish")
                except SkillIndexBuildCancelled:
                    return self._cancelled_result(build_id=build_id, started=started, fingerprint=fingerprint)
                if not _is_complete_index(output_dir):
                    raise RuntimeError("index artifacts are incomplete")
                capability_category_paths = _load_capability_category_paths(
                    output_dir,
                    worker_ids=plan.response_worker_ids,
                )
                _validate_index_worker_ids(
                    output_dir,
                    expected_worker_ids={record.resolved_worker_id for record in records},
                )
                committed_state = _committed_index_state(
                    build_id=build_id,
                    fingerprint=fingerprint,
                    policy_fingerprint=policy_fingerprint,
                    settings_fingerprint=settings_fingerprint,
                    operation=operation,
                    record_hashes=record_hashes,
                    source_file_hashes=source_file_hashes,
                )
                _write_index_commit(output_dir, committed_state)
                self._set_running_state(
                    build_id=build_id,
                    stage="publish",
                    message="Publishing skill index.",
                    progress=0.9,
                    force=force,
                    indexed_count=len(records),
                    fingerprint=fingerprint,
                )
                _publish_index(candidate_dir=output_dir, index_dir=self.index_dir, expected_build_id=build_id,
                               check=build_check)
                published = True
                elapsed = time.monotonic() - started
                state = _build_state(
                    status="success",
                    stage="success",
                    message="Skill index build completed.",
                    progress=1.0,
                    build_id=build_id,
                    force=force,
                    indexed_count=len(records),
                    fingerprint=fingerprint,
                    record_hashes=record_hashes,
                    source_file_hashes=source_file_hashes,
                    finished=True,
                    elapsed_seconds=elapsed,
                )
                state["build_policy_fingerprint"] = policy_fingerprint
                state["build_settings_fingerprint"] = settings_fingerprint
                state["build"]["operation"] = operation
                state["build"]["planned_operation"] = plan.operation
                _set_capability_category_paths(state, capability_category_paths)
                self._write_state(state, expected_build_id=build_id)
                return _result(
                    True,
                    f"Skill index build completed. {len(records)} skills indexed.",
                    data={
                        **state,
                        "index_dir": str(self.index_dir),
                        "elapsed_seconds": elapsed,
                        "capability_category_paths": capability_category_paths,
                    },
                )
            except SkillIndexBuildCancelled:
                return self._cancelled_result(build_id=build_id, started=started, fingerprint=fingerprint)
            except Exception as exc:
                error = _normalize_error(exc)
                if published:
                    return _result(
                        True,
                        "Skill index build completed; status metadata could not be updated.",
                        data={
                            **committed_state,
                            "index_dir": str(self.index_dir),
                            "index_updated": True,
                            "status_warning": error,
                        },
                    )
                if not build_cfg.task.preserve_previous_index_on_failure:
                    _cleanup_index(self.index_dir)
                state = _build_state(
                    status="failed",
                    stage="failed",
                    message="Skill index build failed.",
                    error=error,
                    progress=1.0,
                    build_id=build_id,
                    force=force,
                    indexed_count=0,
                    fingerprint=fingerprint,
                    finished=True,
                    elapsed_seconds=time.monotonic() - started,
                )
                _set_failed_index_state(
                    state,
                    previous_state=previous_index_state,
                    previous_index_available=previous_index_available,
                    previous_index_preserved=build_cfg.task.preserve_previous_index_on_failure,
                    attempted_fingerprint=fingerprint,
                )
                self._write_state(state, expected_build_id=build_id)
                return _result(
                    False,
                    f"Skill index build failed: {error}",
                    data={
                        **state,
                        "index_updated": False,
                        "previous_index_available": previous_index_available,
                        "previous_index_preserved": (
                            previous_index_available and build_cfg.task.preserve_previous_index_on_failure
                        ),
                    },
                    error={"code": "build_failed", "message": error},
                )

    def build_index_async(
        self,
        *,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        skills_dir: str | Path | None = None,
        force: bool = False,
        config: RetrievalBuildConfig | None = None,
        model: Any = None,
        replace_running: bool = False,
        prepare_artifact: Callable[[Path], None] | None = None,
    ) -> dict[str, Any]:
        resolved_model = model if model is not None else self._model
        try:
            resolved_model = _bind_model_to_loop(resolved_model, asyncio.get_running_loop())
        except RuntimeError:
            pass
        _validate_model_binding(resolved_model)
        key = str(self.index_root)
        with _TASKS_LOCK:
            existing = _TASKS.get(key)
            cancel_event = threading.Event()
            build_id = _new_build_id()
            active_build_id = self._set_queued_state(
                build_id=build_id,
                force=force,
                replace_running=replace_running,
            )
            if active_build_id is not None:
                return _result(
                    True,
                    "Skill index build is already running.",
                    data={"build_id": active_build_id, "state": "running"},
                )
            if existing and existing.thread.is_alive():
                existing.cancel_event.set()
            thread = threading.Thread(
                target=self._run_async_build,
                kwargs={
                    "build_id": build_id,
                    "cancel_event": cancel_event,
                    "skills": skills,
                    "skills_dir": skills_dir,
                    "force": force,
                    "config": config,
                    "model": resolved_model,
                    "prepare_artifact": prepare_artifact,
                    "_build_id": build_id,
                },
                daemon=True,
                name=f"symphony-skill-index-{build_id}",
            )
            _TASKS[key] = _BuildTask(thread=thread, cancel_event=cancel_event, build_id=build_id)
            try:
                thread.start()
            except Exception as exc:
                _TASKS.pop(key, None)
                self._mark_start_failure(build_id=build_id, error=_normalize_error(exc))
                raise
        return _result(True, "Skill index build started.", data={"build_id": build_id, "state": "running"})

    def check_build_status(
        self,
        *,
        build_id: str | None = None,
        include_logs: bool = True,
        include_inventory: bool = False,
        refresh_inventory: bool = False,
    ) -> dict[str, Any]:
        status = self.load_index_status(
            build_id=build_id,
            include_logs=include_logs,
            include_inventory=include_inventory,
            refresh_inventory=refresh_inventory,
        )
        message = _status_message(str(status.get("status") or "idle"))
        return _result(True, message, data=status)

    def cancel_build(
        self,
        *,
        build_id: str | None = None,
        wait: bool = False,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        key = str(self.index_root)
        cancelled = False
        active_build_id = ""
        with _TASKS_LOCK:
            task = _TASKS.get(key)
            if task is not None and task.thread.is_alive():
                active_build_id = task.build_id
                if build_id and build_id != active_build_id:
                    return _build_id_mismatch_result(
                        requested_build_id=build_id,
                        active_build_id=active_build_id,
                    )
            if _is_running_build_task(task, build_id):
                task.cancel_event.set()
                cancelled = True
        state = self._read_state()
        build = dict(state.get("build") or {})
        persisted_build_id = str(build.get("build_id") or "")
        if not cancelled and build_id:
            if build.get("status") == "running" and build_id != persisted_build_id:
                return _build_id_mismatch_result(
                    requested_build_id=build_id,
                    active_build_id=persisted_build_id,
                )
        if cancelled or build.get("status") == "running":
            cancelled = True
            build["status"] = "cancelled"
            build["stage"] = "cancelled"
            build["message"] = "Skill index build cancellation requested."
            build["progress"] = 1.0
            build["finished_at"] = _now_iso()
            build["capability_category_paths"] = []
            state["build"] = build
            self._write_state(state, expected_build_id=persisted_build_id)
        if wait and cancelled:
            deadline = time.monotonic() + max(0.0, float(timeout_seconds))
            while time.monotonic() < deadline:
                with _TASKS_LOCK:
                    task = _TASKS.get(key)
                    if task is None or not task.thread.is_alive():
                        break
                time.sleep(0.05)
        return _result(
            True,
            "Skill index build cancellation requested." if cancelled else "No running skill index build.",
            data={"state": "cancelled" if cancelled else "idle", "build_id": build_id or build.get("build_id", "")},
        )

    def load_index_status(
        self,
        *,
        build_id: str | None = None,
        include_logs: bool = True,
        include_inventory: bool = False,
        refresh_inventory: bool = False,
    ) -> dict[str, Any]:
        state = self._read_state()
        build = dict(state.get("build") or {})
        committed = self._read_committed_index_state(fallback=state)
        index_exists = _is_complete_index(self.index_dir)
        if not index_exists:
            committed = {}
        committed_build_id = str(committed.get("build_id") or "")
        if index_exists and build.get("status") == "running":
            if committed_build_id and committed_build_id == str(build.get("build_id") or ""):
                build.update(
                    {
                        "status": "success",
                        "stage": "success",
                        "message": "Skill index build completed.",
                        "error": "",
                        "progress": 1.0,
                        "finished_at": str(committed.get("committed_at") or _now_iso()),
                        "operation": str(committed.get("operation") or ""),
                    }
                )
                state["build"] = build
                for key in _INDEX_IDENTITY_FIELDS:
                    if key in committed:
                        state[key] = committed[key]
                self._write_state(state, expected_build_id=committed_build_id)
        if not index_exists:
            state["indexed_count"] = 0
            if build.get("status") == "success":
                build.update(
                    {
                        "status": "idle",
                        "stage": "missing",
                        "message": "No usable skill index is available.",
                        "error": "",
                        "progress": 0.0,
                        "updated_at": _now_iso(),
                    }
                )
                state["build"] = build
                state["fingerprint"] = ""
                self._write_state(state, expected_build_id=str(build.get("build_id") or ""))
        capability_category_paths: list[dict[str, Any]] = []
        if build.get("status") == "success":
            capability_category_paths = list(build.get("capability_category_paths") or [])
        current_records = self._current_records(refresh=refresh_inventory)
        model_identity_checked = self._model is not None
        current_policy = (
            _build_policy_fingerprint(self._config, model=self._model)
            if model_identity_checked else str(committed.get("build_policy_fingerprint") or "")
        )
        current_fingerprint = _index_fingerprint(current_records, self._config, policy_fingerprint=current_policy)
        policy_matches = _matches_build_policy(
            committed, self._config, model=self._model, check_model=model_identity_checked,
        )
        status = {
            "status": str(build.get("status") or "idle"),
            "stage": str(build.get("stage") or ""),
            "operation": str(build.get("operation") or committed.get("operation") or ""),
            "progress": _coerce_progress(build.get("progress")),
            "message": str(build.get("message") or ""),
            "error": str(build.get("error") or ""),
            "build_id": str(build.get("build_id") or ""),
            "force": bool(build.get("force", False)),
            "started_at": str(build.get("started_at") or ""),
            "finished_at": str(build.get("finished_at") or ""),
            "updated_at": str(build.get("updated_at") or state.get("updated_at") or ""),
            "elapsed_seconds": float(build.get("elapsed_seconds") or 0.0),
            "index_dir": str(self.index_dir),
            "index_exists": index_exists,
            "fresh": index_exists and policy_matches and str(committed.get("fingerprint") or "") == current_fingerprint,
            "model_identity_checked": model_identity_checked,
            "indexed_count": int(committed.get("indexed_count") or 0) if index_exists else 0,
            "fingerprint": str(committed.get("fingerprint") or ""),
            "capability_category_paths": capability_category_paths,
        }
        if include_logs:
            status["logs"] = list(build.get("logs") or [])
        if include_inventory:
            status["inventory"] = {
                "count": len(current_records),
                "fingerprint": _records_fingerprint(current_records),
            }
        if build_id and status["build_id"] and build_id != status["build_id"]:
            status["message"] = f"Latest build id is {status['build_id']}; requested {build_id}."
        return status

    def load_index_tree(
        self,
        *,
        language: str = "zh",
        max_nodes: int = 400,
        validate_fresh: bool = True,
        refresh_inventory: bool = False,
    ) -> dict[str, Any]:
        error = self._index_readiness_error(refresh_inventory=refresh_inventory) if validate_fresh else None
        if error:
            text = _index_unavailable_text(error, language=language)
            return _result(False, text, data={"index_exists": _is_complete_index(self.index_dir), "tree": []})
        with portalocker.Lock(
            str(self.index_root / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            _recover_index_directory_unlocked(self.index_dir)
            payload = load_tree_preset(self.index_dir / TREE_INDEX_FILENAME)
        raw_nodes = payload.get("nodes")
        nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
        outline = _render_tree_outline(nodes, max_nodes=max_nodes)
        return _result(
            True,
            outline or "Skill index tree is empty.",
            data={"index_exists": True, "tree": _tree_payload(nodes)},
        )


    def update_skills(
        self,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        *,
        skills_dir: str | Path | None = None,
    ) -> None:
        if skills is not None:
            self._skills = _coerce_skill_records(skills)
        if skills_dir is not None:
            self._skills_dir = Path(skills_dir).expanduser().resolve()


    def _run_async_build(self, **kwargs: Any) -> None:
        build_id = str(kwargs.pop("build_id"))
        cancel_event = kwargs.pop("cancel_event")
        try:
            self.build_index(cancel_token=cancel_event, **kwargs)
        except Exception as exc:
            self._mark_async_failure(build_id=build_id, error=_normalize_error(exc))
        finally:
            with _TASKS_LOCK:
                task = _TASKS.get(str(self.index_root))
                if task and task.build_id == build_id:
                    _TASKS.pop(str(self.index_root), None)

    def _resolve_records(
        self,
        *,
        skills: Sequence[SkillRecord | dict[str, Any]] | None,
        skills_dir: str | Path | None,
    ) -> list[SkillRecord]:
        if skills is not None:
            records = _coerce_skill_records(skills)
        elif skills_dir is not None:
            records = scan_skill_records(skills_dir)
        elif self._skills_dir is not None:
            records = scan_skill_records(self._skills_dir)
        elif self._skills:
            records = list(self._skills)
        else:
            records = []
        return [record for record in records if record.enabled and record.resolved_worker_id]

    def _current_records(self, *, refresh: bool = False) -> list[SkillRecord]:
        if refresh and self._skills_dir is not None:
            self._skills = scan_skill_records(self._skills_dir)
        return [record for record in self._skills if record.enabled and record.resolved_worker_id]

    def _snapshot_index_base(self, destination_dir: Path) -> dict[str, Any]:
        """Copy one committed index generation for an incremental build."""

        self.index_root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(self.index_root / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            _recover_index_directory_unlocked(self.index_dir)
            if not _is_complete_index(self.index_dir):
                return {}
            shutil.copytree(self.index_dir, destination_dir)
            commit_path = destination_dir / COMMIT_FILENAME
            if commit_path.is_file():
                return _read_json_object(commit_path)
            return self._read_state()

    def _select_build_plan(
        self,
        *,
        records: Sequence[SkillRecord],
        force: bool,
        base_index_dir: Path | None = None,
        committed_state: dict[str, Any] | None = None,
        policy_fingerprint: str | None = None,
    ) -> _IndexBuildPlan:
        resolved_base_dir = base_index_dir or self.index_dir
        if force or not _is_complete_index(resolved_base_dir):
            return _IndexBuildPlan("build", tuple(records))
        state = (
            dict(committed_state)
            if committed_state is not None
            else self._read_committed_index_state(fallback=self._read_state())
        )
        expected_policy = policy_fingerprint or _build_policy_fingerprint(self._config, model=self._model)
        if (str(state.get("build_policy_fingerprint") or "") != expected_policy
                or str(state.get("build_settings_fingerprint") or "") != _build_settings_fingerprint(self._config)):
            return _IndexBuildPlan("build", tuple(records))
        previous_hashes = dict(state.get("record_hashes") or {})
        current_hashes = _record_hashes(records)
        if not previous_hashes:
            return _IndexBuildPlan("build", tuple(records))
        previous = set(previous_hashes)
        current = set(current_hashes)
        added = current - previous
        removed = previous - current
        changed = {key for key in current & previous if current_hashes.get(key) != previous_hashes.get(key)}
        changed_or_added = added | changed
        if changed or (added and removed):
            return _IndexBuildPlan(
                "build",
                tuple(records),
                response_worker_ids=tuple(sorted(changed_or_added)),
            )
        if added and not removed:
            added_records = tuple(record for record in records if record.resolved_worker_id in added)
            return _IndexBuildPlan(
                "add",
                added_records,
                response_worker_ids=tuple(sorted(added)),
            )
        if removed and not added:
            removed_records = tuple(SkillRecord(name=worker_id, worker_id=worker_id) for worker_id in sorted(removed))
            return _IndexBuildPlan("delete", removed_records)
        return _IndexBuildPlan("build", tuple(records))

    def _is_fresh(self, fingerprint: str) -> bool:
        state = self._read_committed_index_state(fallback=self._read_state())
        return (
            _is_complete_index(self.index_dir)
            and str(state.get("fingerprint") or "") == fingerprint
            and _matches_build_policy(state, self._config, model=self._model)
        )


    def _index_readiness_error(self, *, refresh_inventory: bool = False) -> str | None:
        state = self._read_committed_index_state(fallback=self._read_state())
        if not _is_complete_index(self.index_dir):
            return f"Skill index is missing or incomplete: {self.index_dir}"
        if not _matches_build_policy(state, self._config, model=self._model, check_model=self._model is not None):
            return "Skill index is stale because build settings or the signature protocol changed."
        records = self._current_records(refresh=refresh_inventory)
        if not records:
            return None
        expected = _index_fingerprint(
            records, self._config, policy_fingerprint=str(state.get("build_policy_fingerprint") or ""),
        )
        actual = str(state.get("fingerprint") or "")
        if actual != expected:
            return "Skill index is stale because skills or build settings changed."
        return None


    def _set_running_state(
        self,
        *,
        build_id: str,
        stage: str,
        message: str,
        progress: float,
        force: bool,
        indexed_count: int,
        fingerprint: str,
    ) -> None:
        state = self._read_state()
        previous_build = dict(state.get("build") or {})
        logs = list(previous_build.get("logs") or [])
        logs.append({"stage": stage, "status": "running", "message": message, "time": _now_iso()})
        build = {
            **previous_build,
            "status": "running",
            "stage": stage,
            "message": message,
            "error": "",
            "progress": _coerce_progress(progress),
            "build_id": build_id,
            "force": bool(force),
            "target_count": int(indexed_count),
            "started_at": str(previous_build.get("started_at") or _now_iso()),
            "updated_at": _now_iso(),
            "attempted_fingerprint": fingerprint,
            "capability_category_paths": [],
            "logs": logs[-40:],
        }
        state.update(
            {
                "build": build,
                "attempted_fingerprint": fingerprint,
                "updated_at": _now_iso(),
            }
        )
        if not _is_complete_index(self.index_dir):
            state.setdefault("fingerprint", "")
            state.setdefault("indexed_count", 0)
        self._write_state(
            state,
            expected_build_id=build_id,
            claim=stage == "prepare",
            progress=stage != "prepare",
        )

    def _set_queued_state(self, *, build_id: str, force: bool, replace_running: bool = False) -> str | None:
        self.index_root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(self.index_root / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            _recover_index_directory_unlocked(self.index_dir)
            state = _read_json_object(self.index_root / STATE_FILENAME)
            previous_build = dict(state.get("build") or {})
            active_build_id = str(previous_build.get("build_id") or "")
            committed = (
                _read_json_object(self.index_dir / COMMIT_FILENAME) if _is_complete_index(self.index_dir) else {}
            )
            committed_build_id = str(committed.get("build_id") or "")
            if previous_build.get("status") == "running" and active_build_id and active_build_id == committed_build_id:
                previous_build.update(
                    status="success",
                    stage="success",
                    message="Skill index build completed.",
                    error="",
                    progress=1.0,
                    finished_at=str(committed.get("committed_at") or _now_iso()),
                    operation=str(committed.get("operation") or ""),
                )
                for key in _INDEX_IDENTITY_FIELDS:
                    if key in committed:
                        state[key] = committed[key]
            if previous_build.get("status") == "running" and active_build_id:
                if active_build_id != build_id and not replace_running:
                    return active_build_id
            now = _now_iso()
            logs = list(previous_build.get("logs") or [])
            logs.append(
                {
                    "stage": "queued",
                    "status": "running",
                    "message": "Skill index build is queued.",
                    "time": now,
                }
            )
            state.update(
                {
                    "build": {
                        **previous_build,
                        "status": "running",
                        "stage": "queued",
                        "message": "Skill index build is queued.",
                        "error": "",
                        "progress": 0.0,
                        "build_id": build_id,
                        "force": bool(force),
                        "started_at": now,
                        "updated_at": now,
                        "finished_at": "",
                        "capability_category_paths": [],
                        "logs": logs[-40:],
                    },
                    "updated_at": now,
                }
            )
            atomic_write_json(self.index_root / STATE_FILENAME, state)
            return None

    def _mark_start_failure(self, *, build_id: str, error: str) -> None:
        state = self._read_state()
        build = dict(state.get("build") or {})
        build.update(
            status="failed",
            stage="failed",
            message="Skill index build thread could not be started.",
            error=error,
            progress=1.0,
            finished_at=_now_iso(),
        )
        state["build"] = build
        self._write_state(state, expected_build_id=build_id)

    def _mark_async_failure(self, *, build_id: str, error: str) -> None:
        state = self._read_state()
        build = dict(state.get("build") or {})
        if str(build.get("build_id") or "") != build_id or str(build.get("status") or "") != "running":
            return
        now = _now_iso()
        logs = list(build.get("logs") or [])
        logs.append(
            {
                "stage": "failed",
                "status": "failed",
                "message": "Skill index build thread failed unexpectedly.",
                "time": now,
            }
        )
        build.update(
            status="failed",
            stage="failed",
            message="Skill index build thread failed unexpectedly.",
            error=error,
            progress=1.0,
            updated_at=now,
            finished_at=now,
            logs=logs[-40:],
        )
        state["build"] = build
        self._write_state(state, expected_build_id=build_id, progress=True)

    def _cancelled_result(self, *, build_id: str, started: float, fingerprint: str) -> dict[str, Any]:
        state = self._read_state()
        committed = self._read_committed_index_state(fallback=state)
        previous_build = dict(state.get("build") or {})
        now = _now_iso()
        build = {
            **previous_build,
            "status": "cancelled",
            "stage": "cancelled",
            "message": "Skill index build was cancelled.",
            "error": "",
            "progress": 1.0,
            "build_id": build_id,
            "updated_at": now,
            "finished_at": now,
            "elapsed_seconds": time.monotonic() - started,
            "attempted_fingerprint": fingerprint,
            "capability_category_paths": [],
        }
        state.update(
            {
                "build": build,
                "attempted_fingerprint": fingerprint,
                "updated_at": now,
            }
        )
        if _is_complete_index(self.index_dir):
            for key in _INDEX_IDENTITY_FIELDS:
                if key in committed:
                    state[key] = committed[key]
        else:
            state["fingerprint"] = ""
            state["build_policy_fingerprint"] = ""
            state["build_settings_fingerprint"] = ""
            state["indexed_count"] = 0
            state["record_hashes"] = {}
            state["source_file_hashes"] = {}
        self._write_state(state, expected_build_id=build_id)
        return _result(
            False,
            "Skill index build was cancelled.",
            data=state,
            error={"code": "cancelled", "message": "cancelled"},
        )

    def _failed_build_result(
        self,
        *,
        error: str,
        build_id: str,
        started: float,
        fingerprint: str,
        force: bool,
        indexed_count: int = 0,
        preserve_previous_index: bool = True,
        previous_state: dict[str, Any] | None = None,
        previous_index_available: bool | None = None,
    ) -> dict[str, Any]:
        previous_state = previous_state if previous_state is not None else self._read_state()
        previous_index_available = (
            _is_complete_index(self.index_dir) if previous_index_available is None else previous_index_available
        )
        if not preserve_previous_index:
            _cleanup_index(self.index_dir)
        state = _build_state(
            status="failed",
            stage="failed",
            message="Skill index build failed.",
            error=error,
            progress=1.0,
            build_id=build_id,
            force=force,
            indexed_count=indexed_count,
            fingerprint=fingerprint,
            finished=True,
            elapsed_seconds=time.monotonic() - started,
        )
        _set_failed_index_state(
            state,
            previous_state=previous_state,
            previous_index_available=previous_index_available,
            previous_index_preserved=preserve_previous_index,
            attempted_fingerprint=fingerprint,
        )
        self._write_state(state, expected_build_id=build_id)
        return _result(
            False,
            f"Skill index build failed: {error}",
            data={
                **state,
                "index_updated": False,
                "previous_index_available": previous_index_available,
                "previous_index_preserved": previous_index_available and preserve_previous_index,
            },
            error={"code": "build_failed", "message": error},
        )

    def _read_state(self) -> dict[str, Any]:
        path = self.index_root / STATE_FILENAME
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _read_committed_index_state(
        self,
        *,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.index_root.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            str(self.index_root / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            _recover_index_directory_unlocked(self.index_dir)
            commit_path = self.index_dir / COMMIT_FILENAME
            if commit_path.is_file():
                return _read_json_object(commit_path)
        return dict(fallback or {})

    def _write_state(
        self,
        state: dict[str, Any],
        *,
        expected_build_id: str | None = None,
        claim: bool = False,
        progress: bool = False,
    ) -> bool:
        self.index_root.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload["updated_at"] = _now_iso()
        with portalocker.Lock(
            str(self.index_root / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            current = _read_json_object(self.index_root / STATE_FILENAME)
            current_build = dict(current.get("build") or {})
            current_build_id = str(current_build.get("build_id") or "")
            current_status = str(current_build.get("status") or "")
            if expected_build_id is not None:
                if claim:
                    if current_status == "running" and current_build_id not in {"", expected_build_id}:
                        return False
                elif progress:
                    if current_build_id != expected_build_id or current_status != "running":
                        return False
                elif current_build_id != expected_build_id:
                    return False
            atomic_write_json(self.index_root / STATE_FILENAME, payload)
            return True

    def _has_running_task(self, build_id: Any) -> bool:
        with _TASKS_LOCK:
            task = _TASKS.get(str(self.index_root))
            return _is_running_build_task(task, build_id)


def _is_running_build_task(task: _BuildTask | None, build_id: Any) -> bool:
    if task is None:
        return False
    if not task.thread.is_alive():
        return False
    if not build_id:
        return True
    return task.build_id == build_id


def scan_skill_records(skills_dir: str | Path) -> list[SkillRecord]:
    root = Path(skills_dir).expanduser().resolve()
    records: list[SkillRecord] = []
    if not root.exists():
        return records
    candidates = [path for path in root.iterdir() if path.is_dir()]
    for skill_dir in sorted(candidates, key=lambda item: item.name):
        skill_file = find_skill_file(skill_dir)
        if skill_file is None:
            continue
        try:
            frontmatter, body, content_hash = read_skill_file(skill_file, fallback_to_simple=False)
        except (OSError, UnicodeError):
            continue
        name = str(frontmatter.get("name") or skill_dir.name).strip() or skill_dir.name
        description = str(frontmatter.get("description") or "").strip() or first_paragraph(body)
        records.append(
            SkillRecord(
                name=name,
                worker_id=skill_dir.name,
                description=description,
                skill_md_path=str(skill_file),
                enabled=True,
                metadata=dict(frontmatter),
                content=body.strip(),
                content_hash=content_hash,
            )
        )
    return records


def _run_index_builder(
    *,
    operation: str,
    item_jsonl_path: Path,
    output_dir: Path,
    base_index_dir: Path,
    config: RetrievalBuildConfig,
    model=None,
    check=None,
) -> None:
    if operation == "add":
        IndexBuilder.add(
            item_jsonl_path=str(item_jsonl_path),
            base_index_dir=base_index_dir,
            output_dir=output_dir,
            item_type="skill",
            config=config,
            model=model,
            check=check,
        )
        return
    if operation == "delete":
        IndexBuilder.delete(
            item_jsonl_path=str(item_jsonl_path),
            base_index_dir=base_index_dir,
            output_dir=output_dir,
            item_type="skill",
            config=config,
            model=model,
            check=check,
        )
        return
    IndexBuilder.build(
        item_paths=[],
        item_jsonl_path=str(item_jsonl_path),
        output_dir=output_dir,
        item_type="skill",
        config=config,
        model=model,
        check=check,
    )


def _coerce_skill_records(records: Sequence[SkillRecord | dict[str, Any]] | None) -> list[SkillRecord]:
    out: list[SkillRecord] = []
    for record in records or []:
        if isinstance(record, SkillRecord):
            out.append(record)
            continue
        payload = dict(record or {})
        out.append(
            SkillRecord(
                name=str(payload.get("name") or payload.get("worker_id") or "").strip(),
                description=str(payload.get("description") or "").strip(),
                worker_id=str(payload.get("worker_id") or payload.get("id") or payload.get("name") or "").strip(),
                skill_md_path=str(payload.get("skill_md_path") or payload.get("path") or "").strip(),
                enabled=bool(payload.get("enabled", True)),
                metadata=dict(payload.get("metadata") or {}),
                content=str(payload.get("content") or "").strip(),
                content_hash=str(payload.get("content_hash") or "").strip(),
            )
        )
    return out


def _write_records_jsonl(records: Sequence[SkillRecord], path: Path) -> None:
    lines = []
    for record in records:
        worker_id = record.resolved_worker_id
        content_hash = record.content_hash or _skill_record_hash(record)
        content_extend = {
            **dict(record.metadata or {}),
            "skillId": worker_id,
            "skillName": record.name or worker_id,
            "skillDesc": record.description,
            "skillPath": record.skill_md_path,
            "skillContent": record.content,
            "contentHash": content_hash,
        }
        lines.append(json.dumps({"contentExtendParam": content_extend}, ensure_ascii=False, default=str))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _tree_payload(nodes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    by_cid: dict[str, dict[str, Any]] = {}
    for node in nodes:
        cid = str(node.get("cid") or "").strip()
        if not cid:
            continue
        item: dict[str, Any] = {
            "id": cid,
            "label": cid.rsplit(".", 1)[-1],
            "type": str(node.get("type") or ""),
            "description": str(node.get("description") or ""),
            "worker_id": str(node.get("worker_id") or ""),
            "children": [],
        }
        by_cid[cid] = item
        parent = cid.rsplit(".", 1)[0] if "." in cid else ""
        by_parent.setdefault(parent, []).append(item)
    for cid, item in by_cid.items():
        item["children"] = sorted(by_parent.get(cid, []), key=lambda child: str(child.get("id")))
    return sorted(by_parent.get("", []), key=lambda child: str(child.get("id")))


def _render_tree_outline(nodes: Sequence[dict[str, Any]], *, max_nodes: int) -> str:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        cid = str(node.get("cid") or "").strip()
        if not cid:
            continue
        parent = cid.rsplit(".", 1)[0] if "." in cid else ""
        by_parent.setdefault(parent, []).append(node)
    lines: list[str] = []
    count = 0

    def visit(parent: str, depth: int) -> None:
        nonlocal count
        for node in sorted(by_parent.get(parent, []), key=lambda item: str(item.get("cid") or "")):
            if count >= max_nodes:
                return
            count += 1
            cid = str(node.get("cid") or "")
            label = cid.rsplit(".", 1)[-1]
            node_type = str(node.get("type") or "")
            marker = "- " if depth == 0 else "  " * depth + "- "
            worker = str(node.get("worker_id") or "")
            suffix = f" -> `{worker}`" if worker else ""
            lines.append(f"{marker}`{cid}` {label} [{node_type}]{suffix}".rstrip())
            visit(cid, depth + 1)

    visit("", 0)
    if count >= max_nodes:
        lines.append(f"- ... truncated at {max_nodes} nodes")
    return "\n".join(lines)


def _build_state(
    *,
    status: str,
    stage: str,
    message: str,
    progress: float,
    build_id: str,
    force: bool,
    indexed_count: int,
    fingerprint: str,
    error: str = "",
    record_hashes: dict[str, str] | None = None,
    source_file_hashes: dict[str, str] | None = None,
    finished: bool = False,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "fingerprint": fingerprint,
        "indexed_count": int(indexed_count),
        "record_hashes": dict(record_hashes or {}),
        "source_file_hashes": dict(source_file_hashes or {}),
        "updated_at": now,
        "build": {
            "status": status,
            "stage": stage,
            "message": message,
            "error": error,
            "progress": _coerce_progress(progress),
            "build_id": build_id,
            "force": bool(force),
            "started_at": now,
            "updated_at": now,
            "finished_at": now if finished else "",
            "elapsed_seconds": float(elapsed_seconds),
            "logs": [{"stage": stage, "status": status, "message": message, "time": now}],
        },
    }


def _set_capability_category_paths(state: dict[str, Any], paths: Sequence[dict[str, Any]]) -> None:
    build = dict(state.get("build") or {})
    build["capability_category_paths"] = [dict(item) for item in paths]
    state["build"] = build


def _load_capability_category_paths(
    index_dir: str | Path,
    *,
    worker_ids: Sequence[str],
) -> list[dict[str, Any]]:
    requested: list[str] = []
    requested_set: set[str] = set()
    for raw_worker_id in worker_ids:
        worker_id = str(raw_worker_id).strip()
        if not worker_id or worker_id in requested_set:
            continue
        requested.append(worker_id)
        requested_set.add(worker_id)
    if not requested:
        return []

    records_by_worker_id: dict[str, CatalogRecord] = {}
    duplicate_worker_ids: set[str] = set()
    for record in load_retriever_index(index_dir).catalog_records:
        worker_id = str(record.worker_id or "").strip()
        if worker_id not in requested_set:
            continue
        if worker_id in records_by_worker_id:
            duplicate_worker_ids.add(worker_id)
        records_by_worker_id[worker_id] = record
    if duplicate_worker_ids:
        joined = ", ".join(sorted(duplicate_worker_ids)[:5])
        raise RuntimeError(f"built index contains duplicate catalog records for capability IDs: {joined}")

    missing: list[str] = []
    for worker_id in requested:
        if worker_id not in records_by_worker_id:
            missing.append(worker_id)
    if missing:
        joined = ", ".join(missing[:5])
        raise RuntimeError(f"built index is missing category paths for updated capability IDs: {joined}")

    paths: list[dict[str, Any]] = []
    for worker_id in requested:
        category_path: list[str] = []
        for raw_part in records_by_worker_id[worker_id].branch_path:
            part = str(raw_part).strip()
            if part:
                category_path.append(part)
        if not category_path:
            raise RuntimeError(f"built index contains an empty category path for updated capability ID: {worker_id}")
        paths.append({"capability_id": worker_id, "category_path": category_path})
    return paths


def _validate_index_worker_ids(
    index_dir: Path,
    *,
    expected_worker_ids: set[str],
) -> None:
    actual: set[str] = set()
    for line in (index_dir / CATALOG_FILENAME).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError("built index contains an invalid catalog record")
        worker_id = str(payload.get("worker_id") or payload.get("skill_id") or "").strip()
        if not worker_id or worker_id in actual:
            raise RuntimeError("built index contains a missing or duplicate capability ID")
        actual.add(worker_id)
    if actual != expected_worker_ids:
        missing = sorted(expected_worker_ids - actual)
        unexpected = sorted(actual - expected_worker_ids)
        detail: list[str] = []
        if missing:
            detail.append(f"missing: {', '.join(missing[:5])}")
        if unexpected:
            detail.append(f"unexpected: {', '.join(unexpected[:5])}")
        raise RuntimeError(
            "built index catalog does not match the requested Skill snapshot"
            + (f" ({'; '.join(detail)})" if detail else "")
        )


def _set_failed_index_state(
    state: dict[str, Any],
    *,
    previous_state: dict[str, Any],
    previous_index_available: bool,
    previous_index_preserved: bool,
    attempted_fingerprint: str,
) -> None:
    if previous_index_available and previous_index_preserved:
        state["fingerprint"] = str(previous_state.get("fingerprint") or "")
        state["build_policy_fingerprint"] = str(previous_state.get("build_policy_fingerprint") or "")
        state["build_settings_fingerprint"] = str(previous_state.get("build_settings_fingerprint") or "")
        state["indexed_count"] = int(previous_state.get("indexed_count") or 0)
        state["record_hashes"] = dict(previous_state.get("record_hashes") or {})
        state["source_file_hashes"] = dict(previous_state.get("source_file_hashes") or {})
    else:
        state["fingerprint"] = ""
        state["build_policy_fingerprint"] = ""
        state["build_settings_fingerprint"] = ""
        state["indexed_count"] = 0
        state["record_hashes"] = {}
        state["source_file_hashes"] = {}
    state["attempted_fingerprint"] = attempted_fingerprint
    build = dict(state.get("build") or {})
    build["attempted_fingerprint"] = attempted_fingerprint
    state["build"] = build


def _committed_index_state(
    *,
    build_id: str,
    fingerprint: str,
    policy_fingerprint: str,
    settings_fingerprint: str,
    operation: str,
    record_hashes: dict[str, str],
    source_file_hashes: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "build_id": build_id,
        "fingerprint": fingerprint,
        "build_policy_fingerprint": policy_fingerprint,
        "build_settings_fingerprint": settings_fingerprint,
        "operation": operation,
        "indexed_count": len(record_hashes),
        "record_hashes": dict(record_hashes),
        "source_file_hashes": dict(source_file_hashes),
        "committed_at": _now_iso(),
    }


def _write_index_commit(index_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_json(index_dir / COMMIT_FILENAME, state)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _recover_index_directory_unlocked(
    index_dir: Path,
    *,
    replace_invalid: bool = False,
    validator: Callable[[Path], bool] | None = None,
) -> bool:
    """Recover an interrupted directory swap while the publication lock is held."""

    if _is_complete_index(index_dir) and not replace_invalid:
        return False
    parent = index_dir.parent
    candidates: list[Path] = []
    for pattern in (f".{index_dir.name}.staging-*", f".{index_dir.name}.backup-*"):
        for path in parent.glob(pattern):
            try:
                valid = validator(path) if validator is not None else _is_complete_index(path)
            except Exception:
                valid = False
            if path.is_dir() and valid:
                candidates.append(path)
    if not candidates:
        return False
    candidates.sort(
        key=lambda path: (
            int((path / COMMIT_FILENAME).is_file()),
            int(".staging-" in path.name),
            path.stat().st_mtime_ns,
        ),
        reverse=True,
    )
    selected = candidates[0]
    if index_dir.exists():
        shutil.rmtree(index_dir, ignore_errors=True)
    selected.replace(index_dir)
    return True


def _publish_index(
    *, candidate_dir: Path, index_dir: Path, expected_build_id: str,
    check: Callable[[str], None] | None = None,
) -> None:
    parent = index_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    backup = parent / f".{index_dir.name}.backup-{token}"
    pending = parent / f".{index_dir.name}.pending-{token}"
    staging = parent / f".{index_dir.name}.staging-{token}"
    try:
        # Recovery deliberately ignores pending copies. The completed candidate
        # only becomes recoverable after it is handed off under the publish lock.
        shutil.copytree(candidate_dir, pending)
        with portalocker.Lock(
            str(parent / _INDEX_PUBLISH_LOCK_FILENAME),
            mode="a+",
            timeout=30,
        ):
            current = _read_json_object(parent / STATE_FILENAME)
            build = dict(current.get("build") or {})
            if str(build.get("build_id") or "") != expected_build_id or str(build.get("status") or "") != "running":
                raise SkillIndexBuildCancelled("Skill index build was superseded before publication.")
            # Copying and waiting for the lock can outlive the last task check.
            # Recheck at the generation-switch boundary while the lock is held.
            if check is not None:
                check("publish")
            pending.replace(staging)
            if index_dir.exists():
                index_dir.replace(backup)
            try:
                staging.replace(index_dir)
            except Exception:
                if backup.exists() and not index_dir.exists():
                    backup.replace(index_dir)
                raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    finally:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _cleanup_index(index_dir: Path) -> None:
    parent = index_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(
        str(parent / _INDEX_PUBLISH_LOCK_FILENAME),
        mode="a+",
        timeout=30,
    ):
        shutil.rmtree(index_dir, ignore_errors=True)


def _is_complete_index(index_dir: Path) -> bool:
    return all(
        (index_dir / filename).exists() for filename in (TREE_INDEX_FILENAME, CATALOG_FILENAME, MANIFEST_FILENAME)
    )


def _index_fingerprint(
    records: Sequence[SkillRecord],
    config: RetrievalBuildConfig,
    *,
    model=None,
    policy_fingerprint: str | None = None,
    record_hashes: dict[str, str] | None = None,
    source_file_hashes: dict[str, str] | None = None,
) -> str:
    snapshot_records = (
        list(records)
        if record_hashes is not None and source_file_hashes is not None
        else [_snapshot_record_source(record) for record in records]
    )
    payload = {
        "records": dict(record_hashes or _record_hashes(snapshot_records)),
        "source_files": dict(source_file_hashes or _source_file_hashes(snapshot_records)),
        "build_policy_fingerprint": policy_fingerprint or _build_policy_fingerprint(config, model=model),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_policy_fingerprint(config: RetrievalBuildConfig, *, model=None) -> str:
    payload = {
        "build_settings_fingerprint": _build_settings_fingerprint(config),
        "model": _model_identity(model),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _matches_build_policy(state: dict[str, Any], config: RetrievalBuildConfig, *, model=None, check_model=True) -> bool:
    policy = str(state.get("build_policy_fingerprint") or "")
    return (
        bool(policy)
        and str(state.get("build_settings_fingerprint") or "") == _build_settings_fingerprint(config)
        and (not check_model or policy == _build_policy_fingerprint(config, model=model))
    )


def _build_settings_fingerprint(config: RetrievalBuildConfig) -> str:
    payload = {
        "protocol": "symphony-tree-build.v3",
        "config": {
            "structure": {
                **config.structure.model_dump(mode="json"),
                "root_categories": resolve_tree_root_categories(config.structure.root_categories),
            },
            "request": config.request.model_dump(mode="json"),
            "profiles": config.profiles.model_dump(mode="json"),
            "prompt": config.prompt.model_dump(mode="json"),
            "postprocess": config.postprocess.model_dump(mode="json"),
            "equivalence": config.equivalence.model_dump(mode="json"),
            "incremental": config.incremental.model_dump(mode="json"),
            "allow_fallback_tree": config.task.allow_fallback_tree,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _records_fingerprint(records: Sequence[SkillRecord]) -> str:
    encoded = json.dumps(_record_hashes(records), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_hashes(records: Sequence[SkillRecord]) -> dict[str, str]:
    return {
        record.resolved_worker_id: _skill_record_hash(record)
        for record in sorted(records, key=lambda item: item.resolved_worker_id)
    }


def _skill_record_hash(record: SkillRecord) -> str:
    payload = {
        "name": record.name,
        "description": record.description,
        "worker_id": record.resolved_worker_id,
        "skill_md_path": record.skill_md_path,
        "content": record.content,
        "content_hash": record.content_hash,
        "metadata": record.metadata,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_file_hashes(records: Sequence[SkillRecord]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for record in sorted(records, key=lambda item: item.resolved_worker_id):
        value = str(record.content_hash or "").strip()
        if not value and record.skill_md_path:
            path = Path(record.skill_md_path).expanduser()
            if path.is_file():
                value = sha256_file(path)
        if not value:
            value = _skill_record_hash(record)
        hashes[record.resolved_worker_id] = value
    return hashes


def _snapshot_record_source(record: SkillRecord) -> SkillRecord:
    """Bind a path-backed record without a hash to one immutable file snapshot."""

    if record.content_hash or not record.skill_md_path:
        return record
    path = Path(record.skill_md_path).expanduser()
    if not path.is_file():
        return record
    try:
        frontmatter, body, content_hash = read_skill_file(path, fallback_to_simple=False)
    except (OSError, UnicodeError):
        return record
    metadata = {**record.metadata, **frontmatter}
    return replace(
        record,
        name=str(frontmatter.get("name") or record.name).strip() or record.resolved_worker_id,
        description=(str(frontmatter.get("description") or record.description).strip() or first_paragraph(body)),
        metadata=metadata,
        content=body.strip(),
        content_hash=content_hash,
    )


def _first_description_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("Select when:") and not stripped.startswith("Don't select when:"):
            return stripped
    return ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _cancel_check(cancel_token: Callable[[], bool] | threading.Event | None) -> Callable[[], bool]:
    if cancel_token is None:
        return lambda: False
    if isinstance(cancel_token, threading.Event):
        return cancel_token.is_set
    if callable(cancel_token):
        return lambda: bool(cancel_token())
    return lambda: False


def _build_check(
    *,
    cancel_check: Callable[[], bool],
    started: float,
    total_timeout_seconds: float,
) -> Callable[[str], None]:
    total_timeout = float(total_timeout_seconds or 0.0)

    def check(stage: str) -> None:
        if cancel_check():
            raise SkillIndexBuildCancelled(f"Skill index build cancelled at stage `{stage}`.")
        if total_timeout > 0 and time.monotonic() - started > total_timeout:
            raise SkillIndexBuildTimeout(
                f"Skill index build exceeded total timeout {total_timeout:.1f}s at stage `{stage}`."
            )

    return check


def _result(
    success: bool,
    result: str,
    *,
    data: dict[str, Any] | None = None,
    detailed_output: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": bool(success), "result": str(result or "")}
    if data is not None:
        payload["data"] = data
    if detailed_output is not None:
        payload["detailed_output"] = detailed_output
    if error is not None:
        payload["error"] = error
    return payload


def _emit(callback: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    callback(payload)


def _status_message(status: str) -> str:
    return {
        "idle": "Skill index build is idle.",
        "running": "Skill index build is running.",
        "success": "Skill index build completed.",
        "failed": "Skill index build failed.",
        "cancelled": "Skill index build was cancelled.",
    }.get(status, f"Skill index build status: {status}.")


def _index_unavailable_text(reason: str, *, language: str) -> str:
    if language.startswith("zh"):
        return (
            "技能索引当前不可用。\n\n"
            f"原因：{str(reason or '').strip()}\n\n"
            "处理方法：构建或刷新技能索引后重试；如果当前任务不需要索引化技能检索，"
            "也可以忽略该结果并继续使用宿主系统原有流程。"
        )
    return (
        "Skill index is not available.\n\n"
        f"Reason: {str(reason or '').strip()}\n\n"
        "Next step: build or refresh the skill index and retry. If indexed skill retrieval is not useful "
        "for the task, ignore this result and continue with the host system's original flow."
    )


def _coerce_progress(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _compact_text(text: str, *, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


def _normalize_error(exc: Exception) -> str:
    return " ".join(str(exc or exc.__class__.__name__).split()) or exc.__class__.__name__


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_build_id() -> str:
    return (
        f"build-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex}"
    )


def _build_id_mismatch_result(*, requested_build_id: str, active_build_id: str) -> dict[str, Any]:
    message = f"Build id mismatch: active build is {active_build_id}; requested {requested_build_id}."
    return _result(
        False,
        message,
        data={
            "state": "running",
            "build_id": active_build_id,
            "requested_build_id": requested_build_id,
        },
        error={"code": "build_id_mismatch", "message": message},
    )
