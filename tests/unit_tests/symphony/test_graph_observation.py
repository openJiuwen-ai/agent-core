from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openjiuwen.symphony.observation import (
    CapabilityEvidence,
    EvidenceStrength,
    EvolutionGraph,
    EvolutionGraphEdge,
    EvolutionEdgeMetadata,
    FailureDomain,
    GraphEvolutionInput,
    GraphSnapshotRef,
    PortMapping,
    TaskEvidence,
    TaskOutcome,
    TaskOutcomeLabel,
    TraceEvidence,
)
from openjiuwen.symphony.observation.service import GraphObservationService, _runtime_adjustment
from openjiuwen.symphony.observation.identity import stable_hash
from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts, GraphArtifactStore, load_graph_artifacts
from openjiuwen.symphony.orchestration.planning.fast import FastOneShotPlanner
from openjiuwen.symphony.orchestration.planning.plan_builder import edge_plan_item
from openjiuwen.symphony.orchestration.planning.runtime import RuntimeEdgeResolver


def test_verified_success_is_idempotent_and_weak_evidence_is_audit_only(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    evidence = _evidence("evidence-1", "session-1", static_revision="static-v1")

    receipt = service.submit(evidence)
    service.flush()
    duplicate = service.submit(evidence)
    weak = service.submit(
        _evidence(
            "evidence-weak",
            "session-weak",
            static_revision="static-v1",
            strength=EvidenceStrength.WEAK,
        )
    )
    service.flush()

    stats = _only_edge(service.get_snapshot().overlay)
    assert receipt.status == "accepted"
    assert duplicate.status == "duplicate"
    assert weak.status == "audit_only"
    assert weak.reason == "outcome_not_strong"
    assert stats["success_count"] == 1
    assert stats["attempt_count"] == 1
    assert stats["runtime_weight"] == pytest.approx(1.05)
    assert stats["binding_status"] == "active_static"
    service.close()


def test_runtime_only_edge_with_explicit_runtime_mapping_is_accepted(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=False)
    service = GraphObservationService(tmp_path)

    receipt = service.submit(_evidence("runtime-only", "session-1", static_revision="static-v1"))
    service.flush()

    stats = _only_edge(service.get_snapshot().overlay)
    assert receipt.status == "accepted"
    assert stats["binding_status"] == "insufficient_evidence"
    service.close()


def test_sdd_canonical_json_contract_is_accepted_without_persisting_query(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    mapping_hash = stable_hash(({"source_output": "text", "target_input": "text"},))
    value = GraphEvolutionInput.model_validate(
        {
            "schema_version": "symphony.graph_evolution_input.v1",
            "evidence_id": "canonical-1",
            "graph_scope_id": "workspace:test",
            "observed_at": "2026-08-25T01:02:03Z",
            "trace": {
                "trajectory_id": "trajectory-1",
                "session_id": "session-1",
                "capture_mode": "otlp_trace",
                "truncated": False,
                "quality_flags": [],
                "span_refs": ["otlp://trace-1/span-1"],
                "member_trajectory_refs": [],
            },
            "task": {
                "query": "private user request",
                "task_cluster_id": "document-summary",
                "outcome": {
                    "label": "verified_success",
                    "evidence_strength": "strong",
                    "evidence_refs": ["evaluator://task/1"],
                },
            },
            "graph_snapshot": {
                "static_revision": "static-v1",
                "observation_revision": "observation-v0",
            },
            "capabilities": {
                "extract": {"type": "skill", "version": "1", "content_hash": "content-extract-v1"},
                "summarize": {"type": "skill", "version": "1", "content_hash": "content-summarize-v1"},
            },
            "planned_graph": None,
            "execution_graph": {
                "id": "execution-1",
                "type": "execution_graph",
                "directed": True,
                "nodes": {"extract": {"label": "skill"}, "summarize": {"label": "skill"}},
                "edges": [
                    {
                        "source": "extract",
                        "target": "summarize",
                        "relation": "can_feed",
                        "metadata": {
                            "success": True,
                            "port_mapping_hash": f"sha256:{mapping_hash}",
                            "evidence_refs": ["otlp://trace-1/span-1"],
                        },
                    }
                ],
            },
        }
    )
    service = GraphObservationService(tmp_path)

    receipt = service.submit(value)
    service.flush()
    stats = _only_edge(service.get_snapshot("workspace:test").overlay)

    assert receipt.status == "accepted"
    assert stats["success_count"] == 1
    persisted_rows = list(service._evidence_store.iter_after("workspace:test", 0))
    assert len(persisted_rows) == 1
    persisted = json.dumps(persisted_rows[0][2], ensure_ascii=False)
    assert "private user request" not in persisted
    assert "session-1" not in persisted
    assert "otlp://trace-1/span-1" in persisted
    assert "evaluator://task/1" in persisted
    service.close()


def test_only_explicit_orchestration_failure_changes_edge_weight(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("success", "session-1", static_revision="static-v1"))
    external = service.submit(
        _evidence(
            "external-failure",
            "session-2",
            static_revision="static-v1",
            task_outcome=TaskOutcomeLabel.VERIFIED_FAILURE,
            task_failure_domain=FailureDomain.EXTERNAL_SERVICE,
            edge_success=False,
            edge_failure_domain=FailureDomain.EXTERNAL_SERVICE,
            edge_evidence_refs=("span:external",),
        )
    )
    explicit = service.submit(
        _evidence(
            "orchestration-failure",
            "session-3",
            static_revision="static-v1",
            task_outcome=TaskOutcomeLabel.VERIFIED_FAILURE,
            task_failure_domain=FailureDomain.ORCHESTRATION,
            edge_success=False,
            edge_failure_domain=FailureDomain.ORCHESTRATION,
            edge_evidence_refs=("evaluator:edge-order",),
        )
    )
    service.flush()

    stats = _only_edge(service.get_snapshot().overlay)
    assert external.status == "audit_only"
    assert "no_qualified_edge_outcome" in external.reason
    assert explicit.status == "accepted"
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["attempt_count"] == 2
    assert stats["runtime_weight"] == pytest.approx(1.0)
    service.close()


def test_runtime_adjustment_is_symmetric_and_bounded() -> None:
    assert _runtime_adjustment({"success_count": 1, "failure_count": 0}) == pytest.approx(0.05)
    assert _runtime_adjustment({"success_count": 0, "failure_count": 1}) == pytest.approx(-0.05)
    assert _runtime_adjustment({"success_count": 100, "failure_count": 0}) == pytest.approx(1.0)
    assert _runtime_adjustment({"success_count": 0, "failure_count": 100}) == pytest.approx(-1.0)


def test_successful_task_with_failed_edge_is_audit_only(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)

    receipt = service.submit(
        _evidence(
            "conflicting-outcome",
            "session-1",
            static_revision="static-v1",
            edge_success=False,
            edge_failure_domain=FailureDomain.ORCHESTRATION,
            edge_evidence_refs=("evaluator:edge",),
        )
    )
    service.flush()

    assert receipt.status == "audit_only"
    assert "task_edge_outcome_conflict" in receipt.reason
    assert not (service.get_snapshot().overlay.get("edges") or {})
    service.close()


def test_edge_evidence_counts_independent_sessions_not_delivery_attempts(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)

    service.submit(_evidence("delivery-1", "same-session", static_revision="static-v1"))
    service.submit(_evidence("delivery-2", "same-session", static_revision="static-v1"))
    service.flush()

    stats = _only_edge(service.get_snapshot().overlay)
    assert stats["success_count"] == 1
    assert stats["attempt_count"] == 1
    assert stats["runtime_weight"] == pytest.approx(1.05)
    service.close()


def test_projection_cursor_does_not_skip_evidence_appended_during_a_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    monkeypatch.setattr(service, "_schedule", lambda graph_scope_id: None)
    service.submit(_evidence("first", "session-1", static_revision="static-v1"))
    original_iter_after = service._evidence_store.iter_after

    def _append_after_snapshot(graph_scope_id: str, sequence: int):
        yield from original_iter_after(graph_scope_id, sequence)
        service.submit(_evidence("second", "session-2", static_revision="static-v1"))
        monkeypatch.setattr(service._evidence_store, "iter_after", original_iter_after)

    monkeypatch.setattr(service._evidence_store, "iter_after", _append_after_snapshot)

    first = service.reproject()
    second = service.reproject()

    assert first.high_water_sequence == 1
    assert _only_edge(first.overlay)["success_count"] == 1
    assert second.high_water_sequence == 2
    assert _only_edge(second.overlay)["success_count"] == 2
    service.close()


def test_projection_failure_publishes_retryable_neutral_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)

    def _fail_projection(graph_scope_id: str = "default") -> None:
        del graph_scope_id
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(service, "reproject", _fail_projection)
    snapshots = service.reproject_all_safely()
    snapshot = service.get_snapshot()

    assert snapshots[0].static_revision == "static-v1"
    assert snapshots[0].overlay["projection_status"] == "safe_fallback"
    assert snapshot.overlay["edges"] == {}
    assert snapshot.high_water_sequence == 0
    service.close()


def test_runtime_only_edge_requires_two_current_revision_sessions(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=False)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("runtime-1", "session-1", static_revision="static-v1"))
    service.flush()
    first = _only_edge(service.get_snapshot().overlay)

    service.submit(_evidence("runtime-2", "session-2", static_revision="static-v1"))
    service.flush()
    second = _only_edge(service.get_snapshot().overlay)

    assert first["binding_status"] == "insufficient_evidence"
    assert second["binding_status"] == "active_runtime_only"
    assert len(second["success_sessions_by_revision"]["static-v1"]) == 2

    artifacts = load_graph_artifacts(tmp_path)
    fast_edges = RuntimeEdgeResolver(
        artifacts,
        min_edge_confidence=0.5,
        candidate_skill_ids=("extract",),
        dynamic_overlay=service.get_snapshot().overlay,
        task_cluster_id="different-cluster",
    ).resolve()
    beam_edges = RuntimeEdgeResolver(
        artifacts,
        min_edge_confidence=0.5,
        candidate_skill_ids=(),
        dynamic_overlay=service.get_snapshot().overlay,
        task_cluster_id="document-summary",
    ).resolve()
    assert [(edge["source"], edge["target"]) for edge in fast_edges] == [("extract", "summarize")]
    assert beam_edges == fast_edges
    assert fast_edges[0]["runtime_only"] is True
    service.close()


def test_runtime_weight_only_applies_to_the_observed_port_mapping(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True, include_alternate_mapping=True)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("text-mapping", "session-1", static_revision="static-v1"))
    service.flush()

    resolved = RuntimeEdgeResolver(
        load_graph_artifacts(tmp_path),
        min_edge_confidence=0.5,
        candidate_skill_ids=("extract",),
        dynamic_overlay=service.get_snapshot().overlay,
    ).resolve()
    by_mapping = {
        tuple(
            (item["source_output"], item["target_input"])
            for item in edge["evidence"]["supporting_fields"]["port_mappings"]
        ): edge
        for edge in resolved
    }

    assert by_mapping[(("text", "text"),)]["runtime_weight"] == pytest.approx(1.05)
    assert "runtime_weight" not in by_mapping[(("title", "title"),)]
    plan_edge = edge_plan_item(by_mapping[(("text", "text"),)])
    assert plan_edge["planner_weight"] == pytest.approx(0.84)
    assert "effective_score" not in plan_edge
    service.close()


def test_dynamic_candidates_preserve_the_static_candidate_subgraph(tmp_path: Path) -> None:
    skills = [
        {
            "id": current_id,
            "name": current_id,
            "inputs": [{"name": "text", "type": "text"}] if current_id == "seed" else [],
            "outputs": [] if current_id == "seed" else [{"name": "text", "type": "text"}],
        }
        for current_id in ("static-neighbor", "runtime-neighbor", "seed")
    ]

    def edge(source: str, confidence: float) -> dict:
        return {
            "source": f"capability:{source}",
            "target": "capability:seed",
            "type": "can_feed",
            "confidence": confidence,
            "evidence": {"supporting_fields": {"port_mappings": [{"source_output": "text", "target_input": "text"}]}},
        }

    artifacts = GraphArtifacts(
        graph_dir=tmp_path,
        manifest={},
        skills=skills,
        graph={"edges": [edge("static-neighbor", 0.8), edge("runtime-neighbor", 0.7)]},
        lookup={},
    )
    overlay = {
        "edges": {
            "runtime-neighbor": {
                "source_id": "runtime-neighbor",
                "target_id": "seed",
                "relation_type": "can_feed",
                "port_mappings": [{"source_output": "text", "target_input": "text"}],
                "binding_status": "active_static",
                "runtime_weight": 2.0,
                "success_count": 20,
                "failure_count": 0,
                "attempt_count": 20,
            }
        }
    }
    static_planner = FastOneShotPlanner(
        artifacts,
        model=None,
        min_edge_confidence=0.5,
        candidate_skill_ids=("seed",),
    )
    dynamic_planner = FastOneShotPlanner(
        artifacts,
        model=None,
        min_edge_confidence=0.5,
        candidate_skill_ids=("seed",),
        dynamic_overlay=overlay,
    )

    static_subgraph = static_planner._candidate_subgraph("query")
    dynamic_subgraph = dynamic_planner._candidate_subgraph("query")
    static_edges = {(item["source_id"], item["target_id"]) for item in static_subgraph["edges"]}
    dynamic_edges = {(item["source_id"], item["target_id"]) for item in dynamic_subgraph["edges"]}

    assert static_edges == {("static-neighbor", "seed")}
    assert static_edges < dynamic_edges
    assert dynamic_edges == {
        ("static-neighbor", "seed"),
        ("runtime-neighbor", "seed"),
    }


def test_removed_static_edge_is_quarantined_until_fresh_revalidation(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("old-1", "session-old-1", static_revision="static-v1"))
    service.submit(_evidence("old-2", "session-old-2", static_revision="static-v1"))
    service.flush()

    _publish_static(tmp_path, "static-v2", include_edge=False)
    quarantined = _only_edge(service.reproject().overlay)
    assert quarantined["binding_status"] == "quarantined"

    service.submit(_evidence("new-1", "session-new-1", static_revision="static-v2"))
    service.flush()
    assert _only_edge(service.get_snapshot().overlay)["binding_status"] == "insufficient_evidence"

    service.submit(_evidence("new-2", "session-new-2", static_revision="static-v2"))
    service.flush()
    assert _only_edge(service.get_snapshot().overlay)["binding_status"] == "active_runtime_only"
    service.close()


def test_inflight_old_revision_evidence_is_validated_then_rebound(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    _publish_static(tmp_path, "static-v2", include_edge=False)

    receipt = service.submit(_evidence("inflight-v1", "session-old", static_revision="static-v1"))
    service.flush()
    stats = _only_edge(service.get_snapshot().overlay)

    assert receipt.status == "accepted"
    assert stats["success_count"] == 1
    assert stats["binding_status"] == "quarantined"
    assert stats["last_evidence_static_revision"] == "static-v1"
    service.close()


def test_endpoint_deletion_or_identity_change_invalidates_old_observation(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("evidence-1", "session-1", static_revision="static-v1"))
    service.flush()

    _publish_static(tmp_path, "static-v2", include_edge=False, target_content_hash="content-summarize-v2")
    invalidated = _only_edge(service.reproject().overlay)
    assert invalidated["binding_status"] == "invalidated"

    _publish_static(tmp_path, "static-v3", include_edge=False, include_target=False)
    orphaned = _only_edge(service.reproject().overlay)
    assert orphaned["binding_status"] == "orphaned"
    service.close()


def test_merged_revision_can_be_pinned_after_new_evidence(tmp_path: Path) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    service.submit(_evidence("first", "session-1", static_revision="static-v1"))
    service.flush()
    first = service.get_snapshot()

    service.submit(_evidence("second", "session-2", static_revision="static-v1"))
    service.flush()
    current = service.get_snapshot()
    pinned = service.get_snapshot(merged_revision=first.merged_revision)

    assert current.merged_revision != first.merged_revision
    assert _only_edge(current.overlay)["success_count"] == 2
    assert _only_edge(pinned.overlay)["success_count"] == 1
    service.close()


def test_declared_merged_revision_must_match_the_static_and_observation_pair(
    tmp_path: Path,
) -> None:
    _publish_static(tmp_path, "static-v1", include_edge=True)
    service = GraphObservationService(tmp_path)
    pinned = service.get_snapshot()
    valid = _evidence("valid-snapshot", "session-1", static_revision="static-v1").model_copy(
        update={
            "graph_snapshot": GraphSnapshotRef(
                static_revision=pinned.static_revision,
                observation_revision=pinned.observation_revision,
                merged_revision=pinned.merged_revision,
            )
        }
    )
    invalid = _evidence("invalid-snapshot", "session-2", static_revision="static-v1").model_copy(
        update={
            "graph_snapshot": GraphSnapshotRef(
                static_revision=pinned.static_revision,
                observation_revision="observation-wrong",
                merged_revision=pinned.merged_revision,
            )
        }
    )

    accepted = service.submit(valid)
    rejected = service.submit(invalid)
    service.flush()

    assert accepted.status == "accepted"
    assert rejected.status == "audit_only"
    assert rejected.reason == "merged_observation_revision_mismatch"
    assert _only_edge(service.get_snapshot().overlay)["success_count"] == 1
    service.close()


def test_observed_at_requires_an_explicit_timezone() -> None:
    payload = _evidence("naive-time", "session-1", static_revision="static-v1").model_dump(mode="json")
    payload["observed_at"] = "2026-08-25T01:02:03"

    with pytest.raises(ValueError, match="observed_at must include a timezone"):
        GraphEvolutionInput.model_validate(payload)


def _publish_static(
    root: Path,
    version: str,
    *,
    include_edge: bool,
    include_target: bool = True,
    include_alternate_mapping: bool = False,
    target_graph_hash: str = "graph-summarize-v1",
    target_content_hash: str = "content-summarize-v1",
) -> None:
    capabilities = [
        {
            "capability_id": "extract",
            "capability_type": "skill",
            "name": "Extract",
            "inputs": [],
            "outputs": [
                {"name": "text", "type": "text"},
                *([{"name": "title", "type": "text"}] if include_alternate_mapping else []),
            ],
        }
    ]
    if include_target:
        capabilities.append(
            {
                "capability_id": "summarize",
                "capability_type": "skill",
                "name": "Summarize",
                "inputs": [
                    {"name": "text", "type": "text"},
                    *([{"name": "title", "type": "text"}] if include_alternate_mapping else []),
                ],
                "outputs": [{"name": "summary", "type": "text"}],
            }
        )
    edges = []
    if include_edge and include_target:
        edges.append(
            {
                "source": "capability:extract",
                "target": "capability:summarize",
                "type": "can_feed",
                "confidence": 0.8,
                "evidence": {
                    "supporting_fields": {"port_mappings": [{"source_output": "text", "target_input": "text"}]}
                },
            }
        )
        if include_alternate_mapping:
            edges.append(
                {
                    "source": "capability:extract",
                    "target": "capability:summarize",
                    "type": "can_feed",
                    "confidence": 0.8,
                    "evidence": {
                        "supporting_fields": {"port_mappings": [{"source_output": "title", "target_input": "title"}]}
                    },
                }
            )
    payload = {
        "schema_version": "1.0",
        "generated_at": f"2026-08-25T00:00:0{version[-1]}+00:00",
        "source_snapshot": {"snapshot_id": version},
        "capabilities": capabilities,
        "nodes": [
            {"id": f"capability:{item['capability_id']}", "type": item["capability_type"]} for item in capabilities
        ],
        "edges": edges,
        "lookup": {},
        "capability_hashes": {
            "skill:extract": "content-extract-v1",
            **({"skill:summarize": target_content_hash} if include_target else {}),
        },
        "graph_identity_hashes": {
            "skill:extract": "graph-extract-v1",
            **({"skill:summarize": target_graph_hash} if include_target else {}),
        },
        "build_protocol_hash": "protocol-v1",
    }
    GraphArtifactStore(root).publish(payload, version=version)


def _evidence(
    evidence_id: str,
    session_id: str,
    *,
    static_revision: str,
    strength: EvidenceStrength = EvidenceStrength.STRONG,
    task_outcome: TaskOutcomeLabel = TaskOutcomeLabel.VERIFIED_SUCCESS,
    task_failure_domain: FailureDomain | None = None,
    edge_success: bool | None = True,
    edge_failure_domain: FailureDomain | None = None,
    edge_evidence_refs: tuple[str, ...] = ("otlp://trace/span-edge",),
) -> GraphEvolutionInput:
    return GraphEvolutionInput(
        evidence_id=evidence_id,
        observed_at=datetime(2026, 8, 25, 1, 2, 3, tzinfo=timezone.utc),
        graph_snapshot=GraphSnapshotRef(
            static_revision=static_revision,
            observation_revision="observation-v0",
        ),
        trace=TraceEvidence(
            trajectory_id=f"trajectory-{evidence_id}",
            session_id=session_id,
            capture_mode="evaluator",
        ),
        task=TaskEvidence(
            task_cluster_id="document-summary",
            outcome=TaskOutcome(
                label=task_outcome,
                evidence_strength=strength,
                failure_domain=task_failure_domain,
                evidence_refs=("evaluator:task",),
            ),
        ),
        capabilities={
            "extract": CapabilityEvidence(
                content_hash="content-extract-v1",
            ),
            "summarize": CapabilityEvidence(
                content_hash="content-summarize-v1",
            ),
        },
        execution_graph=EvolutionGraph(
            id=f"execution-{evidence_id}",
            type="execution_graph",
            edges=(
                EvolutionGraphEdge(
                    source="extract",
                    target="summarize",
                    metadata=EvolutionEdgeMetadata(
                        port_mappings=(PortMapping(source_output="text", target_input="text"),),
                        success=edge_success,
                        failure_domain=edge_failure_domain,
                        evidence_refs=edge_evidence_refs,
                    ),
                ),
            ),
        ),
    )


def _only_edge(overlay: dict) -> dict:
    edges = list((overlay.get("edges") or {}).values())
    assert len(edges) == 1
    return edges[0]
