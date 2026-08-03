# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for offline Team Skill optimizer artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from openjiuwen.agent_evolving.checkpointing import EvolutionStore as RealEvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.rsi.config import TeamSkillOptimizerConfig
from openjiuwen.rsi.team_skill_optimizer import TeamSkillOptimizer, evolution_pipeline
from openjiuwen.rsi.team_skill_optimizer import artifacts as artifacts_module
from openjiuwen.rsi.team_skill_optimizer.analysis import (
    load_team_skill_issues_from_analysis,
)
from openjiuwen.rsi.team_skill_optimizer.artifacts import publish_candidate
from openjiuwen.rsi.team_skill_optimizer.signals import build_signals
from openjiuwen.rsi.team_skill_optimizer.trajectory_loader import load_offline_trajectory


class _FakeModel:
    def __init__(self, bodies: list[str] | None = None) -> None:
        self.model_config = SimpleNamespace(model_name="fake-team-skill-model")
        self.bodies = list(bodies or [_optimized_body("default")])
        self.invocations: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> SimpleNamespace:
        self.invocations.append(kwargs)
        body = self.bodies.pop(0) if self.bodies else _optimized_body("fallback")
        return SimpleNamespace(content=body)


class _FakeOnlineEvolutionOrchestrator:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.store = kwargs["store"]
        self.kwargs = kwargs

    async def evolve(self, **kwargs: Any) -> SimpleNamespace:
        self.__class__.calls.append(kwargs)
        record = EvolutionRecord.make(
            source="team_skill_trajectory_patch",
            context="analysis issue",
            change=EvolutionPatch(
                section="Workflow",
                action="append",
                content="Add explicit reviewer handoff before final answer.",
                target=EvolutionTarget.BODY,
            ),
            summary="Improve team handoff",
        )
        await self.store.append_record(kwargs["skill_name"], record)
        return SimpleNamespace(
            request_id="auto_team_skill_evolve_req_001",
            proposal=SimpleNamespace(records=[record]),
            pending_change=None,
        )


class _FakeNoRecordOnlineEvolutionOrchestrator:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def evolve(self, **kwargs: Any) -> SimpleNamespace:
        self.__class__.calls.append(kwargs)
        return SimpleNamespace(
            request_id="auto_team_skill_evolve_no_records",
            proposal=SimpleNamespace(records=[]),
            pending_change=None,
        )


@pytest.fixture(autouse=True)
def _reset_fake_orchestrator() -> None:
    _FakeOnlineEvolutionOrchestrator.calls = []
    _FakeNoRecordOnlineEvolutionOrchestrator.calls = []


def _optimized_body(label: str) -> str:
    return (
        "## Instructions\n"
        f"- Optimized team coordination body for {label} with explicit routing and verification gates.\n"
        "## Workflow\n"
        "- Solver must hand off reasoning, reviewer must verify, and leader must check final answer readiness.\n"
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, model: _FakeModel | None = None) -> _FakeModel:
    fake_model = model or _FakeModel()
    monkeypatch.setattr(evolution_pipeline, "load_member_optimizer_model", lambda _path: fake_model)
    monkeypatch.setattr(evolution_pipeline, "OnlineEvolutionOrchestrator", _FakeOnlineEvolutionOrchestrator)
    return fake_model


class _RecordingEvolutionStore(RealEvolutionStore):
    skill_exists_calls: list[str] = []

    def skill_exists(self, name: str) -> bool:
        self.__class__.skill_exists_calls.append(name)
        return super().skill_exists(name)


def _write_source_team_skill(root: Path, name: str = "math_team") -> Path:
    source_dir = root / name
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text(
        (f"---\nname: {name}\nkind: team-skill\n---\n## Instructions\n- Existing coordination policy.\n"),
        encoding="utf-8",
    )
    (source_dir / "team_agent_spec.yaml").write_text(
        yaml.safe_dump({"team_name": name, "agents": {"leader": {}}}, sort_keys=False),
        encoding="utf-8",
    )
    return source_dir


def _write_eval_ref(root: Path, trace_path: Path | None = None) -> Path:
    eval_dir = root / "evaluation"
    eval_dir.mkdir(parents=True)
    trace_path = trace_path or _write_trace(eval_dir / "case_results" / "case_001")
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval_001",
                "eval_dir": str(eval_dir),
                "case_results_dir": str(eval_dir / "case_results"),
                "case_traces_dir": str(eval_dir / "case_results"),
                "cases": [
                    {
                        "case_id": "case_001",
                        "trace_path": str(trace_path),
                        "result_path": str(trace_path.parent / "result.json"),
                        "status": "failed",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return eval_ref_path


def _write_trace(case_dir: Path) -> Path:
    case_dir.mkdir(parents=True, exist_ok=True)
    trace_path = case_dir / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "case_id": "case_001",
                "input": "Solve the problem.",
                "response": "Draft answer without reviewer handoff.",
                "behavior_trace": {
                    "trajectory_window_summary": {
                        "recent_events": [
                            {
                                "event_index": 1,
                                "event_type": "send_message",
                                "summary": "Solver did not pass intermediate reasoning to reviewer.",
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return trace_path


def _write_analysis_ref(root: Path, issues: list[dict[str, Any]]) -> Path:
    analysis_dir = root / "analysis"
    analysis_dir.mkdir(parents=True)
    analysis_ref_path = analysis_dir / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues": issues}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return analysis_ref_path


def _write_analysis_ref_with_issues_path(root: Path, issues: list[dict[str, Any]]) -> Path:
    analysis_dir = root / "analysis"
    analysis_dir.mkdir(parents=True)
    issues_path = analysis_dir / "issues.yaml"
    issues_path.write_text(
        yaml.safe_dump({"issues": issues}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    analysis_ref_path = analysis_dir / "analysis_ref.yaml"
    analysis_ref_path.write_text(
        yaml.safe_dump({"issues_path": str(issues_path)}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return analysis_ref_path


def _team_issue() -> dict[str, Any]:
    return {
        "issue_id": "team_issue_001",
        "optimization_target": "team_skill",
        "category": "team_skill.handoff_protocol",
        "severity": "high",
        "summary": "Reviewer did not receive solver reasoning.",
        "recommendation": "Require solver-to-reviewer handoff before final answer.",
        "affected_cases": ["case_001"],
        "affected_components": ["reviewer"],
        "metadata": {
            "attribution": {
                "target_ref": "team_skill.handoff_protocol",
                "general_mechanism": "handoff missing",
                "root_cause": "shared context contract is underspecified",
                "critical_mistake": "final answer emitted before review",
            }
        },
    }


def test_analysis_module_filters_team_skill_issues_and_records_warnings(tmp_path: Path) -> None:
    analysis_ref_path = _write_analysis_ref(
        tmp_path,
        [
            _team_issue(),
            {"issue_id": "member_issue", "optimization_target": "member_harness", "summary": "Member issue."},
            ["not", "mapping"],
            {"issue_id": "empty_team_issue", "optimization_target": "team_skill"},
        ],
    )

    result = load_team_skill_issues_from_analysis(str(analysis_ref_path))

    assert [issue["issue_id"] for issue in result.issues] == ["team_issue_001"]
    assert result.skipped_warnings == [
        {"reason": "issue_not_mapping", "index": 2},
        {"reason": "team_skill_issue_missing_actionable_content", "index": 3},
    ]


def test_signal_module_maps_attribution_fields_without_secondary_attribution() -> None:
    issue = {
        "issue_id": "team_issue_002",
        "optimization_target": "team_skill",
        "category": "team_skill.routing_policy",
        "severity": "critical",
        "summary": "Routing was ambiguous.",
        "evidence": [{"affected_component": "leader"}],
        "metadata": {
            "attribution": {
                "target_ref": "team_skill.routing_policy",
                "root_cause": "role routing is underspecified",
            }
        },
    }

    signals = build_signals(
        issues=[issue],
        skill_name="math_team",
        skill_content="## Instructions\n- Existing policy.\n",
        eval_ref_path="eval_ref.yaml",
        analysis_result_path="analysis_ref.yaml",
    )

    trajectory_issue = signals[0].context["trajectory_issues"][0]
    assert signals[0].signal_type == "trajectory_issue"
    assert signals[0].context["source"] == "auto_coordinating_harness_analysis"
    assert trajectory_issue["issue_type"] == "routing_policy"
    assert trajectory_issue["affected_role"] == "leader"
    assert trajectory_issue["severity"] == "medium"
    assert "root_cause: role routing is underspecified" in trajectory_issue["description"]


def test_trajectory_loader_returns_none_when_declared_trace_is_missing(tmp_path: Path) -> None:
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval_001",
                "eval_dir": str(eval_dir),
                "case_results_dir": str(eval_dir / "case_results"),
                "cases": [
                    {
                        "case_id": "case_missing",
                        "trace_path": "case_results/case_missing/trace.json",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    trajectory = load_offline_trajectory(
        str(eval_ref_path),
        [{"optimization_target": "team_skill", "affected_cases": ["case_missing"]}],
    )

    assert trajectory is None


def test_artifacts_publish_candidate_overwrites_single_current_dir(tmp_path: Path) -> None:
    first_candidate = tmp_path / "candidate_001" / "math_team"
    second_candidate = tmp_path / "candidate_002" / "math_team"
    publish_dir = tmp_path / "current_team_skill"
    first_candidate.mkdir(parents=True)
    second_candidate.mkdir(parents=True)
    (first_candidate / "SKILL.md").write_text("first", encoding="utf-8")
    (second_candidate / "SKILL.md").write_text("second", encoding="utf-8")

    publish_candidate(first_candidate, publish_dir)
    publish_candidate(second_candidate, publish_dir)

    assert (publish_dir / "SKILL.md").read_text(encoding="utf-8") == "second"
    assert not list(tmp_path.glob(".current_team_skill_tmp_*"))
    assert not list(tmp_path.glob(".current_team_skill_backup_*"))


def test_candidate_workspace_uses_short_paths_for_windows_max_path(tmp_path: Path) -> None:
    """Team Skill candidate copying must avoid deep Windows MAX_PATH-sensitive paths."""
    source_dir = _write_source_team_skill(
        tmp_path / "initial_team_skills",
        name="energy-storage-investor-page-swarm",
    )
    roles_dir = source_dir / "roles"
    roles_dir.mkdir()
    (roles_dir / "market-product-researcher.md").write_text("# Role\n", encoding="utf-8")
    output_dir = (
        tmp_path
        / ("webpage_single_harness_" + "x" * 32)
        / "workspace"
        / "energy-storage-investor-page-swarm"
        / "team_skills"
    )
    output_dir.mkdir(parents=True)
    run_dir = artifacts_module.allocate_optimization_dir(output_dir)
    source_ref = artifacts_module.resolve_source_skill(str(source_dir))

    artifacts = artifacts_module.prepare_candidate_workspace(
        output_root=output_dir,
        run_dir=run_dir,
        source_ref=source_ref,
    )

    copied_role = artifacts.candidate_skill_dir / "roles" / "market-product-researcher.md"
    assert copied_role.is_file()
    assert len(str(copied_role)) < 260


@pytest.mark.asyncio
async def test_team_issue_generates_records_rebuilds_and_publishes_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source_dir = _write_source_team_skill(tmp_path)
    eval_ref_path = _write_eval_ref(tmp_path)
    analysis_ref_path = _write_analysis_ref(tmp_path, [_team_issue()])
    output_dir = tmp_path / "team_skills"

    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))
    optimized_dir = Path(
        await optimizer.optimize(
            eval_ref_path=str(eval_ref_path),
            analysis_result_path=str(analysis_ref_path),
            team_skill_ref_path=str(source_dir),
            output_dir=str(output_dir),
        )
    )

    assert optimized_dir == output_dir.resolve() / "current_team_skill"
    assert (optimized_dir / "SKILL.md").is_file()
    assert "Optimized team coordination body" in (optimized_dir / "SKILL.md").read_text(encoding="utf-8")
    assert not (optimized_dir / "optimization_plan.yaml").exists()

    evolve_call = _FakeOnlineEvolutionOrchestrator.calls[0]
    assert evolve_call["requires_approval"] is False
    assert evolve_call["source"] == "auto_coordinating_harness_team_skill_optimizer"
    assert evolve_call["trajectory"] is not None
    from openjiuwen.agent_evolving.trajectory import to_legacy_trajectory

    assert to_legacy_trajectory(evolve_call["trajectory"]).meta["trace_paths"] == [
        str((eval_ref_path.parent / "case_results" / "case_001" / "trace.json").resolve())
    ]
    signal = evolve_call["signals"][0]
    assert signal.signal_type == "trajectory_issue"
    assert signal.context["source"] == "auto_coordinating_harness_analysis"
    assert signal.context["analysis_issue"]["issue_id"] == "team_issue_001"
    assert signal.context["trajectory_issues"][0] == {
        "issue_type": "handoff_protocol",
        "description": (
            "summary: Reviewer did not receive solver reasoning.\n"
            "recommendation: Require solver-to-reviewer handoff before final answer.\n"
            "general_mechanism: handoff missing\n"
            "root_cause: shared context contract is underspecified\n"
            "critical_mistake: final answer emitted before review\n"
            "target_ref: team_skill.handoff_protocol"
        ),
        "affected_role": "reviewer",
        "severity": "high",
    }

    run_dir = output_dir / "tso_001"
    metadata = yaml.safe_load((run_dir / "optimization_metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["status"] == "success"
    assert metadata["selected_issue_ids"] == ["team_issue_001"]
    assert metadata["signal_count"] == 1
    assert metadata["request_id"] == "auto_team_skill_evolve_req_001"
    assert metadata["applied_count"] == 1
    assert metadata["returned_team_skill_ref_path"] == str(optimized_dir)
    assert metadata["candidate_path"].endswith("c1\\math_team") or metadata["candidate_path"].endswith("c1/math_team")

    evolution_log = json.loads((optimized_dir / "evolutions.json").read_text(encoding="utf-8"))
    assert evolution_log["entries"][0]["applied"] is True
    current_ref = yaml.safe_load((output_dir / "current_team_skill_ref.yaml").read_text(encoding="utf-8"))
    assert current_ref["team_skill_ref_path"] == str(optimized_dir)
    assert Path(current_ref["source_optimization_ref_path"]).name == "team_skill_optimization_ref.yaml"


@pytest.mark.asyncio
async def test_analyzer_team_issue_uses_trajectory_patch_when_aggregate_flow_returns_no_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = _FakeModel([_optimized_body("direct trajectory patch")])
    monkeypatch.setattr(evolution_pipeline, "load_member_optimizer_model", lambda _path: fake_model)
    monkeypatch.setattr(
        evolution_pipeline,
        "OnlineEvolutionOrchestrator",
        _FakeNoRecordOnlineEvolutionOrchestrator,
    )
    trajectory_patch_calls: list[dict[str, Any]] = []

    async def fake_generate_trajectory_patch(
        self: Any,
        trajectory: Any,
        skill_name: str,
        content: str,
        issues: list,
    ) -> EvolutionRecord:
        trajectory_patch_calls.append(
            {
                "skill_name": skill_name,
                "content": content,
                "issues": issues,
                "trace_paths": getattr(trajectory, "meta", {}).get("trace_paths"),
            }
        )
        return EvolutionRecord.make(
            source="team_skill_trajectory_patch",
            context="analysis trajectory issue",
            change=EvolutionPatch(
                section="Workflow",
                action="append",
                content="After artifact harvest reports missing=[], call clean_team immediately.",
                target=EvolutionTarget.BODY,
            ),
            summary="Terminate lifecycle after successful artifact harvest",
        )

    monkeypatch.setattr(
        evolution_pipeline.TeamSkillExperienceOptimizer,
        "generate_trajectory_patch",
        fake_generate_trajectory_patch,
    )
    source_dir = _write_source_team_skill(tmp_path, name="card_team")
    eval_ref_path = _write_eval_ref(tmp_path)
    issue = _team_issue()
    issue["metadata"]["attribution"]["target_ref"] = "team_skill.team_leader.workflow_inefficiency"
    issue["recommendation"] = "After artifact harvest reports missing=[], call clean_team immediately."
    analysis_ref_path = _write_analysis_ref(tmp_path, [issue])
    output_dir = tmp_path / "team_skills"

    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))
    optimized_dir = Path(
        await optimizer.optimize(
            eval_ref_path=str(eval_ref_path),
            analysis_result_path=str(analysis_ref_path),
            team_skill_ref_path=str(source_dir),
            output_dir=str(output_dir),
        )
    )

    assert optimized_dir == output_dir.resolve() / "current_team_skill"
    assert trajectory_patch_calls
    assert trajectory_patch_calls[0]["skill_name"] == "card_team"
    assert "clean_team" in json.dumps(trajectory_patch_calls[0]["issues"], ensure_ascii=False)
    assert "direct trajectory patch" in (optimized_dir / "SKILL.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load((output_dir / "tso_001" / "optimization_metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["status"] == "success"
    assert metadata["generated_record_count"] == 1
    assert metadata["applied_count"] == 1


@pytest.mark.asyncio
async def test_skill_directory_resolution_uses_evolution_store_skill_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    _RecordingEvolutionStore.skill_exists_calls = []
    monkeypatch.setattr(artifacts_module, "EvolutionStore", _RecordingEvolutionStore)
    monkeypatch.setattr(evolution_pipeline, "EvolutionStore", _RecordingEvolutionStore)
    source_dir = _write_source_team_skill(tmp_path, name="coordinator_skill")
    eval_ref_path = _write_eval_ref(tmp_path)
    analysis_ref_path = _write_analysis_ref_with_issues_path(tmp_path, [_team_issue()])

    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))
    optimized_dir = await optimizer.optimize(
        eval_ref_path=str(eval_ref_path),
        analysis_result_path=str(analysis_ref_path),
        team_skill_ref_path=str(source_dir),
        output_dir=str(tmp_path / "team_skills"),
    )

    assert Path(optimized_dir).name == "current_team_skill"
    assert "coordinator_skill" in _RecordingEvolutionStore.skill_exists_calls
    metadata = yaml.safe_load(
        (tmp_path / "team_skills" / "tso_001" / "optimization_metadata.yaml").read_text(encoding="utf-8")
    )
    assert metadata["source_issues_path"].endswith("issues.yaml")


@pytest.mark.asyncio
async def test_member_only_analysis_noops_without_evolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    source_dir = _write_source_team_skill(tmp_path)
    eval_ref_path = _write_eval_ref(tmp_path)
    analysis_ref_path = _write_analysis_ref(
        tmp_path,
        [
            {
                "issue_id": "member_issue_001",
                "optimization_target": "member_harness",
                "category": "member_harness",
                "summary": "Member prompt issue.",
            }
        ],
    )

    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))
    optimized_dir = Path(
        await optimizer.optimize(
            eval_ref_path=str(eval_ref_path),
            analysis_result_path=str(analysis_ref_path),
            team_skill_ref_path=str(source_dir),
            output_dir=str(tmp_path / "team_skills"),
        )
    )

    assert optimized_dir == source_dir.resolve()
    assert _FakeOnlineEvolutionOrchestrator.calls == []
    run_dir = tmp_path / "team_skills" / "tso_001"
    assert not (run_dir / "optimization_plan.yaml").exists()
    metadata = yaml.safe_load((run_dir / "optimization_metadata.yaml").read_text(encoding="utf-8"))
    assert metadata["status"] == "no_team_skill_issues"
    assert metadata["selected_issue_ids"] == []


@pytest.mark.asyncio
async def test_missing_model_config_ref_fails_clearly(tmp_path: Path) -> None:
    source_dir = _write_source_team_skill(tmp_path)
    eval_ref_path = _write_eval_ref(tmp_path)
    analysis_ref_path = _write_analysis_ref(tmp_path, [_team_issue()])

    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig())

    with pytest.raises(RuntimeError, match="model_config_ref"):
        await optimizer.optimize(
            eval_ref_path=str(eval_ref_path),
            analysis_result_path=str(analysis_ref_path),
            team_skill_ref_path=str(source_dir),
            output_dir=str(tmp_path / "team_skills"),
        )


@pytest.mark.asyncio
async def test_max_candidates_greater_than_one_fails(tmp_path: Path) -> None:
    source_dir = _write_source_team_skill(tmp_path)
    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml", max_candidates=2))

    with pytest.raises(RuntimeError, match="max_candidates"):
        await optimizer.optimize(
            eval_ref_path=str(tmp_path / "eval_ref.yaml"),
            analysis_result_path=str(tmp_path / "analysis_ref.yaml"),
            team_skill_ref_path=str(source_dir),
            output_dir=str(tmp_path / "team_skills"),
        )


@pytest.mark.asyncio
async def test_missing_team_skill_directory_fails_clearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)
    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))

    with pytest.raises(RuntimeError, match="team_skill_ref_path"):
        await optimizer.optimize(
            eval_ref_path=str(tmp_path / "eval_ref.yaml"),
            analysis_result_path=str(tmp_path / "analysis_ref.yaml"),
            team_skill_ref_path=str(tmp_path / "missing_team_skill"),
            output_dir=str(tmp_path / "team_skills"),
        )


@pytest.mark.asyncio
async def test_repeated_optimize_overwrites_same_current_team_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _patch_pipeline(monkeypatch, _FakeModel([_optimized_body("first"), _optimized_body("second")]))
    source_dir = _write_source_team_skill(tmp_path)
    eval_ref_path = _write_eval_ref(tmp_path)
    analysis_ref_path = _write_analysis_ref(tmp_path, [_team_issue()])
    output_dir = tmp_path / "team_skills"
    optimizer = TeamSkillOptimizer(TeamSkillOptimizerConfig(model_config_ref="model.yaml"))

    first = await optimizer.optimize(
        eval_ref_path=str(eval_ref_path),
        analysis_result_path=str(analysis_ref_path),
        team_skill_ref_path=str(source_dir),
        output_dir=str(output_dir),
    )
    second = await optimizer.optimize(
        eval_ref_path=str(eval_ref_path),
        analysis_result_path=str(analysis_ref_path),
        team_skill_ref_path=first,
        output_dir=str(output_dir),
    )

    assert first == second
    assert Path(second) == output_dir.resolve() / "current_team_skill"
    assert "for second" in (Path(second) / "SKILL.md").read_text(encoding="utf-8")
    assert (output_dir / "tso_001").is_dir()
    assert (output_dir / "tso_002").is_dir()
    assert len(model.invocations) == 2
