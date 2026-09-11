"""Public build, artifact, and online planning service."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence
from uuid import uuid4

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.models import CapabilityFingerprint, SourceSnapshot
from openjiuwen.symphony.orchestration.artifacts import (
    SCHEMA_VERSION,
    GraphArtifactStore,
    filter_disabled_graph_artifacts,
    load_graph_artifacts,
)
from openjiuwen.symphony.orchestration.config import OrchestrationConfig
from openjiuwen.symphony.orchestration.contracts import (
    CapabilityGraph,
    GraphArtifactStatus,
    GraphBuildResult,
    GraphMutationResult,
    OrchestrationPlan,
    OrchestrationProgress,
)
from openjiuwen.symphony.orchestration.planned_graph import build_planned_graph
from openjiuwen.symphony.orchestration.graph.build import GraphBuildPipeline
from openjiuwen.symphony.orchestration.graph.candidates import CandidateGenerator
from openjiuwen.symphony.orchestration.graph.matcher.cache import (
    CACHE_INDEX_SCHEMA,
    CACHE_RECORD_SCHEMA,
    sanitize_matcher_metadata,
)
from openjiuwen.symphony.orchestration.graph.matcher.ontology import (
    DEFAULT_PROMPT_VERSION,
    MATCH_SCHEMA_VERSION,
    MATCHER_VERSION,
    OntologyMatcher,
)
from openjiuwen.symphony.orchestration.language import resolve_orchestration_language
from openjiuwen.symphony.orchestration.model import ModelResponseObserver
from openjiuwen.symphony.orchestration.mutation_service import GraphMutationCoordinator
from openjiuwen.symphony.orchestration.mutations import (
    MutationJournal,
    MutationOperation,
    load_inventory,
    load_sync_inventory,
)
from openjiuwen.symphony.orchestration.mutations import (
    build_protocol_hash as _build_protocol_hash,
)
from openjiuwen.symphony.orchestration.mutations import (
    capability_identity as _capability_identity,
)
from openjiuwen.symphony.orchestration.mutations import (
    fingerprint_content_hash as _fingerprint_content_hash,
)
from openjiuwen.symphony.orchestration.mutations import (
    run_prepare_artifact as _run_prepare_artifact,
)
from openjiuwen.symphony.orchestration.mutations import (
    stable_sha256 as _stable_sha256,
)
from openjiuwen.symphony.orchestration.planning.beam import BidirectionalBeamPlanner
from openjiuwen.symphony.orchestration.planning.fast import FastOneShotPlanner
from openjiuwen.symphony.shared.fingerprint import Fingerprint, FingerprintService, coerce_fingerprint

if TYPE_CHECKING:
    from openjiuwen.symphony.interfaces.capability import CapabilityProvider

ProgressCallback = Callable[[OrchestrationProgress], Any]
PrepareArtifactHook = Callable[[Path], Any]
LOGGER = logging.getLogger(__name__)


class OrchestrationService:
    """Own the capability graph lifecycle and produce online execution plans."""

    def __init__(
        self,
        *,
        graph_artifact_root: str | Path,
        capability_provider: CapabilityProvider
        | Sequence[Any]
        | Callable[[], Sequence[Any] | Awaitable[Sequence[Any]]],
        model: Model | None,
        model_response_observer: ModelResponseObserver | None = None,
        config: OrchestrationConfig | None = None,
        source_snapshot: dict[str, Any] | Callable[[Sequence[Fingerprint]], dict[str, Any]] | None = None,
        graph_config: dict[str, Any] | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
        fingerprint_service: FingerprintService | None = None,
    ) -> None:
        self.graph_artifact_root = Path(graph_artifact_root)
        self.capability_provider = capability_provider
        self.model = model
        self.model_response_observer = model_response_observer
        self.config = config or OrchestrationConfig()
        self.source_snapshot = source_snapshot
        self.graph_config = dict(graph_config or {})
        self.prepare_artifact = prepare_artifact
        self.fingerprint_service = fingerprint_service
        self._store = GraphArtifactStore(self.graph_artifact_root)
        self._mutation_journal = MutationJournal(self._store)
        self._build_task: asyncio.Task[Any] | None = None
        self._write_lock = asyncio.Lock()

    def status(self, *, expected_snapshot: dict[str, Any] | None = None) -> GraphArtifactStatus:
        if expected_snapshot is None:
            capabilities = self._sync_capabilities()
            snapshot = self._source_snapshot(capabilities, self._matcher_identity())
        else:
            snapshot = self._merge_build_identity(expected_snapshot, self._matcher_identity())
        return self._store.status(
            expected_snapshot=snapshot,
            building=self._build_task is not None and not self._build_task.done(),
        )

    def read(self, version: str | None = None) -> CapabilityGraph:
        return CapabilityGraph(self._store.read(version))

    async def build(
        self,
        force: bool = False,
        *,
        progress: ProgressCallback | None = None,
        progress_callback: ProgressCallback | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
    ) -> GraphBuildResult:
        callback = _resolve_progress_callback(progress, progress_callback)
        progress_dispatcher = _BuildProgressDispatcher(callback)
        current = asyncio.current_task()
        if self._build_task is not None and not self._build_task.done() and self._build_task is not current:
            raise RuntimeError("A Symphony graph build is already running.")
        self._build_task = current
        try:
            async with self._write_lock:
                if self.model is None:
                    raise ValueError("Graph build requires a model for internal ontology matching.")
                expected_version = self._store.current_version()
                capabilities, provider_snapshot, _canonical = await self._load_inventory(force_fingerprint=force)
                matcher = self._create_matcher(capabilities, force=force)
                snapshot = self._source_snapshot(
                    capabilities,
                    matcher.identity_metadata(),
                    provider_snapshot=provider_snapshot,
                )
                if not force:
                    try:
                        existing = self._store.read()
                        if existing.get("source_snapshot") == snapshot:
                            status = self._store.status()
                            return GraphBuildResult(
                                version=str(status.version),
                                graph_path=self.graph_artifact_root / "versions" / str(status.version) / "graph.json",
                                generated_at=str(status.generated_at),
                            )
                    except FileNotFoundError:
                        pass
                progress_dispatcher.start()
                await progress_dispatcher.emit("build_started", capability_count=len(capabilities))
                payload, _diagnostics = await self._construct_graph_payload(
                    capabilities,
                    matcher=matcher,
                    snapshot=snapshot,
                    provider_snapshot=provider_snapshot,
                    progress_dispatcher=progress_dispatcher,
                )
                version = _version_id(str(payload["generated_at"]), snapshot)
                staged = await asyncio.to_thread(self._store.stage, payload, version=version)
                artifact_hook = prepare_artifact if prepare_artifact is not None else self.prepare_artifact
                await _run_prepare_artifact(artifact_hook, staged.graph_path.parent)
                await asyncio.sleep(0)
                published = self._store.activate_if_current(staged, expected_version=expected_version)
                if self._build_task is current:
                    self._build_task = None
                try:
                    await progress_dispatcher.emit("build_published", version=version)
                    await progress_dispatcher.close()
                except asyncio.CancelledError:
                    await progress_dispatcher.close()
                return published
        except asyncio.CancelledError:
            await progress_dispatcher.cancel()
            await progress_dispatcher.deliver("build_cancelled")
            raise
        except Exception as exc:
            await progress_dispatcher.drain()
            await progress_dispatcher.emit("build_failed", error=str(exc))
            await progress_dispatcher.close()
            raise
        finally:
            await progress_dispatcher.cancel()
            if self._build_task is current:
                self._build_task = None

    async def apply_skill_graph_mutation(
        self,
        operation: MutationOperation,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        """Apply the low-level mutation requested through ``SymphonyGraphEngine``."""

        coordinator = GraphMutationCoordinator(
            store=self._store,
            journal=self._mutation_journal,
            write_lock=self._write_lock,
            model_available=self.model is not None,
            inventory_loader=lambda: self._load_inventory(require_atomic=True),
            matcher_factory=self._create_matcher,
            snapshot_factory=self._source_snapshot,
            graph_builder=self._construct_graph_payload,
            prepare_artifact=self.prepare_artifact,
        )
        return await coordinator.mutate(
            operation,
            changed_skill_ids,
            request_id=request_id,
            source_revision=source_revision,
        )

    async def cancel_build(self) -> GraphArtifactStatus:
        task = self._build_task
        if task is not None and not task.done():
            task.cancel()
        return self._store.status(building=task is not None and not task.done())

    async def plan(
        self,
        query: str,
        candidate_ids: Sequence[str] | None = None,
        *,
        language: str = "cn",
        progress: ProgressCallback | None = None,
        disabled_capability_ids: Sequence[str] | None = None,
        dynamic_overlay: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        mode: str | None = None,
    ) -> OrchestrationPlan:
        callback = _resolve_progress_callback(progress, progress_callback)
        if self.model is None:
            raise ValueError("Symphony planning requires a model.")
        await _emit(callback, "plan_started", query=query)
        artifacts = filter_disabled_graph_artifacts(
            load_graph_artifacts(self.graph_artifact_root),
            disabled_capability_ids,
        )
        planning_mode = mode or self.config.mode
        if planning_mode not in {"fast", "beam"}:
            raise ValueError(f"Unsupported orchestration mode: {planning_mode}")
        normalized_language = resolve_orchestration_language(language)
        selected, _summary = _input_candidate_summary(candidate_ids, artifacts.skills)
        effective_overlay = dynamic_overlay if self.config.dynamic_graph_enabled and dynamic_overlay else {}
        planner: Any
        if planning_mode == "beam":
            planner = BidirectionalBeamPlanner(
                artifacts,
                model=self.model,
                model_response_observer=self.model_response_observer,
                min_edge_confidence=self.config.min_edge_confidence,
                top_k=self.config.top_k,
                max_depth=self.config.max_depth,
                candidate_skill_ids=selected,
                progress_callback=_planner_progress_callback(callback),
                language=normalized_language,
            )
        else:
            planner = FastOneShotPlanner(
                artifacts,
                model=self.model,
                model_response_observer=self.model_response_observer,
                min_edge_confidence=self.config.min_edge_confidence,
                max_depth=self.config.max_depth,
                candidate_skill_ids=selected,
                language=normalized_language,
                dynamic_overlay=effective_overlay,
            )
        result = await planner.plan(query)
        if result.get("success") is False:
            detail = str(result.get("detail") or "Symphony planner failed to produce a valid plan.").strip()
            raise ValueError(f"Symphony planning failed: {detail}")
        result["plan_id"] = str(uuid4())
        public_result = OrchestrationPlan({"planned_graph": build_planned_graph(result, artifacts)})
        await _emit(callback, "plan_completed", plan_id=result["plan_id"])
        return public_result

    async def _construct_graph_payload(
        self,
        capabilities: Sequence[Fingerprint],
        *,
        matcher: OntologyMatcher,
        snapshot: dict[str, Any],
        provider_snapshot: SourceSnapshot | None,
        progress_dispatcher: _BuildProgressDispatcher | None = None,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        def graph_progress(stage: str, **details: Any) -> None:
            if progress_dispatcher is not None:
                progress_dispatcher.enqueue(stage, **details)

        def matcher_progress(event: str, current: int, total: int, details: dict[str, Any]) -> None:
            candidate_progress = {}
            for key in (
                "completed_candidate_count",
                "total_candidate_count",
                "reused_candidate_count",
            ):
                if key in details:
                    candidate_progress[key] = details[key]
            graph_progress(
                "graph.resolve.progress",
                matcher_event=event,
                current=current,
                total=total,
                details=details,
                **candidate_progress,
            )

        matcher.progress = matcher_progress
        candidate_generator = CandidateGenerator(
            max_candidates_per_skill_relation=matcher.graph_config["max_candidates_per_skill_relation"],
            max_port_mappings_per_candidate=matcher.graph_config["max_port_mappings_per_candidate"],
            max_exact_io_pair_fanout=matcher.graph_config["max_exact_io_pair_fanout"],
        )
        build_result = await GraphBuildPipeline(
            resolver=matcher,
            candidate_generator=candidate_generator,
        ).build(
            capabilities,
            progress=graph_progress,
        )
        if progress_dispatcher is not None:
            await progress_dispatcher.drain()
        diagnostics = tuple(item.to_dict() for item in build_result.diagnostics)
        generated_at = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "source_snapshot": snapshot,
            "capabilities": [item.to_dict() for item in build_result.skills],
            "nodes": [item.to_dict() for item in build_result.graph.nodes],
            "edges": [item.to_dict() for item in build_result.graph.edges],
            "lookup": build_result.lookup.to_dict(),
            "diagnostics": list(diagnostics),
            "capability_hashes": {
                _capability_identity(item.type, item.id): _fingerprint_content_hash(item) for item in capabilities
            },
            "graph_identity_hashes": {
                _capability_identity(item.type, item.id): _stable_sha256(item.graph_identity_dict())
                for item in capabilities
            },
            "build_protocol_hash": _build_protocol_hash(snapshot),
            "config": {
                "orchestration": self.config.to_dict(),
                "graph": matcher.graph_config,
                "thresholds": build_result.manifest.thresholds,
                "candidate_generation": build_result.manifest.candidate_generation,
                "llm": build_result.manifest.llm,
            },
        }
        if provider_snapshot is not None:
            payload["provider_source_snapshot"] = provider_snapshot.model_dump(mode="json", exclude_none=True)
        return payload, diagnostics

    async def _load_inventory(
        self,
        *,
        require_atomic: bool = False,
        force_fingerprint: bool = False,
    ) -> tuple[list[Fingerprint], SourceSnapshot | None, list[CapabilityFingerprint] | None]:
        if self.fingerprint_service is not None:
            artifact = await self.fingerprint_service.build(force=force_fingerprint)
            fingerprints = list(artifact.fingerprints)
            return (
                [coerce_fingerprint(item) for item in fingerprints],
                artifact.source_snapshot,
                fingerprints,
            )
        return await load_inventory(
            self.capability_provider,
            require_atomic=require_atomic,
            current_version=self._store.current_version(),
        )

    def _sync_capabilities(self) -> list[Fingerprint]:
        return load_sync_inventory(self.capability_provider)

    def _source_snapshot(
        self,
        capabilities: Sequence[Fingerprint],
        matcher_identity: dict[str, Any],
        *,
        provider_snapshot: SourceSnapshot | None = None,
    ) -> dict[str, Any]:
        if provider_snapshot is not None:
            supplemental: dict[str, Any] = {}
            if callable(self.source_snapshot):
                supplemental = dict(self.source_snapshot(capabilities))
            elif isinstance(self.source_snapshot, dict):
                supplemental = dict(self.source_snapshot)
            base = {
                **supplemental,
                **provider_snapshot.model_dump(mode="json", exclude_none=True),
            }
        elif callable(self.source_snapshot):
            base = dict(self.source_snapshot(capabilities))
        elif isinstance(self.source_snapshot, dict):
            base = dict(self.source_snapshot)
        else:
            canonical = json.dumps(
                [item.to_internal_dict() for item in sorted(capabilities, key=lambda item: item.id)],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            base = {
                "capability_count": len(capabilities),
                "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            }
        return self._merge_build_identity(base, matcher_identity)

    def _merge_build_identity(
        self,
        snapshot: dict[str, Any],
        matcher_identity: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_graph_config = _normalized_graph_config(self.graph_config)
        candidate_generation = {
            "max_candidates_per_skill_relation": normalized_graph_config.get("max_candidates_per_skill_relation"),
            "max_port_mappings_per_candidate": normalized_graph_config.get("max_port_mappings_per_candidate"),
            "max_exact_io_pair_fanout": normalized_graph_config.get("max_exact_io_pair_fanout"),
        }
        return sanitize_matcher_metadata(
            {
                **snapshot,
                "symphony_graph_build": {
                    "matcher": matcher_identity,
                    "candidate_generation": candidate_generation,
                },
            }
        )

    def _matcher_identity(self) -> dict[str, Any]:
        if self.model is None:
            current_identity = {
                "matcher_version": MATCHER_VERSION,
                "prompt_version": DEFAULT_PROMPT_VERSION,
                "match_schema_version": MATCH_SCHEMA_VERSION,
                "cache_record_schema_version": CACHE_RECORD_SCHEMA,
                "cache_index_schema_version": CACHE_INDEX_SCHEMA,
                "graph_config": _normalized_graph_config(self.graph_config),
                "batch_size": _positive_graph_int(self.graph_config, "batch_size", 12),
                "max_workers": _graph_workers(self.graph_config),
                "require_consensus": _graph_bool(self.graph_config, "require_consensus", True),
                "consensus_runs": 2 if _graph_bool(self.graph_config, "require_consensus", True) else 1,
                "thresholds": {"can_feed": self.config.min_edge_confidence},
            }
            try:
                source_snapshot = self._store.read().get("source_snapshot", {})
            except FileNotFoundError:
                source_snapshot = {}
            build_identity = source_snapshot.get("symphony_graph_build", {})
            stored_identity = build_identity.get("matcher", {}) if isinstance(build_identity, dict) else {}
            identity = dict(stored_identity) if isinstance(stored_identity, dict) else {}
            identity.update(current_identity)
            identity.setdefault("model", None)
            identity.setdefault("backend", None)
            identity.setdefault("temperature", None)
            return identity
        return self._create_matcher([], force=True).identity_metadata()

    def _create_matcher(self, capabilities: Sequence[Fingerprint], *, force: bool) -> OntologyMatcher:
        if self.model is None:
            raise ValueError("Graph build requires a model for internal ontology matching.")
        normalized_graph_config = _normalized_graph_config(self.graph_config)
        return OntologyMatcher(
            self.model,
            model_response_observer=self.model_response_observer,
            fingerprints=capabilities,
            cache_path=None if force else self.graph_artifact_root / "cache" / "relation_matches.json",
            batch_size=normalized_graph_config["batch_size"],
            max_workers=normalized_graph_config["max_workers"],
            require_consensus=normalized_graph_config["require_consensus"],
            graph_config=normalized_graph_config,
            thresholds={"can_feed": self.config.min_edge_confidence},
        )


def _input_candidate_summary(
    values: Sequence[str] | None,
    capabilities: Sequence[dict[str, Any]],
) -> tuple[tuple[str, ...] | None, dict[str, Any]]:
    normalized = tuple(dict.fromkeys(str(item or "").strip() for item in values or [] if str(item or "").strip()))
    known = {str(item.get("id") or "") for item in capabilities}
    by_name: dict[str, list[str]] = {}
    for item in capabilities:
        by_name.setdefault(str(item.get("name") or ""), []).append(str(item.get("id") or ""))
    selected = []
    for value in normalized:
        resolved = value if value in known else ""
        matching_ids = by_name.get(value, [])
        if not resolved and len(matching_ids) == 1:
            resolved = matching_ids[0]
        if resolved and resolved not in selected:
            selected.append(resolved)
    fallback_reason = ""
    if values is None:
        fallback_reason = "candidate_ids not provided"
    elif not normalized:
        fallback_reason = "candidate_ids is empty"
    elif not selected:
        fallback_reason = "candidate_ids did not match current graph"
    return tuple(selected) or None, {
        "source": "input",
        "used": bool(selected),
        "candidate_ids": selected,
        "candidate_count": len(selected),
        "fallback_reason": fallback_reason,
    }


def _resolve_progress_callback(
    progress: ProgressCallback | None,
    progress_callback: ProgressCallback | None,
) -> ProgressCallback | None:
    if progress is not None and progress_callback is not None and progress is not progress_callback:
        raise ValueError("Pass either progress or progress_callback, not both.")
    return progress if progress is not None else progress_callback


def _planner_progress_callback(
    callback: ProgressCallback | None,
) -> Callable[[dict[str, Any]], Any] | None:
    if callback is None:
        return None

    def emit(item: dict[str, Any]) -> Any:
        details = {key: value for key, value in item.items() if key != "event"}
        return callback(OrchestrationProgress(str(item.get("event") or "plan_progress"), **details))

    return emit


def _positive_graph_int(config: dict[str, Any], key: str, default: int) -> int:
    raw = config.get(key, default)
    if isinstance(raw, bool):
        raise TypeError(f"graph_config[{key!r}] must be a positive integer.")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"graph_config[{key!r}] must be a positive integer.") from exc
    if value < 1:
        raise ValueError(f"graph_config[{key!r}] must be a positive integer.")
    return value


def _graph_workers(config: dict[str, Any]) -> int:
    key = "max_workers" if "max_workers" in config else "workers"
    return _positive_graph_int(config, key, 1)


def _graph_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"graph_config[{key!r}] must be a boolean.")
    return value


def _normalized_graph_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_size": _positive_graph_int(config, "batch_size", 12),
        "max_workers": _graph_workers(config),
        "require_consensus": _graph_bool(config, "require_consensus", True),
        "max_candidates_per_skill_relation": _positive_graph_int(config, "max_candidates_per_skill_relation", 32),
        "max_port_mappings_per_candidate": _positive_graph_int(config, "max_port_mappings_per_candidate", 12),
        "max_exact_io_pair_fanout": _positive_graph_int(config, "max_exact_io_pair_fanout", 64),
    }


def _version_id(generated_at: str, snapshot: dict[str, Any]) -> str:
    stamp = generated_at.replace("+00:00", "Z").replace(":", "").replace("-", "").replace(".", "")
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:10]
    return f"{stamp}-{digest}"


async def _emit(callback: ProgressCallback | None, event: str, **details: Any) -> None:
    if callback is None:
        return
    result = callback(OrchestrationProgress(event, **details))
    if inspect.isawaitable(result):
        await result


@dataclass
class _QueuedBuildProgress:
    item: OrchestrationProgress
    completion: asyncio.Future[None] | None = None


class _BuildProgressDispatcher:
    """Serialize build progress through one build-scoped worker."""

    def __init__(self, callback: ProgressCallback | None) -> None:
        self.callback = callback
        self._queue: asyncio.Queue[_QueuedBuildProgress | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.callback is not None and self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="symphony-build-progress")

    def enqueue(self, event: str, **details: Any) -> None:
        if self.callback is not None:
            self._queue.put_nowait(_QueuedBuildProgress(OrchestrationProgress(event, **details)))

    async def drain(self) -> None:
        if self._worker is not None:
            await self._queue.join()

    async def emit(self, event: str, **details: Any) -> None:
        if self._worker is None:
            return
        completion = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(
            _QueuedBuildProgress(
                OrchestrationProgress(event, **details),
                completion,
            )
        )
        await completion

    async def close(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if not worker.done():
            self._queue.put_nowait(None)
        await asyncio.gather(worker, return_exceptions=True)
        self._worker = None

    async def cancel(self) -> None:
        worker = self._worker
        if worker is not None:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self._worker = None
        while not self._queue.empty():
            queued = self._queue.get_nowait()
            if queued is not None and queued.completion is not None and not queued.completion.done():
                queued.completion.cancel()
            self._queue.task_done()

    async def deliver(self, event: str, **details: Any) -> None:
        await self._deliver(OrchestrationProgress(event, **details))

    async def _run(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                if queued is None:
                    return
                await self._deliver(queued.item)
                if queued.completion is not None and not queued.completion.done():
                    queued.completion.set_result(None)
            except asyncio.CancelledError:
                if queued is not None and queued.completion is not None and not queued.completion.done():
                    queued.completion.cancel()
                raise
            finally:
                self._queue.task_done()

    async def _deliver(self, item: OrchestrationProgress) -> None:
        if self.callback is None:
            return
        try:
            result = self.callback(item)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            LOGGER.warning("Symphony build progress callback cancelled for event %s", item.event)
        except Exception:
            LOGGER.warning("Symphony build progress callback failed for event %s", item.event, exc_info=True)
