from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable, Sequence

from openjiuwen.symphony.retrieval.build.io import load_tree_preset
from openjiuwen.symphony.retrieval.build.workflows.artifacts import BuildConfig
from openjiuwen.symphony.retrieval.build.workflows.index_builder import IndexBuilder
from openjiuwen.symphony.retrieval.common.models import RetrieverItem, RetrieverNode
from openjiuwen.symphony.retrieval.common.prompts import AGENTIC_RETRIEVAL_YAML, get_prompt
from openjiuwen.symphony.retrieval.search.artifacts.loading import (
    CatalogRecord,
    LoadedRetrieverIndex,
    load_retriever_index,
)
from openjiuwen.symphony.retrieval.search.runtime.subtree import DefaultCurrentSubtreeProvider
from openjiuwen.symphony.retrieval.search.runtime.types import ProgressiveRetrieverConfig, SearchCursor

TREE_INDEX_FILENAME = "tree_index.yaml"
CATALOG_FILENAME = "catalog.jsonl"
MANIFEST_FILENAME = "manifest.json"
STATE_FILENAME = "state.json"

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


@dataclass(frozen=True)
class LLMConfig:
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    client: Any | None = None
    seed: int | None = None


@dataclass(frozen=True)
class SkillIndexBuildConfig:
    max_depth: int = 6
    branching_factor: int = 128
    max_workers: int = 2
    max_retries: int = 2
    request_timeout_seconds: float = 420.0
    total_timeout_seconds: float = 0.0
    classification_batch_limit: int = 32
    root_categories: Any = None
    caching: bool = False
    context_window: int = 0
    max_output_tokens: int = 0
    discovery_seed: int = 42
    postprocess_enabled: bool = True
    postprocess_max_passes: int = 1
    postprocess_min_skills: int = 6
    equivalence_enabled: bool = False
    equivalence_max_groups_per_parent: int = 6
    equivalence_allow_singleton_groups: bool = True
    equivalence_min_lexical_similarity: float = 0.0
    deterministic_prompts: bool = True
    prompt_fingerprint_version: str = "v1"
    cache_observability: bool = True
    skill_profiles_enabled: bool = False
    skill_profile_select_rules_enabled: bool = True
    skill_profile_batch_size: int = 48
    skill_profile_description_limit: int = 140
    skill_profile_rule_limit: int = 120
    incremental_max_change_ratio: float = 0.25
    incremental_min_add_confidence: float = 0.18
    incremental_min_add_confidence_margin: float = 0.04
    incremental_branch_imbalance_ratio: float = 3.0
    generate_tree_html: bool = False
    preserve_previous_index_on_failure: bool = True
    strict_failure: bool = False


@dataclass(frozen=True)
class SkillIndexRuntimeConfig:
    index_root: str | Path
    state_filename: str = STATE_FILENAME


@dataclass(frozen=True)
class AgenticRetrievalConfig:
    top_k: int = 10
    compact_codes_enabled: bool = False
    flatten_tree: bool = False
    max_exposure_depth: int = 1
    exposure_threshold: int = 12
    max_tokens: int = 96
    request_timeout_seconds: float = 120.0
    index_build_tool_name: str = "skill_index_build"
    branch_explore_tool_name: str = "skill_branch_explore"
    branch_peek_tool_name: str = "skill_branch_peek"


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


class SkillIndexBuildCancelled(RuntimeError):
    """Raised when a cooperative index build cancellation is requested."""


class SkillIndexBuildTimeout(RuntimeError):
    """Raised when an index build exceeds the configured total timeout."""


class AgenticSkillRetrievalToolkit:
    """Single SDK entrypoint for indexed, agent-controlled skill retrieval."""

    def __init__(
        self,
        *,
        index_root: str | Path,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        skills_dir: str | Path | None = None,
        build_config: SkillIndexBuildConfig | None = None,
        retrieval_config: AgenticRetrievalConfig | None = None,
        llm_config: LLMConfig | None = None,
        visible_skill_names: Iterable[str] | None = None,
    ) -> None:
        self.index_root = Path(index_root).expanduser().resolve()
        self.index_dir = self.index_root / "index"
        self._build_config = build_config or SkillIndexBuildConfig()
        self._retrieval_config = retrieval_config or AgenticRetrievalConfig()
        self._llm_config = llm_config or LLMConfig()
        self._skills = _coerce_skill_records(skills)
        self._skills_dir = Path(skills_dir).expanduser().resolve() if skills_dir else None
        self._visible_skill_names = _normalize_visible_skill_names(visible_skill_names)
        self._loaded_index: LoadedRetrieverIndex | None = None
        self._filtered_root: RetrieverNode | None = None
        self._node_by_id: dict[str, RetrieverNode] = {}
        self._path_by_id: dict[str, tuple[str, ...]] = {}
        self._stats_by_id: dict[str, dict[str, int]] = {}
        self._catalog_by_payload: dict[str, CatalogRecord] = {}

    def build_index(
        self,
        *,
        skills: Sequence[SkillRecord | dict[str, Any]] | None = None,
        skills_dir: str | Path | None = None,
        force: bool = False,
        build_config: SkillIndexBuildConfig | None = None,
        llm_config: LLMConfig | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_token: Callable[[], bool] | threading.Event | None = None,
        _build_id: str | None = None,
    ) -> dict[str, Any]:
        records = self._resolve_records(skills=skills, skills_dir=skills_dir)
        if skills is not None:
            self._skills = list(records)
        if skills_dir is not None:
            self._skills_dir = Path(skills_dir).expanduser()
            self._skills = list(records)
        build_cfg = build_config or self._build_config
        llm_cfg = llm_config or self._llm_config
        if build_config is not None:
            self._build_config = build_cfg
        if llm_config is not None:
            self._llm_config = llm_cfg
        started = time.monotonic()
        build_id = str(_build_id or _new_build_id())
        fingerprint = _index_fingerprint(records, build_cfg)
        previous_index_state = self._read_state()
        previous_index_available = _is_complete_index(self.index_dir)
        self.index_root.mkdir(parents=True, exist_ok=True)

        if not records:
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
            self._write_state(state)
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
                record_hashes=_record_hashes(records),
                finished=True,
                elapsed_seconds=time.monotonic() - started,
            )
            _set_capability_category_paths(state, capability_category_paths)
            self._write_state(state)
            return _result(
                True,
                f"Skill index is fresh. {len(records)} skills indexed.",
                data={**state, "capability_category_paths": capability_category_paths},
            )

        cancel_check = _cancel_check(cancel_token)
        build_check = _build_check(
            cancel_check=cancel_check,
            started=started,
            total_timeout_seconds=build_cfg.total_timeout_seconds,
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
                preserve_previous_index=build_cfg.preserve_previous_index_on_failure,
                previous_state=previous_index_state,
                previous_index_available=previous_index_available,
            )

        if build_cfg.strict_failure and not (llm_cfg.model and (llm_cfg.client is not None or llm_cfg.api_key)):
            error = "Skill index build requires a model and API key in strict failure mode."
            if not build_cfg.preserve_previous_index_on_failure:
                _cleanup_index(self.index_dir)
            state = _build_state(
                status="failed",
                stage="llm_config",
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
                previous_index_preserved=build_cfg.preserve_previous_index_on_failure,
                attempted_fingerprint=fingerprint,
            )
            self._write_state(state)
            return _result(
                False,
                f"Skill index build failed: {error}",
                data={
                    **state,
                    "index_updated": False,
                    "previous_index_available": previous_index_available,
                    "previous_index_preserved": (
                        previous_index_available and build_cfg.preserve_previous_index_on_failure
                    ),
                },
                error={"code": "llm_config_missing", "message": error},
            )

        with TemporaryDirectory(prefix="symphony-skill-index-") as tmp:
            tmp_root = Path(tmp)
            item_jsonl = tmp_root / "skills.jsonl"
            plan = self._select_build_plan(records=records, force=force)
            _write_records_jsonl(plan.records, item_jsonl)
            output_dir = tmp_root / "index"
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
                config = _to_retrieval_build_config(build_cfg, llm_cfg)
                if plan.operation == "build":
                    _run_index_builder(
                        operation=plan.operation,
                        item_jsonl_path=item_jsonl,
                        output_dir=output_dir,
                        base_index_dir=self.index_dir,
                        config=config,
                    )
                else:
                    try:
                        _run_index_builder(
                            operation=plan.operation,
                            item_jsonl_path=item_jsonl,
                            output_dir=output_dir,
                            base_index_dir=self.index_dir,
                            config=config,
                        )
                    except Exception:
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
                            base_index_dir=self.index_dir,
                            config=config,
                        )
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
                self._set_running_state(
                    build_id=build_id,
                    stage="publish",
                    message="Publishing skill index.",
                    progress=0.9,
                    force=force,
                    indexed_count=len(records),
                    fingerprint=fingerprint,
                )
                _publish_index(candidate_dir=output_dir, index_dir=self.index_dir)
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
                    record_hashes=_record_hashes(records),
                    finished=True,
                    elapsed_seconds=elapsed,
                )
                _set_capability_category_paths(state, capability_category_paths)
                self._write_state(state)
                self._clear_loaded_index()
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
            except Exception as exc:
                error = _normalize_error(exc)
                if not build_cfg.preserve_previous_index_on_failure:
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
                    previous_index_preserved=build_cfg.preserve_previous_index_on_failure,
                    attempted_fingerprint=fingerprint,
                )
                self._write_state(state)
                return _result(
                    False,
                    f"Skill index build failed: {error}",
                    data={
                        **state,
                        "index_updated": False,
                        "previous_index_available": previous_index_available,
                        "previous_index_preserved": (
                            previous_index_available and build_cfg.preserve_previous_index_on_failure
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
        build_config: SkillIndexBuildConfig | None = None,
        llm_config: LLMConfig | None = None,
        replace_running: bool = False,
    ) -> dict[str, Any]:
        key = str(self.index_root)
        with _TASKS_LOCK:
            existing = _TASKS.get(key)
            if existing and existing.thread.is_alive():
                if not replace_running:
                    return _result(
                        True,
                        "Skill index build is already running.",
                        data={"build_id": existing.build_id, "state": "running"},
                    )
                existing.cancel_event.set()
            cancel_event = threading.Event()
            build_id = _new_build_id()
            thread = threading.Thread(
                target=self._run_async_build,
                kwargs={
                    "build_id": build_id,
                    "cancel_event": cancel_event,
                    "skills": skills,
                    "skills_dir": skills_dir,
                    "force": force,
                    "build_config": build_config,
                    "llm_config": llm_config,
                    "_build_id": build_id,
                },
                daemon=True,
                name=f"symphony-skill-index-{build_id}",
            )
            _TASKS[key] = _BuildTask(thread=thread, cancel_event=cancel_event, build_id=build_id)
            thread.start()
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
        with _TASKS_LOCK:
            task = _TASKS.get(key)
            if _is_running_build_task(task, build_id):
                task.cancel_event.set()
                cancelled = True
        state = self._read_state()
        build = dict(state.get("build") or {})
        if cancelled or build.get("status") == "running":
            build["status"] = "cancelled"
            build["stage"] = "cancelled"
            build["message"] = "Skill index build cancellation requested."
            build["progress"] = 1.0
            build["finished_at"] = _now_iso()
            build["capability_category_paths"] = []
            state["build"] = build
            self._write_state(state)
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
        index_exists = _is_complete_index(self.index_dir)
        if build.get("status") == "running" and not self._has_running_task(build.get("build_id")):
            build.update(
                {
                    "status": "failed",
                    "stage": "interrupted",
                    "message": "Skill index build was interrupted.",
                    "error": "No running build task was found for the persisted running state.",
                    "finished_at": _now_iso(),
                    "progress": 1.0,
                }
            )
            state["build"] = build
            self._write_state(state)
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
                self._write_state(state)
        capability_category_paths: list[dict[str, Any]] = []
        if build.get("status") == "success":
            capability_category_paths = list(build.get("capability_category_paths") or [])
        status = {
            "status": str(build.get("status") or "idle"),
            "stage": str(build.get("stage") or ""),
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
            "fresh": index_exists
            and str(state.get("fingerprint") or "")
            == _index_fingerprint(self._current_records(refresh=refresh_inventory), self._build_config),
            "indexed_count": int(state.get("indexed_count") or 0) if index_exists else 0,
            "fingerprint": str(state.get("fingerprint") or ""),
            "capability_category_paths": capability_category_paths,
        }
        if include_logs:
            status["logs"] = list(build.get("logs") or [])
        if include_inventory:
            records = self._current_records(refresh=refresh_inventory)
            status["inventory"] = {"count": len(records), "fingerprint": _records_fingerprint(records)}
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
        payload = load_tree_preset(self.index_dir / TREE_INDEX_FILENAME)
        raw_nodes = payload.get("nodes")
        nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
        outline = _render_tree_outline(nodes, max_nodes=max_nodes)
        return _result(
            True,
            outline or "Skill index tree is empty.",
            data={"index_exists": True, "tree": _tree_payload(nodes)},
        )

    def branch_explore(
        self,
        node_ids: Sequence[str],
        *,
        visible_skill_names: Iterable[str] | None = None,
        retrieval_config: AgenticRetrievalConfig | None = None,
        max_exposure_depth: int | None = None,
    ) -> dict[str, Any]:
        try:
            self._ensure_runtime(visible_skill_names=visible_skill_names)
        except Exception as exc:
            return _result(False, _index_unavailable_text(_normalize_error(exc), language="en"))
        nodes, error = self._resolve_nodes(node_ids, default_root=False)
        if error:
            return _result(False, error)
        if any(node.node_id == "ROOT" for node in nodes):
            return _result(
                False,
                "`ROOT` is already summarized in the retrieval prompt. "
                "Call `branch_explore` with a first-level category id.",
            )
        base_config = retrieval_config or self._retrieval_config
        config = replace(
            base_config,
            max_exposure_depth=(
                max_exposure_depth if max_exposure_depth is not None else base_config.max_exposure_depth
            ),
        )
        provider = DefaultCurrentSubtreeProvider(
            config=_progressive_config(config),
            subtree_item_count=lambda current: self._node_stats(current)["skill_count"],
            cache={},
            cache_lock=None,
        )
        lines = ["# Skill Branch Explore", ""]
        steps: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            if index:
                lines.append("")
            path = self._path_by_id.get(node.node_id, ("ROOT", node.node_id))
            subtree = provider.get_current_subtree(
                cursor=SearchCursor(node=node, depth=max(0, len(path) - 1), branch_path=path, top_k=config.top_k)
            )
            self._render_explore_fragment(lines, node=node, fragment=subtree.fragment, candidates=candidates)
            steps.append(_step_payload("explore", node))
        return _result(
            True,
            "\n".join(lines).rstrip(),
            detailed_output={
                "skill_tree": {
                    "query": {"tool": "branch_explore", "node_ids": list(node_ids)},
                    "steps": steps,
                    "candidates": candidates,
                }
            },
        )

    def branch_peek(
        self,
        node_ids: Sequence[str],
        *,
        visible_skill_names: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        try:
            self._ensure_runtime(visible_skill_names=visible_skill_names)
        except Exception as exc:
            return _result(False, _index_unavailable_text(_normalize_error(exc), language="en"))
        nodes, error = self._resolve_nodes(node_ids, default_root=True)
        if error:
            return _result(False, error)
        lines = ["# Skill Branch Peek", ""]
        steps: list[dict[str, Any]] = []
        for index, node in enumerate(nodes):
            if index:
                lines.append("")
            self._render_peek_node(lines, node)
            steps.append(_step_payload("peek", node))
        return _result(
            True,
            "\n".join(lines).rstrip(),
            detailed_output={
                "skill_tree": {
                    "query": {"tool": "branch_peek", "node_ids": list(node_ids)},
                    "steps": steps,
                    "candidates": [],
                }
            },
        )

    def render_retrieval_prompt(
        self,
        *,
        language: str = "zh",
        visible_skill_names: Iterable[str] | None = None,
        max_children: int = 30,
    ) -> str:
        error = self._index_readiness_error()
        if error:
            return self._render_index_unavailable_prompt(error, language=language)
        try:
            self._ensure_runtime(visible_skill_names=visible_skill_names)
        except Exception as exc:
            return self._render_index_unavailable_prompt(_normalize_error(exc), language=language)
        children = list((self._filtered_root or RetrieverNode("ROOT", "ROOT")).children)
        category_lines: list[str] = []
        for child in children[: max(1, int(max_children))]:
            stats = self._node_stats(child)
            description = _compact_text(child.description, limit=120)
            suffix = f" - {description}" if description else ""
            category_lines.append(f"- `{child.node_id}`{suffix} ({stats['skill_count']} skills)")
        if len(children) > max_children:
            category_lines.append(f"- ... {len(children) - max_children} more categories")
        if not category_lines:
            category_lines.append(
                "当前索引树没有第一层分支。" if language.startswith("zh") else "No first-level branches are available."
            )
        key = "zh" if language.startswith("zh") else "en"
        return get_prompt(AGENTIC_RETRIEVAL_YAML, "root_prompt", key).format(
            build_tool=self._retrieval_config.index_build_tool_name,
            explore_tool=self._retrieval_config.branch_explore_tool_name,
            peek_tool=self._retrieval_config.branch_peek_tool_name,
            categories="\n".join(category_lines),
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

    def update_visible_skills(self, visible_skill_names: Iterable[str] | None) -> None:
        self._visible_skill_names = _normalize_visible_skill_names(visible_skill_names)
        self._clear_loaded_index()

    def close(self) -> None:
        self._clear_loaded_index()

    def _run_async_build(self, **kwargs: Any) -> None:
        build_id = str(kwargs.pop("build_id"))
        cancel_event = kwargs.pop("cancel_event")
        try:
            self.build_index(cancel_token=cancel_event, **kwargs)
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
        elif self._skills:
            records = list(self._skills)
        elif self._skills_dir is not None:
            records = scan_skill_records(self._skills_dir)
        else:
            records = []
        return [record for record in records if record.enabled and record.resolved_worker_id]

    def _current_records(self, *, refresh: bool = False) -> list[SkillRecord]:
        if refresh and self._skills_dir is not None:
            self._skills = scan_skill_records(self._skills_dir)
        return [record for record in self._skills if record.enabled and record.resolved_worker_id]

    def _select_build_plan(self, *, records: Sequence[SkillRecord], force: bool) -> _IndexBuildPlan:
        if force or not _is_complete_index(self.index_dir):
            return _IndexBuildPlan("build", tuple(records))
        state = self._read_state()
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
        state = self._read_state()
        return _is_complete_index(self.index_dir) and str(state.get("fingerprint") or "") == fingerprint

    def _ensure_runtime(self, *, visible_skill_names: Iterable[str] | None = None) -> None:
        error = self._index_readiness_error()
        if error:
            raise RuntimeError(error)
        visible = _normalize_visible_skill_names(visible_skill_names)
        if visible is None:
            visible = self._visible_skill_names
        if self._loaded_index is not None and visible == self._visible_skill_names:
            return
        if not _is_complete_index(self.index_dir):
            raise RuntimeError(f"skill index is not complete: {self.index_dir}")
        loaded = load_retriever_index(self.index_dir)
        self._loaded_index = loaded
        self._visible_skill_names = visible
        self._catalog_by_payload = {str(record.payload): record for record in loaded.catalog_records}
        self._filtered_root = _filter_tree(loaded.tree_root, self._catalog_by_payload, visible)
        self._node_by_id = {}
        self._path_by_id = {}
        self._stats_by_id = {}
        self._index_nodes(self._filtered_root, ("ROOT",))

    def _index_readiness_error(self, *, refresh_inventory: bool = False) -> str | None:
        if not _is_complete_index(self.index_dir):
            return f"Skill index is missing or incomplete: {self.index_dir}"
        records = self._current_records(refresh=refresh_inventory)
        if not records:
            return None
        expected = _index_fingerprint(records, self._build_config)
        state = self._read_state()
        actual = str(state.get("fingerprint") or "")
        if actual != expected:
            return "Skill index is stale because skills or build settings changed."
        return None

    def _render_index_unavailable_prompt(self, reason: str, *, language: str) -> str:
        key = "zh" if language.startswith("zh") else "en"
        return get_prompt(AGENTIC_RETRIEVAL_YAML, "index_unavailable", key).format(
            build_tool=self._retrieval_config.index_build_tool_name,
            reason=_index_unavailable_text(reason, language=language),
        )

    def _clear_loaded_index(self) -> None:
        self._loaded_index = None
        self._filtered_root = None
        self._node_by_id = {}
        self._path_by_id = {}
        self._stats_by_id = {}
        self._catalog_by_payload = {}

    def _index_nodes(self, node: RetrieverNode, path: tuple[str, ...]) -> None:
        self._node_by_id[node.node_id] = node
        self._path_by_id[node.node_id] = path
        for child in node.children:
            self._index_nodes(child, (*path, child.node_id))

    def _resolve_nodes(self, node_ids: Sequence[str], *, default_root: bool) -> tuple[list[RetrieverNode], str]:
        normalized = [str(item or "").strip() for item in (node_ids or []) if str(item or "").strip()]
        if not normalized and default_root:
            normalized = ["ROOT"]
        if not normalized:
            return [], "No branch node ids were provided."
        nodes: list[RetrieverNode] = []
        missing: list[str] = []
        for node_id in normalized:
            node = self._node_by_id.get(node_id)
            if node is None:
                missing.append(node_id)
            else:
                nodes.append(node)
        if missing:
            return [], f"Unknown skill tree branch id(s): {', '.join(missing)}."
        return nodes, ""

    def _node_stats(self, node: RetrieverNode) -> dict[str, int]:
        cached = self._stats_by_id.get(node.node_id)
        if cached is not None:
            return cached
        branch_count = 0
        skill_count = len(node.items)
        for child in node.children:
            branch_count += 1
            child_stats = self._node_stats(child)
            branch_count += child_stats["branch_count"]
            skill_count += child_stats["skill_count"]
        stats = {"branch_count": branch_count, "skill_count": skill_count}
        self._stats_by_id[node.node_id] = stats
        return stats

    def _render_peek_node(self, lines: list[str], node: RetrieverNode) -> None:
        lines.append(f"## Input Node: `{node.node_id}`")
        children = list(node.children)
        if not children:
            lines.append("")
            lines.append("No child branches.")
            return
        lines.append("")
        for child in children:
            stats = self._node_stats(child)
            description = _compact_text(child.description, limit=140)
            suffix = f" - {description}" if description else ""
            lines.append(f"- `{child.node_id}`{suffix} ({stats['skill_count']} skills)")

    def _render_explore_fragment(
        self,
        lines: list[str],
        *,
        node: RetrieverNode,
        fragment: Any,
        candidates: list[dict[str, Any]],
    ) -> None:
        lines.append(f"## Input Node: `{node.node_id}`")
        lines.append("")
        resolutions = {
            str(resolution.canonical_id): resolution
            for resolution in fragment.code_to_resolution.values()
            if getattr(resolution, "item", None) is not None
        }
        children = list(getattr(fragment.root, "children", ()) or ())
        if not children:
            lines.append("No exposed branches or visible skills.")
            return
        self._render_exposed_children(lines, children, resolutions=resolutions, level=3, candidates=candidates)

    def _render_exposed_children(
        self,
        lines: list[str],
        children: Sequence[Any],
        *,
        resolutions: dict[str, Any],
        level: int,
        candidates: list[dict[str, Any]],
    ) -> None:
        terminal_children = [child for child in children if _terminal_resolution(child, resolutions) is not None]
        branch_children = [child for child in children if _terminal_resolution(child, resolutions) is None]
        if terminal_children:
            for index, child in enumerate(terminal_children, start=1):
                resolution = _terminal_resolution(child, resolutions)
                entry = self._skill_entry_from_exposed(child, resolution)
                candidates.append(asdict(entry))
                lines.append(f"{index}. `{entry.label}`")
                if entry.description:
                    lines.append(f"   - Description: {entry.description}")
                if entry.skill_md_path:
                    lines.append(f"   - SKILL.md: `{entry.skill_md_path}`")
            if branch_children:
                lines.append("")
        for child in branch_children:
            node_id = str(getattr(child, "canonical_id", "") or getattr(child, "label", "") or "").strip()
            title = _compact_text(str(getattr(child, "label", "") or node_id), limit=80)
            lines.append(f"{'#' * max(3, level)} `{node_id}` {title}".rstrip())
            description = _compact_text(str(getattr(child, "description", "") or ""), limit=180)
            if description:
                lines.append("")
                lines.append(description)
            grandchildren = list(getattr(child, "children", ()) or ())
            if grandchildren:
                lines.append("")
                self._render_exposed_children(
                    lines,
                    grandchildren,
                    resolutions=resolutions,
                    level=level + 1,
                    candidates=candidates,
                )
                lines.append("")

    def _skill_entry_from_exposed(self, child: Any, resolution: Any | None) -> "_SkillEntry":
        item = getattr(resolution, "item", None)
        payload = str(getattr(item, "payload", "") or getattr(child, "canonical_id", "") or "").strip()
        record = self._catalog_by_payload.get(payload)
        metadata = dict(getattr(record, "metadata", {}) or {}) if record else {}
        label = str(
            (getattr(record, "name", "") if record else "")
            or getattr(item, "label", "")
            or getattr(child, "label", "")
            or payload
        ).strip()
        description = str(
            (getattr(record, "description", "") if record else "")
            or getattr(item, "description", "")
            or getattr(child, "description", "")
            or ""
        ).strip()
        skill_md_path = str(metadata.get("skill_path") or "").strip()
        return _SkillEntry(label=label, description=_first_description_line(description), skill_md_path=skill_md_path)

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
            "started_at": str(previous_build.get("started_at") or _now_iso()),
            "updated_at": _now_iso(),
            "capability_category_paths": [],
            "logs": logs[-40:],
        }
        state.update(
            {
                "build": build,
                "fingerprint": fingerprint,
                "indexed_count": int(indexed_count),
                "updated_at": _now_iso(),
            }
        )
        self._write_state(state)

    def _cancelled_result(self, *, build_id: str, started: float, fingerprint: str) -> dict[str, Any]:
        state = _build_state(
            status="cancelled",
            stage="cancelled",
            message="Skill index build was cancelled.",
            progress=1.0,
            build_id=build_id,
            force=False,
            indexed_count=0,
            fingerprint=fingerprint,
            finished=True,
            elapsed_seconds=time.monotonic() - started,
        )
        self._write_state(state)
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
        self._write_state(state)
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

    def _write_state(self, state: dict[str, Any]) -> None:
        self.index_root.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload["updated_at"] = _now_iso()
        tmp = self.index_root / f".{STATE_FILENAME}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_root / STATE_FILENAME)

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


@dataclass(frozen=True)
class _SkillEntry:
    label: str
    description: str
    skill_md_path: str


def scan_skill_records(skills_dir: str | Path) -> list[SkillRecord]:
    root = Path(skills_dir).expanduser().resolve()
    records: list[SkillRecord] = []
    if not root.exists():
        return records
    candidates = [path for path in root.iterdir() if path.is_dir()]
    for skill_dir in sorted(candidates, key=lambda item: item.name):
        skill_file = _find_skill_file(skill_dir)
        if skill_file is None:
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        frontmatter, body = _parse_frontmatter(content)
        name = str(frontmatter.get("name") or skill_dir.name).strip() or skill_dir.name
        description = str(frontmatter.get("description") or "").strip() or _first_paragraph(body)
        records.append(
            SkillRecord(
                name=name,
                worker_id=skill_dir.name,
                description=description,
                skill_md_path=str(skill_file),
                enabled=True,
                metadata=dict(frontmatter),
                content=body.strip(),
                content_hash=_sha256_text(content),
            )
        )
    return records


def _run_index_builder(
    *,
    operation: str,
    item_jsonl_path: Path,
    output_dir: Path,
    base_index_dir: Path,
    config: BuildConfig,
) -> None:
    if operation == "add":
        IndexBuilder.add(
            item_jsonl_path=str(item_jsonl_path),
            base_index_dir=base_index_dir,
            output_dir=output_dir,
            item_type="skill",
            config=config,
        )
        return
    if operation == "delete":
        IndexBuilder.delete(
            item_jsonl_path=str(item_jsonl_path),
            base_index_dir=base_index_dir,
            output_dir=output_dir,
            item_type="skill",
            config=config,
        )
        return
    IndexBuilder.build(
        item_paths=[],
        item_jsonl_path=str(item_jsonl_path),
        output_dir=output_dir,
        item_type="skill",
        config=config,
    )


def _to_retrieval_build_config(config: SkillIndexBuildConfig, llm: LLMConfig) -> BuildConfig:
    return BuildConfig(
        llm_openai_client=llm.client,
        llm_model=llm.model,
        llm_api_key=llm.api_key,
        llm_base_url=llm.base_url,
        llm_seed=llm.seed,
        tree_branching_factor=config.branching_factor,
        tree_max_depth=config.max_depth,
        tree_root_categories=config.root_categories,
        tree_max_workers=config.max_workers,
        tree_caching=config.caching,
        tree_num_retries=config.max_retries,
        tree_timeout_seconds=config.request_timeout_seconds,
        tree_classify_batch_cap=config.classification_batch_limit,
        tree_context_window=config.context_window,
        tree_max_output_tokens=config.max_output_tokens,
        tree_postprocess_enabled=config.postprocess_enabled,
        tree_postprocess_max_passes=config.postprocess_max_passes,
        tree_postprocess_min_skills=config.postprocess_min_skills,
        tree_equiv_grouping_enabled=config.equivalence_enabled,
        tree_equiv_max_groups_per_parent=config.equivalence_max_groups_per_parent,
        tree_equiv_allow_singleton_groups=config.equivalence_allow_singleton_groups,
        tree_equiv_min_lexical_similarity=config.equivalence_min_lexical_similarity,
        tree_deterministic_prompts=config.deterministic_prompts,
        tree_discovery_seed=config.discovery_seed,
        tree_prompt_fingerprint_version=config.prompt_fingerprint_version,
        tree_cache_observability=config.cache_observability,
        tree_skill_profiles_enabled=config.skill_profiles_enabled,
        tree_skill_profile_select_rules_enabled=config.skill_profile_select_rules_enabled,
        tree_skill_profile_batch_size=config.skill_profile_batch_size,
        tree_skill_profile_description_limit=config.skill_profile_description_limit,
        tree_skill_profile_rule_limit=config.skill_profile_rule_limit,
        incremental_max_change_ratio=config.incremental_max_change_ratio,
        incremental_min_add_confidence=config.incremental_min_add_confidence,
        incremental_min_add_confidence_margin=config.incremental_min_add_confidence_margin,
        incremental_branch_imbalance_ratio=config.incremental_branch_imbalance_ratio,
        generate_tree_html=config.generate_tree_html,
        allow_fallback_tree=not config.strict_failure,
    )


def _progressive_config(config: AgenticRetrievalConfig) -> ProgressiveRetrieverConfig:
    return ProgressiveRetrieverConfig(
        top_k=max(1, int(config.top_k)),
        max_tokens=max(1, int(config.max_tokens)),
        request_timeout=config.request_timeout_seconds,
        compact_boundary_codes_enabled=bool(config.compact_codes_enabled),
        flatten_full_tree_in_prompt=bool(config.flatten_tree),
        max_exposure_depth_per_call=max(0, int(config.max_exposure_depth)),
        exposure_threshold=max(0, int(config.exposure_threshold)),
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


def _filter_tree(
    root: RetrieverNode,
    catalog_by_payload: dict[str, CatalogRecord],
    visible_skill_names: frozenset[str] | None,
) -> RetrieverNode:
    if visible_skill_names is None:
        return root

    def item_visible(item: RetrieverItem) -> bool:
        record = catalog_by_payload.get(str(item.payload))
        values = {
            str(item.item_id or "").strip(),
            str(item.label or "").strip(),
            str(item.payload or "").strip(),
        }
        if record:
            values.update(
                {
                    str(record.worker_id or "").strip(),
                    str(record.name or "").strip(),
                    str(record.choice_id or "").strip(),
                    str(record.payload or "").strip(),
                }
            )
        return any(value in visible_skill_names for value in values if value)

    def visit(node: RetrieverNode) -> RetrieverNode | None:
        items = tuple(item for item in node.items if item_visible(item))
        children = tuple(child for child in (visit(child) for child in node.children) if child is not None)
        if node.node_id == "ROOT" or items or children:
            return RetrieverNode(
                node_id=node.node_id,
                label=node.label,
                description=node.description,
                children=children,
                items=items,
            )
        return None

    return visit(root) or RetrieverNode(node_id="ROOT", label="ROOT")


def _terminal_resolution(child: Any, resolutions: dict[str, Any]) -> Any | None:
    selectable = str(getattr(child, "selectable_canonical_id", "") or "").strip()
    if not selectable:
        selectable = str(getattr(child, "canonical_id", "") or "").strip()
    resolution = resolutions.get(selectable)
    if resolution is not None:
        return resolution
    for candidate in resolutions.values():
        item = getattr(candidate, "item", None)
        if item is not None and str(getattr(item, "payload", "") or "") == selectable:
            return candidate
    return None


def _step_payload(kind: str, node: RetrieverNode) -> dict[str, Any]:
    return {
        "source": kind,
        "node_id": node.node_id,
        "label": node.label,
        "description": _compact_text(node.description, limit=180),
    }


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
    finished: bool = False,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "fingerprint": fingerprint,
        "indexed_count": int(indexed_count),
        "record_hashes": dict(record_hashes or {}),
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
        state["indexed_count"] = int(previous_state.get("indexed_count") or 0)
    else:
        state["fingerprint"] = ""
        state["indexed_count"] = 0
    state["attempted_fingerprint"] = attempted_fingerprint
    build = dict(state.get("build") or {})
    build["attempted_fingerprint"] = attempted_fingerprint
    state["build"] = build


def _publish_index(*, candidate_dir: Path, index_dir: Path) -> None:
    parent = index_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    backup = parent / f".{index_dir.name}.backup-{time.time_ns()}"
    if index_dir.exists():
        index_dir.replace(backup)
    shutil.copytree(candidate_dir, index_dir)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _cleanup_index(index_dir: Path) -> None:
    shutil.rmtree(index_dir, ignore_errors=True)


def _is_complete_index(index_dir: Path) -> bool:
    return all(
        (index_dir / filename).exists() for filename in (TREE_INDEX_FILENAME, CATALOG_FILENAME, MANIFEST_FILENAME)
    )


def _index_fingerprint(records: Sequence[SkillRecord], config: SkillIndexBuildConfig) -> str:
    payload = {
        "records": _record_hashes(records),
        "config": asdict(config),
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
    if record.content_hash:
        return str(record.content_hash)
    payload = {
        "name": record.name,
        "description": record.description,
        "worker_id": record.resolved_worker_id,
        "skill_md_path": record.skill_md_path,
        "content": record.content,
        "metadata": record.metadata,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _find_skill_file(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md", "Skill.md"):
        path = skill_dir / name
        if path.exists():
            return path
    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    text = str(content or "")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip()
    body_start = end + 4
    body = text[body_start:].lstrip()
    try:
        import yaml

        parsed = yaml.safe_load(raw) or {}
        return (parsed if isinstance(parsed, dict) else {}), body
    except Exception:
        return {}, body


def _first_paragraph(text: str) -> str:
    paragraph: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped and paragraph:
            break
        if stripped:
            paragraph.append(stripped)
    return " ".join(paragraph)


def _first_description_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("Select when:") and not stripped.startswith("Don't select when:"):
            return stripped
    return ""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _normalize_visible_skill_names(values: Iterable[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip())


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
    return f"build-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{time.time_ns()}"
