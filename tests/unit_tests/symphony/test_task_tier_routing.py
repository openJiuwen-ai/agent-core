"""Public-entry behavior for automatic task routing and fixed planner compatibility."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.symphony import OrchestrationConfig, OrchestrationService, PlanningContext, SymphonyGraphEngine
from openjiuwen.symphony.orchestration.artifacts import GraphArtifacts
from openjiuwen.symphony.orchestration.routing import route_task


_LABELLED_CASES = json.loads((Path(__file__).parent / "fixtures" / "task_tier_cases.json").read_text(encoding="utf-8"))


def _context(**changes):
    return PlanningContext.model_validate({
        "request_id": "request-a",
        "skills": [{"id": "read"}, {"id": "write"}],
        "tools": [{"id": "search"}, {"id": "read_file"}],
        "experts": [{"id": "reviewer"}], **changes,
    })


class _Model:
    def __init__(self, route, task_count=None):
        self.route = route
        self.task_count = task_count
        self.requests = []
        self.system_prompts = []
        self.invoke_options = []

    async def invoke(self, messages, **kwargs):
        data = json.loads(messages[-1]["content"])
        self.requests.append(data)
        self.invoke_options.append(kwargs)
        self.system_prompts.append(messages[0]["content"])
        if data.get("operation") == "task_routing":
            response = self.route(data) if callable(self.route) else self.route
        elif "experts" in data:
            count = self.task_count if self.task_count is not None else data.get("max_tasks", 2)
            response = {"tasks": [
                {"id": f"task-{index}", "task": "Check the evidence.",
                 "role": {"name": "Verifier", "description": "Independently check facts."},
                 "outputs": [{"name": "review", "type": "text"}]}
                for index in range(count)
            ]}
        else:
            response = {"status": "ready", "steps": [{"skill_id": "read"}, {"skill_id": "write"}]}
        return SimpleNamespace(content=json.dumps(response))


def _service(tmp_path, model):
    return OrchestrationService(graph_artifact_root=tmp_path / "graph", capability_provider=[], model=model,
                                config=OrchestrationConfig(enabled=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("route, strategy", [
    ({"strategy": "direct_answer", "reason": "Known facts suffice"}, "direct_answer"),
    ({"strategy": "single_capability", "reason": "One Skill", "skill_ids": ["read"]}, "single_capability"),
    ({"strategy": "single_capability", "reason": "One tool", "tool_ids": ["search"]}, "single_capability"),
    ({"strategy": "capability_workflow", "reason": "Host tools", "tool_ids": ["search", "read_file"]},
     "capability_workflow"),
    ({"strategy": "capability_workflow", "reason": "Mixed host workflow",
      "tool_ids": ["search"], "skill_ids": ["write"]},
     "capability_workflow"),
])
@pytest.mark.parametrize("target", ["service", "engine"])
async def test_auto_routes_without_building_or_planning(tmp_path, route, strategy, target):
    model = _Model(route)
    service = _service(tmp_path, model)
    planner = service if target == "service" else SymphonyGraphEngine(service, unavailable_reason="No Skill graph")
    events = []
    candidates = [identifier
                  for kind in ("skill", "expert", "tool")
                  for identifier in route.get(f"{kind}_ids", [])]
    result = await planner.plan("Complete the task", candidates, context=_context(), progress=events.append)
    assert all("extra_body" not in options for options in model.invoke_options)
    assert result["execution_route"]["strategy"] == strategy
    assert "planned_graph" not in result
    assert len(model.requests) == 1
    assert not (tmp_path / "graph").exists()
    assert [item.event for item in events] == ["route_started", "route_completed"]
    supplied = model.requests[0]["context"]
    assert {item["id"]
            for kind in ("skills", "experts", "tools")
            for item in supplied[kind]} == set(candidates)
    assert set(supplied) == {"skills", "experts", "tools", "context_complete"}


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy, count", [("single_expert", 1), ("multi_expert", 2)])
@pytest.mark.parametrize("mode", ["fast", "beam"])
async def test_expert_strategies_need_no_skill_graph_and_limit_workers(tmp_path, strategy, count, mode):
    model = _Model({"strategy": strategy, "reason": "Independent specialist verification"})
    engine = SymphonyGraphEngine(_service(tmp_path, model), unavailable_reason="No Skill graph")
    result = await engine.plan("Verify the evidence", context=_context(experts=[]), mode=mode)
    assert all("extra_body" not in options for options in model.invoke_options)
    assert model.requests[0]["context"]["experts"] == []
    prompt = " ".join(model.system_prompts[0].split())
    assert "use an expert route even when no installed expert is listed or suitable" in prompt
    assert "planner can create task-specific inline roles" in prompt
    assert result["execution_route"]["strategy"] == strategy
    graph = result["planned_graph"]["graph"]
    assert graph["metadata"]["plan_kind"] == "expert_tasks"
    assert len(graph["nodes"]) == count
    assert len(model.requests) == 2
    assert engine._observation_service is None


@pytest.mark.asyncio
async def test_single_expert_overflow_reconsiders_multi_expert_only_once(tmp_path):
    def decide(payload):
        return {
            "strategy": "multi_expert" if payload.get("planning_feedback") else "single_expert",
            "reason": "Independent verification",
        }

    model = _Model(decide, task_count=2)
    result = await _service(tmp_path, model).plan("Verify independently", context=_context())
    assert result["execution_route"]["strategy"] == "multi_expert"
    assert result["execution_route"]["reconsiderations"] == 1
    assert len(result["planned_graph"]["graph"]["nodes"]) == 2
    assert len(model.requests) == 4


@pytest.mark.asyncio
async def test_skill_workflow_prepares_graph_after_routing_and_filters_visibility(tmp_path, monkeypatch):
    model = _Model({"strategy": "capability_workflow", "reason": "Two Skills", "skill_ids": ["read", "write"]})
    service = _service(tmp_path, model)
    engine = SymphonyGraphEngine(service)
    artifacts = GraphArtifacts(tmp_path, {}, [
        {"id": "read", "name": "Read", "outputs": [{"name": "text", "type": "text"}]},
        {"id": "write", "name": "Write", "inputs": [{"name": "text", "type": "text"}]},
        {"id": "hidden", "name": "Hidden"},
    ], {"edges": []}, {})
    monkeypatch.setattr("openjiuwen.symphony.orchestration.service.load_graph_artifacts", lambda *a, **k: artifacts)
    prepared = []

    async def prepare():
        assert len(model.requests) == 1
        prepared.append(True)

    result = await engine.plan(
        "Read then write", ["read", "write"],
        context=_context(skills=[{"id": key} for key in ["read", "write", "hidden"]]), prepare_skill_graph=prepare,
    )
    assert prepared == [True]
    assert result["execution_route"]["strategy"] == "capability_workflow"
    assert {item["id"] for item in model.requests[-1]["skills"]} == {"read", "write"}
    assert set(result["planned_graph"]["graph"]["nodes"]) == {"read", "write"}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid, context", [
    ({"strategy": "single_capability", "reason": "Unknown Skill", "skill_ids": ["hidden"]}, {}),
    ({"strategy": "multi_expert", "reason": "Delegation despite restriction"}, {"allow_experts": False}),
    ({"strategy": "direct_answer", "reason": "Must not silently drop a required expert"},
     {"required_expert_ids": ["reviewer"]}),
])
async def test_invalid_decisions_get_one_repair_then_fail(tmp_path, invalid, context):
    model = _Model(invalid)
    view = _context(**context)
    candidates = None
    if invalid.get("skill_ids"):
        view = _context(skills=[{"id": "read"}, {"id": "hidden"}])
        candidates = ["read"]
    with pytest.raises(ValueError, match="invalid decision"):
        await _service(tmp_path, model).plan("Task", candidates, context=view)
    assert len(model.requests) == 2
    assert "validation_feedback" in model.requests[-1]
    if not view.allow_experts:
        assert "single_expert" not in model.system_prompts[0]
        assert "multi_expert" not in model.system_prompts[0]
        assert "allow_experts" not in model.requests[0]["context"]
        assert "experts" not in model.requests[0]["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_routing_failure_propagates_without_retry_or_execution(tmp_path, error):
    def failure(_request):
        raise error("interrupted")

    model = _Model(failure)
    engine = SymphonyGraphEngine(_service(tmp_path, model))
    with pytest.raises(error, match="interrupted"):
        await engine.plan("Task", context=_context())
    assert len(model.requests) == 1
    assert not engine._operations


def test_route_is_not_an_exported_host_entrypoint(tmp_path):
    import openjiuwen.symphony as symphony

    engine = SymphonyGraphEngine(_service(tmp_path, _Model({})))
    assert not hasattr(engine, "route")
    assert not hasattr(symphony, "route")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _LABELLED_CASES, ids=lambda case: case["id"])
async def test_labelled_route_decisions_obey_execution_contract(case):
    # Replay curated labels to test host contracts; this is not a model-accuracy evaluation.
    expected = case["expected"]
    draft = {key: value for key, value in expected.items() if key != "requires_planning"}
    draft["reason"] = case["rationale"]
    model = _Model(draft)
    result = await route_task(case["query"], model=model, context=_context(), inputs=[])
    assert result.strategy == expected["strategy"]
    assert result.requires_planning == expected["requires_planning"]
    assert model.requests[0]["query"] == case["query"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_kind", ["sequence", "protocol"])
async def test_default_sdk_inventory_supports_dicts_and_capability_provider(tmp_path, provider_kind):
    rows = [{"id": "read", "type": "skill", "name": "Read"},
            {"id": "other", "type": "skill", "name": "Other"}]
    queried = []

    async def capabilities():
        queried.append(True)
        return rows

    model = _Model({"strategy": "single_capability", "reason": "One Skill", "skill_ids": ["read"]})
    service = _service(tmp_path, model)
    service.capability_provider = rows if provider_kind == "sequence" else SimpleNamespace(capabilities=capabilities)
    result = await service.plan("Read the material", candidate_ids=["read"])
    assert result["execution_route"]["skill_ids"] == ["read"]
    assert [item["id"] for item in model.requests[0]["context"]["skills"]] == ["read"]
    assert "candidate_ids" not in model.requests[0]
    assert bool(queried) == (provider_kind == "protocol")


@pytest.mark.asyncio
async def test_unavailable_inventory_entry_is_never_routed_directly(tmp_path):
    model = _Model({"strategy": "single_capability", "reason": "Unavailable", "skill_ids": ["read"]})
    service = _service(tmp_path, model)
    service.capability_provider = [{"id": "read", "type": "skill", "available": False}]
    with pytest.raises(ValueError, match="invalid decision"):
        await service.plan("Read", candidate_ids=["read"])
    assert model.requests[0]["context"]["skills"] == []


@pytest.mark.asyncio
async def test_manifest_restriction_applies_before_router_model(tmp_path):
    model = _Model({"strategy": "direct_answer", "reason": "Known facts"})
    service = _service(tmp_path, model)
    service.expert_inventory = SimpleNamespace(experts=lambda **kwargs: SimpleNamespace(capabilities=[]))
    await service.plan("Answer", context=_context(), expert_manifest_paths=[])
    assert model.requests[0]["context"]["experts"] == []
    assert "reviewer" not in json.dumps(model.requests[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["dynamic_overlay", "graph_revision", "graph_snapshot", "task_cluster_id"])
async def test_auto_rejects_explicit_graph_options_without_model_call(tmp_path, option):
    model = _Model({"strategy": "direct_answer", "reason": "Known facts"})
    with pytest.raises(ValueError, match="require type=skill"):
        await _service(tmp_path, model).plan("Task", **{option: {}})
    assert model.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["auto", "expert"])
async def test_engine_preserves_expert_cluster_rejection(tmp_path, kind):
    model = _Model({})
    with pytest.raises(ValueError, match="Skill graph options"):
        await SymphonyGraphEngine(_service(tmp_path, model)).plan("Task", type=kind, task_cluster_id="cluster")
    assert model.requests == []


@pytest.mark.asyncio
async def test_required_expert_is_checked_on_the_final_graph(tmp_path, monkeypatch):
    model = _Model({"strategy": "single_expert", "reason": "Required expert", "expert_ids": ["reviewer"]})
    service = _service(tmp_path, model)
    # The planner sees the required expert but incorrectly returns an inline task.
    artifacts = GraphArtifacts(tmp_path, {}, [{"id": "reviewer", "name": "Reviewer"}], {"edges": []}, {})
    monkeypatch.setattr(service, "_load_expert_task_artifacts", lambda _: artifacts)
    with pytest.raises(ValueError, match="required installed expert"):
        await service.plan("Verify", context=_context(required_expert_ids=["reviewer"]))
    supplied = model.requests[-1]["inputs"]
    assert supplied == [{"name": "required_capabilities", "kind": "text", "value": json.dumps({
        "required_skill_ids": [], "required_expert_ids": ["reviewer"],
    })}]


@pytest.mark.asyncio
async def test_router_context_and_input_previews_are_bounded(tmp_path):
    model = _Model({"strategy": "direct_answer", "reason": "Self-contained question"})
    await _service(tmp_path, model).plan("What is two plus two?", context=_context(
        skills=[{"id": f"skill-{index}", "description": "c" * 5000} for index in range(100)],
    ), inputs=[{"name": "large" * 1000, "description": "e" * 100000,
                "kind": "text", "value": "d" * 100000}])
    request = model.requests[0]
    assert len(request["context"]["skills"]) == 32
    assert request["context"]["context_complete"] is False
    assert request["inputs"][0]["preview_truncated"] is True
    assert len(json.dumps(request)) < 50000


@pytest.mark.asyncio
async def test_no_plan_reconsiders_once_and_can_use_inline_expert(tmp_path, monkeypatch):
    def decide(payload):
        return ({"strategy": "single_expert", "reason": "Skills cannot cover the specialist assignment"}
                if payload.get("planning_feedback") else
                {"strategy": "capability_workflow", "reason": "Try two Skills", "skill_ids": ["read", "write"]})

    model = _Model(decide)
    service = _service(tmp_path, model)
    prepared = []

    async def skill_plan(*args, **kwargs):
        prepared.append(True)
        return {"planned_graph": {"graph": {"nodes": {}, "metadata": {"status": "no_plan"}}}}

    monkeypatch.setattr(service, "plan_skill_graph", skill_plan)
    result = await service.plan("Complete the task", ["read", "write"], context=_context())
    assert result["execution_route"]["strategy"] == "single_expert"
    assert result["execution_route"]["reconsiderations"] == 1
    assert len(model.requests) == 3
    assert model.requests[0]["context"]["experts"] == model.requests[1]["context"]["experts"] == []
    assert prepared == [True]


@pytest.mark.asyncio
async def test_exhausted_request_budget_does_not_route_a_third_time(tmp_path, monkeypatch):
    model = _Model({"strategy": "capability_workflow", "reason": "Two Skills", "skill_ids": ["read", "write"]})
    service = _service(tmp_path, model)

    async def no_plan(*args, **kwargs):
        return {"planned_graph": {"graph": {"nodes": {}, "metadata": {"status": "no_plan"}}}}

    monkeypatch.setattr(service, "plan_skill_graph", no_plan)
    result = await service.plan("Task", context=_context(reconsiderations=1))
    assert result["execution_route"]["reconsiderations"] == 1
    assert result["planned_graph"]["graph"]["metadata"]["status"] == "no_plan"
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_skill_downgrade_reports_actual_id_and_preserves_full_named_inputs(tmp_path, monkeypatch):
    model = _Model({"strategy": "capability_workflow", "reason": "Two Skills", "skill_ids": ["read", "write"]})
    service = _service(tmp_path, model)
    captured = []

    async def skill_plan(*args, **kwargs):
        captured.extend(kwargs["inputs"])
        return {"planned_graph": {"graph": {"nodes": {"read": {"metadata": {}}},
                                            "metadata": {"status": "ready"}}}}

    monkeypatch.setattr(service, "plan_skill_graph", skill_plan)
    material = {"name": "evidence", "kind": "text", "value": "original" * 1000}
    result = await service.plan("Read the evidence", context=_context(), inputs=[material])
    route = result["execution_route"]
    assert route["strategy"] == "single_capability"
    assert route["initial_strategy"] == "capability_workflow"
    assert route["skill_ids"] == ["read"]
    assert captured[0].value == material["value"]
    assert len(captured) == 1
    assert result["planning_inputs"] == [material]


@pytest.mark.asyncio
async def test_ready_skill_route_includes_nodes_added_by_graph_expansion(tmp_path, monkeypatch):
    model = _Model({"strategy": "capability_workflow", "reason": "Two seed Skills", "skill_ids": ["read", "write"]})
    service = _service(tmp_path, model)

    async def expanded_plan(*args, **kwargs):
        return {"planned_graph": {"graph": {"nodes": {key: {"metadata": {}} for key in ["read", "bridge", "write"]},
                                            "metadata": {"status": "ready"}}}}

    monkeypatch.setattr(service, "plan_skill_graph", expanded_plan)
    result = await service.plan("Task", context=_context(skills=[{"id": key} for key in ["read", "bridge", "write"]]))
    assert result["execution_route"]["skill_ids"] == ["read", "bridge", "write"]
