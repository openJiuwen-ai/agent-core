# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluated context and diagnosis-status contracts; no model calls."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.harness_context import prepare_harness_context
from openjiuwen.rsi.harness_rsi.member_optimizer.loader import AnalysisUnavailableError, load_analysis_ref


def _yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    package = tmp_path / "package"
    _yaml(package / "harness.yaml", {"name": "evaluated", "skills": ["skills"]})
    (package / "identity.md").write_text("Evaluated identity, not the current candidate.", encoding="utf-8")
    skill = package / "skills" / "trace_owner"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: trace_owner\ndescription: Inspect ownership\n---\nRead owner.", encoding="utf-8"
    )
    _yaml(tmp_path / "refs.yaml", {"harness_refs": {"solver": str(package)}})
    evaluation = tmp_path / "eval_ref.yaml"
    _yaml(evaluation, {"harness_refs_path": "refs.yaml"})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return package, evaluation, workspace


def test_context_uses_evaluated_role_and_full_instructions_not_candidate(tmp_path: Path) -> None:
    package, evaluation, workspace = _fixture(tmp_path)
    text = "important instruction\n" * 2000
    _yaml(package / "prompt_sections" / "sections.yaml", {"sections": [{"name": "procedure", "content": text}]})
    context = prepare_harness_context(
        eval_ref_path=str(evaluation),
        harness_refs_path=str(tmp_path / "different_refs.yaml"),
        runtime_dir=workspace,
    )
    role = context["roles"][0]
    assert role["role"] == "solver"
    prompts = {
        item["name"]: json.loads((workspace / item["path"]).read_text(encoding="utf-8"))
        for item in role["prompt_sections"]
    }
    assert prompts["procedure"]["en"] == text
    assert "Evaluated identity" in str(prompts)
    assert role["skills"][0]["name"] == "trace_owner"
    assert "Read owner." in (workspace / role["skills"][0]["path"]).read_text(encoding="utf-8")


def test_context_matches_plugin_manifest_prompt_and_resource_declarations(tmp_path: Path) -> None:
    from openjiuwen.harness.resources import load_plugin_package

    package, evaluation, workspace = _fixture(tmp_path)
    prompt = package / "prompt_sections" / "check.md"
    prompt.parent.mkdir()
    prompt.write_text("Reopen the output before accepting it.", encoding="utf-8")
    manifest = {
        "package_type": "plugin",
        "id": "evaluated",
        "prompt_sections": [{"file": "prompt_sections/check.md"}],
        "skills": [{"dir": "skills"}],
        "tools": [{"file": "tools/check.py", "class": "CheckTool"}],
        "rails": [{"file": "rails/check.py", "class": "CheckRail"}],
    }
    for relative in ("tools/check.py", "rails/check.py"):
        source = package / relative
        source.parent.mkdir()
        source.write_text("# Declaration only; must not execute during diagnosis.\n", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    runtime_package = load_plugin_package(package / "manifest.json")
    context = prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=workspace)
    role = context["roles"][0]
    assert [item["name"] for item in role["prompt_sections"]] == ["check"]
    content = json.loads((workspace / role["prompt_sections"][0]["path"]).read_text(encoding="utf-8"))
    assert content == runtime_package.prompt_sections[0].content
    assert role["tools"] == manifest["tools"]
    assert role["rails"] == manifest["rails"]
    assert [item["name"] for item in role["skills"]] == ["trace_owner"]


@pytest.mark.parametrize("declaration", ["skills", "skills/trace_owner", "skills/trace_owner/SKILL.md"])
def test_context_matches_runtime_skill_mount_formats(tmp_path: Path, declaration: str) -> None:
    package, evaluation, workspace = _fixture(tmp_path)
    _yaml(package / "harness.yaml", {"name": "evaluated", "skills": [declaration]})
    context = prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=workspace)
    assert [item["name"] for item in context["roles"][0]["skills"]] == ["trace_owner"]


def test_context_does_not_export_runtime_configuration_or_follow_subagents(tmp_path: Path) -> None:
    package, evaluation, workspace = _fixture(tmp_path)
    (package / "config.json").write_text(
        json.dumps(
            {
                "model": {"api_key": "not-a-real-secret"},
                "env": {"PASSWORD": "not-a-real-secret"},
                "subagents": [{"file": "../../outside.yaml"}],
                "tools": [{"builtin": "read_file", "params": {"token": "not-a-real-secret"}}],
            }
        ),
        encoding="utf-8",
    )
    context = prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=workspace)
    assert context["roles"][0]["tools"] == [{"builtin": "read_file"}]
    assert "not-a-real-secret" not in "".join(
        file.read_text(encoding="utf-8") for file in workspace.rglob("*") if file.is_file()
    )


@pytest.mark.parametrize("surface", ["prompt_sections", "skills"])
def test_context_rejects_file_references_outside_package(tmp_path: Path, surface: str) -> None:
    package, evaluation, workspace = _fixture(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("not evidence", encoding="utf-8")
    value = [{"name": "unsafe", "file": str(outside)}] if surface == "prompt_sections" else [str(outside)]
    _yaml(package / "harness.yaml", {"name": "evaluated", surface: value})
    with pytest.raises(ValueError, match="leaves its package"):
        prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=workspace)


def test_context_rejects_symlinked_identity(tmp_path: Path) -> None:
    package, evaluation, workspace = _fixture(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("not evidence", encoding="utf-8")
    identity = package / "identity.md"
    identity.unlink()
    try:
        identity.symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit test symlinks")
    with pytest.raises(ValueError, match="leaves its package"):
        prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=workspace)


def test_legacy_evaluation_without_harness_does_not_invent_context(tmp_path: Path) -> None:
    evaluation = tmp_path / "eval_ref.yaml"
    _yaml(evaluation, {})
    assert prepare_harness_context(eval_ref_path=str(evaluation), harness_refs_path="", runtime_dir=tmp_path) == {
        "status": "not_provided",
        "roles": [],
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"analysis_status": "failed"},
        {"analysis_status": "partial"},
        {"analysis_status": "completed", "diagnosis_failed_count": 1},
    ],
)
def test_failed_diagnosis_is_not_no_issues(tmp_path: Path, metadata: dict) -> None:
    ref = tmp_path / "analysis_ref.yaml"
    _yaml(ref, {"issues": [], "metadata": metadata})
    with pytest.raises(AnalysisUnavailableError, match="not a no-issues result"):
        load_analysis_ref(ref).require_usable()


def test_partial_diagnosis_keeps_completed_issues_from_referenced_file(tmp_path: Path) -> None:
    _yaml(tmp_path / "issues.yaml", {"issues": [{"issue_id": "supported"}]})
    ref = tmp_path / "analysis_ref.yaml"
    _yaml(ref, {"issues_path": "issues.yaml", "metadata": {"analysis_status": "partial", "diagnosis_failed_count": 1}})
    analysis = load_analysis_ref(ref)
    analysis.require_usable()
    assert analysis.issues[0]["issue_id"] == "supported"
    assert analysis.diagnosis_incomplete


@pytest.mark.parametrize("metadata", [{}, {"analysis_status": "completed", "diagnosis_failed_count": 0}])
def test_genuine_no_issues_remains_valid(tmp_path: Path, metadata: dict) -> None:
    ref = tmp_path / "analysis_ref.yaml"
    _yaml(ref, {"issues": [], "metadata": metadata})
    assert load_analysis_ref(ref).require_usable() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_count,expected", [(0, "completed"), (1, "partial"), (2, "failed")])
async def test_strategy_publishes_actual_diagnosis_status(tmp_path: Path, monkeypatch, failed_count, expected) -> None:
    from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer as module
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import DeterministicSignals
    from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation

    strategy = module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    monkeypatch.setattr(strategy._case_reader, "read_eval_ref", lambda path: {})
    monkeypatch.setattr(
        strategy._case_reader, "read_summary", lambda path: SimpleNamespace(evaluation_method="default")
    )
    monkeypatch.setattr(
        strategy._case_reader,
        "read_case_inputs",
        lambda path: [
            SimpleNamespace(evaluation_passed=False),
            SimpleNamespace(evaluation_passed=False),
        ],
    )
    monkeypatch.setattr(
        module,
        "build_signal_extractor",
        lambda method: SimpleNamespace(
            extract=lambda summary, cases: DeterministicSignals(method="default"),
        ),
    )
    diagnosis = AsyncMock(
        return_value=[{"case_id": str(index), "analysis_failed": index < failed_count} for index in range(2)]
    )
    monkeypatch.setattr(strategy, "_per_case_diagnosis", diagnosis)
    monkeypatch.setattr(strategy, "_aggregate_diagnosis", AsyncMock(return_value=[]))
    invocation = EvaluationResultAnalysisInvocation(
        eval_ref_path="evaluated.yaml",
        case_results_dir="cases",
        case_traces_dir="traces",
        team_skill_ref_path="",
        harness_refs_path="current.yaml",
        output_dir=str(tmp_path),
    )
    result = await strategy.analyze(invocation)
    assert result.metadata["analysis_status"] == expected
    assert result.metadata["diagnosis_failed_count"] == failed_count
    assert diagnosis.call_args.kwargs["eval_ref_path"] == "evaluated.yaml"
    assert diagnosis.call_args.kwargs["harness_refs_path"] == "current.yaml"


@pytest.mark.asyncio
async def test_optimizer_rejects_failed_diagnosis_before_loading_model(tmp_path: Path) -> None:
    from openjiuwen.rsi.harness_rsi.config import MemberOptimizerConfig
    from openjiuwen.rsi.harness_rsi.member_optimizer.optimizer import MemberOptimizer

    ref = tmp_path / "analysis_ref.yaml"
    _yaml(ref, {"issues": [], "metadata": {"analysis_status": "failed"}})
    optimizer = MemberOptimizer(MemberOptimizerConfig(model_config_ref="nonexistent.yaml"))
    with pytest.raises(AnalysisUnavailableError):
        await optimizer.optimize("unused_eval.yaml", str(ref), "unused_harness.yaml", str(tmp_path / "output"))
    assert not list((tmp_path / "output").glob("member_optimization_*"))


@pytest.mark.asyncio
async def test_resume_retries_unavailable_analysis_without_losing_original_failure(tmp_path: Path) -> None:
    from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import SingleHarnessIterativeOptimizationOrchestrator

    original = tmp_path / "analysis" / "analysis_ref.yaml"
    _yaml(original, {"issues": [], "metadata": {"analysis_status": "failed"}})
    old_bytes = original.read_bytes()

    async def succeed(invocation):
        ref = Path(invocation.output_dir) / "analysis_ref.yaml"
        _yaml(ref, {"issues": [], "metadata": {"analysis_status": "completed"}})
        return str(ref)

    analyzer = SimpleNamespace(analyze=AsyncMock(side_effect=succeed))
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(),
        evaluator=object(),
        analyzer=analyzer,
        member_optimizer=object(),
    )
    arguments = dict(eval_ref_path="eval.yaml", harness_refs_path="h0.yaml", output_dir=original.parent)
    result = await orchestrator._analyze(**arguments)
    assert Path(result).parent.name == "retry_001"
    assert original.read_bytes() == old_bytes
    assert await orchestrator._analyze(**arguments) == result
    analyzer.analyze.assert_awaited_once()
