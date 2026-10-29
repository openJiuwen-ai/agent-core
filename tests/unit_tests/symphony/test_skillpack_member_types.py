"""The SkillPack product path only accepts host-resolved ordinary Skills."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
import yaml

from openjiuwen.symphony.flow import (
    CapabilityPackager, SkillPackAdapter, SkillPackNotInstallableError, SymphonyFlowEngine,
)
from openjiuwen.symphony.flow.distill import normalize_execution_graph
from openjiuwen.symphony.orchestration import SymphonyFlowConfig
from tests.unit_tests.symphony.test_capability_flow import _execution_graph


def _typed_graph(trace_id, member_type="skill"):
    graph = _execution_graph(trace_id)
    for node in graph["graph"]["nodes"].values():
        node["metadata"]["capability_type"] = "skill"
        if member_type is not None:
            node["metadata"]["skill_type"] = member_type
    return graph


@pytest.mark.parametrize("member_type", [None, "unknown", "skillpack", "swarm_skill", "swarm-skill", "team-skill", "tool"])
def test_pack_ingest_rejects_nonordinary_and_untyped_members(tmp_path, member_type):
    engine = SymphonyFlowEngine(tmp_path, skill_adapter=SkillPackAdapter())
    assert engine.ingest(_typed_graph("trace-1", member_type)) is False
    assert engine.store.read_evidence() == []


@pytest.mark.asyncio
async def test_historical_untyped_evidence_cannot_become_candidate(tmp_path):
    engine = SymphonyFlowEngine(tmp_path, skill_adapter=SkillPackAdapter())
    for index in range(3):
        engine.store.append_evidence(normalize_execution_graph(_typed_graph(f"old-{index}", None)))
    report = await engine.distill()
    assert report.evidence_total == 0
    assert engine.list_candidates() == ()


@pytest.mark.asyncio
async def test_historical_untyped_recipe_cannot_bypass_candidate_or_install_checks(tmp_path):
    engine = SymphonyFlowEngine(tmp_path / "flow")
    for index in range(3):
        engine.ingest(_typed_graph(f"old-{index}", None))
    report = await engine.distill()
    recipe = engine.get_recipe(report.recipes_saved[0])
    package = CapabilityPackager.build_package(recipe)
    engine.skill_adapter = SkillPackAdapter()
    assert engine.get_candidate(recipe.recipe_id) is None
    prepared = await engine.review_and_prepare_install(recipe.recipe_id, recipe_version=recipe.version)
    assert prepared.verdict == "rejected"
    with pytest.raises(SkillPackNotInstallableError, match="ordinary Skill"):
        SkillPackAdapter.render(package, tmp_path / "artifact")
    assert not (tmp_path / "artifact").exists()


@pytest.mark.asyncio
async def test_type_is_preserved_through_distillation_and_rendering(tmp_path):
    engine = SymphonyFlowEngine(tmp_path / "flow", skill_adapter=SkillPackAdapter())
    for index in range(3):
        assert engine.ingest(_typed_graph(f"trace-{index}"))
    report = await engine.distill()
    recipe = engine.get_recipe(report.recipes_saved[0])
    assert all(node["metadata"]["skill_type"] == "skill" for node in recipe.combination_structure["nodes"].values())
    package = CapabilityPackager.build_package(recipe)
    document = SkillPackAdapter.render(package, tmp_path / "artifact")[0]
    text = document.read_text()
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] not in frontmatter["skills"]
    for section in ("When to use", "Do not use", "Required inputs", "Side effects and confirmation", "Final output"):
        assert f"## {section}\n" in text


@pytest.mark.parametrize("fresh_count", [2, 3, 4])
@pytest.mark.asyncio
async def test_fresh_typed_evidence_rebuilds_legacy_recipe_without_rewriting_history(tmp_path, fresh_count):
    config = SymphonyFlowConfig(min_successes=3)
    legacy = SymphonyFlowEngine(tmp_path, config=config)
    for index in range(3):
        legacy.ingest(_typed_graph(f"old-{index}", None))
    old_report = await legacy.distill()
    recipe_id = old_report.recipes_saved[0]
    old_recipe = legacy.get_recipe(recipe_id)
    assert old_recipe.grade == "verified"
    old_version_file = legacy.store.recipe_version_path(recipe_id, old_recipe.version)
    original_bytes = old_version_file.read_bytes()

    upgraded = SymphonyFlowEngine(tmp_path, config=config, skill_adapter=SkillPackAdapter())
    for index in range(fresh_count):
        assert upgraded.ingest(_typed_graph(f"fresh-{index}"))
    report = await upgraded.distill()
    current = upgraded.get_recipe(recipe_id)

    assert report.evidence_total == fresh_count
    assert current.version > old_recipe.version
    assert current.quality["execution_count"] == fresh_count
    assert all(node["metadata"]["skill_type"] == "skill" for node in current.combination_structure["nodes"].values())
    assert (current.grade == "verified") is (fresh_count >= config.min_successes)
    assert bool(upgraded.list_candidates()) is (fresh_count >= config.min_successes)
    assert upgraded.get_candidate(recipe_id, version=old_recipe.version) is None
    assert old_version_file.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_filtered_historical_evidence_does_not_redistill_after_restart(tmp_path, monkeypatch):
    engine = SymphonyFlowEngine(tmp_path, skill_adapter=SkillPackAdapter())
    for index in range(3):
        engine.store.append_evidence(normalize_execution_graph(_typed_graph(f"old-{index}", None)))
    report = await engine.distill()
    assert report.evidence_total == 0
    assert engine.store.read_distillation_fingerprint() == engine.store.evidence_fingerprint()

    restarted = SymphonyFlowEngine(tmp_path, skill_adapter=SkillPackAdapter())
    distill = AsyncMock(wraps=restarted.distill)
    monkeypatch.setattr(restarted, "distill", distill)
    try:
        assert await restarted.start() == ()
        distill.assert_not_awaited()
        assert restarted.ingest(_typed_graph("fresh-0"))
        assert await restarted.start()
        distill.assert_awaited_once()
        assert restarted.store.read_distillation_fingerprint() == restarted.store.evidence_fingerprint()
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_untyped_generic_flow_keeps_existing_version_and_counts(tmp_path):
    engine = SymphonyFlowEngine(tmp_path, config=SymphonyFlowConfig(min_successes=3))
    for index in range(3):
        assert engine.ingest(_typed_graph(f"old-{index}", None))
    first = await engine.distill()
    recipe_id = first.recipes_saved[0]
    version = engine.get_recipe(recipe_id).version
    version_file = engine.store.recipe_version_path(recipe_id, version)
    original_bytes = version_file.read_bytes()

    assert engine.ingest(_typed_graph("fresh-0", None))
    report = await engine.distill()
    current = engine.get_recipe(recipe_id)
    assert report.recipes_saved == []
    assert current.version == version
    assert current.quality["execution_count"] == 4
    assert len(engine.list_candidates()) == 1
    assert version_file.read_bytes() == original_bytes
    assert engine.store.read_distillation_fingerprint() == engine.store.evidence_fingerprint()


@pytest.mark.parametrize("pack_adapter", [False, True])
@pytest.mark.asyncio
async def test_evidence_added_during_distillation_remains_pending(tmp_path, monkeypatch, pack_adapter):
    engine = SymphonyFlowEngine(tmp_path, skill_adapter=SkillPackAdapter() if pack_adapter else None)
    if pack_adapter:
        engine.store.append_evidence(normalize_execution_graph(_typed_graph("old-0", None)))
    assert engine.ingest(_typed_graph("initial"))
    original_build = engine._build_recipe

    async def append_while_building(*args, **kwargs):
        await asyncio.sleep(0)
        assert engine.ingest(_typed_graph("arrived-during-build"))
        return await original_build(*args, **kwargs)

    monkeypatch.setattr(engine, "_build_recipe", append_while_building)
    first = await engine.distill()
    recipe_id = first.recipes_saved[0]
    assert engine.get_recipe(recipe_id).quality["execution_count"] == 1
    assert engine.store.read_distillation_fingerprint() != engine.store.evidence_fingerprint()

    monkeypatch.setattr(engine, "_build_recipe", original_build)
    await engine.distill()
    assert engine.get_recipe(recipe_id).quality["execution_count"] == 2
    assert engine.store.read_distillation_fingerprint() == engine.store.evidence_fingerprint()
