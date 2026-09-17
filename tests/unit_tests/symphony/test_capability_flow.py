"""Capability flow（经验沉淀 + 能力包）端到端测试。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.symphony.flow import (
    RECIPE_GRADE_VERIFIED,
    RECIPE_STATUS_ACTIVE,
    RECIPE_STATUS_DRAFT,
    TARGET_KIND_PLUGIN,
    VERDICT_APPROVED,
    VERDICT_NEEDS_HUMAN_REVIEW,
    VERDICT_REJECTED,
    CapabilityPackager,
    LLMPackageReviewAgent,
    PackageReviewGate,
    SymphonyFlowEngine,
    render_package,
)
from openjiuwen.symphony.flow.codegen import (
    generate_swarmflow_script,
    validate_generated_script,
    validate_meta_name,
)
from openjiuwen.symphony.flow.distill import (
    EdgeStats,
    break_cycles,
    normalize_execution_graph,
    topological_order,
)
from openjiuwen.symphony.flow.models import content_hash
from openjiuwen.symphony.orchestration import SymphonyFlowConfig


class _ApprovingReviewAgent:
    async def review(self, package) -> str:
        del package
        return VERDICT_APPROVED


def _execution_graph(
    trace_id: str,
    *,
    query: str = "调研多智能体系统进展并写综述报告",
    outcome: str = "success",
    failed_edge: bool = False,
) -> dict:
    edges = [
        {
            "source": "skill:web-search",
            "target": "skill:summarize-paper",
            "relation": "can_feed",
            "metadata": {"success": True},
        },
        {
            "source": "skill:summarize-paper",
            "target": "skill:write-report",
            "relation": "can_feed",
            "metadata": {"success": not failed_edge},
        },
    ]
    return {
        "trace_id": trace_id,
        "query": query,
        "outcome": outcome,
        "graph": {
            "id": f"graph_{trace_id}",
            "type": "execution_graph",
            "directed": True,
            "nodes": {
                "skill:web-search": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
                "skill:summarize-paper": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
                "skill:write-report": {
                    "label": "skill",
                    "metadata": {"version": "1.0.0"},
                },
            },
            "edges": edges,
        },
    }


def _engine(tmp_path: Path) -> SymphonyFlowEngine:
    config = SymphonyFlowConfig(
        min_edge_support=2,
        min_edge_success_rate=0.8,
        min_successes_candidate=3,
        min_successes_verified=5,
        min_pack_success_rate_verified=0.8,
    )
    return SymphonyFlowEngine(tmp_path / "flow", config=config)


def _verified_recipe_id(engine: SymphonyFlowEngine, report) -> str:
    """从蒸馏报告中取 active+verified 的 recipe（绕行子结构会另成组）。"""

    for recipe_id in report.recipes_saved:
        recipe = engine.get_recipe(recipe_id)
        if recipe is not None and recipe.status == RECIPE_STATUS_ACTIVE and recipe.grade == RECIPE_GRADE_VERIFIED:
            return recipe_id
    raise AssertionError("no verified recipe distilled")


def _feed_verified_engine(tmp_path: Path) -> SymphonyFlowEngine:
    engine = _engine(tmp_path)
    for index in range(6):
        engine.ingest(
            _execution_graph(
                f"trace-{index}",
                query=f"调研任务 {index}",
                failed_edge=index == 0,
            )
        )
    return engine


def test_engine_compatibility_config_falls_back_to_effective_defaults(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow", config=SimpleNamespace())
    assert engine.ingest(_execution_graph("trace-defaults"))

    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(report.recipes_saved[0])

    assert recipe is not None
    assert recipe.grade == RECIPE_GRADE_VERIFIED
    assert recipe.status == RECIPE_STATUS_ACTIVE


def test_ingest_and_distill_end_to_end(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)

    # trace_id 幂等
    assert engine.ingest(_execution_graph("trace-0")) is False

    report = asyncio.run(engine.distill())
    assert report.evidence_total == 6
    # 两条结构组：完整链（5 条轨迹）+ 绕行子结构（trace-0 仅走通前半段）
    assert len(report.recipes_saved) == 2
    recipe_id = _verified_recipe_id(engine, report)

    recipe = engine.get_recipe(recipe_id)
    assert recipe is not None
    assert recipe.status == RECIPE_STATUS_ACTIVE
    assert recipe.grade == RECIPE_GRADE_VERIFIED
    assert set(recipe.capability_ids) == {
        "web-search",
        "summarize-paper",
        "write-report",
    }
    # 归因严格：trace-0（靠前半段成功、write 边失败）不计入完整链统计
    assert recipe.quality["execution_count"] == 5
    assert recipe.quality["success_count"] == 5
    assert recipe.quality["pack_success_rate"] == 1.0
    assert set(recipe.provenance["evidence_trace_ids"]) == {f"trace-{index}" for index in range(1, 6)}

    # 绕行子结构：单边 {web-search → summarize-paper}，证据不足以 active
    bypass_ids = [recipe_id_ for recipe_id_ in report.recipes_saved if recipe_id_ != recipe_id]
    bypass = engine.get_recipe(bypass_ids[0])
    assert bypass is not None
    assert set(bypass.capability_ids) == {"web-search", "summarize-paper"}
    assert bypass.quality["execution_count"] == 1
    assert bypass.status == RECIPE_STATUS_DRAFT

    # 内容未变化时 distill 不产生新版本
    report_again = asyncio.run(engine.distill())
    assert report_again.recipes_saved == []
    assert sorted(report_again.recipes_unchanged) == sorted(report.recipes_saved)
    assert engine.get_recipe(recipe_id).version == 1


@pytest.mark.parametrize("outcome", ["failed", "partial"])
def test_ingest_and_submit_reject_non_success_outcomes(tmp_path: Path, outcome: str) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    payload = _execution_graph("trace-1", outcome=outcome)

    assert engine.ingest(payload) is False
    assert asyncio.run(engine.submit(payload)) == ()
    assert engine.store.read_evidence() == []
    assert engine.list_recipes() == []


def test_unchanged_recipe_updates_current_quality_without_new_version(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    assert engine.ingest(_execution_graph("trace-1", query="same task"))
    first = asyncio.run(engine.distill())
    recipe_id = first.recipes_saved[0]

    assert engine.ingest(_execution_graph("trace-2", query="same task"))
    second = asyncio.run(engine.distill())

    current = engine.get_recipe(recipe_id)
    assert current is not None
    assert current.version == 1
    assert current.quality["execution_count"] == 2
    assert current.provenance["evidence_count"] == 2
    assert second.recipes_saved == []
    assert second.recipes_unchanged == [recipe_id]
    assert not engine.store.recipe_version_path(recipe_id, 2).exists()

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    assert preparation.package is not None
    packaged_recipe = preparation.package["materials"]["recipe"]
    assert packaged_recipe["quality"]["execution_count"] == 2
    assert packaged_recipe["provenance"]["evidence_count"] == 2


def test_submit_replays_candidate_until_explicit_ack(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")

    first = asyncio.run(engine.submit(_execution_graph("trace-1")))
    duplicate = asyncio.run(engine.submit(_execution_graph("trace-1")))

    assert len(first) == 1
    assert first[0].recipe_id.startswith("recipe_")
    assert first[0].version == 1
    assert first == duplicate
    assert first[0].name == "web-search → summarize-paper → write-report"
    assert first[0].applicability
    assert first[0].structure
    assert first[0].execution_count == 1
    assert first[0].success_count == 1
    assert first[0].success_rate == 1.0
    assert engine.get_candidate(first[0].recipe_id) == first[0]
    with pytest.raises(FrozenInstanceError):
        first[0].name = "changed"  # type: ignore[misc]

    assert engine.acknowledge_candidate(first[0].recipe_id, first[0].version) is True
    assert engine.acknowledge_candidate(first[0].recipe_id, first[0].version) is False
    assert asyncio.run(engine.submit(_execution_graph("trace-1"))) == ()


def test_duplicate_and_restart_replay_without_redistilling(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "task_description": "research and write",
            "trigger_conditions": "research task",
            "example_requests": ["prepare a review"],
            "execution_narrative": "search, summarize, then write",
        }
    )
    llm = Mock(invoke=AsyncMock(return_value=response))
    flow_dir = tmp_path / "flow"
    engine = SymphonyFlowEngine(flow_dir, llm_client=llm)

    first = asyncio.run(engine.submit(_execution_graph("trace-1")))
    duplicate = asyncio.run(engine.submit(_execution_graph("trace-1")))
    restarted = SymphonyFlowEngine(flow_dir, llm_client=llm)
    replayed = asyncio.run(restarted.start())

    assert first == duplicate == replayed
    assert llm.invoke.await_count == 1
    assert restarted.get_recipe(first[0].recipe_id).version == 1
    assert not restarted.store.recipe_version_path(first[0].recipe_id, 2).exists()

    assert restarted.acknowledge_candidate(first[0].recipe_id, first[0].version)
    assert asyncio.run(SymphonyFlowEngine(flow_dir, llm_client=llm).start()) == ()
    assert llm.invoke.await_count == 1


def test_restart_distills_new_evidence_before_replaying_unacknowledged_candidate(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "task_description": "research and write",
            "trigger_conditions": "research task",
            "example_requests": ["prepare a review"],
            "execution_narrative": "search, summarize, then write",
        }
    )
    llm = Mock(invoke=AsyncMock(return_value=response))
    flow_dir = tmp_path / "flow"
    first_engine = SymphonyFlowEngine(flow_dir, llm_client=llm)
    first = asyncio.run(first_engine.submit(_execution_graph("trace-1")))
    assert len(first) == 1
    assert first_engine.ingest(_execution_graph("trace-2"))

    restarted = SymphonyFlowEngine(flow_dir, llm_client=llm)
    refreshed = asyncio.run(restarted.start())

    assert len(refreshed) == 1
    assert refreshed[0].execution_count == 2
    assert restarted.get_recipe(refreshed[0].recipe_id).version == 1
    assert restarted.store.read_distillation_fingerprint() == restarted.store.evidence_fingerprint()
    assert llm.invoke.await_count == 2

    replayed = asyncio.run(restarted.submit(_execution_graph("trace-2")))
    assert replayed == refreshed
    assert llm.invoke.await_count == 2
    assert not restarted.store.recipe_version_path(refreshed[0].recipe_id, 2).exists()


def test_candidate_ack_persists_across_engine_instances(tmp_path: Path) -> None:
    first_engine = SymphonyFlowEngine(tmp_path / "flow")
    candidate = asyncio.run(first_engine.submit(_execution_graph("trace-1")))[0]
    assert first_engine.acknowledge_candidate(candidate.recipe_id, candidate.version)

    restarted = SymphonyFlowEngine(tmp_path / "flow")

    assert asyncio.run(restarted.start()) == ()
    assert restarted.get_candidate(candidate.recipe_id) == candidate


def test_ack_uses_current_verified_state_when_immutable_version_was_candidate(tmp_path: Path) -> None:
    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.8,
        min_successes_candidate=1,
        min_successes_verified=2,
        min_pack_success_rate_verified=0.8,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)

    assert asyncio.run(engine.submit(_execution_graph("trace-1"))) == ()
    current_candidate = engine.get_recipe(engine.list_recipes()[0])
    assert current_candidate is not None
    assert current_candidate.grade != RECIPE_GRADE_VERIFIED

    candidates = asyncio.run(engine.submit(_execution_graph("trace-2")))
    assert len(candidates) == 1
    current_verified = engine.get_recipe(candidates[0].recipe_id)
    immutable_version = engine.store.read_recipe(candidates[0].recipe_id, version=1)
    assert current_verified is not None
    assert current_verified.version == 1
    assert current_verified.grade == RECIPE_GRADE_VERIFIED
    assert immutable_version is not None
    assert immutable_version.grade != RECIPE_GRADE_VERIFIED

    assert engine.acknowledge_candidate(candidates[0].recipe_id, candidates[0].version) is True
    assert asyncio.run(engine.submit(_execution_graph("trace-2"))) == ()


@pytest.mark.asyncio
async def test_distillation_worker_is_single_and_notifies_only_one_concurrent_waiter(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    original_distill = engine.distill
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def blocked_distill():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        try:
            return await original_distill()
        finally:
            active -= 1

    engine.distill = blocked_distill  # type: ignore[method-assign]
    first = asyncio.create_task(engine.submit(_execution_graph("trace-1")))
    await started.wait()
    second = asyncio.create_task(engine.submit(_execution_graph("trace-2")))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)

    assert max_active == 1
    assert sum(len(result) for result in results) == 1


@pytest.mark.asyncio
async def test_failed_distillation_retries_on_next_duplicate_submission(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    original_distill = engine.distill
    calls = 0

    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("distill failed")
        return await original_distill()

    engine.distill = fail_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="distill failed"):
        await engine.submit(_execution_graph("trace-1"))

    recovered = await engine.submit(_execution_graph("trace-1"))

    assert calls == 2
    assert len(recovered) == 1


@pytest.mark.asyncio
async def test_failed_distillation_recovers_after_engine_restart(tmp_path: Path) -> None:
    flow_dir = tmp_path / "flow"
    failed = SymphonyFlowEngine(flow_dir)
    failed.distill = AsyncMock(side_effect=RuntimeError("distill failed"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="distill failed"):
        await failed.submit(_execution_graph("trace-1"))

    restarted = SymphonyFlowEngine(flow_dir)
    recovered = await restarted.start()

    assert len(recovered) == 1
    assert restarted.store.read_distillation_fingerprint() == restarted.store.evidence_fingerprint()


@pytest.mark.asyncio
async def test_failed_worker_releases_waiters_queued_during_distillation(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_failure():
        started.set()
        await release.wait()
        raise RuntimeError("distill failed")

    engine.distill = blocked_failure  # type: ignore[method-assign]
    first = asyncio.create_task(engine.submit(_execution_graph("trace-1")))
    await started.wait()
    second = asyncio.create_task(engine.submit(_execution_graph("trace-2")))
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, RuntimeError) for result in results)


@pytest.mark.asyncio
async def test_close_flushes_worker_and_rejects_later_submissions(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    submitted = asyncio.create_task(engine.submit(_execution_graph("trace-1")))
    await asyncio.sleep(0)

    await engine.close()

    assert len(await submitted) == 1
    with pytest.raises(RuntimeError, match="closed"):
        await engine.submit(_execution_graph("trace-2"))


@pytest.mark.asyncio
async def test_close_records_worker_failure_and_still_closes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    engine.distill = AsyncMock(side_effect=RuntimeError("private"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="private"):
        await engine.submit(_execution_graph("trace-1"))
    await engine.close()

    assert "RuntimeError" in caplog.text
    assert "private" not in caplog.text
    with pytest.raises(RuntimeError, match="closed"):
        await engine.submit(_execution_graph("trace-1"))


def test_start_recovers_persisted_evidence(tmp_path: Path) -> None:
    first = SymphonyFlowEngine(tmp_path / "flow")
    assert first.ingest(_execution_graph("trace-1"))

    recovered = SymphonyFlowEngine(tmp_path / "flow")
    candidates = asyncio.run(recovered.start())

    assert len(candidates) == 1


def test_review_and_prepare_install_approved(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    assert preparation.verdict == VERDICT_APPROVED
    assert preparation.artifact_dir is not None

    artifact_dir = Path(preparation.artifact_dir)
    assert (artifact_dir / "SKILL.md").is_file()
    assert (artifact_dir / "dependencies.yaml").is_file()
    assert (artifact_dir / "swarmflow" / "run.py").is_file()

    package = preparation.package
    assert package is not None
    assert package["materials"]["swarmflow_script"]
    problems = validate_generated_script(package["materials"]["swarmflow_script"])
    assert problems == []
    assert validate_meta_name(package["materials"]["meta_name"])

    skill_md = (artifact_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "## 任务描述" in skill_md
    assert "## 执行过程" in skill_md

    # 完整性校验
    assert CapabilityPackager.verify_package_integrity(package)


def test_approved_preparation_replaces_tampered_cached_artifact(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    first = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    artifact_dir = Path(first.artifact_dir)
    skill_path = artifact_dir / "SKILL.md"
    run_path = artifact_dir / "swarmflow" / "run.py"
    expected_skill = skill_path.read_text(encoding="utf-8")
    expected_run = run_path.read_text(encoding="utf-8")
    skill_path.write_text("MALICIOUS SKILL CACHE", encoding="utf-8")
    run_path.write_text("raise RuntimeError('MALICIOUS RUN CACHE')", encoding="utf-8")

    second = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert second.verdict == VERDICT_APPROVED
    assert second.artifact_dir == first.artifact_dir
    assert skill_path.read_text(encoding="utf-8") == expected_skill
    assert run_path.read_text(encoding="utf-8") == expected_run
    assert "MALICIOUS" not in skill_path.read_text(encoding="utf-8")
    assert "MALICIOUS" not in run_path.read_text(encoding="utf-8")


def test_approved_render_failure_preserves_previous_valid_artifact(tmp_path: Path) -> None:
    class BrokenAdapter:
        @staticmethod
        def render(package, artifact_dir) -> None:
            del package, artifact_dir
            raise RuntimeError("render unavailable")

    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    first = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    artifact_dir = Path(first.artifact_dir)
    expected = {path.relative_to(artifact_dir): path.read_bytes() for path in artifact_dir.rglob("*") if path.is_file()}
    engine.skill_adapter = BrokenAdapter()

    second = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert second.verdict == VERDICT_REJECTED
    assert second.artifact_dir is None
    assert artifact_dir.is_dir()
    assert {
        path.relative_to(artifact_dir): path.read_bytes() for path in artifact_dir.rglob("*") if path.is_file()
    } == expected
    assert list(artifact_dir.parent.glob(f".{artifact_dir.name}.*")) == []


def test_first_approved_package_save_failure_removes_published_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    package = CapabilityPackager.build_package(engine.get_recipe(recipe_id))
    artifact_dir = engine.store.artifact_dir(package["package_id"], package["target_kind"])
    monkeypatch.setattr(engine.store, "save_package", Mock(side_effect=OSError("package store unavailable")))

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert preparation.verdict == VERDICT_REJECTED
    assert preparation.artifact_dir is None
    assert not artifact_dir.exists()
    assert engine.store.read_package(package["package_id"]) is None
    assert list(artifact_dir.parent.glob(f".{artifact_dir.name}.*")) == []


def test_replacement_package_save_failure_restores_previous_artifact_and_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ChangedAdapter:
        @staticmethod
        def render(package, artifact_dir) -> None:
            render_package(package, artifact_dir)
            with (Path(artifact_dir) / "SKILL.md").open("a", encoding="utf-8") as handle:
                handle.write("\nNEW UNPERSISTED ARTIFACT\n")

    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    first = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    package_id = first.package["package_id"]
    artifact_dir = Path(first.artifact_dir)
    previous_artifact = {
        path.relative_to(artifact_dir): path.read_bytes() for path in artifact_dir.rglob("*") if path.is_file()
    }
    previous_package = engine.store.read_package(package_id)
    engine.skill_adapter = ChangedAdapter()
    monkeypatch.setattr(engine.store, "save_package", Mock(side_effect=OSError("package store unavailable")))

    second = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert second.verdict == VERDICT_REJECTED
    assert second.artifact_dir is None
    assert {
        path.relative_to(artifact_dir): path.read_bytes() for path in artifact_dir.rglob("*") if path.is_file()
    } == previous_artifact
    assert engine.store.read_package(package_id) == previous_package
    assert "NEW UNPERSISTED" not in (artifact_dir / "SKILL.md").read_text(encoding="utf-8")
    assert list(artifact_dir.parent.glob(f".{artifact_dir.name}.*")) == []


@pytest.mark.parametrize("verdict", [VERDICT_REJECTED, VERDICT_NEEDS_HUMAN_REVIEW])
def test_non_approved_review_removes_previous_artifact(tmp_path: Path, verdict: str) -> None:
    class ReviewAgent:
        async def review(self, package) -> str:
            del package
            return verdict

    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=_ApprovingReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    first = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))
    artifact_dir = Path(first.artifact_dir)
    assert artifact_dir.is_dir()
    engine.gate = PackageReviewGate(review_agent=ReviewAgent())

    second = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert second.verdict == verdict
    assert second.artifact_dir is None
    assert not artifact_dir.exists()
    assert list(artifact_dir.parent.glob(f".{artifact_dir.name}.*")) == []


@pytest.mark.asyncio
async def test_concurrent_approved_preparations_serialize_and_publish_cleanly(tmp_path: Path) -> None:
    class CoordinatedReviewAgent:
        active = 0
        max_active = 0

        async def review(self, package) -> str:
            del package
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return VERDICT_APPROVED

    class RecordingAdapter:
        calls = 0

        def render(self, package, artifact_dir) -> None:
            self.calls += 1
            render_package(package, artifact_dir)

    engine = _feed_verified_engine(tmp_path)
    report = await engine.distill()
    recipe_id = _verified_recipe_id(engine, report)
    reviewer = CoordinatedReviewAgent()
    adapter = RecordingAdapter()
    engine.gate = PackageReviewGate(review_agent=reviewer)
    engine.skill_adapter = adapter

    first, second = await asyncio.gather(
        engine.review_and_prepare_install(recipe_id, recipe_version=1),
        engine.review_and_prepare_install(recipe_id, recipe_version=1),
    )

    assert first.verdict == second.verdict == VERDICT_APPROVED
    assert first.artifact_dir == second.artifact_dir
    assert first.package["package_id"] == second.package["package_id"]
    assert reviewer.max_active == 1
    assert adapter.calls == 2
    artifact_dir = Path(first.artifact_dir)
    assert (artifact_dir / "SKILL.md").is_file()
    assert (artifact_dir / "swarmflow" / "run.py").is_file()
    assert list(artifact_dir.parent.glob(f".{artifact_dir.name}.*")) == []


def test_missing_review_agent_needs_human_and_does_not_render_or_persist_package(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert preparation.verdict == VERDICT_NEEDS_HUMAN_REVIEW
    assert preparation.artifact_dir is None
    assert preparation.package is not None
    assert engine.store.read_package(preparation.package["package_id"]) is None
    assert len(engine.review_history(preparation.package["package_id"])) == 1


def test_review_and_prepare_install_rejections(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)

    # recipe 不存在
    missing = asyncio.run(engine.review_and_prepare_install("recipe_missing", recipe_version=1))
    assert missing.verdict == VERDICT_REJECTED
    assert missing.package is None

    # plugin 目标暂不支持
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)
    plugin = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1, target_kind=TARGET_KIND_PLUGIN))
    assert plugin.verdict == VERDICT_REJECTED

    # 证据不足：结构无法达到 active/verified，install 准备必须拒绝
    lone = _engine(tmp_path / "lone")
    lone.ingest(_execution_graph("trace-lone"))
    report = asyncio.run(lone.distill())
    assert report.recipes_saved == []  # support < 2 → 无合格结构


def test_review_agent_rejection_never_calls_target_adapter(tmp_path: Path) -> None:
    class RejectingReviewAgent:
        async def review(self, package) -> str:
            with pytest.raises(TypeError):
                package["tampered"] = True
            return VERDICT_REJECTED

    adapter = Mock()
    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=RejectingReviewAgent())
    engine.skill_adapter = adapter
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert preparation.verdict == VERDICT_REJECTED
    assert preparation.artifact_dir is None
    adapter.render.assert_not_called()
    assert not (engine.store.root / "packages" / preparation.package["package_id"] / "skill").exists()
    assert engine.store.read_package(preparation.package["package_id"]) is None


def test_review_agent_failure_fails_closed_without_target_directory(tmp_path: Path) -> None:
    class BrokenReviewAgent:
        async def review(self, package) -> str:
            del package
            raise RuntimeError("unavailable")

    engine = _feed_verified_engine(tmp_path)
    engine.gate = PackageReviewGate(review_agent=BrokenReviewAgent())
    report = asyncio.run(engine.distill())
    recipe_id = _verified_recipe_id(engine, report)

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert preparation.verdict == VERDICT_NEEDS_HUMAN_REVIEW
    assert preparation.artifact_dir is None
    assert engine.store.read_package(preparation.package["package_id"]) is None


def test_distinct_final_review_verdicts_have_distinct_history_entries(tmp_path: Path) -> None:
    class RejectingReviewAgent:
        async def review(self, package) -> str:
            del package
            return VERDICT_REJECTED

    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(_verified_recipe_id(engine, report))
    package = CapabilityPackager.build_package(recipe)
    approved = asyncio.run(PackageReviewGate(_ApprovingReviewAgent()).review(package))
    rejected = asyncio.run(PackageReviewGate(RejectingReviewAgent()).review(package))

    engine.store.save_review(approved.to_dict())
    engine.store.save_review(rejected.to_dict())
    history = engine.review_history(package["package_id"])

    assert approved.review_id != rejected.review_id
    assert {review.verdict for review in history} == {VERDICT_APPROVED, VERDICT_REJECTED}


def test_store_rejects_traversal_ids_without_touching_outside_path(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    engine.ingest(_execution_graph("trace-1"))
    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(report.recipes_saved[0])
    assert recipe is not None
    recipe.recipe_id = "../../outside"

    assert engine.store.read_recipe("../../outside") is None
    assert engine.store.read_package("../../outside") is None
    assert engine.store.read_reviews("../../outside") == []
    assert engine.store.acknowledge_candidate("../../outside", 1) is False
    with pytest.raises(ValueError, match="recipe_id"):
        engine.store.save_recipe(recipe)
    with pytest.raises(ValueError, match="package_id"):
        engine.store.save_package({"package_id": "../../outside"})
    with pytest.raises(ValueError, match="package_id"):
        engine.store.save_review({"package_id": "../../outside", "review_id": "review_000000000000"})
    with pytest.raises(ValueError, match="package_id"):
        engine.store.artifact_dir("../../outside", "skill")
    assert not (tmp_path / "outside").exists()


def test_static_review_requires_permissions_and_license(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(_verified_recipe_id(engine, report))
    package = CapabilityPackager.build_package(recipe)

    assert engine.gate.review_static(package).verdict == VERDICT_APPROVED
    package["materials"].pop("permissions")
    package["materials"].pop("license")
    package["integrity"] = content_hash(package["materials"])

    review = engine.gate.review_static(package)

    assert review.verdict == VERDICT_REJECTED
    assert {check.check for check in review.failed_checks} == {"permissions", "license"}

    package = CapabilityPackager.build_package(recipe)
    package["materials"]["permissions"] = ["credential_access"]
    package["materials"]["license"] = "UNKNOWN"
    package["integrity"] = content_hash(package["materials"])

    policy_review = engine.gate.review_static(package)

    assert policy_review.verdict == VERDICT_REJECTED
    assert {check.check for check in policy_review.failed_checks} == {"permissions", "license"}


def test_stale_recipe_version_is_rejected(tmp_path: Path) -> None:
    engine = SymphonyFlowEngine(tmp_path / "flow")
    engine.ingest(_execution_graph("trace-1", query="first wording"))
    first = asyncio.run(engine.distill())
    recipe_id = first.recipes_saved[0]
    engine.ingest(_execution_graph("trace-2", query="second wording"))
    asyncio.run(engine.distill())
    assert engine.get_recipe(recipe_id).version == 2

    preparation = asyncio.run(engine.review_and_prepare_install(recipe_id, recipe_version=1))

    assert preparation.verdict == VERDICT_REJECTED
    assert "stale recipe version" in preparation.reasons[0]
    assert preparation.package is None


def test_branching_recipe_is_not_installable_in_v1(tmp_path: Path) -> None:
    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.5,
        min_successes_candidate=1,
        min_successes_verified=1,
        min_pack_success_rate_verified=0.5,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)
    engine.ingest(_graph_with_edges("branch", [("s1", "s2", True), ("s1", "s3", True)]))
    report = asyncio.run(engine.distill())

    preparation = asyncio.run(engine.review_and_prepare_install(report.recipes_saved[0], recipe_version=1))

    assert preparation.verdict == VERDICT_REJECTED
    assert "simple skill chain" in preparation.reasons[0]
    assert preparation.package is None


def test_narrative_prefers_symphony_llm_invoke(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "task_description": "research then write",
            "trigger_conditions": "research request",
            "example_requests": ["write a report"],
            "execution_narrative": "search, summarize, and write",
        }
    )
    llm = Mock(invoke=AsyncMock(return_value=response))
    engine = SymphonyFlowEngine(tmp_path / "flow", llm_client=llm)
    engine.ingest(_execution_graph("trace-1"))

    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(report.recipes_saved[0])

    llm.invoke.assert_awaited_once()
    assert recipe.provenance["narrative_source"] == "llm"
    assert recipe.execution_narrative == "search, summarize, and write"


def test_package_redacts_raw_queries_examples_traces_and_credentials(tmp_path: Path) -> None:
    raw_query = "summarize private case api_key=private-token"
    response = json.dumps(
        {
            "task_description": raw_query,
            "trigger_conditions": "use credential=another-private-value",
            "example_requests": [raw_query],
            "execution_narrative": "send token=third-private-value through the chain",
        }
    )
    engine = SymphonyFlowEngine(
        tmp_path / "flow",
        llm_client=Mock(invoke=AsyncMock(return_value=response)),
        gate=PackageReviewGate(review_agent=_ApprovingReviewAgent()),
    )
    assert engine.ingest(_execution_graph("private-trace", query=raw_query))
    report = asyncio.run(engine.distill())

    preparation = asyncio.run(engine.review_and_prepare_install(report.recipes_saved[0], recipe_version=1))
    serialized = json.dumps(preparation.package, ensure_ascii=False)

    assert preparation.verdict == VERDICT_APPROVED
    assert raw_query not in serialized
    assert "private-trace" not in serialized
    assert "example_requests" not in serialized
    assert "private-token" not in serialized
    assert "another-private-value" not in serialized
    assert "third-private-value" not in serialized


def test_common_bare_credentials_never_enter_prompt_recipe_or_package(tmp_path: Path) -> None:
    secrets = (
        "sk-abcdefghijklmnop",
        "Bearer abcdefghijklmnop",
        "eyJheader.eyJpayload.signaturevalue",
        "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----",
        "AKIAABCDEFGHIJKLMNOP",
    )
    raw_query = "review credentials " + " ".join(secrets)
    response = json.dumps(
        {
            "task_description": raw_query,
            "trigger_conditions": secrets[0],
            "example_requests": [raw_query],
            "execution_narrative": secrets[1],
        }
    )
    llm = Mock(invoke=AsyncMock(return_value=response))
    engine = SymphonyFlowEngine(
        tmp_path / "flow",
        llm_client=llm,
        gate=PackageReviewGate(review_agent=_ApprovingReviewAgent()),
    )

    candidates = asyncio.run(engine.submit(_execution_graph("trace-private", query=raw_query)))
    prompt = json.dumps(llm.invoke.await_args.args[0], ensure_ascii=False)
    recipe = engine.get_recipe(candidates[0].recipe_id)
    preparation = asyncio.run(
        engine.review_and_prepare_install(candidates[0].recipe_id, recipe_version=candidates[0].version)
    )
    serialized_recipe = json.dumps(recipe.to_dict(), ensure_ascii=False)
    serialized_package = json.dumps(preparation.package, ensure_ascii=False)

    for secret in secrets:
        assert secret not in prompt
        assert secret not in serialized_recipe
        assert secret not in serialized_package


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnop",
        "Bearer abcdefghijklmnop",
        "eyJheader.eyJpayload.signaturevalue",
        "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----",
        "AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_static_review_fails_closed_on_common_bare_credentials(tmp_path: Path, secret: str) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    package = CapabilityPackager.build_package(engine.get_recipe(_verified_recipe_id(engine, report)))
    package["materials"]["recipe"]["execution_narrative"] += f" {secret}"
    package["integrity"] = content_hash(package["materials"])

    review = engine.gate.review_static(package)

    assert review.verdict == VERDICT_REJECTED
    assert "secrets" in {check.check for check in review.failed_checks}


def test_llm_review_agent_receives_only_canonical_redacted_package(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    package = CapabilityPackager.build_package(engine.get_recipe(_verified_recipe_id(engine, report)))
    llm = Mock(invoke=AsyncMock(return_value='{"verdict":"approved"}'))
    reviewer = LLMPackageReviewAgent(llm)

    verdict = asyncio.run(reviewer.review(package))

    messages = llm.invoke.await_args.args[0]
    request = json.loads(messages[1]["content"])
    assert verdict == VERDICT_APPROVED
    assert request == {"package": package}
    assert list(request) == ["package"]
    assert "artifact_dir" not in messages[1]["content"]
    assert "tools" not in llm.invoke.await_args.kwargs
    assert llm.invoke.await_args.kwargs == {"temperature": 0.0}


# 4.3.1 约定的执行图外层对象样本：含失败边、分支边与证据引用
_DOCUMENTED_EXECUTION_GRAPH = {
    "trace_id": "trace_20260820_001",
    "query": "整理调研数据并生成分析报告",
    "outcome": "success",
    "graph": {
        "id": "execution_graph_20260820_001",
        "type": "execution_graph",
        "label": "capability execution graph",
        "directed": True,
        "nodes": {
            "skill1": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill1-content"},
            },
            "skill2": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill2-content"},
            },
            "skill3": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill3-content"},
            },
            "skill5": {
                "label": "skill",
                "metadata": {"version": "1.0.0", "content_hash": "sha256:skill5-content"},
            },
        },
        "edges": [
            {
                "source": "skill1",
                "target": "skill2",
                "relation": "can_feed",
                "metadata": {
                    "success": True,
                    "evidence_refs": ["trace_20260820_001#events=6-14"],
                },
            },
            {
                "source": "skill2",
                "target": "skill3",
                "relation": "can_feed",
                "metadata": {
                    "success": False,
                    "reason": "skill3 未能继续处理 skill2 产生的中间结果",
                    "evidence_refs": ["trace_20260820_001#events=15-23"],
                },
            },
            {
                "source": "skill2",
                "target": "skill5",
                "relation": "can_feed",
                "metadata": {
                    "success": True,
                    "evidence_refs": ["trace_20260820_001#events=24-36"],
                },
            },
        ],
    },
}


def test_ingest_accepts_documented_execution_graph(tmp_path: Path) -> None:
    """4.3.1 文档格式的执行图可直接接入：失败边剔除成员、分支边保留。"""

    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.5,
        min_successes_candidate=1,
        min_successes_verified=1,
        min_pack_success_rate_verified=0.5,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)

    assert engine.ingest(_DOCUMENTED_EXECUTION_GRAPH) is True
    # 证据层保留失败原因与证据引用（可追溯，但不进 skill pack）
    stored = engine.store.read_evidence()[0]
    reasons = [edge["metadata"].get("reason") for edge in stored.graph["edges"]]
    assert "skill3 未能继续处理 skill2 产生的中间结果" in reasons

    report = asyncio.run(engine.distill())
    assert len(report.recipes_saved) == 1
    recipe = engine.get_recipe(report.recipes_saved[0])
    pack = recipe.combination_structure

    # 失败边（skill2→skill3）不合格：skill3 不进入组合
    assert set(pack["nodes"]) == {"skill1", "skill2", "skill5"}
    # 同一源（skill2）的多条合格出边保留为分支
    edge_keys = {(edge["source"], edge["target"]) for edge in pack["edges"]}
    assert edge_keys == {("skill1", "skill2"), ("skill2", "skill5")}
    # 整体 outcome 与边级归因独立：样本整体成功
    assert recipe.quality["execution_count"] == 1
    assert recipe.quality["success_count"] == 1


def test_non_success_evidence_does_not_reduce_pack_success_rate(tmp_path: Path) -> None:
    """Flow only distills overall-success executions."""

    config = SymphonyFlowConfig(
        min_edge_support=1,
        min_edge_success_rate=0.5,
        min_successes_candidate=1,
        min_successes_verified=3,
        min_pack_success_rate_verified=0.8,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)
    for index in range(6):
        assert engine.ingest(_execution_graph(f"trace-{index}", query=f"调研任务 {index}"))
    for index in range(6, 8):
        assert not engine.ingest(_execution_graph(f"trace-{index}", query=f"调研任务 {index}", outcome="failed"))

    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(report.recipes_saved[0])

    # failed executions never enter Flow evidence or recipe statistics.
    assert report.evidence_total == 6
    assert recipe.quality["success_count"] == 6
    assert recipe.quality["execution_count"] == 6
    assert recipe.quality["pack_success_rate"] == 1.0
    assert recipe.grade == RECIPE_GRADE_VERIFIED
    assert recipe.status == RECIPE_STATUS_ACTIVE


def test_generated_script_uses_whitelisted_operators(tmp_path: Path) -> None:
    engine = _feed_verified_engine(tmp_path)
    report = asyncio.run(engine.distill())
    recipe = engine.get_recipe(_verified_recipe_id(engine, report))

    script = generate_swarmflow_script(
        recipe.combination_structure,
        recipe_id=recipe.recipe_id,
        task_description="调研并生成报告",
    )
    assert "from swarmflow import agent" in script
    assert "await agent(" in script
    assert validate_generated_script(script) == []


def _graph_with_edges(
    trace_id: str,
    edges: list[tuple[str, str, bool]],
) -> dict:
    """构造固定节点集合 {s1,s2,s3,s5}、自定义边成败的执行图。"""

    return {
        "trace_id": trace_id,
        "query": f"任务 {trace_id}",
        "outcome": "success",
        "graph": {
            "id": f"graph_{trace_id}",
            "type": "execution_graph",
            "directed": True,
            "nodes": {node: {"label": "skill", "metadata": {"version": "1.0.0"}} for node in ("s1", "s2", "s3", "s5")},
            "edges": [
                {
                    "source": source,
                    "target": target,
                    "relation": "can_feed",
                    "metadata": {"success": success},
                }
                for source, target, success in edges
            ],
        },
    }


def test_same_nodes_different_structures_split_groups(tmp_path: Path) -> None:
    """同节点集合 ≠ 同 pack：按合格成功边结构分组，归因互不污染。

    全部轨迹节点集合均为 {s1,s2,s3,s5}：
    - t1/t2/t3/t6 走 s1→s2→s5（t6 另有失败尝试 s5→s3）
    - t4/t5     走 s1→s2→s3
    期望：两个独立 recipe；t6 的失败边不分裂分组、t4/t5 的成功
    不给 s1→s2→s5 组虚增成功经验（边方向由 source→target 决定）。
    """

    config = SymphonyFlowConfig(
        min_edge_support=2,
        min_edge_success_rate=0.8,
        min_successes_candidate=2,
        min_successes_verified=3,
        min_pack_success_rate_verified=0.8,
    )
    engine = SymphonyFlowEngine(tmp_path / "flow", config=config)
    engine.ingest(_graph_with_edges("t1", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t2", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t3", [("s1", "s2", True), ("s2", "s5", True)]))
    engine.ingest(_graph_with_edges("t4", [("s1", "s2", True), ("s2", "s3", True)]))
    engine.ingest(_graph_with_edges("t5", [("s1", "s2", True), ("s2", "s3", True)]))
    # 失败尝试（s5→s3）：不影响分组键，也不给任何 pack 计成功
    engine.ingest(
        _graph_with_edges(
            "t6",
            [("s1", "s2", True), ("s2", "s5", True), ("s5", "s3", False)],
        )
    )

    report = asyncio.run(engine.distill())
    assert len(report.recipes_saved) == 2

    by_traces = {}
    for recipe_id in report.recipes_saved:
        recipe = engine.get_recipe(recipe_id)
        by_traces[tuple(sorted(recipe.provenance["evidence_trace_ids"]))] = recipe

    # 组1：s1→s2→s5（t1/t2/t3/t6），execution_count 不含 t4/t5 的成功
    group1 = by_traces[("t1", "t2", "t3", "t6")]
    assert set(group1.capability_ids) == {"s1", "s2", "s5"}
    assert group1.quality["execution_count"] == 4
    assert group1.quality["success_count"] == 4
    assert group1.grade == RECIPE_GRADE_VERIFIED

    # 组2：s1→s2→s3（t4/t5），独立 pack、独立统计
    group2 = by_traces[("t4", "t5")]
    assert set(group2.capability_ids) == {"s1", "s2", "s3"}
    assert group2.quality["execution_count"] == 2

    # 两个 pack 的边结构不同（方向由 source→target 决定）
    edges1 = {(edge["source"], edge["target"]) for edge in group1.combination_structure["edges"]}
    edges2 = {(edge["source"], edge["target"]) for edge in group2.combination_structure["edges"]}
    assert edges1 == {("s1", "s2"), ("s2", "s5")}
    assert edges2 == {("s1", "s2"), ("s2", "s3")}
    assert group1.recipe_id != group2.recipe_id


def test_team_subagent_nodes_keep_capability_type() -> None:
    evidence = normalize_execution_graph(
        {
            "trace_id": "team-trace",
            "outcome": "success",
            "graph": {
                "id": "team-graph",
                "type": "execution_graph",
                "directed": True,
                "nodes": {
                    "leader": {
                        "label": "subagent",
                        "metadata": {"version": "v1", "content_hash": "leader-hash"},
                    },
                    "writer": {
                        "label": "subagent",
                        "metadata": {"version": "v1", "content_hash": "writer-hash"},
                    },
                },
                "edges": [
                    {
                        "source": "leader",
                        "target": "writer",
                        "relation": "can_feed",
                        "metadata": {"success": True},
                    }
                ],
            },
        }
    )

    assert evidence is not None
    assert evidence.graph["nodes"]["leader"]["metadata"]["capability_type"] == "subagent"
    assert evidence.graph["nodes"]["writer"]["metadata"]["capability_type"] == "subagent"


def _stats(support: int) -> EdgeStats:
    return EdgeStats(support=support, success=support)


def test_break_cycles_removes_weakest_edge_on_ring() -> None:
    """纯环输入：拆除一条环边后必须无环，且拓扑序覆盖全部成员。"""

    edges = [("A", "B", "r"), ("B", "C", "r"), ("C", "A", "r")]
    stats = {edge: _stats(5) for edge in edges}

    kept = break_cycles(edges, stats)

    assert len(kept) == 2
    # 同 support 按边元组排序：删除 ("A", "B", "r")
    assert ("A", "B", "r") not in kept
    pack = {
        "nodes": {node: {} for node in ("A", "B", "C")},
        "edges": [{"source": src, "target": dst} for src, dst, _ in kept],
    }
    assert sorted(topological_order(pack)) == ["A", "B", "C"]


def test_break_cycles_keeps_tree_edges_outside_ring() -> None:
    """环外树边不参与拆环：即使 support 最低也不能被误删。"""

    edges = [("A", "B", "r"), ("B", "C", "r"), ("C", "A", "r"), ("D", "E", "r")]
    stats = {
        ("A", "B", "r"): _stats(5),
        ("B", "C", "r"): _stats(5),
        ("C", "A", "r"): _stats(5),
        ("D", "E", "r"): _stats(1),
    }

    kept = break_cycles(edges, stats)

    assert ("D", "E", "r") in kept
    assert len(kept) == 3


def test_break_cycles_removes_self_loop() -> None:
    kept = break_cycles([("A", "A", "r")], {("A", "A", "r"): _stats(5)})

    assert kept == []


def test_break_cycles_handles_multiple_rings_by_support() -> None:
    """8 字双环：每个环各拆一条 support 最低的环边。"""

    edges = [
        ("A", "B", "r"),
        ("B", "C", "r"),
        ("C", "A", "r"),
        ("C", "D", "r"),
        ("D", "E", "r"),
        ("E", "C", "r"),
    ]
    stats = {
        ("A", "B", "r"): _stats(9),
        ("B", "C", "r"): _stats(9),
        ("C", "A", "r"): _stats(2),
        ("C", "D", "r"): _stats(9),
        ("D", "E", "r"): _stats(9),
        ("E", "C", "r"): _stats(1),
    }

    kept = break_cycles(edges, stats)

    # 环1 拆 C→A（support=2），环2 拆 E→C（support=1），公共边 B→C 保留
    assert ("C", "A", "r") not in kept
    assert ("E", "C", "r") not in kept
    assert len(kept) == 4


def test_break_cycles_acyclic_input_unchanged() -> None:
    edges = [("A", "B", "r"), ("B", "C", "r")]

    assert break_cycles(edges, {edge: _stats(5) for edge in edges}) == edges
