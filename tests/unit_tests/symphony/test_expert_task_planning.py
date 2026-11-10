# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Behavior tests for expert task plans and their handoff constraints."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.symphony import OrchestrationConfig, OrchestrationService, SymphonyGraphEngine
from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts


class _TaskModel:
    def __init__(self, draft):
        self.draft = draft
        self.request = None

    async def invoke(self, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        if "candidates" in request:
            return SimpleNamespace(content=json.dumps({
                "judgements": [
                    {"candidate_id": item["candidate_id"], "score": 0.95}
                    for item in request["candidates"]
                ],
            }))
        self.request = request
        return SimpleNamespace(content=json.dumps(self.draft))


def _port(name="evidence", port_type="csv"):
    return {"name": name, "type": port_type}


def _task(task_id, *, expert_id=None, inputs=(), outputs=()):
    task = {
        "id": task_id,
        "title": task_id,
        "task": f"Complete the {task_id} work.",
        "inputs": list(inputs),
        "outputs": list(outputs) or [_port("result", "text")],
    }
    if expert_id is not None:
        return {**task, "type": "expert", "expert_id": expert_id}
    return {**task, "type": "inline", "role": {"name": task_id, "description": f"Handle {task_id}."}}


def _handoff(source, target, output="evidence", target_input="evidence"):
    return {"source": source, "target": target, "source_output": output, "target_input": target_input}


@pytest.fixture
def expert_artifacts(tmp_path):
    experts = [
        {"id": "research", "name": "Research", "outputs": [_port()]},
        {"id": "report", "name": "Report", "inputs": [_port()]},
        {"id": "isolated", "name": "Citation verifier", "description": "Verify citations independently"},
    ]
    edge = {
        "source": "research", "target": "report", "type": "can_feed", "confidence": 0.9,
        "evidence": {"supporting_fields": {"port_mappings": [{
            "source_output": "evidence", "source_type": "csv",
            "target_input": "evidence", "target_type": "csv",
        }]}},
    }
    return GraphArtifacts(tmp_path, {}, experts, {"edges": [edge]}, {})


def _service(tmp_path, draft, monkeypatch, artifacts=None):
    model = _TaskModel(draft)
    service = OrchestrationService(
        graph_artifact_root=tmp_path, capability_provider=[], model=model,
        config=OrchestrationConfig(enabled=True),
        expert_inventory=SimpleNamespace(), expert_fingerprint_service=SimpleNamespace(),
    )
    if artifacts is not None:
        monkeypatch.setattr(service, "_load_expert_task_artifacts", lambda _: artifacts)
    return service, model


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_expert_task_plan_preserves_relevant_isolated_expert(mode, tmp_path, monkeypatch, expert_artifacts):
    draft = {
        "status": "ready",
        "tasks": [_task("first", expert_id="isolated"), _task("second", expert_id="isolated")],
    }
    service, model = _service(tmp_path, draft, monkeypatch, expert_artifacts)

    result = await service.plan_expert_tasks("Verify citations independently", mode=mode)
    graph = result["planned_graph"]["graph"]

    assert "isolated" in {expert["id"] for expert in model.request["experts"]}
    assert graph["metadata"]["status"] == "ready"
    assert graph["metadata"]["plan_kind"] == "expert_tasks"
    assert set(graph["nodes"]) == {"task-1", "task-2"}
    assert all(node["metadata"]["expert_id"] == "isolated" for node in graph["nodes"].values())
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_missing_expert_graph_allows_inline_plan(mode, tmp_path, monkeypatch):
    service, model = _service(tmp_path, {"status": "ready", "tasks": [_task("verify")]}, monkeypatch)

    engine = SymphonyGraphEngine(service, unavailable_reason="Static graph sources are unavailable.")
    result = await engine.plan_expert_tasks("Check the evidence", candidate_ids=["removed"], mode=mode)
    graph = result["planned_graph"]["graph"]

    assert model.request["experts"] == []
    assert model.request["unavailable_candidate_ids"] == ["removed"]
    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-1"]["metadata"]["role"]["name"] == "verify"
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_format", ["mapping", "fields"])
async def test_expert_task_plan_exposes_published_handoff_hints(tmp_path, monkeypatch, expert_artifacts, evidence_format):
    support = expert_artifacts.graph["edges"][0]["evidence"]["supporting_fields"]
    if evidence_format == "mapping":
        support["port_mappings"][0:0] = [
            {"source_output": f"other_{index}", "target_input": f"other_{index}"}
            for index in range(5)
        ]
    else:
        support.clear()
        support.update(source_outputs=[{"name": "evidence", "type": "csv"}], target_inputs=["evidence: csv"])
    draft = {
        "status": "ready",
        "tasks": [
            _task("research", expert_id="research", outputs=[_port()]),
            _task("report", expert_id="report", inputs=[_port()]),
        ],
        "handoffs": [_handoff("research", "report")],
    }
    service, model = _service(tmp_path, draft, monkeypatch, expert_artifacts)

    graph = (await service.plan_expert_tasks("Research and report"))["planned_graph"]["graph"]

    assert model.request["published_expert_handoffs"] == [{
        "source_id": "research",
        "target_id": "report",
        "port_mappings": support.get("port_mappings", []),
        "source_outputs": support.get("source_outputs", []),
        "target_inputs": support.get("target_inputs", []),
    }]
    assert graph["metadata"]["status"] == "ready"
    assert graph["edges"] == [{
        "source": "task-1", "target": "task-2", "relation": "can_feed",
        "source_output": "evidence", "target_input": "evidence",
    }]


@pytest.mark.asyncio
async def test_expert_task_plan_uses_task_local_ports_for_expert_without_published_ports(tmp_path, monkeypatch):
    expert_id = "bilibili-content-strategist"
    artifacts = GraphArtifacts(
        tmp_path, {}, [{"id": expert_id, "name": "Bilibili content strategist", "inputs": [], "outputs": []}], {}, {},
    )
    checklist = _port("platform_rules_checklist", "json")
    script = _port("bilibili_shooting_script", "markdown")
    draft = {
        "status": "ready",
        "tasks": [
            _task("research-rules", outputs=[checklist]),
            _task("write-script", expert_id=expert_id, inputs=[checklist], outputs=[script]),
            _task("independent-review", inputs=[script]),
        ],
        "handoffs": [
            _handoff("research-rules", "write-script", checklist["name"], checklist["name"]),
            _handoff("write-script", "independent-review", script["name"], script["name"]),
        ],
    }
    service, _ = _service(tmp_path, draft, monkeypatch, artifacts)

    result = await service.plan_expert_tasks("Check platform rules, write the script, and review it", [expert_id])
    graph = result["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-2"]["metadata"]["expert_id"] == expert_id
    assert graph["nodes"]["task-3"]["metadata"]["type"] == "inline"
    assert graph["edges"] == [
        {
            "source": "task-1", "target": "task-2", "relation": "can_feed",
            "source_output": "platform_rules_checklist", "target_input": "platform_rules_checklist",
        },
        {
            "source": "task-2", "target": "task-3", "relation": "can_feed",
            "source_output": "bilibili_shooting_script", "target_input": "bilibili_shooting_script",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tasks", "handoffs", "reason"), [
    (
        [_task("first", expert_id="research", outputs=[_port()]),
         _task("second", inputs=[_port()])],
        [_handoff("first", "second", output="undeclared")],
        "unknown task or port",
    ),
    (
        [_task("first", outputs=[_port()]),
         _task("second", inputs=[_port(port_type="image")])],
        [_handoff("first", "second")],
        "incompatible types",
    ),
    (
        [_task("first", expert_id="research", inputs=[_port()], outputs=[_port()]),
         _task("second", inputs=[_port()], outputs=[_port()])],
        [_handoff("first", "second"), _handoff("second", "first")],
        "cycle",
    ),
    (
        [_task("first", outputs=[_port()])],
        [_handoff("first", "missing")],
        "unknown task or port",
    ),
    ([_task("first", expert_id="removed")], [], "visible installed expert"),
])
async def test_expert_task_plan_rejects_invalid_assignments(
    tasks, handoffs, reason, tmp_path, monkeypatch, expert_artifacts,
):
    service, _ = _service(
        tmp_path, {"status": "ready", "tasks": tasks, "handoffs": handoffs}, monkeypatch, expert_artifacts,
    )

    graph = (await service.plan_expert_tasks("Research and report"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "no_plan"
    assert reason in graph["metadata"]["reason"]
    assert graph["nodes"] == {}
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_edge", [False, True])
async def test_expert_task_plan_accepts_unpublished_expert_handoff(tmp_path, monkeypatch, expert_artifacts, keep_edge):
    if keep_edge:
        expert_artifacts.graph["edges"][0]["evidence"]["supporting_fields"] = {}
    else:
        expert_artifacts.graph["edges"] = []
    draft = {
        "status": "ready",
        "tasks": [
            _task("research", expert_id="research", outputs=[_port()]),
            _task("report", expert_id="report", inputs=[_port()]),
        ],
        "handoffs": [_handoff("research", "report")],
    }
    service, _ = _service(tmp_path, draft, monkeypatch, expert_artifacts)

    result = await service.plan_expert_tasks("Research and report", candidate_ids=["research", "report"])
    graph = result["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["edges"] == [{
        "source": "task-1", "target": "task-2", "relation": "can_feed",
        "source_output": "evidence", "target_input": "evidence",
    }]


@pytest.mark.asyncio
async def test_expert_task_plan_returns_missing_inputs_without_tentative_roles(tmp_path, monkeypatch):
    service, _ = _service(
        tmp_path,
        {"status": "needs_input", "missing_inputs": ["source document"], "tasks": [_task("verify")]},
        monkeypatch,
    )

    graph = (await service.plan_expert_tasks("Verify the evidence"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "needs_input"
    assert graph["metadata"]["missing_inputs"] == ["source document"]
    assert graph["nodes"] == {}
