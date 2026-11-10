"""Exercise mixed collections through published artifacts and the model-facing tool."""

import asyncio
import json

import pytest

from openjiuwen.symphony import SymphonyConfig, SymphonyRuntime
from openjiuwen.symphony.shared.fingerprint import FingerprintArtifactStore
from tests.unit_tests.symphony.test_runtime_config_integration import GraphModel


def _config(root, sources):
    return SymphonyConfig(
        capability={"sources": [{"type": kind, "root": str(path)} for kind, path in sources]},
        storage={"artifact_root": str(root / "runtime")},
        retrieval={"enabled": True, "use_existing_index": True, "index_build": {"output": {"show_tree": False}}},
        orchestration={"enabled": True},
    )


def _skill(root, name):
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Reconcile invoices\n---\nBody.\n")


def _expert(root, name, description="Reconcile invoices", **fields):
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "package_type": "agent_template", "name": name, "description": description, **fields,
    }))
    return manifest


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
        flat_hits = await flat.invoke({"type": "expert", "query": "invoices"})
        assert {hit["id"] for hit in flat_hits["matches"]} == {"ledger", "invoice"}
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
