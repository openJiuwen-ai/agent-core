import asyncio
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.symphony import (
    ArtifactSpec,
    CapabilityFingerprint,
    CapabilityGraph,
    GraphArtifactStatus,
    GraphBuildResult,
    OrchestrationConfig,
    OrchestrationPlan,
    OrchestrationProgress,
    OrchestrationService,
    ParameterSpec,
    SymphonyRuntime,
)
from openjiuwen.symphony.orchestration.artifacts import load_graph_artifacts
from openjiuwen.symphony.orchestration.planned_graph import build_planned_graph
from openjiuwen.symphony.orchestration.graph.build import GraphBuildPipeline
from openjiuwen.symphony.orchestration.graph.matcher.ontology import OntologyMatcher
from openjiuwen.symphony.orchestration.graph.models import GraphDiagnostic, LLMMatch


def test_public_artifact_contract_uses_graph_terminology() -> None:
    import openjiuwen.symphony as symphony

    graph_exports = {
        "CapabilityGraph",
        "GraphArtifactStatus",
        "GraphBuildResult",
    }
    legacy_score_exports = {
        "CapabilityScore",
        "ScoreArtifactStatus",
        "ScoreBuildResult",
    }
    ambiguous_artifact_aliases = {"ArtifactBuild", "ArtifactStatus"}

    assert graph_exports.issubset(symphony.__all__)
    assert legacy_score_exports.isdisjoint(symphony.__all__)
    assert all(not hasattr(symphony, name) for name in legacy_score_exports)
    assert ambiguous_artifact_aliases.isdisjoint(symphony.__all__)
    assert all(not hasattr(symphony, name) for name in ambiguous_artifact_aliases)


def _capability(
    capability_id: str,
    *,
    capability_type: str = "skill",
    inputs: list[ParameterSpec] | None = None,
    outputs: list[ArtifactSpec] | None = None,
) -> CapabilityFingerprint:
    return CapabilityFingerprint(
        capability_id=capability_id,
        capability_type=capability_type,
        name=capability_id,
        description=capability_id,
        version="1.0.0",
        inputs=inputs or [],
        outputs=outputs or [],
    )


def _inventory() -> list[CapabilityFingerprint]:
    return [
        _capability("extract", outputs=[ArtifactSpec(name="text", type="text")]),
        _capability("summarize", inputs=[ParameterSpec(name="text", type="text")]),
    ]


class _AcceptMatcher:
    thresholds = {"can_feed": 0.7}
    diagnostics: list[GraphDiagnostic] = []

    async def match(self, registry, candidates):
        del registry
        return [
            LLMMatch(
                source_id=item.source_id,
                target_id=item.target_id,
                relation_type="can_feed",
                confidence=0.9,
                accepted=True,
                candidate_id=item.key,
                supporting_fields=item.evidence["directions"][item.key],
            )
            for item in candidates
        ]

    @staticmethod
    def manifest_metadata():
        return {"matcher": "accept"}


class _PlanLLM:
    async def invoke(self, messages, **kwargs):
        del kwargs
        payload = json.loads(messages[-1]["content"])
        if "candidates" in payload:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "matches": [
                            {"id": item["id"], "direction": "forward", "confidence": 0.9, "accepted": True}
                            for item in payload["candidates"]
                        ]
                    }
                )
            )
        return SimpleNamespace(content=json.dumps({"status": "no_plan", "steps": []}))


class _FailingFastPlanLLM(_PlanLLM):
    async def invoke(self, messages, **kwargs):
        del kwargs
        payload = json.loads(messages[-1]["content"])
        if "candidates" in payload:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "matches": [
                            {"id": item["id"], "direction": "forward", "confidence": 0.9, "accepted": True}
                            for item in payload["candidates"]
                        ]
                    }
                )
            )
        return SimpleNamespace(
            content=json.dumps(
                {
                    "status": "ready",
                    "steps": [{"skill_id": "unknown-capability"}],
                }
            )
        )


@pytest.mark.asyncio
async def test_build_then_load_preserves_graph_nodes_and_lookup(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    await service.build()

    loaded = load_graph_artifacts(tmp_path)

    assert [item["id"] for item in loaded.graph["nodes"]] == [
        "capability:extract",
        "capability:summarize",
    ]
    assert loaded.lookup["neighbors"] == {"extract": ["summarize"]}
    assert loaded.lookup["upstream_by_input"] == {"text": ["extract"]}
    assert loaded.lookup["downstream_by_output"] == {"text": ["summarize"]}


@pytest.mark.asyncio
async def test_status_detects_sync_provider_snapshot_change(tmp_path: Path) -> None:
    capabilities = _inventory()
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=lambda: capabilities,
        model=_PlanLLM(),
    )
    await service.build()
    assert service.status().fresh is True

    capabilities.append(_capability("publish"))

    assert service.status().exists is True
    assert service.status().fresh is False


def test_status_requires_expected_snapshot_for_async_provider(tmp_path: Path) -> None:
    async def provider():
        return _inventory()

    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=provider,
        model=_PlanLLM(),
    )

    with pytest.raises(RuntimeError, match="explicit expected_snapshot"):
        service.status()
    assert service.status(expected_snapshot={"capability_count": 2}).exists is False


def test_capability_fingerprint_accepts_generic_constructor_fields() -> None:
    fingerprint = _capability("generic")

    assert fingerprint.capability_id == "generic"
    assert fingerprint.capability_type == "skill"
    assert fingerprint.id == "generic"
    assert fingerprint.type == "skill"
    assert fingerprint.to_dict()["capability_id"] == "generic"


def test_json_output_does_not_feed_markdown_input() -> None:
    source = _capability(
        "source",
        outputs=[ArtifactSpec(name="document", type="json", description="shared document")],
    )
    target = _capability(
        "target",
        inputs=[ParameterSpec(name="document", type="markdown", description="shared document")],
    )
    from openjiuwen.symphony.orchestration.graph.candidates import CandidateGenerator
    from openjiuwen.symphony.orchestration.graph.models import SkillRegistry

    registry = SkillRegistry(skills={source.id: source, target.id: target})
    assert CandidateGenerator().generate(registry) == []


class _CompactMatcherLLM:
    def __init__(self, accepted: object = True) -> None:
        self.accepted = accepted
        self.calls: list[dict] = []

    async def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        payload = json.loads(messages[-1]["content"])
        return SimpleNamespace(
            content=json.dumps(
                {
                    "matches": [
                        {
                            "id": item["id"],
                            "direction": "forward",
                            "confidence": 0.9,
                            "accepted": self.accepted,
                            "reason": "ports are compatible",
                        }
                        for item in payload["candidates"]
                    ]
                }
            )
        )


@pytest.mark.asyncio
async def test_default_matcher_batches_consensus_and_preserves_port_mappings() -> None:
    llm = _CompactMatcherLLM()
    inventory = _inventory()
    result = await GraphBuildPipeline(
        resolver=OntologyMatcher(
            llm,
            fingerprints=inventory,
            batch_size=1,
            require_consensus=True,
        )
    ).build(inventory)

    assert len(llm.calls) == 2
    assert len(result.llm_matches) == 1
    assert result.llm_matches[0].accepted is True
    assert result.llm_matches[0].supporting_fields["port_mappings"]


@pytest.mark.asyncio
async def test_default_matcher_does_not_treat_string_false_as_accepted() -> None:
    inventory = _inventory()
    result = await GraphBuildPipeline(
        resolver=OntologyMatcher(
            _CompactMatcherLLM(accepted="false"),
            fingerprints=inventory,
            require_consensus=False,
        )
    ).build(inventory)

    assert result.llm_matches[0].accepted is False
    assert result.graph.edges == []


@pytest.mark.asyncio
async def test_graph_pipeline_emits_all_stages_and_manifest_metadata() -> None:
    events: list[str] = []

    class _DiagnosticMatcher(_AcceptMatcher):
        async def match(self, registry, candidates):
            matches = await super().match(registry, candidates)
            return [
                LLMMatch(
                    **{
                        **item.__dict__,
                        "diagnostics": ["review warning"],
                    }
                )
                for item in matches
            ]

    result = await GraphBuildPipeline(resolver=_DiagnosticMatcher()).build(
        _inventory(),
        progress=lambda stage, **details: events.append(stage),
    )

    assert events == [
        "graph.registry.start",
        "graph.registry.done",
        "graph.candidates.start",
        "graph.candidates.done",
        "graph.resolve.start",
        "graph.resolve.done",
        "graph.materialize.start",
        "graph.materialize.done",
        "graph.lookup.start",
        "graph.lookup.done",
    ]
    assert result.manifest.candidate_generation["max_candidates_per_skill_relation"] == 32
    assert result.diagnostics[-1].message == "review warning"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_dynamic_overlay_respects_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    enabled: bool,
) -> None:
    captured: list[dict] = []

    class _Planner:
        def __init__(self, *args, dynamic_overlay=None, **kwargs):
            del args, kwargs
            captured.append(dynamic_overlay)

        async def plan(self, query):
            del query
            return {"plans": [], "recommended_plans": []}

    monkeypatch.setattr(
        "openjiuwen.symphony.orchestration.service.FastOneShotPlanner",
        _Planner,
    )
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        config=OrchestrationConfig(dynamic_graph_enabled=enabled),
    )
    await service.build()
    overlay = {"edges": {"extract->summarize:can_feed": {"runtime_weight": 2}}}

    result = await service.plan("query", dynamic_overlay=overlay)

    assert captured == [overlay if enabled else {}]
    assert result == {
        "planned_graph": {
            "graph": {
                "id": result["planned_graph"]["graph"]["id"],
                "type": "planned_graph",
                "directed": True,
                "metadata": {"status": "no_plan"},
                "nodes": {},
                "edges": [],
            }
        }
    }


@pytest.mark.asyncio
async def test_dynamic_graph_enabled_reports_config_without_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Planner:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def plan(self, query):
            del query
            return {"plans": [], "recommended_plans": [], "dynamic_overlay_used": False}

    monkeypatch.setattr("openjiuwen.symphony.orchestration.service.FastOneShotPlanner", _Planner)
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        config=OrchestrationConfig(dynamic_graph_enabled=True),
    )
    await service.build()

    result = await service.plan("query")

    assert set(result) == {"planned_graph"}
    assert result["planned_graph"]["graph"]["metadata"] == {"status": "no_plan"}


@pytest.mark.asyncio
async def test_beam_planner_receives_same_dynamic_overlay_as_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[dict] = []

    class _Planner:
        def __init__(self, *args, dynamic_overlay=None, **kwargs):
            del args, kwargs
            captured.append(dynamic_overlay)

        async def plan(self, query):
            del query
            return {
                "plans": [],
                "recommended_plans": [],
                "dynamic_overlay_used": True,
            }

    monkeypatch.setattr("openjiuwen.symphony.orchestration.service.BidirectionalBeamPlanner", _Planner)
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        config=OrchestrationConfig(dynamic_graph_enabled=True),
    )
    await service.build()
    overlay = {"edges": {"edge-1": {"runtime_weight": 1.2}}}

    await service.plan("query", dynamic_overlay=overlay, mode="beam")

    assert captured == [overlay]


@pytest.mark.asyncio
async def test_graph_engine_plan_pins_merged_snapshot(tmp_path: Path) -> None:
    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        orchestration_config=OrchestrationConfig(dynamic_graph_enabled=True),
    )
    await runtime.graph_engine.build()

    snapshot = runtime.graph_engine.get_snapshot()
    result = await runtime.graph_engine.plan("query", merged_revision=snapshot.merged_revision)

    graph_snapshot = result["planned_graph"]["graph_snapshot"]
    assert graph_snapshot["static_revision"] == snapshot.static_revision
    assert graph_snapshot["observation_revision"] == snapshot.observation_revision
    assert graph_snapshot["merged_revision"] == snapshot.merged_revision
    assert set(result) == {"planned_graph"}
    runtime.graph_engine.close()


@pytest.mark.asyncio
async def test_graph_engine_does_not_materialize_observation_snapshot_when_disabled(tmp_path: Path) -> None:
    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        orchestration_config=OrchestrationConfig(dynamic_graph_enabled=False),
    )
    await runtime.graph_engine.build()

    result = await runtime.graph_engine.plan("query")

    assert "graph_snapshot" not in result["planned_graph"]
    assert not (tmp_path / "evolution").exists()
    runtime.close()


@pytest.mark.asyncio
async def test_graph_engine_falls_back_to_static_graph_when_current_observation_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        orchestration_config=OrchestrationConfig(dynamic_graph_enabled=True),
    )
    await runtime.graph_engine.build()

    def fail_snapshot(*args, **kwargs):
        del args, kwargs
        raise OSError("observation store unavailable")

    monkeypatch.setattr(runtime.graph_engine._observation_service, "get_snapshot", fail_snapshot)
    result = await runtime.graph_engine.plan("query")

    assert "graph_snapshot" not in result["planned_graph"]
    runtime.close()


@pytest.mark.asyncio
async def test_graph_engine_does_not_fallback_when_a_pinned_revision_is_missing(tmp_path: Path) -> None:
    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        orchestration_config=OrchestrationConfig(dynamic_graph_enabled=True),
    )
    await runtime.graph_engine.build()

    with pytest.raises(FileNotFoundError, match="Unknown Symphony merged revision"):
        await runtime.graph_engine.plan("query", merged_revision="merged-missing")
    runtime.close()


class _MixedPlanLLM:
    async def invoke(self, messages, **kwargs):
        del kwargs
        payload = json.loads(messages[-1]["content"])
        if "candidates" in payload and "current_skill" not in payload:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "matches": [
                            {"id": item["id"], "direction": "forward", "confidence": 0.9, "accepted": True}
                            for item in payload["candidates"]
                        ]
                    }
                )
            )
        if "candidate_plans" in payload:
            return SimpleNamespace(content=json.dumps({"selected_plan_index": 1}))
        if "current_skill" in payload:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "judgements": [
                            {"candidate_id": item["candidate_id"], "score": 0.9, "reason": "useful"}
                            for item in payload["candidates"]
                        ]
                    }
                )
            )
        return SimpleNamespace(
            content=json.dumps(
                {
                    "status": "ready",
                    "reason": "capability:reason must stay",
                    "steps": [{"skill_id": "extract"}, {"skill_id": "summarize"}],
                    "can_feed_edges": [{"source_id": "extract", "target_id": "summarize"}],
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_mixed_capability_types_survive_plan_projection(tmp_path: Path, mode: str) -> None:
    inventory = [
        _capability("extract", capability_type="skill", outputs=[ArtifactSpec(name="text", type="text")]),
        _capability(
            "summarize",
            capability_type="agent",
            inputs=[ParameterSpec(name="text", type="text")],
        ),
    ]
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=inventory,
        model=_MixedPlanLLM(),
        config=OrchestrationConfig(mode=mode, max_depth=2),
    )
    await service.build()

    result = await service.plan("skill:query must stay", candidate_ids=["extract"])

    assert set(result) == {"planned_graph"}
    graph = result["planned_graph"]["graph"]
    assert graph["nodes"] == {
        "extract": {"label": "extract", "metadata": {"type": "skill"}},
        "summarize": {"label": "summarize", "metadata": {"type": "agent"}},
    }
    assert graph["edges"] == [
        {"source": "extract", "target": "summarize", "relation": "can_feed"},
    ]
    assert {"execution_graph", "plans", "recommended_plans", "query"}.isdisjoint(result)


@pytest.mark.asyncio
async def test_activate_is_terminal_before_post_publish_callback(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def progress(event):
        if event["event"] != "build_published":
            return
        entered.set()
        await release.wait()
        raise RuntimeError("observer failed after publish")

    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    task = asyncio.create_task(service.build(progress_callback=progress))
    await entered.wait()

    status = await service.cancel_build()
    assert isinstance(status, GraphArtifactStatus)
    assert status.version is not None
    release.set()
    built = await task
    assert service.status().version == built.version


@pytest.mark.asyncio
async def test_cancel_during_stage_never_switches_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_stage = service._store.stage

    def blocking_stage(payload, *, version):
        entered.set()
        release.wait(timeout=5)
        try:
            return original_stage(payload, version=version)
        finally:
            finished.set()

    monkeypatch.setattr(service._store, "stage", blocking_stage)
    task = asyncio.create_task(service.build(force=True))
    assert await asyncio.to_thread(entered.wait, 5)

    status = await service.cancel_build()
    assert status.exists is True
    assert status.building is True
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 5)
    assert (tmp_path / "current.json").read_bytes() == current_before


@pytest.mark.asyncio
async def test_public_service_contracts_accept_planned_call_shapes(tmp_path: Path) -> None:
    async def provider():
        return _inventory()

    progress_events: list[OrchestrationProgress] = []
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=provider,
        model=_PlanLLM(),
    )

    built = await service.build(False, progress=progress_events.append)
    graph = service.read()
    plan = await service.plan(
        "summarize text",
        ["extract", "summarize"],
        language="en",
        progress=progress_events.append,
    )
    status = await service.cancel_build()

    assert isinstance(built, GraphBuildResult)
    assert isinstance(graph, CapabilityGraph)
    assert isinstance(plan, OrchestrationPlan)
    assert isinstance(status, GraphArtifactStatus)
    assert progress_events
    assert all(isinstance(item, OrchestrationProgress) for item in progress_events)
    assert {item.event for item in progress_events} >= {
        "build_started",
        "build_published",
        "plan_started",
        "plan_completed",
    }


@pytest.mark.asyncio
async def test_early_build_failure_is_delivered_without_started_progress_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    error = build_error(StatusCode.MODEL_CALL_FAILED, error_msg="model unavailable during fingerprint extraction")
    events: list[OrchestrationProgress] = []
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )

    async def fail_inventory(*, require_atomic: bool = False, force_fingerprint: bool = False):
        del require_atomic, force_fingerprint
        raise error

    monkeypatch.setattr(service, "_load_inventory", fail_inventory)

    with pytest.raises(BaseError) as exc_info:
        await service.build(progress=events.append)

    assert exc_info.value is error
    assert events == [OrchestrationProgress("build_failed", error=str(error))]


@pytest.mark.asyncio
async def test_graph_config_drives_default_matcher_candidates_and_progress(tmp_path: Path) -> None:
    llm = _CompactMatcherLLM()
    inventory = [
        _capability("source", outputs=[ArtifactSpec(name="text", type="text")]),
        _capability("target-a", inputs=[ParameterSpec(name="text", type="text")]),
        _capability("target-b", inputs=[ParameterSpec(name="text", type="text")]),
    ]
    events: list[OrchestrationProgress] = []
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=inventory,
        model=llm,
        graph_config={
            "batch_size": 1,
            "workers": 3,
            "require_consensus": False,
            "max_candidates_per_skill_relation": 1,
        },
    )

    await service.build(progress=events.append)
    graph = service.read()

    assert len(llm.calls) == 1
    llm_metadata = graph["config"]["llm"]
    assert {
        key: llm_metadata[key]
        for key in (
            "model",
            "backend",
            "temperature",
            "prompt_version",
            "batch_size",
            "max_workers",
            "require_consensus",
            "consensus_runs",
        )
    } == {
        "model": None,
        "backend": None,
        "temperature": None,
        "prompt_version": "Orchestration-graph-match-v2",
        "batch_size": 1,
        "max_workers": 3,
        "require_consensus": False,
        "consensus_runs": 1,
    }
    assert llm_metadata["matcher_version"] == "Symphony-ontology-matcher-v2"
    assert llm_metadata["match_schema_version"] == "Symphony-ontology-match-schema-v1"
    assert llm_metadata["relation_cache"]["resolved_count"] == 1
    assert graph["config"]["candidate_generation"]["max_candidates_per_skill_relation"] == 1
    assert any(item.event == "graph.resolve.progress" for item in events)


@pytest.mark.asyncio
async def test_planned_graph_is_minimal_and_never_falls_back_to_static_edges(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    await service.build()
    artifacts = load_graph_artifacts(tmp_path)

    planned_graph = build_planned_graph(
        {
            "plan_id": "plan-1",
            "status": "ready",
            "recommended_plans": [
                {
                    "steps": [{"skill_id": "extract"}, {"skill_id": "summarize"}],
                    "can_feed_edges": [],
                }
            ],
        },
        artifacts,
    )

    assert planned_graph == {
        "graph": {
            "id": "plan-1",
            "type": "planned_graph",
            "directed": True,
            "metadata": {"status": "ready"},
            "nodes": {
                "extract": {"label": "extract", "metadata": {"type": "skill"}},
                "summarize": {"label": "summarize", "metadata": {"type": "skill"}},
            },
            "edges": [],
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason", "missing_inputs", "expected_metadata"),
    [
        ("ready", "", [], {"status": "ready"}),
        (
            "needs_input",
            "A source document is required.",
            [{"name": "source_document", "reason": "required"}],
            {
                "status": "needs_input",
                "reason": "A source document is required.",
                "missing_inputs": ["source_document"],
            },
        ),
        ("no_plan", "No capability applies.", [], {"status": "no_plan", "reason": "No capability applies."}),
    ],
)
async def test_planned_graph_covers_all_public_statuses(
    tmp_path: Path,
    status: str,
    reason: str,
    missing_inputs: list[dict[str, str]],
    expected_metadata: dict[str, object],
) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    await service.build()
    artifacts = load_graph_artifacts(tmp_path)

    planned_graph = build_planned_graph(
        {
            "plan_id": f"plan-{status}",
            "status": status,
            "reason": reason,
            "recommended_plans": [
                {
                    "status": status,
                    "reason": reason,
                    "steps": [],
                    "missing_inputs": missing_inputs,
                    "can_feed_edges": [],
                }
            ],
        },
        artifacts,
    )

    graph = planned_graph["graph"]
    assert graph["metadata"] == expected_metadata
    assert graph["nodes"] == {}
    assert graph["edges"] == []


@pytest.mark.asyncio
async def test_planned_graph_rejects_edge_endpoints_outside_selected_nodes(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    await service.build()
    artifacts = load_graph_artifacts(tmp_path)

    with pytest.raises(ValueError, match="selected capability nodes.*target='summarize'"):
        build_planned_graph(
            {
                "plan_id": "plan-invalid-edge",
                "status": "ready",
                "recommended_plans": [
                    {
                        "steps": [{"skill_id": "extract"}],
                        "can_feed_edges": [{"source_id": "capability:extract", "target_id": "skill:summarize"}],
                    }
                ],
            },
            artifacts,
        )


@pytest.mark.asyncio
async def test_service_rejects_fast_planner_failure_instead_of_projecting_no_plan(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FailingFastPlanLLM(),
        config=OrchestrationConfig(mode="fast"),
    )
    await service.build()

    with pytest.raises(ValueError, match="Fast planner selected unknown skill IDs"):
        await service.plan("extract and summarize")


@pytest.mark.asyncio
async def test_async_build_progress_is_serial_and_completed_before_return(tmp_path: Path) -> None:
    class _BlockingMatcherLLM(_CompactMatcherLLM):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def invoke(self, messages, **kwargs):
            self.entered.set()
            await self.release.wait()
            return await super().invoke(messages, **kwargs)

    llm = _BlockingMatcherLLM()
    progress_events: list[OrchestrationProgress] = []
    live_matcher_progress = asyncio.Event()
    active_callbacks = 0
    max_active_callbacks = 0
    published_completed = False

    async def progress(item: OrchestrationProgress) -> None:
        nonlocal active_callbacks, max_active_callbacks, published_completed
        active_callbacks += 1
        max_active_callbacks = max(max_active_callbacks, active_callbacks)
        await asyncio.sleep(0)
        progress_events.append(item)
        if item.get("matcher_event") == "batch_start":
            live_matcher_progress.set()
        if item.event == "build_published":
            published_completed = True
        active_callbacks -= 1

    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=llm,
        graph_config={"require_consensus": False},
    )

    task = asyncio.create_task(service.build(progress=progress))
    await llm.entered.wait()
    await asyncio.wait_for(live_matcher_progress.wait(), timeout=1)
    assert task.done() is False
    llm.release.set()
    await task

    event_names = [item.event for item in progress_events]
    resolve_start = event_names.index("graph.resolve.start")
    resolve_done = event_names.index("graph.resolve.done")
    matcher_progress = [
        item.get("matcher_event")
        for item in progress_events[resolve_start + 1 : resolve_done]
        if item.event == "graph.resolve.progress"
    ]
    assert matcher_progress == ["matching_start", "batch_start", "batch_done", "matching_done"]
    matching_done = next(
        item
        for item in progress_events
        if item.event == "graph.resolve.progress" and item.get("matcher_event") == "matching_done"
    )
    assert matching_done["completed_candidate_count"] == 1
    assert matching_done["total_candidate_count"] == 1
    assert matching_done["reused_candidate_count"] == 0
    assert event_names[-1] == "build_published"
    assert max_active_callbacks == 1
    assert published_completed is True


@pytest.mark.asyncio
async def test_async_build_progress_failures_are_logged_and_do_not_break_publish(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    unhandled: list[dict] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    caplog.set_level(logging.WARNING, logger="openjiuwen.symphony.orchestration.service")

    async def progress(item: OrchestrationProgress) -> None:
        events.append(item.event)
        await asyncio.sleep(0)
        if item.event in {"graph.candidates.done", "build_published"}:
            raise RuntimeError(f"observer failed: {item.event}")

    try:
        service = OrchestrationService(
            graph_artifact_root=tmp_path,
            capability_provider=_inventory,
            model=_PlanLLM(),
        )
        built = await service.build(progress=progress)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert service.status().version == built.version
    assert events.index("graph.candidates.done") < events.index("graph.resolve.start")
    assert events[-1] == "build_published"
    assert "progress callback failed for event graph.candidates.done" in caplog.text
    assert "progress callback failed for event build_published" in caplog.text
    assert unhandled == []


@pytest.mark.asyncio
async def test_cancel_during_async_progress_drain_leaves_no_pending_dispatch_task(tmp_path: Path) -> None:
    callback_entered = asyncio.Event()
    events: list[str] = []

    async def progress(item: OrchestrationProgress) -> None:
        events.append(item.event)
        if item.event == "graph.registry.start":
            callback_entered.set()
            await asyncio.Event().wait()

    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    task = asyncio.create_task(service.build(progress=progress), name="symphony-progress-build")
    await callback_entered.wait()

    status = await service.cancel_build()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert status.building is True
    assert events == ["build_started", "graph.registry.start", "build_cancelled"]
    assert not [pending for pending in asyncio.all_tasks() if pending.get_name() == "symphony-progress-build"]


@pytest.mark.asyncio
async def test_prepare_artifact_sync_hook_writes_before_activation(tmp_path: Path) -> None:
    hook_calls: list[Path] = []

    def prepare_artifact(version_dir: Path) -> None:
        hook_calls.append(version_dir)
        (version_dir / "fingerprints.json").write_text("prepared", encoding="utf-8")
        assert not (tmp_path / "current.json").exists()

    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )

    built = await service.build(prepare_artifact=prepare_artifact)

    assert hook_calls == [built.graph_path.parent]
    assert (built.graph_path.parent / "fingerprints.json").read_text(encoding="utf-8") == "prepared"
    assert service.status().version == built.version


@pytest.mark.asyncio
async def test_runtime_forwards_async_prepare_artifact_default(tmp_path: Path) -> None:
    hook_completed = False

    async def prepare_artifact(version_dir: Path) -> None:
        nonlocal hook_completed
        await asyncio.sleep(0)
        (version_dir / "graph_state.json").write_text("prepared", encoding="utf-8")
        hook_completed = True

    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
        prepare_artifact=prepare_artifact,
    )

    built = await runtime.orchestration.build()

    assert hook_completed is True
    assert (built.graph_path.parent / "graph_state.json").read_text(encoding="utf-8") == "prepared"


@pytest.mark.asyncio
async def test_prepare_artifact_failure_preserves_current_version(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    first = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()

    def fail_prepare(version_dir: Path) -> None:
        (version_dir / "io_vocab.json").write_text("incomplete", encoding="utf-8")
        raise RuntimeError("prepare failed")

    with pytest.raises(RuntimeError, match="prepare failed"):
        await service.build(force=True, prepare_artifact=fail_prepare)

    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status().version == first.version


@pytest.mark.asyncio
async def test_prepare_artifact_cancellation_preserves_current_version(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_PlanLLM(),
    )
    first = await service.build()
    current_before = (tmp_path / "current.json").read_bytes()
    hook_entered = asyncio.Event()
    hook_cancelled = asyncio.Event()

    async def blocking_prepare(version_dir: Path) -> None:
        (version_dir / "graph_state.json").write_text("incomplete", encoding="utf-8")
        hook_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            hook_cancelled.set()

    task = asyncio.create_task(service.build(force=True, prepare_artifact=blocking_prepare))
    await hook_entered.wait()
    await service.cancel_build()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert hook_cancelled.is_set()
    assert (tmp_path / "current.json").read_bytes() == current_before
    assert service.status().version == first.version
