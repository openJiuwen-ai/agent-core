"""Exercise mixed collections through published artifacts and the model-facing tool."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.symphony.shared.fingerprint import FingerprintArtifactStore, SkillFolderScanner
from tests.unit_tests.symphony.test_runtime_config_integration import GraphModel


def _config(root, sources):
    return SymphonyConfig(
        capability={"sources": [{"type": kind, "root": str(path)} for kind, path in sources]},
        storage={"artifact_root": str(root / "runtime")},
        retrieval={"enabled": True, "use_existing_index": True, "index_build": {"output": {"show_tree": False}}},
        orchestration={"enabled": True},
    )


def _skill(root, name, description="Reconcile invoices"):
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody.\n")


def _expert(root, name, description="Reconcile invoices", **fields):
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "package_type": "agent_template", "name": name, "description": description, **fields,
    }))
    return manifest


class _HandoffModel(GraphModel):
    model_config = SimpleNamespace(model_name="handoff-fixture")

    def __init__(self):
        super().__init__()
        self.expert_extractions = 0
        self.force_steps = None

    async def invoke(self, messages, **kwargs):
        try:
            payload = json.loads(messages[-1]["content"])
        except json.JSONDecodeError:
            return await super().invoke(messages, **kwargs)
        if "declared_inputs" in payload:
            if payload["capability_type"] == "expert":
                self.expert_extractions += 1
            evidence = {"name": "evidence_table", "type": "csv", "description": "Source evidence table"}
            if "produces evidence_table" in payload["description"]:
                result = {"outputs": [evidence]}
            elif "accepts evidence_table" in payload["description"]:
                result = {"inputs": [{**evidence, "required": True}], "outputs": [{"name": "brief", "type": "text"}]}
            else:
                result = {}
        elif "current_skill" in payload:
            result = {"judgements": [
                {"candidate_id": item["candidate_id"], "score": 0.95} for item in payload["candidates"]
            ]}
        elif "candidate_plans" in payload:
            result = {"selected_plan_index": 1, "steps": [{"skill_id": name} for name in ("ledger", "writer")]}
        elif "can_feed_edges" in payload and "query" in payload:
            ids = {item["id"] for item in payload["skills"]}
            steps = self.force_steps or [name for name in ("ledger", "writer") if name in ids]
            result = {"status": "ready", "steps": [{"skill_id": name} for name in steps]}
        else:
            return await super().invoke(messages, **kwargs)
        return SimpleNamespace(content=json.dumps(result), finish_reason="stop")


@pytest.fixture
def handoff_config(tmp_path):
    skills, experts = tmp_path / "skills", tmp_path / "experts"
    _skill(skills, "ledger", "Extracting invoices produces evidence_table as CSV")
    _skill(skills, "writer", "Writing a brief accepts evidence_table as CSV")
    _expert(experts / "research", "ledger", "Research produces evidence_table as CSV")
    _expert(experts / "report", "writer", "Report accepts evidence_table as CSV and writes a brief")
    _expert(experts / "observer", "observer", "Observe business trends")
    config = _config(tmp_path, [("skill", skills), ("expert", experts)]).model_dump()
    config["capability"]["fingerprint"]["extraction"]["llm_enabled"] = True
    config["orchestration"]["build"]["matching"]["require_consensus"] = False
    return SymphonyConfig.model_validate(config)


@pytest.mark.asyncio
async def test_shared_graph_projects_layers_and_reuses_expert_fingerprints(handoff_config):
    legacy_config = handoff_config.model_dump()
    sources = legacy_config["capability"]["sources"]
    legacy_config["capability"]["sources"] = [item for item in sources if item["type"] == "skill"]
    legacy = SymphonyRuntime(_HandoffModel(), SymphonyConfig.model_validate(legacy_config))
    try:
        old = await legacy.orchestration.build()
    finally:
        await legacy.aclose()

    model = _HandoffModel()
    runtime = SymphonyRuntime(model, handoff_config)
    try:
        assert not runtime.orchestration.status().fresh
        assert (await runtime.retrieval.build())["success"]
        assert model.expert_extractions == 3
        result = await runtime.orchestration.build()
        assert model.expert_extractions == 3
        assert runtime.orchestration.status().fresh
        assert result.counts["skill_count"] == 2 and result.counts["expert_count"] == 3
        assert runtime.orchestration.read(old.version)["schema_version"] == "1.0"
        graph = runtime.orchestration.read()
        assert graph["schema_version"] == "2.0"
        assert {node["id"] for node in graph["nodes"]} == {
            "skill:ledger", "skill:writer", "expert:ledger", "expert:writer", "expert:observer",
        }
        assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
            ("skill:ledger", "skill:writer"), ("expert:ledger", "expert:writer"),
        }
        assert (await runtime.orchestration.build()).version == result.version

        for mode in ("fast", "beam"):
            plan = (await runtime.orchestration.plan("write a brief", ["ledger"], type="expert", mode=mode))[
                "planned_graph"
            ]["graph"]
            assert plan["metadata"]["status"] == "ready"
            assert {key: node["metadata"]["type"] for key, node in plan["nodes"].items()} == {
                "ledger": "expert", "writer": "expert",
            }
            assert plan["edges"] == [{"source": "ledger", "target": "writer", "relation": "can_feed"}]

        plan = (await runtime.orchestration.plan("use ledger", ["ledger"]))["planned_graph"]["graph"]
        assert plan["nodes"]["ledger"]["metadata"]["type"] == "skill"

        model.force_steps = ["ledger", "observer"]
        plan = (await runtime.orchestration.plan("write a brief", ["ledger", "observer"], type="expert"))[
            "planned_graph"
        ]["graph"]
        assert plan["metadata"]["status"] == "no_plan" and not plan["edges"]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_mixed_graph_mutation_and_interrupted_publication_keep_expert_layer(tmp_path, handoff_config):
    runtime = SymphonyRuntime(_HandoffModel(), handoff_config)
    try:
        await runtime.orchestration.build()
        _skill(tmp_path / "skills", "new-skill")
        snapshot = SkillFolderScanner(tmp_path / "skills").scan().source_snapshot
        mutation = await runtime.orchestration.add_capabilities(
            ["new-skill"], request_id="add-skill", source_revision=snapshot.snapshot_id,
        )
        assert mutation.delta.added_node_count == 1 and mutation.delta.removed_node_count == 0
        before = runtime.orchestration.read()
        assert {(edge["source"], edge["target"]) for edge in before["edges"]} == {
            ("skill:ledger", "skill:writer"), ("expert:ledger", "expert:writer"),
        }
        assert runtime.orchestration.status().fresh

        def edit_expert(_version_dir):
            _expert(tmp_path / "experts" / "observer", "observer", "Observe industry trends")

        with pytest.raises(RuntimeError, match="Expert inventory changed"):
            await runtime.orchestration.build(force=True, prepare_artifact=edit_expert)
        assert runtime.orchestration.read() == before
        assert not runtime.orchestration.status().fresh
        with pytest.raises(BaseError) as error:
            await runtime.orchestration.update_capabilities(
                ["new-skill"], request_id="stale-expert-layer", source_revision=snapshot.snapshot_id,
            )
        assert error.value.details["mutation_code"] == "full_rebuild_required"

        staged = asyncio.Event()

        async def pause(_version_dir):
            staged.set()
            await asyncio.Event().wait()

        build = asyncio.create_task(runtime.orchestration.build(prepare_artifact=pause))
        await asyncio.wait_for(staged.wait(), 5)
        status = await runtime.orchestration.cancel_build()
        assert build.cancelled() and status.version == mutation.version
        assert runtime.orchestration.read() == before
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_shared_expert_fingerprint_cache_supports_concurrent_tree_and_graph_builds(handoff_config):
    model = _HandoffModel()
    tree_runtime = SymphonyRuntime(model, handoff_config)
    graph_runtime = SymphonyRuntime(model, handoff_config)
    try:
        tree, graph = await asyncio.gather(tree_runtime.retrieval.build(), graph_runtime.orchestration.build())
        assert tree["success"] and graph_runtime.orchestration.status().version == graph.version
        assert model.expert_extractions == 3
    finally:
        await asyncio.gather(tree_runtime.aclose(), graph_runtime.aclose())


@pytest.mark.asyncio
async def test_mixed_graph_qualifies_skill_observations_without_learning_same_named_experts(handoff_config):
    config = handoff_config.model_dump()
    config["orchestration"]["experience"]["enabled"] = True
    runtime = SymphonyRuntime(_HandoffModel(), SymphonyConfig.model_validate(config))
    try:
        await runtime.orchestration.build()
        snapshot = runtime.capture_graph_snapshot()
        for kind in ("expert", "skill"):
            execution = {
                "trace_id": f"trace-{kind}", "outcome": "success", "query": "Write a brief", "graph_snapshot": snapshot,
                "graph": {
                    "id": f"trace-{kind}", "type": "execution_graph", "directed": True,
                    "nodes": {
                        f"skill:{name}": {"label": kind, "metadata": {
                            "capability_type": kind, "version": "1.0.0", "skill_type": "skill",
                        }}
                        for name in ("ledger", "writer")
                    },
                    "edges": [{"source": "skill:ledger", "target": "skill:writer", "metadata": {"success": True}}],
                },
            }
            outcome = await runtime.submit_evolution(None, execution, session_id=f"session-{kind}", capture_mode="agent")
            if kind == "skill":
                assert outcome.graph_receipt.status == "accepted"
            else:
                assert outcome.graph_receipt is None
        assert len(runtime.flow.store.read_evidence()) == 1
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_skill_source", [False, True])
async def test_shared_tree_projects_typed_results_and_preserves_legacy_collection(tmp_path, extra_skill_source):
    skills, experts, more_experts = (tmp_path / name for name in ("skills", "experts", "more-experts"))
    _skill(skills, "ledger")
    _expert(experts / "ledger", "ledger", persona={"file": "missing.md"})
    _expert(experts / "ledger" / "subagents" / "hidden", "nested")
    _expert(more_experts / "invoice", "invoice")
    legacy = SymphonyRuntime(None, _config(tmp_path, [("skill", skills)]))
    old_tree = await legacy.retrieval.build()
    await legacy.aclose()
    marker = tmp_path / "runtime" / "runtime.json"
    old_marker = marker.read_bytes()
    sources = [("skill", skills), ("expert", experts), ("expert", more_experts)]
    expected_leaves = {("skill", "ledger"), ("expert", "ledger"), ("expert", "invoice")}
    if extra_skill_source:
        more_skills = tmp_path / "more-skills"
        _skill(more_skills, "receipts")
        sources.append(("skill", more_skills))
        expected_leaves.add(("skill", "receipts"))
    runtime = SymphonyRuntime(None, _config(tmp_path, sources))
    try:
        assert runtime.retrieval.artifact_root != tmp_path / "runtime" / "tree"
        if extra_skill_source:
            with pytest.raises(ValueError, match="exactly one skill"):
                runtime.orchestration.status()
        else:
            assert not runtime.orchestration.status().exists
        flat, = runtime.retrieval.create_session(pin_index_revision=True).get_tools()
        queries = ["ledger", "invoice", "invoices"]
        flat_hits = await flat.invoke({"type": "expert", "query": queries})
        assert [hit["id"] for hit in flat_hits["matches"]] == ["ledger", "invoice"]
        assert {hit["category"] for hit in flat_hits["matches"]} == {"ROOT"}

        built = await runtime.retrieval.build()
        assert built["success"] and built["counts"]["capability_count"] == len(expected_leaves)
        branch, = runtime.retrieval.read()["data"]["tree"]
        assert {(leaf["capability_type"], leaf["capability_id"]) for leaf in branch["children"]} == expected_leaves
        artifact = runtime.retrieval.artifact_root.parent
        for kind, count in (("skill", len(expected_leaves) - 2), ("expert", 2)):
            fingerprints = FingerprintArtifactStore(artifact / "cache" / "fingerprints" / kind).read()
            assert len(fingerprints.fingerprints) == count
            assert {item.capability_type for item in fingerprints.fingerprints} == {kind}

        session = runtime.retrieval.create_session(pin_index_revision=True)
        assert session.environment.pinned_index_snapshot.fingerprint == session.pinned_expert_index_snapshot.fingerprint
        search, = session.get_tools()
        skill = await search.invoke({"query": "ledger"})
        assert skill.detailed_output["observed_skill_ids"] == ["ledger"]
        root = await search.invoke({"type": "expert", "operation": "list"})
        category, = root["categories"]
        assert category["count"] == 2
        scoped = await search.invoke({"type": "expert", "category": category["category"], "query": "ledger"})
        assert [hit["id"] for hit in scoped["matches"]] == ["ledger"]
        batch = await search.invoke({
            "type": "expert", "category": category["category"], "query": queries, "top_k": 1,
        })
        hit, = batch["matches"]
        assert (hit["id"], hit["category"]) == ("ledger", category["category"])
        with pytest.raises(ValueError, match="cursor query differs"):
            await search.invoke({"type": "expert", "query": ["invoice"], "cursor": batch["next_cursor"]})
        continued = await search.invoke({"type": "expert", "cursor": batch["next_cursor"]})
        hit, = continued["matches"]
        assert (hit["id"], hit["category"]) == ("invoice", category["category"])
        assert continued["next_cursor"] is None
        first = await search.invoke({"type": "expert", "operation": "list", "category": category["category"], "top_k": 1})
        with pytest.raises(ValueError, match="belongs to type=expert"):
            await search.invoke({"operation": "list", "cursor": first["next_cursor"]})
        second = await search.invoke({"type": "expert", "operation": "list", "cursor": first["next_cursor"]})
        assert {hit["id"] for hit in first["matches"] + second["matches"]} == {"ledger", "invoice"}
        assert second["next_cursor"] is None
        assert marker.read_bytes() == old_marker
    finally:
        await runtime.aclose()

    reordered = SymphonyRuntime(None, _config(tmp_path, list(reversed(sources))))
    original = SymphonyRuntime(None, _config(tmp_path, [("skill", skills)]))
    try:
        assert reordered.retrieval.artifact_root == runtime.retrieval.artifact_root
        assert reordered.retrieval.status()["fresh"]
        assert original.retrieval.read()["version"] == old_tree["version"]
    finally:
        await reordered.aclose()
        await original.aclose()


@pytest.mark.asyncio
async def test_legacy_expert_catalog_batches_queries_through_the_model_facing_tool(tmp_path):
    skills = tmp_path / "skills"
    _skill(skills, "ledger")
    rankings = {"ledger": ("ledger", "invoice", "payment", "archive"), "receipt": ("receipt", "ledger")}
    calls = []

    async def search_catalog(kind, query, top_k):
        calls.append((kind, query, top_k))
        return {"matches": [{"type": kind, "id": identifier} for identifier in rankings[query][:top_k]]}

    runtime = SymphonyRuntime(None, _config(tmp_path, [("skill", skills)]))
    try:
        tool, = runtime.retrieval.create_session(search_catalog=search_catalog).get_tools()
        result = await tool.invoke({"type": "expert", "query": ["ledger", "receipt"], "top_k": 4})
        assert [hit["id"] for hit in result["matches"]] == ["ledger", "receipt", "invoice", "payment"]
        assert calls == [("expert", "ledger", 4), ("expert", "receipt", 4)]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_expert_only_collection_keeps_published_tree_on_invalid_inventory_change_and_cancel(tmp_path):
    experts, other = tmp_path / "experts", tmp_path / "other"
    entry = _expert(experts / "first", "first")
    _expert(other / "second", "second")
    runtime = SymphonyRuntime(GraphModel(), _config(tmp_path, [("expert", experts), ("expert", other)]))
    try:
        with pytest.raises(ValueError, match="exactly one skill"):
            runtime.orchestration.status()
        built = await runtime.retrieval.build()
        assert built["success"]
        published = runtime.retrieval.artifact_root / "index" / "commit.json"
        original = published.read_bytes()
        bad = experts / "bad" / "manifest.json"
        bad.parent.mkdir()
        bad.write_text("not JSON")
        duplicate = _expert(experts / "duplicate", "second")
        session = runtime.retrieval.create_session()
        hits = await session.capability_search(type="expert", query="invoices")
        assert [hit["id"] for hit in hits["matches"]] == ["first"]
        status = runtime.retrieval.status()
        assert not status["fresh"]
        assert {item["code"] for item in status["diagnostics"]} == {
            "invalid_expert_manifest", "duplicate_capability_id",
        }
        with pytest.raises(ValueError, match="Capability inventory is incomplete"):
            await runtime.retrieval.build(force=True)
        assert published.read_bytes() == original
        bad.unlink()
        duplicate.unlink()

        def change(_candidate):
            _expert(entry.parent, "first", "Prepare payroll")

        changed = await runtime.retrieval.build(force=True, prepare_artifact=change)
        assert not changed["success"] and "inventory changed" in changed["result"]
        assert published.read_bytes() == original

        entered = asyncio.Event()

        async def prepare(_candidate):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(runtime.retrieval.build(force=True, prepare_artifact=prepare))
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=10)
        assert published.read_bytes() == original
    finally:
        await runtime.aclose()
