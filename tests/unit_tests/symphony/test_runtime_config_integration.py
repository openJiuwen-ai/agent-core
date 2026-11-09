"""Exercise configured Runtime entry points with local inventory and native-model doubles."""

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.symphony import EvolutionSubmitResult, SymphonyConfig, SymphonyRuntime


def make_config(tmp_path, **overrides):
    return SymphonyConfig.model_validate({
        "capability": {"root": str(tmp_path / "capabilities"), "type": "skill"},
        "storage": {"artifact_root": str(tmp_path / "runtime")},
        **overrides,
    })


def write_capability(tmp_path, name="alpha", description="Process invoice documents"):
    root = tmp_path / "capabilities" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nInvoice processing instructions.\n")
    return root


@pytest.mark.asyncio
async def test_constructor_is_lazy_isolated_and_does_not_close_host_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = SimpleNamespace(invoke=AsyncMock(), aclose=AsyncMock())
    config = SymphonyConfig(capability={"root": "capabilities", "type": "skill"}, storage={"artifact_root": "runtime"})
    runtime = SymphonyRuntime(model, config)
    config.retrieval.prompt.preferred_capability_ids.append("external-mutation")
    assert "external-mutation" not in runtime.config.retrieval.prompt.preferred_capability_ids
    assert runtime.config.capability.root == str(tmp_path / "capabilities")
    assert runtime.config.storage.artifact_root == str(tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()
    assert not hasattr(runtime, "graph_engine") and not hasattr(runtime, "flow_engine")
    await runtime.aclose()
    await runtime.aclose()
    model.invoke.assert_not_called()
    model.aclose.assert_not_called()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.asyncio
async def test_independent_disabled_capabilities_are_queryable_but_reject_execution(tmp_path):
    runtime = SymphonyRuntime(None, make_config(tmp_path, retrieval={"enabled": False}, orchestration={"enabled": False}))
    assert runtime.retrieval.status()["enabled"] is False
    assert runtime.orchestration.status().enabled is False
    assert runtime.flow.status()["enabled"] is False
    for operation in (runtime.retrieval.build, runtime.orchestration.build):
        with pytest.raises(RuntimeError, match="disabled"):
            await operation()
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.retrieval.read()
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.retrieval.create_session(config=runtime.config.retrieval.model_copy(update={"enabled": True}))
    with pytest.raises(RuntimeError, match="disabled"):
        await runtime.flow.start()
    await runtime.aclose()
    assert not (tmp_path / "runtime").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("orchestration", [{}, {"enabled": False, "experience": {"enabled": True}}])
async def test_disabled_experience_rejects_all_new_evidence_without_creating_storage(tmp_path, orchestration):
    runtime = SymphonyRuntime(None, make_config(tmp_path, orchestration=orchestration))
    try:
        assert not runtime.flow.enabled
        assert await runtime.submit_evolution(
            None, {}, session_id="session", capture_mode="agent",
        ) == EvolutionSubmitResult(None)
        with pytest.raises(RuntimeError, match="disabled"):
            runtime.orchestration.submit_observation(None)
        with pytest.raises(RuntimeError, match="disabled"):
            await runtime.flow.start()
        assert not (tmp_path / "runtime").exists()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("kind", ["expert", "connector", "mcp"])
@pytest.mark.asyncio
async def test_unimplemented_reader_fails_explicitly_on_use(tmp_path, kind):
    config = make_config(tmp_path, capability={"root": str(tmp_path / "capabilities"), "type": kind})
    runtime = SymphonyRuntime(None, config)
    with pytest.raises(ValueError, match="Unsupported capability reader"):
        runtime.retrieval.status()
    assert not (tmp_path / "runtime").exists()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_no_model_discovery_and_fallback_build_with_real_directory(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(None, make_config(tmp_path, retrieval={"prompt": {"context_window_tokens": 4000}, "output": {"max_results": 2}}))
    session = runtime.retrieval.create_session(session_scope="test")
    settings = session.environment.settings
    assert settings == runtime.config.retrieval
    assert settings is not runtime.config.retrieval
    runtime.config.retrieval.prompt.preferred_capability_ids.append("runtime-mutation")
    assert "runtime-mutation" not in settings.prompt.preferred_capability_ids
    assert [r.worker_id for r in session.records()] == ["alpha"]
    assert settings.prompt.candidate_budget_tokens == 40
    result = await session.skill_index(operation="search", query="invoice")
    assert "alpha" in str(result)
    built = await runtime.retrieval.build()
    assert built["success"], built
    assert runtime.retrieval.read() is not None
    assert json.loads((tmp_path / "runtime" / "runtime.json").read_text())["schema_version"] == "symphony.runtime.v1"
    with pytest.raises(ValueError, match="requires a model"):
        await runtime.orchestration.build()
    with pytest.raises(ValueError, match="requires a model"):
        await runtime.orchestration.plan("invoice")
    await runtime.aclose()


@pytest.mark.asyncio
async def test_live_session_visibility_and_additional_directory_do_not_enter_config(tmp_path):
    write_capability(tmp_path, "alpha")
    overlay = tmp_path / "session"
    write_capability(overlay, "session-only")
    disabled = set()
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    session = runtime.retrieval.create_session(
        session_scope="overlay", capability_directories=[tmp_path / "capabilities", overlay / "capabilities"],
        disabled_capability_ids=lambda: disabled, visible_capability_ids=lambda: {"alpha", "session-only"},
    )
    assert set(session.environment.selection_cards()) == {"alpha", "session-only"}
    disabled.add("session-only")
    assert set(session.environment.selection_cards(refresh=True)) == {"alpha"}
    assert runtime.config.capability.root == str(tmp_path / "capabilities")
    await runtime.aclose()


@pytest.mark.asyncio
async def test_layout_rejects_legacy_and_different_collection_without_deleting_files(tmp_path):
    write_capability(tmp_path)
    legacy = tmp_path / "runtime"
    legacy.mkdir()
    old = legacy / "old.json"
    old.write_text("historical")
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    with pytest.raises(ValueError, match="layout|populated"):
        await runtime.retrieval.build()
    assert old.read_text() == "historical"
    await runtime.aclose()
    old.unlink()
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    assert (await runtime.retrieval.build())["success"]
    await runtime.aclose()
    other = SymphonyRuntime(None, make_config(tmp_path, capability={"root": str(tmp_path / "other"), "type": "skill"}))
    with pytest.raises(ValueError, match="different collection"):
        other.retrieval.read()
    await other.aclose()


@pytest.mark.asyncio
async def test_async_prepare_hook_cancel_cannot_publish_or_leak_worker(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def prepare(_path):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    task = asyncio.create_task(runtime.retrieval.build(prepare_artifact=prepare))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    await asyncio.wait_for(exited.wait(), 5)
    assert not (tmp_path / "runtime" / "tree" / "index" / "commit.json").exists()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_tree_status_is_read_only_and_build_metadata_tracks_reuse(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    initial = runtime.retrieval.status()
    assert initial["counts"]["added_count"] == 1
    assert initial["version"] == ""
    assert not (tmp_path / "runtime").exists()
    first = await runtime.retrieval.build()
    assert first["success"] and first["build_id"] and first["version"]
    assert first["counts"]["added_count"] == 1
    assert first["counts"]["reused_count"] == 0
    reused = await runtime.retrieval.build()
    assert reused["build_id"] != first["build_id"]
    assert reused["version"] == first["version"]
    assert reused["counts"]["reused_count"] == 1
    assert reused["counts"]["added_count"] == 0
    assert runtime.retrieval.read()["version"] == first["version"]
    assert runtime.retrieval.status()["version"] == first["version"]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_cancel_wrong_tree_build_id_does_not_cancel_current_build(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(None, make_config(tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(_path):
        entered.set()
        await release.wait()

    task = asyncio.create_task(runtime.retrieval.build(prepare_artifact=prepare))
    await asyncio.wait_for(entered.wait(), 5)
    status = await runtime.retrieval.cancel_build(build_id="another-request")
    assert status["status"] == "running"
    assert not task.done()
    release.set()
    assert (await asyncio.wait_for(task, 5))["success"]
    await runtime.aclose()


class GraphModel:
    def __init__(self):
        self.calls = 0

    async def invoke(self, messages, **kwargs):
        self.calls += 1
        try:
            payload = json.loads(messages[-1]["content"])
        except json.JSONDecodeError:
            names = re.findall(r"^### (.+)$", messages[-1]["content"], re.MULTILINE)
            return SimpleNamespace(content=json.dumps({"skills/documents": names}), finish_reason="stop")
        return SimpleNamespace(content=json.dumps({"matches": [
            {"id": item["id"], "direction": next(iter(item["directions"])), "confidence": 0.95}
            for item in payload.get("candidates", [])
        ]}))


@pytest.mark.asyncio
async def test_experience_distills_on_third_success_and_planning_can_read_after_disabling_it(tmp_path):
    for name in ("alpha", "beta"):
        write_capability(tmp_path, name)
    runtime = SymphonyRuntime(GraphModel(), make_config(tmp_path, orchestration={"experience": {"enabled": True}}))
    try:
        await runtime.orchestration.build()
        initial = runtime.capture_graph_snapshot()
        for index in range(3):
            trace_id = f"trace-{index}"
            execution = {
                "trace_id": trace_id, "query": "Process invoice documents", "outcome": "success",
                "graph_snapshot": initial,
                "graph": {
                    "id": trace_id, "type": "execution_graph", "directed": True,
                    "nodes": {
                        f"skill:{name}": {"label": "skill", "metadata": {
                            "capability_type": "skill", "version": "1.0.0", "skill_type": "skill",
                        }}
                        for name in ("alpha", "beta")
                    },
                    "edges": [{"source": "skill:alpha", "target": "skill:beta", "relation": "can_feed",
                               "metadata": {"success": True}}],
                },
            }
            result = await runtime.submit_evolution(None, execution, session_id=trace_id, capture_mode="agent")
            assert result.graph_receipt.status == "accepted"
            assert bool(result.new_candidates) == (index == 2)
        await asyncio.to_thread(runtime.orchestration.flush_observations)
        learned = runtime.capture_graph_snapshot()
        assert learned["observation_revision"] != initial["observation_revision"]
        assert len(runtime.flow.store.read_evidence()) == 3
    finally:
        await runtime.aclose()

    config = make_config(tmp_path, orchestration={"plan": {"dynamic_graph_enabled": True}})
    reader = SymphonyRuntime(GraphModel(), config)
    try:
        assert not reader.flow.enabled
        planned = (await reader.orchestration.plan("invoice", candidate_ids=[])).to_dict()["planned_graph"]
        assert planned["graph_snapshot"]["observation_revision"] == learned["observation_revision"]
    finally:
        await reader.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fast", "beam"])
@pytest.mark.parametrize("candidates", [[], ["unavailable"]])
async def test_empty_or_unknown_candidate_set_never_expands_to_inventory(tmp_path, mode, candidates):
    write_capability(tmp_path)
    model = GraphModel()
    runtime = SymphonyRuntime(model, make_config(tmp_path, orchestration={"plan": {"mode": mode}}))
    await runtime.orchestration.build()
    calls_before = model.calls
    result = (await runtime.orchestration.plan("invoice", candidate_ids=candidates)).to_dict()["planned_graph"]["graph"]
    assert result["metadata"]["status"] == "no_plan"
    assert result["nodes"] == {} and result["edges"] == []
    assert model.calls == calls_before
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["retrieval", "orchestration"])
async def test_inventory_change_in_prepare_hook_keeps_previous_version(tmp_path, kind):
    root = write_capability(tmp_path)
    runtime = SymphonyRuntime(GraphModel(), make_config(tmp_path))
    engine = getattr(runtime, kind)
    first = await engine.build()
    version = first["version"] if kind == "retrieval" else first.version

    def change(_path):
        path = root / "SKILL.md"
        path.write_text(path.read_text() + "\nChanged while building.\n")

    if kind == "retrieval":
        result = await engine.build(force=True, prepare_artifact=change)
        assert result["success"] is False
        assert "inventory changed" in result["result"]
        assert engine.status()["version"] == version
    else:
        with pytest.raises(RuntimeError, match="inventory changed"):
            await engine.build(force=True, prepare_artifact=change)
        assert engine.status().version == version
    await runtime.aclose()


@pytest.mark.asyncio
async def test_first_discovery_session_does_not_claim_unbound_storage(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(None, make_config(tmp_path, retrieval={"use_existing_index": True}))
    session = runtime.retrieval.create_session(pin_index_revision=True)
    assert session.records()
    assert not (tmp_path / "runtime").exists()
    assert (await runtime.retrieval.build())["success"]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_close_drains_graph_mutation_before_it_can_publish(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(GraphModel(), make_config(tmp_path))
    original = await runtime.orchestration.build()
    write_capability(tmp_path, "beta")
    revision = runtime.orchestration._inventory.scan().source_snapshot.snapshot_id
    entered = asyncio.Event()

    async def prepare(_path):
        entered.set()
        await asyncio.Event().wait()

    runtime.orchestration._orchestration_service.prepare_artifact = prepare
    task = asyncio.create_task(runtime.orchestration.add_capabilities(
        ["beta"], request_id="close-test", source_revision=revision,
    ))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.wait_for(runtime.aclose(), 5)
    assert task.done()
    with pytest.raises(asyncio.CancelledError):
        await task
    reader = SymphonyRuntime(None, make_config(tmp_path))
    assert reader.orchestration.status().version == original.version
    await reader.aclose()


@pytest.mark.asyncio
async def test_discovery_limits_and_source_ids_apply_through_session(tmp_path):
    for index in range(6):
        write_capability(tmp_path, f"object-{index}")
    path = tmp_path / "capabilities" / "object-0" / "SKILL.md"
    path.write_text(path.read_text().replace("name: object-0", "name: Display Name"))
    runtime = SymphonyRuntime(None, make_config(tmp_path, retrieval={"output": {"max_results": 2, "max_list_entries": 1}}))
    session = runtime.retrieval.create_session(source_by_id={"object-0": "session"})
    assert next(item for item in session.records() if item.worker_id == "object-0").source == "session"
    listed = await session.skill_index(operation="list")
    searched = await session.skill_index(operation="search", query=["invoice", "documents"])
    assert listed.detailed_output["shown_count"] <= 1
    assert searched.detailed_output["shown_count"] <= 2
    await runtime.aclose()


@pytest.mark.asyncio
async def test_cancelled_close_waiter_still_drains_nested_graph_operation_and_flow(tmp_path):
    write_capability(tmp_path)
    config = make_config(tmp_path, orchestration={"experience": {"enabled": True}})
    runtime = SymphonyRuntime(GraphModel(), config)
    original = await runtime.orchestration.build()
    entered, release_flow = asyncio.Event(), asyncio.Event()
    flow = runtime.flow._get_engine()
    flow._worker_task = asyncio.create_task(release_flow.wait())

    async def prepare(_path):
        await runtime.orchestration.plan("invoice", candidate_ids=[])
        entered.set()
        await asyncio.Event().wait()

    build = asyncio.create_task(runtime.orchestration.build(force=True, prepare_artifact=prepare))
    await asyncio.wait_for(entered.wait(), 5)
    first_close = asyncio.create_task(runtime.aclose())
    while not flow._closed:
        await asyncio.sleep(0)
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close
    second_close = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert not second_close.done()
    release_flow.set()
    await asyncio.wait_for(second_close, 5)
    with pytest.raises(asyncio.CancelledError):
        await build
    assert flow._worker_task.done()
    reader = SymphonyRuntime(None, config)
    assert reader.orchestration.status().version == original.version
    await reader.aclose()


@pytest.mark.asyncio
async def test_runtime_close_waits_for_graph_staging_thread(tmp_path, monkeypatch):
    import threading

    write_capability(tmp_path)
    runtime = SymphonyRuntime(GraphModel(), make_config(tmp_path))
    original = await runtime.orchestration.build()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    store = runtime.orchestration._orchestration_service._store
    stage = store.stage

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        try:
            return stage(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(store, "stage", blocked)
    build = asyncio.create_task(runtime.orchestration.build(force=True))
    assert await asyncio.to_thread(entered.wait, 5)
    close = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0.02)
    assert not close.done() and not finished.is_set()
    release.set()
    await asyncio.wait_for(close, 5)
    with pytest.raises(asyncio.CancelledError):
        await build
    assert finished.is_set() and store.current_version() == original.version


@pytest.mark.asyncio
async def test_graph_progress_tail_keeps_build_identity_and_current_operation_counts(tmp_path):
    write_capability(tmp_path)
    runtime = SymphonyRuntime(GraphModel(), make_config(tmp_path))
    published, release = asyncio.Event(), asyncio.Event()

    async def progress(event):
        if event["event"] == "build_published":
            published.set()
            await release.wait()

    pending = asyncio.create_task(runtime.orchestration.build(progress=progress))
    await asyncio.wait_for(published.wait(), 5)
    active_id = runtime.orchestration.status().build_id
    with pytest.raises(RuntimeError, match="already running"):
        await runtime.orchestration.build()
    release.set()
    first = await pending
    assert first.build_id == active_id == runtime.orchestration.read()["build_id"]
    reused = await runtime.orchestration.build()
    assert reused.version == first.version and reused.build_id != first.build_id
    assert reused.counts["graph_reused_count"] == 1
    assert reused.counts["relation_resolved_count"] == 0
    await runtime.aclose()
