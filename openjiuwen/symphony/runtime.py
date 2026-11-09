"""Top-level Symphony runtime composition."""

from __future__ import annotations

import asyncio
import logging
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Sequence, TypeAlias

from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony.flow.engine import SymphonyFlowEngine
from openjiuwen.symphony.flow.models import CombinationCandidate
from openjiuwen.symphony.graph_engine import SymphonyGraphEngine
from openjiuwen.symphony.observation import (
    EvidenceStrength,
    EvolutionGraph,
    FailureDomain,
    GraphEvolutionInput,
    GraphSnapshotRef,
    ObservationReceipt,
    TaskEvidence,
    TaskOutcome,
    TaskOutcomeLabel,
    TraceEvidence,
)
from openjiuwen.symphony.config import SymphonyConfig, FingerprintProtocolConfig
from openjiuwen.symphony.orchestration import OrchestrationService
from openjiuwen.symphony.orchestration.model import model_identity
from openjiuwen.symphony.shared.fingerprint import FingerprintService
from openjiuwen.symphony.inventory import DirectoryInventory
from openjiuwen.symphony.runtime_storage import RuntimeStorage, LAYOUT_SCHEMA
from openjiuwen.symphony.runtime_flow import RuntimeFlow
from openjiuwen.symphony.tree_engine import TreeEngine

CaptureMode: TypeAlias = Literal["agent", "team"]


@dataclass(frozen=True)
class EvolutionSubmitResult:
    """Result of one graph observation and optional flow submission."""

    graph_receipt: ObservationReceipt | None
    new_candidates: tuple[CombinationCandidate, ...] = ()


class SymphonyRuntime:
    """Compose explicitly injected Symphony services for an application runtime."""

    def __init__(self, model: Model | None, config: SymphonyConfig) -> None:
        if not isinstance(config, SymphonyConfig):
            raise TypeError("config must be a SymphonyConfig")
        source = config.capability.require_single_source()
        values = config.model_dump(mode="python")
        capability_root = Path(source.root).expanduser().resolve()
        artifact_root = Path(config.storage.artifact_root).expanduser().resolve()
        if capability_root == artifact_root or capability_root.is_relative_to(artifact_root):
            raise ValueError("artifact_root must not equal or contain capability.sources.0.root")
        values["capability"]["sources"][0]["root"] = str(capability_root)
        values["storage"]["artifact_root"] = str(artifact_root)
        structure = values["retrieval"]["index_build"]["structure"]
        categories = structure["root_categories"]
        if isinstance(categories, str):
            structure["root_categories"] = str(Path(categories).expanduser().resolve())
        self.config = SymphonyConfig.model_validate(values)
        self.model = model
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self._storage = RuntimeStorage(self.config)
        inventory = DirectoryInventory(self.config.capability, self._storage)
        policy = self.config.capability.fingerprint.model_copy(deep=True)
        extraction = policy.extraction.model_copy(update={
            "max_concurrency": min(policy.extraction.max_concurrency, self.config.orchestration.build.max_concurrency),
        })
        policy = policy.model_copy(update={"extraction": extraction})
        identity = model_identity(model) if model is not None and (
            getattr(model, "model_config", None) is not None or getattr(model, "model_client_config", None) is not None
        ) else {}
        identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() if identity else ""
        protocol = FingerprintProtocolConfig(configuration_signature=identity_hash)
        observer = getattr(model, "symphony_response_observer", None)
        fingerprints = FingerprintService(
            inventory, artifact_root / "cache" / "fingerprints", llm=model,
            config=policy, protocol=protocol, model_response_observer=observer,
        )
        policy_hash = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
        service = OrchestrationService(
            graph_artifact_root=artifact_root / "graph", capability_provider=inventory,
            model=model, model_response_observer=observer, config=self.config.orchestration,
            source_snapshot={"runtime_schema": LAYOUT_SCHEMA, "fingerprint_policy_sha256": policy_hash,
                             "fingerprint_model_sha256": identity_hash},
            fingerprint_service=fingerprints,
        )
        self.orchestration = SymphonyGraphEngine(service, storage=self._storage, inventory=inventory)
        self.retrieval = TreeEngine(model, self.config.retrieval, self._storage, inventory)
        self.flow = RuntimeFlow(model, self.config.orchestration, self._storage)
        self._graph_scope_id = self._storage.scope_id

    async def submit_evolution(
        self,
        planned_graph: dict[str, Any] | None,
        execution_graph: dict[str, Any],
        *,
        session_id: str,
        capture_mode: CaptureMode,
    ) -> EvolutionSubmitResult:
        """Submit a complete execution graph, then project successful edges to Flow."""

        if self._closed:
            raise RuntimeError("Symphony runtime is closed")
        if not self.config.orchestration.experience_enabled:
            return EvolutionSubmitResult(None)
        try:
            observation = await self._build_graph_evolution_input(
                planned_graph, execution_graph, session_id=session_id, capture_mode=capture_mode,
            )
            receipt = self.orchestration.submit_observation(observation)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Symphony graph evolution submission failed (%s)", type(exc).__name__,
            )
            return EvolutionSubmitResult(None)
        projected = _successful_execution_graph(execution_graph)
        if projected is None:
            return EvolutionSubmitResult(receipt)
        try:
            candidates = await self.flow.submit(projected)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Symphony flow evolution submission failed (%s)",
                type(exc).__name__,
            )
            return EvolutionSubmitResult(receipt)
        return EvolutionSubmitResult(receipt, tuple(candidates))

    async def _build_graph_evolution_input(
        self,
        planned_graph: dict[str, Any] | None,
        execution_graph: dict[str, Any],
        *,
        session_id: str,
        capture_mode: CaptureMode,
    ) -> GraphEvolutionInput:
        graph = execution_graph.get("graph")
        if not isinstance(graph, dict):
            raise ValueError("execution_graph.graph must be an object")
        evidence_id = str(graph.get("id") or "").strip()
        if not evidence_id:
            raise ValueError("execution_graph.graph.id must be non-empty")
        if capture_mode not in {"agent", "team"}:
            raise ValueError("capture_mode must be agent or team")
        snapshot = _snapshot_from_plan(planned_graph) or _snapshot_from_execution(execution_graph)
        if snapshot is None:
            raise ValueError("invoke-start graph snapshot is required")
        outcome = _task_outcome(execution_graph)
        planned_value = planned_graph.get("graph") if isinstance(planned_graph, dict) else None
        planned_model = EvolutionGraph.model_validate(deepcopy(planned_value)) if planned_value is not None else None
        execution_model = EvolutionGraph.model_validate(deepcopy(graph))
        # Host-resolved member types belong to Flow's artifact eligibility check.
        # Keep the canonical graph observation unchanged; retain the annotation
        # on the original execution graph passed to Flow after this validation.
        for node in execution_model.nodes.values():
            node.metadata.pop("skill_type", None)
        return GraphEvolutionInput(
            evidence_id=evidence_id,
            graph_scope_id=self._graph_scope_id,
            observed_at=datetime.now(timezone.utc),
            graph_snapshot=snapshot,
            trace=TraceEvidence(
                trajectory_id=str(execution_graph.get("trace_id") or evidence_id),
                session_id=session_id,
                capture_mode=capture_mode,
                truncated="truncated_trace" in (execution_graph.get("quality_flags") or ()),
                quality_flags=tuple(str(item) for item in (execution_graph.get("quality_flags") or ())),
            ),
            task=TaskEvidence(
                query=str(execution_graph.get("query") or ""),
                task_cluster_id=None,
                outcome=outcome,
            ),
            planned_graph=planned_model,
            execution_graph=execution_model,
        )

    def capture_graph_snapshot(self) -> dict[str, str | None]:
        """Capture a public graph snapshot for Rail invoke-start injection."""

        current = self.orchestration.get_snapshot(self._graph_scope_id)
        return GraphSnapshotRef(
            static_revision=current.static_revision,
            observation_revision=current.observation_revision,
            merged_revision=current.merged_revision,
        ).model_dump(mode="json")

    async def aclose(self) -> None:
        """Drain owned workers and sessions without closing the injected model."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_owned(), name="symphony-runtime-close")
        await asyncio.shield(self._close_task)

    async def _close_owned(self) -> None:
        names = ("retrieval", "flow", "orchestration")
        results = await asyncio.gather(self.retrieval.aclose(), self.flow.close(), self.orchestration.aclose(),
                                       return_exceptions=True)
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                logging.getLogger(__name__).warning("Symphony %s close failed (%s)", name, type(result).__name__)


def _snapshot_from_plan(planned_graph: dict[str, Any] | None) -> GraphSnapshotRef | None:
    if not isinstance(planned_graph, dict):
        return None
    value = planned_graph.get("graph_snapshot")
    if not isinstance(value, dict):
        return None
    try:
        return GraphSnapshotRef.model_validate(value)
    except ValueError:
        return None


def _snapshot_from_execution(execution_graph: dict[str, Any]) -> GraphSnapshotRef | None:
    value = execution_graph.get("graph_snapshot")
    if not isinstance(value, dict):
        return None
    try:
        return GraphSnapshotRef.model_validate(value)
    except ValueError:
        return None


def _task_outcome(execution_graph: dict[str, Any]) -> TaskOutcome:
    outcome = str(execution_graph.get("outcome") or "").lower()
    if outcome == "success":
        return TaskOutcome(
            label=TaskOutcomeLabel.VERIFIED_SUCCESS,
            evidence_strength=EvidenceStrength.STRONG,
        )
    if outcome == "failed":
        return TaskOutcome(
            label=TaskOutcomeLabel.VERIFIED_FAILURE,
            evidence_strength=EvidenceStrength.STRONG,
            failure_domain=FailureDomain.UNKNOWN,
        )
    return TaskOutcome(
        label=TaskOutcomeLabel.PARTIAL,
        evidence_strength=EvidenceStrength.WEAK,
    )


def _successful_execution_graph(value: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("outcome") != "success":
        return None
    graph = value.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), dict):
        return None
    edges = [
        deepcopy(edge)
        for edge in (graph.get("edges") or ())
        if isinstance(edge, dict) and isinstance(edge.get("metadata"), dict) and edge["metadata"].get("success") is True
    ]
    if not edges:
        return None
    endpoint_ids: set[str] = set()
    for edge in edges:
        for key in ("source", "target"):
            if edge.get(key):
                endpoint_ids.add(str(edge[key]))
    projected = deepcopy(value)
    projected["graph"]["edges"] = edges
    projected["graph"]["nodes"] = {
        node_id: deepcopy(node) for node_id, node in graph["nodes"].items() if str(node_id) in endpoint_ids
    }
    return projected
