"""Top-level Symphony runtime composition."""

from __future__ import annotations

import logging
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
    CapabilityEvidence,
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
from openjiuwen.symphony.orchestration import OrchestrationConfig, OrchestrationService, PrepareArtifactHook
from openjiuwen.symphony.orchestration.model import ModelResponseObserver
from openjiuwen.symphony.shared.fingerprint import Fingerprint, FingerprintService

CaptureMode: TypeAlias = Literal["agent", "team"]


@dataclass(frozen=True)
class EvolutionSubmitResult:
    """Result of one graph observation and optional flow submission."""

    graph_receipt: ObservationReceipt | None
    new_candidates: tuple[CombinationCandidate, ...] = ()


class SymphonyRuntime:
    """Compose explicitly injected Symphony services for an application runtime."""

    def __init__(
        self,
        *,
        graph_artifact_root: str | Path,
        capability_provider: Sequence[Any] | Callable[[], Sequence[Any] | Awaitable[Sequence[Any]]],
        model: Model | None,
        model_response_observer: ModelResponseObserver | None = None,
        orchestration_config: OrchestrationConfig | None = None,
        source_snapshot: dict[str, Any] | Callable[[Sequence[Fingerprint]], dict[str, Any]] | None = None,
        graph_config: dict[str, Any] | None = None,
        prepare_artifact: PrepareArtifactHook | None = None,
        fingerprint_service: FingerprintService | None = None,
        flow_engine: SymphonyFlowEngine | None = None,
        graph_scope_id: str = "default",
    ) -> None:
        orchestration_service = OrchestrationService(
            graph_artifact_root=graph_artifact_root,
            capability_provider=capability_provider,
            model=model,
            model_response_observer=model_response_observer,
            config=orchestration_config,
            source_snapshot=source_snapshot,
            graph_config=graph_config,
            prepare_artifact=prepare_artifact,
            fingerprint_service=fingerprint_service,
        )
        self.graph_engine = SymphonyGraphEngine(orchestration_service)
        self.flow_engine = flow_engine
        self.graph_scope_id = str(graph_scope_id).strip()
        if not self.graph_scope_id:
            raise ValueError("graph_scope_id must be non-empty")
        # Preserve the pre-engine lifecycle/planning entry point only.
        self.orchestration = orchestration_service

    async def submit_evolution(
        self,
        planned_graph: dict[str, Any] | None,
        execution_graph: dict[str, Any],
        *,
        session_id: str,
        capture_mode: CaptureMode,
    ) -> EvolutionSubmitResult:
        """Submit a complete execution graph, then project successful edges to Flow."""

        try:
            observation = await self._build_graph_evolution_input(
                planned_graph,
                execution_graph,
                session_id=session_id,
                capture_mode=capture_mode,
            )
            receipt = self.graph_engine.submit_observation(observation)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Symphony graph evolution submission failed (%s)",
                type(exc).__name__,
            )
            return EvolutionSubmitResult(None)
        projected = _successful_execution_graph(execution_graph)
        if self.flow_engine is None or projected is None:
            return EvolutionSubmitResult(receipt)
        try:
            candidates = await self.flow_engine.submit(projected)
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
        outcome = _task_outcome(execution_graph, evidence_id)
        capabilities = _capability_evidence(graph)
        planned_value = planned_graph.get("graph") if isinstance(planned_graph, dict) else None
        planned_model = EvolutionGraph.model_validate(deepcopy(planned_value)) if planned_value is not None else None
        execution_model = EvolutionGraph.model_validate(_observation_graph(graph))
        return GraphEvolutionInput(
            evidence_id=evidence_id,
            graph_scope_id=self.graph_scope_id,
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
            capabilities=capabilities,
            planned_graph=planned_model,
            execution_graph=execution_model,
        )

    def capture_graph_snapshot(self) -> dict[str, str | None]:
        """Capture a public graph snapshot for Rail invoke-start injection."""

        current = self.graph_engine.get_snapshot(self.graph_scope_id)
        return GraphSnapshotRef(
            static_revision=current.static_revision,
            observation_revision=current.observation_revision,
            merged_revision=current.merged_revision,
        ).model_dump(mode="json")

    def close(self) -> None:
        """Drain background graph-observation work owned by this runtime."""

        if self.flow_engine is not None:
            raise RuntimeError("SymphonyRuntime with Flow configured must be closed with await aclose()")
        self.graph_engine.close()

    async def aclose(self) -> None:
        """Drain graph and optional flow workers."""

        if self.flow_engine is not None:
            try:
                await self.flow_engine.close()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Symphony flow runtime close failed (%s)",
                    type(exc).__name__,
                )
        try:
            self.graph_engine.close()
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Symphony graph runtime close failed (%s)",
                type(exc).__name__,
            )


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


def _capability_evidence(graph: dict[str, Any]) -> dict[str, CapabilityEvidence]:
    output: dict[str, CapabilityEvidence] = {}
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict):
        return output
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        output[str(node_id)] = CapabilityEvidence(
            type=str(metadata.get("capability_type") or node.get("label") or "skill"),
            version=str(metadata.get("version") or "") or None,
            content_hash=str(metadata.get("content_hash") or "unknown"),
        )
    return output


def _task_outcome(execution_graph: dict[str, Any], evidence_id: str) -> TaskOutcome:
    outcome = str(execution_graph.get("outcome") or "").lower()
    evidence_refs = (evidence_id,)
    if outcome == "success":
        return TaskOutcome(
            label=TaskOutcomeLabel.VERIFIED_SUCCESS,
            evidence_strength=EvidenceStrength.STRONG,
            evidence_refs=evidence_refs,
        )
    if outcome == "failed":
        return TaskOutcome(
            label=TaskOutcomeLabel.VERIFIED_FAILURE,
            evidence_strength=EvidenceStrength.STRONG,
            failure_domain=FailureDomain.UNKNOWN,
            evidence_refs=evidence_refs,
        )
    return TaskOutcome(
        label=TaskOutcomeLabel.PARTIAL,
        evidence_strength=EvidenceStrength.WEAK,
        evidence_refs=evidence_refs,
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


def _observation_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Project Rail metadata into the public observation edge contract."""

    projected = deepcopy(graph)
    for edge in projected.get("edges") or ():
        if not isinstance(edge, dict) or not isinstance(edge.get("metadata"), dict):
            continue
        metadata = edge["metadata"]
        if metadata.get("success") is False and not metadata.get("failure_domain"):
            metadata["failure_domain"] = FailureDomain.UNKNOWN.value
    return projected
