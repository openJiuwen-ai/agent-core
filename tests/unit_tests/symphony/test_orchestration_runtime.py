import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from openjiuwen.symphony import (
    ArtifactSpec,
    Fingerprint,
    FingerprintService,
    OrchestrationConfig,
    OrchestrationService,
    ParameterSpec,
    SkillGraphUpdater,
    SymphonyGraphEngine,
    SymphonyRuntime,
)
from openjiuwen.symphony.orchestration.graph.build import GraphBuildPipeline
from openjiuwen.symphony.orchestration.graph.candidates import CandidateGenerator
from openjiuwen.symphony.orchestration.graph.models import (
    GraphDiagnostic,
    LLMMatch,
    SkillRegistry,
)


def _capability(
    capability_id: str,
    *,
    inputs: list[ParameterSpec] | None = None,
    outputs: list[ArtifactSpec] | None = None,
) -> Fingerprint:
    return Fingerprint(
        type="skill",
        id=capability_id,
        name=capability_id,
        description=f"Capability {capability_id}",
        version="1.0.0",
        inputs=inputs or [],
        outputs=outputs or [],
    )


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
        return {"matcher": "test"}


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
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
        if "candidates" in payload and "current_skill" in payload:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "judgements": [
                            {
                                "candidate_id": item["candidate_id"],
                                "score": 0.9,
                                "reason": "useful",
                            }
                            for item in payload["candidates"]
                        ]
                    }
                )
            )
        return SimpleNamespace(
            content=json.dumps(
                {
                    "title": "Plan",
                    "status": "ready",
                    "steps": [
                        {"skill_id": "extract"},
                        {"skill_id": "summarize"},
                    ],
                    "can_feed_edges": [{"source_id": "extract", "target_id": "summarize"}],
                }
            )
        )


def _inventory() -> list[Fingerprint]:
    return [
        _capability(
            "extract",
            outputs=[ArtifactSpec(name="text", type="text")],
        ),
        _capability(
            "summarize",
            inputs=[ParameterSpec(name="text", type="text")],
            outputs=[ArtifactSpec(name="summary", type="text")],
        ),
    ]


def test_runtime_wires_internal_fingerprint_service(tmp_path: Path) -> None:
    fingerprint_service = cast(FingerprintService, SimpleNamespace())

    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=None,
        fingerprint_service=fingerprint_service,
    )

    assert isinstance(runtime.graph_engine, SymphonyGraphEngine)
    assert isinstance(runtime.graph_engine, SkillGraphUpdater)
    assert not isinstance(runtime.orchestration, SkillGraphUpdater)
    assert runtime.orchestration.fingerprint_service is fingerprint_service


def test_exact_io_candidate_and_graph_materialization() -> None:
    registry = SkillRegistry(skills={item.id: item for item in _inventory()})
    candidates = CandidateGenerator().generate(registry)

    assert [(item.source_id, item.target_id) for item in candidates] == [("extract", "summarize")]
    assert "exact_io_match" in candidates[0].candidate_methods


@pytest.mark.asyncio
async def test_graph_builder_materializes_accepted_exact_io() -> None:
    result = await GraphBuildPipeline(resolver=_AcceptMatcher()).build(_inventory())

    assert [node.properties["capability_id"] for node in result.graph.nodes] == [
        "extract",
        "summarize",
    ]
    assert [(edge.source, edge.target) for edge in result.graph.edges] == [
        ("capability:extract", "capability:summarize")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_graph_engine_build_and_plan_fast_or_beam(tmp_path: Path, mode: str) -> None:
    events: list[dict] = []
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FakeLLM(),
        config=OrchestrationConfig(mode=mode, max_depth=2),
    )
    graph_engine = SymphonyGraphEngine(service)

    built = await graph_engine.build(progress_callback=events.append)
    result = await graph_engine.plan(
        "extract and summarize",
        candidate_ids=["extract"],
        progress_callback=events.append,
        language="en",
    )

    assert built.version
    assert graph_engine.status().version == built.version
    assert graph_engine.read()["schema_version"].startswith("1.")
    assert result["language"] == "en"
    assert result["execution_graph"]["nodes"]
    assert result["capability_retrieval"]["candidate_ids"] == ["extract"]
    assert events


@pytest.mark.asyncio
async def test_fast_planner_requests_minimal_reasoning_and_disables_thinking(tmp_path: Path) -> None:
    llm = _FakeLLM()
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=llm,
        config=OrchestrationConfig(mode="fast"),
    )
    await service.build()

    await service.plan("extract and summarize", language="en")

    planning_call = next(
        call
        for call in llm.calls
        if set(json.loads(call["messages"][-1]["content"])) == {"query", "skills", "can_feed_edges"}
    )
    system_prompt = planning_call["messages"][0]["content"]
    assert "Prioritize low latency" in system_prompt
    assert "minimum internal reasoning" in system_prompt
    assert planning_call["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_artifact_status_read_schema_and_atomic_failed_build(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FakeLLM(),
    )
    first = await service.build()
    before = (tmp_path / "current.json").read_bytes()
    payload = service.read()

    assert service.status().exists is True
    assert payload["schema_version"].startswith("1.")
    assert {item["capability_id"] for item in payload["capabilities"]} == {
        "extract",
        "summarize",
    }
    assert payload["nodes"][0]["id"].startswith("capability:")
    assert payload["source_snapshot"]["capability_count"] == 2
    assert (tmp_path / "versions" / first.version / "graph.json").is_file()
    assert (tmp_path / ".build_runs").is_dir()

    class _FailLLM(_FakeLLM):
        async def invoke(self, messages, **kwargs):
            del messages, kwargs
            raise RuntimeError("boom")

    failing = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FailLLM(),
    )
    with pytest.raises(RuntimeError, match="boom"):
        await failing.build(force=True)
    assert (tmp_path / "current.json").read_bytes() == before
    assert service.read()["generated_at"] == payload["generated_at"]


@pytest.mark.asyncio
async def test_cancel_build_preserves_last_success(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FakeLLM(),
    )
    await service.build()
    before = (tmp_path / "current.json").read_bytes()

    started = asyncio.Event()

    class _BlockingLLM(_FakeLLM):
        async def invoke(self, messages, **kwargs):
            del messages, kwargs
            started.set()
            await asyncio.Event().wait()

    blocking = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_BlockingLLM(),
    )
    task = asyncio.create_task(blocking.build(force=True))
    await started.wait()
    status = await blocking.cancel_build()
    assert status.exists is True
    assert status.building is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "current.json").read_bytes() == before


@pytest.mark.asyncio
async def test_read_rejects_unsupported_schema_major(tmp_path: Path) -> None:
    service = OrchestrationService(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FakeLLM(),
    )
    built = await service.build()
    graph_path = tmp_path / "versions" / built.version / "graph.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "2.0"
    graph_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported Symphony graph schema"):
        service.read()


def test_runtime_exposes_orchestration_and_package_has_no_jiuwenswarm_dependency(
    tmp_path: Path,
) -> None:
    runtime = SymphonyRuntime(
        graph_artifact_root=tmp_path,
        capability_provider=_inventory,
        model=_FakeLLM(),
    )
    assert isinstance(runtime.graph_engine, SymphonyGraphEngine)
    assert not isinstance(runtime.orchestration, SkillGraphUpdater)
    assert runtime.orchestration.graph_artifact_root == tmp_path

    package_root = Path(importlib.import_module("openjiuwen.symphony").__file__).parent
    for source in package_root.rglob("*.py"):
        assert "jiuwenswarm" not in source.read_text(encoding="utf-8")
