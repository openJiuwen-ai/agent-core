# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Behavior tests for expert task plans and their handoff constraints."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from openjiuwen.symphony import OrchestrationConfig, OrchestrationService, PlanningInput, SymphonyGraphEngine
from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts


class _TaskModel:
    def __init__(self, draft):
        self.draft = draft
        self.request = None
        self.planning_prompts = []
        self.planning_requests = []
        self.judge_requests = []

    async def invoke(self, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        if "candidates" in request:
            self.judge_requests.append(request)
            return SimpleNamespace(content=json.dumps({
                "judgements": [
                    {"candidate_id": item["candidate_id"], "score": 0.95}
                    for item in request["candidates"]
                ],
            }))
        self.request = request
        self.planning_prompts.append(messages[0]["content"])
        self.planning_requests.append(request)
        draft = self.draft(request, messages[0]["content"]) if callable(self.draft) else self.draft
        return SimpleNamespace(content=json.dumps(draft))


def _port(name="evidence", port_type="csv"):
    return {"name": name, "type": port_type}


def _task(task_id, *, expert_id=None, inputs=(), outputs=()):
    task = {
        "id": task_id,
        "title": task_id,
        "task": f"Complete the {task_id} work.",
        "outputs": list(outputs) or [_port("result", "text")],
    }
    if inputs:
        task["inputs"] = list(inputs)
    if expert_id is not None:
        return {**task, "expert_id": expert_id}
    return {**task, "role": {"name": task_id, "description": f"Handle {task_id}."}}


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

    result = await service.plan_subagent_graph("Verify citations independently", mode=mode)
    graph = result["planned_graph"]["graph"]

    assert "isolated" in {expert["id"] for expert in model.request["experts"]}
    assert graph["metadata"]["status"] == "ready"
    assert graph["metadata"]["plan_kind"] == "expert_tasks"
    assert set(graph["nodes"]) == {"task-1", "task-2"}
    assert graph["nodes"]["task-1"]["label"].startswith("first · ")
    assert all(node["metadata"]["expert_id"] == "isolated" for node in graph["nodes"].values())
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_missing_expert_graph_allows_inline_plan(mode, tmp_path, monkeypatch):
    service, model = _service(tmp_path, {"status": "ready", "tasks": [_task("verify")]}, monkeypatch)

    engine = SymphonyGraphEngine(service, unavailable_reason="Static graph sources are unavailable.")
    result = await engine.plan_subagent_graph("Check the evidence", candidate_ids=["removed"], mode=mode)
    graph = result["planned_graph"]["graph"]

    assert model.request["experts"] == []
    assert model.request["unavailable_candidate_ids"] == ["removed"]
    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-1"]["metadata"]["role"]["name"] == "verify"
    assert graph["edges"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "expected_title"), [
    ("核对 facts.csv 的读数。保留原单位。", "核对 facts.csv 的读数。"),
    ("Check version 2.1 of facts.csv. Keep the original units.", "Check version 2.1 of facts.csv."),
    ("Check the readings\n- Keep the original units.", "Check the readings"),
])
async def test_compact_draft_projects_title_and_input_gaps(tmp_path, monkeypatch, body, expected_title):
    task = {
        "id": "verify", "task": body, "role": {"name": "Verifier", "description": "Check readings."},
        "external_inputs": ["facts", "units"], "outputs": [_port("review", "text")],
    }
    source = PlanningInput(name="facts", kind="file", value="facts.csv")
    service, model = _service(tmp_path, {"tasks": [task]}, monkeypatch)

    graph = (await service.plan_subagent_graph("Verify the readings", inputs=[source]))["planned_graph"]["graph"]

    assert len(model.planning_requests) == 1
    assert graph["metadata"]["status"] == "ready"
    assert graph["metadata"]["missing_inputs"] == [{"name": "units", "kind": "text", "description": ""}]
    assert graph["nodes"]["task-1"]["label"].startswith(f"{expected_title} · ")
    assert graph["nodes"]["task-1"]["metadata"]["task"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_type", [False, True])
async def test_expert_task_plan_derives_type_from_assignment(tmp_path, monkeypatch, expert_artifacts, legacy_type):
    tasks = [_task("report", expert_id="report"), _task("research")]
    if legacy_type:
        tasks[0]["type"] = "expert"
        tasks[1]["type"] = "inline"
    service, model = _service(tmp_path, {"status": "ready", "tasks": tasks}, monkeypatch, expert_artifacts)

    graph = (await service.plan_subagent_graph("Write a report and verify its evidence"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    expert = graph["nodes"]["task-1"]["metadata"]
    inline = graph["nodes"]["task-2"]["metadata"]
    assert expert["type"] == "expert"
    assert expert["expert_id"] == "report"
    assert "role" not in expert
    assert inline["type"] == "inline"
    assert inline["role"]["name"] == "research"
    assert "expert_id" not in inline
    assert len(model.planning_prompts) == 1
    assert '"type": "expert"' not in model.planning_prompts[0]
    assert '"type": "inline"' not in model.planning_prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_expert_task_plan_uses_task_outputs_with_catalog_candidates(mode, tmp_path, monkeypatch, expert_artifacts):
    notes = _port("interview_notes", "markdown")
    collect = _task("collect", expert_id="research", outputs=[notes])
    collect["external_inputs"] = ["transcript"]
    draft = {
        "tasks": [collect, _task("write", expert_id="report", outputs=[_port("brief", "markdown")])],
        "handoffs": [{"source": "collect", "target": "write", "source_output": "interview_notes"}],
    }
    service, model = _service(tmp_path, draft, monkeypatch, expert_artifacts)
    transcript = PlanningInput(name="transcript", kind="file", value="interview.txt")

    result = await service.plan_subagent_graph(
        "Write a Markdown brief from the interview.", ["research"], mode=mode, inputs=[transcript],
    )

    assert {expert["id"] for expert in model.request["experts"]} == {"research", "report"}
    assert all(set(expert) == {"id", "name", "description"} for expert in model.request["experts"])
    assert "published_expert_handoffs" not in model.request
    assert model.request["inputs"] == [{"name": "transcript", "kind": "file", "value": "interview.txt"}]
    assert bool(model.judge_requests) == (mode == "beam")
    assert "csv" not in json.dumps([*model.judge_requests, model.request])

    graph = result["planned_graph"]["graph"]
    assert graph["metadata"]["status"] == "ready"
    assert graph["metadata"]["missing_inputs"] == []
    assert graph["nodes"]["task-1"]["metadata"]["outputs"] == [notes]
    assert graph["nodes"]["task-2"]["metadata"]["inputs"] == [notes]
    assert graph["edges"] == [{
        "source": "task-1", "target": "task-2", "relation": "can_feed",
        "source_output": "interview_notes", "target_input": "interview_notes",
    }]


@pytest.mark.asyncio
async def test_expert_task_plan_derives_ports_for_expert_without_published_ports(tmp_path, monkeypatch):
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
            _task("write-script", expert_id=expert_id, outputs=[script]),
            _task("independent-review"),
        ],
        "handoffs": [
            {"source": "research-rules", "target": "write-script", "source_output": checklist["name"]},
            {"source": "write-script", "target": "independent-review", "source_output": script["name"]},
        ],
    }
    service, model = _service(tmp_path, draft, monkeypatch, artifacts)

    result = await service.plan_subagent_graph("Check platform rules, write the script, and review it", [expert_id])
    graph = result["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-2"]["metadata"]["expert_id"] == expert_id
    assert graph["nodes"]["task-2"]["metadata"]["inputs"] == [checklist]
    assert graph["nodes"]["task-3"]["metadata"]["type"] == "inline"
    assert graph["nodes"]["task-3"]["metadata"]["inputs"] == [script]
    assert len(model.planning_prompts) == 1
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
async def test_expert_task_plan_recovers_legacy_node_ids_in_input_ports(tmp_path, monkeypatch):
    platform_outputs = {
        "xhs-final": "xhs_final", "wechat-final": "wechat_final", "bilibili-final": "bilibili_final",
    }
    review_outputs = [_port("review_record", "text"), _port("final_copies", "text")]
    source = "shared-fact-base"
    review = "independent-review"
    tasks = [
        _task(source, outputs=[_port("shared_fact_base", "text")]),
        *(_task(task, inputs=[source], outputs=[_port(output, "text")]) for task, output in platform_outputs.items()),
        _task(review, inputs=[source, *platform_outputs], outputs=review_outputs),
        _task("delivery-html", inputs=[review], outputs=[_port("report_html", "file")]),
    ]
    handoffs = [
        *(_handoff(source, task, "shared_fact_base", source) for task in platform_outputs),
        *(_handoff(task, review, name, task) for task, name in platform_outputs.items()),
        *(_handoff(review, "delivery-html", port["name"], review) for port in review_outputs),
    ]
    service, model = _service(tmp_path, {"status": "ready", "tasks": tasks, "handoffs": handoffs}, monkeypatch)

    graph = (await service.plan_subagent_graph("Draft three platform launches, review them, and package the result"))[
        "planned_graph"
    ]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert len(model.planning_prompts) == 1
    assert len(graph["nodes"]) == 6
    assert len(graph["edges"]) == 8
    assert graph["nodes"]["task-5"]["metadata"]["inputs"] == [
        _port(name, "text") for name in platform_outputs.values()
    ]
    assert graph["nodes"]["task-6"]["metadata"]["inputs"] == review_outputs
    assert [edge["target_input"] for edge in graph["edges"][-2:]] == ["review_record", "final_copies"]


@pytest.mark.asyncio
async def test_expert_task_plan_groups_types_by_target_alias(tmp_path, monkeypatch):
    draft = {
        "status": "ready",
        "tasks": [
            _task("research", outputs=[_port("notes", "text")]),
            _task("photograph", outputs=[_port("photo", "image")]),
            _task("review"),
            _task("archive", inputs=[_port("research", "text")]),
        ],
        "handoffs": [
            _handoff("research", "review", "notes", "research"),
            _handoff("photograph", "review", "photo", "Research"),
            _handoff("research", "archive", "notes", "research"),
        ],
    }
    service, _ = _service(tmp_path, draft, monkeypatch)

    graph = (await service.plan_subagent_graph("Review and archive the evidence"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-3"]["metadata"]["inputs"] == [_port("research", "unknown")]
    assert graph["nodes"]["task-4"]["metadata"]["inputs"] == [_port("research", "text")]
    assert [(edge["source_output"], edge["target_input"]) for edge in graph["edges"]] == [
        ("notes", "research"), ("photo", "research"), ("notes", "research"),
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
    (
        [_task("first", outputs=[_port()]), _task("second")],
        [_handoff("first", "second"), {"source": "first", "target": "second", "source_output": "evidence"}],
        "duplicate handoff",
    ),
    ([_task("first", expert_id="removed")], [], "visible installed expert"),
    ([{**_task("first"), "expert_id": "research"}], [], "exactly one of expert_id or role"),
    ([{key: value for key, value in _task("first").items() if key != "role"}], [],
     "exactly one of expert_id or role"),
    ([{**_task("first", expert_id="research"), "type": "inline"}], [], "Task type is derived"),
])
async def test_expert_task_plan_rejects_invalid_assignments(
    tasks, handoffs, reason, tmp_path, monkeypatch, expert_artifacts,
):
    service, model = _service(
        tmp_path, {"status": "ready", "tasks": tasks, "handoffs": handoffs}, monkeypatch, expert_artifacts,
    )

    graph = (await service.plan_subagent_graph("Research and report"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "no_plan"
    assert reason in graph["metadata"]["reason"]
    assert graph["nodes"] == {}
    assert graph["edges"] == []
    assert len(model.planning_prompts) == 2
    assert reason in model.planning_requests[1]["validation_feedback"]["error"]


@pytest.mark.asyncio
async def test_invalid_expert_draft_reports_field_after_one_retry(tmp_path, monkeypatch):
    draft = {
        "status": "ready",
        "tasks": [_task("research", outputs=[{"name": "evidence"}])],
    }
    service, model = _service(tmp_path, draft, monkeypatch)

    graph = (await service.plan_subagent_graph("Research the evidence"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "no_plan"
    assert "tasks.0.outputs.0.type" in graph["metadata"]["reason"]
    assert graph["nodes"] == {}
    assert graph["edges"] == []
    assert len(model.planning_prompts) == 2
    feedback = model.planning_requests[1]["validation_feedback"]
    assert "tasks.0.outputs.0.type" in feedback["error"]
    assert json.loads(feedback["previous_output"]) == draft


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expert_in_type", "unknown_expert", "handoff", "schema", "empty"])
async def test_expert_task_plan_corrects_invalid_draft(failure, tmp_path, monkeypatch, expert_artifacts):
    valid = {
        "tasks": [_task("research", expert_id="research"), _task("report", expert_id="report")],
        "handoffs": [_handoff("research", "report", "result", "result")],
    }
    invalid = copy.deepcopy(valid)
    if failure == "expert_in_type":
        invalid["tasks"][1]["type"] = invalid["tasks"][1].pop("expert_id")
    elif failure == "unknown_expert":
        invalid["tasks"][1]["expert_id"] = "report-typo"
    elif failure == "handoff":
        invalid["handoffs"][0]["source_output"] = "undeclared"
    elif failure == "schema":
        invalid["tasks"][1]["outputs"][0].pop("type")
    else:
        invalid["tasks"] = []
        invalid["handoffs"] = []

    def draft(request, system_prompt):
        feedback = request.get("validation_feedback")
        if feedback is None:
            return invalid
        assert json.loads(feedback["previous_output"]) == invalid
        assert feedback["error"]
        return valid

    service, model = _service(tmp_path, draft, monkeypatch, expert_artifacts)
    source = PlanningInput(name="facts", kind="file", value="attachment/facts.md")
    graph = (await service.plan_subagent_graph("Research and report with the supplied facts", inputs=[source]))[
        "planned_graph"
    ]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert len(graph["nodes"]) == 2
    assert graph["nodes"]["task-2"]["metadata"]["expert_id"] == "report"
    assert graph["nodes"]["task-2"]["metadata"]["inputs"] == [_port("result", "text")]
    assert len(model.planning_requests) == 2
    first, second = model.planning_requests
    assert {key: value for key, value in second.items() if key != "validation_feedback"} == first
    assert second["inputs"] == [source.model_dump(exclude_none=True)]


@pytest.mark.asyncio
async def test_expert_task_plan_keeps_explicit_no_plan_without_retry(tmp_path, monkeypatch):
    service, model = _service(tmp_path, {"status": "no_plan", "reason": "Required expert is unavailable."}, monkeypatch)

    graph = (await service.plan_subagent_graph("Use the required expert"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "no_plan"
    assert graph["metadata"]["reason"] == "Required expert is unavailable."
    assert graph["nodes"] == {}
    assert len(model.planning_prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError, asyncio.CancelledError])
async def test_expert_task_plan_does_not_retry_model_failure(tmp_path, monkeypatch, error):
    def draft(request, system_prompt):
        raise error("model interrupted")

    service, model = _service(tmp_path, draft, monkeypatch)
    expected_error = asyncio.CancelledError if error is asyncio.CancelledError else RuntimeError

    with pytest.raises(expected_error, match="model interrupted"):
        await service.plan_subagent_graph("Research the evidence")

    assert len(model.planning_prompts) == 1


@pytest.mark.asyncio
async def test_expert_task_plan_shares_retry_budget_with_missing_inputs(tmp_path, monkeypatch):
    def draft(request, system_prompt):
        if "validation_feedback" not in request:
            return {"status": "needs_input", "missing_inputs": ["facts"]}
        return {"status": "ready", "tasks": [_task("report", expert_id="unavailable")]}

    service, model = _service(tmp_path, draft, monkeypatch)

    graph = (await service.plan_subagent_graph("Plan the report even if some facts are missing"))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "no_plan"
    assert "visible installed expert" in graph["metadata"]["reason"]
    assert graph["nodes"] == {}
    assert len(model.planning_prompts) == 2


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

    result = await service.plan_subagent_graph("Research and report", candidate_ids=["research", "report"])
    graph = result["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["edges"] == [{
        "source": "task-1", "target": "task-2", "relation": "can_feed",
        "source_output": "evidence", "target_input": "evidence",
    }]


@pytest.mark.asyncio
async def test_expert_task_plan_preserves_tasks_with_missing_inputs(tmp_path, monkeypatch):
    source = PlanningInput(name="facts", kind="file", value="attachment/facts.md", description="Approved facts")
    service, _ = _service(
        tmp_path,
        {
            "status": "needs_input",
            "missing_inputs": ["facts", {"name": "price", "kind": "text", "description": "Approved launch price"}],
            "tasks": [{**_task("verify"), "external_inputs": ["facts", "price"]}],
        },
        monkeypatch,
    )

    engine = SymphonyGraphEngine(service)
    graph = (await engine.plan_subagent_graph("Verify the evidence", inputs=[source]))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["metadata"]["missing_inputs"] == [
        {"name": "price", "kind": "text", "description": "Approved launch price"},
    ]
    assert graph["nodes"]["task-1"]["metadata"]["external_inputs"] == ["facts", "price"]
    assert source.value not in json.dumps(graph)


@pytest.mark.asyncio
async def test_only_external_materials_count_as_input_gaps(tmp_path, monkeypatch):
    draft = {
        "tasks": [
            _task("write", outputs=[_port("article"), _port("notes"), _port("photos")]),
            {**_task("publish"), "external_inputs": ["article"]},
            {**_task("review"), "external_inputs": ["article", "copy", "notes", "photos", "price"]},
        ],
        "handoffs": [
            _handoff("write", "review", "article", "copy"),
            _handoff("write", "review", "notes", "notes"),
            _handoff("write", "review", "photos", "photos"),
        ],
        "missing_inputs": [{"name": "photos", "kind": "file", "description": "Original external photographs"}],
    }
    source = PlanningInput(name="notes", kind="text", value="Notes supplied by the user")
    service, _ = _service(tmp_path, draft, monkeypatch)

    graph = (await service.plan_subagent_graph("Publish and review", inputs=[source]))["planned_graph"]["graph"]

    assert graph["metadata"]["status"] == "ready"
    assert graph["nodes"]["task-2"]["metadata"]["external_inputs"] == ["article"]
    assert graph["nodes"]["task-3"]["metadata"]["external_inputs"] == ["notes", "photos", "price"]
    assert graph["metadata"]["missing_inputs"] == [
        draft["missing_inputs"][0], {"name": "article", "kind": "text", "description": ""},
        {"name": "price", "kind": "text", "description": ""},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_empty_expert_draft_with_missing_inputs_gets_one_conditional_attempt(tmp_path, monkeypatch, recover):
    missing_only = {"status": "needs_input", "missing_inputs": ["facts"]}

    def draft(request, _system_prompt):
        assert request["inputs"] == []
        if "validation_feedback" in request:
            assert json.loads(request["validation_feedback"]["previous_output"]) == missing_only
            if recover:
                return {"tasks": [{**_task("read facts"), "external_inputs": ["facts"]}]}
        return missing_only

    service, model = _service(tmp_path, draft, monkeypatch)
    graph = (await service.plan_subagent_graph("Plan a launch. You may consult platform rules."))["planned_graph"]["graph"]

    assert len(model.planning_prompts) == 2
    assert graph["metadata"]["status"] == ("ready" if recover else "no_plan")
    if recover:
        assert graph["nodes"]["task-1"]["metadata"]["external_inputs"] == ["facts"]
        assert [item["name"] for item in graph["metadata"]["missing_inputs"]] == ["facts"]
    else:
        assert graph["nodes"] == {}
        assert graph["metadata"]["reason"] == "The model did not produce a valid conditional task graph."
