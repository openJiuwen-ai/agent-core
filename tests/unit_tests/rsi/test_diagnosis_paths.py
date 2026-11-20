# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Diagnosis identity must not make runtime and archived paths unbounded."""

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from openjiuwen.core.common.logging.invocation import invocation_log_scope
from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput, DeterministicSignals


@pytest.fixture
def diagnosis_root(tmp_path: Path) -> Iterator[Path]:
    """Keep real Windows IO independent of pytest's long test-name prefixes."""
    if os.name == "nt":
        with tempfile.TemporaryDirectory(prefix="rd-") as directory:
            yield Path(directory)
    else:
        yield tmp_path


def _case(tmp_path: Path, case_id: str) -> CaseAnalysisInput:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    result = source / "result.json"
    result.write_text("{}", encoding="utf-8")
    return CaseAnalysisInput(
        case_id=case_id, status="failed", score=0.0, input="write the required answer", expected=None,
        response="", error="", evaluation_method="script_based", evaluation_passed=False,
        evaluation_reason="required answer missing", evaluation_metadata={},
        trace_path=str(source / "trace.json"), result_path=str(result),
    )


def _strategy(monkeypatch: pytest.MonkeyPatch, workspaces: list[Path]) -> analyzer.DiagnosisAgentStrategy:
    strategy = analyzer.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))

    async def build(workspace: str, *, invocation_log_dir: str) -> dict[str, str]:
        workspaces.append(Path(workspace))
        # Exercise the real invocation logger's filename and file creation.
        with invocation_log_scope(invocation_log_dir, agent_id="diagnosis", session_id="test"):
            pass
        return {"workspace": workspace}

    async def run(agent: Any, prompt: str, *, max_retries: int) -> str:
        return json.dumps({
            "issue_category": "member_harness", "severity": "medium", "summary": "missing completion gate",
            "failure_mode": "missing_answer", "root_cause": "answer omitted", "critical_mistake": "stopped early",
            "general_mechanism": "require the final answer", "target_ref": "member_harness.prompt_section",
            "evidence_refs": [], "affected_components": ["solver"], "recommendation": "add completion gate",
            "decision_contract": {"acceptance_observable": "the final answer is present"},
            "confidence": "medium",
        })

    monkeypatch.setattr(strategy, "_build_agent", build)
    monkeypatch.setattr(analyzer, "_run_agent", run)
    return strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", [
    "T086zh_llm_cache_lifecycle_exhaustive_model_checker",
    "中文/../CON:" + "😀" * 300,
    "",
], ids=["reported-case", "long-unicode-case", "empty-case"])
async def test_diagnosis_archives_short_attempts_without_losing_case_identity(
    diagnosis_root: Path, monkeypatch: pytest.MonkeyPatch, case_id: str,
) -> None:
    tmp_path = diagnosis_root
    workspaces = []
    strategy = _strategy(monkeypatch, workspaces)
    output = tmp_path / "failure_analysis"
    case = _case(tmp_path, case_id)
    for _ in range(2):
        results = await strategy._per_case_diagnosis(
            [case], DeterministicSignals(method="script_based"), None, output_dir=output,
        )
        assert results[0]["case_id"] == case_id
        assert results[0]["root_cause"] == "answer omitted"
    attempts = list((output / "diagnosis_attempts").iterdir())
    assert len(attempts) == 2
    for attempt in attempts:
        assert len(attempt.name) == 34 and attempt.name.startswith("d-")
        int(attempt.name[2:], 16)
        mapping = json.loads((attempt / "attempt.json").read_text(encoding="utf-8"))
        assert mapping == {"schema_version": 1, "case_id": case_id, "attempt_id": attempt.name}
        assert (attempt / "evidence/runtime_evidence/runtime_config.json").is_file()
        assert (attempt / "evidence/runtime_evidence/termination.json").is_file()
        assert len(list((attempt / "logs").glob("invocation-*.jsonl"))) == 1
    assert len({path.name for path in workspaces}) == 2
    assert not any(path.parent.exists() for path in workspaces)


@pytest.mark.asyncio
async def test_diagnosis_does_not_overwrite_a_colliding_attempt(
    diagnosis_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = diagnosis_root
    workspaces = []
    strategy = _strategy(monkeypatch, workspaces)
    output = tmp_path / "analysis"
    occupied = output / "diagnosis_attempts" / ("d-" + "1" * 32)
    occupied.mkdir(parents=True)
    sentinel = occupied / "attempt.json"
    sentinel.write_text('{"case_id":"previous-case"}', encoding="utf-8")
    ids = iter(["1" * 32, "2" * 32])
    monkeypatch.setattr(analyzer.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)))
    results = await strategy._per_case_diagnosis(
        [_case(tmp_path, "new-case")], DeterministicSignals(), None, output_dir=output,
    )
    assert results[0]["case_id"] == "new-case"
    assert json.loads(sentinel.read_text())["case_id"] == "previous-case"
    new = output / "diagnosis_attempts" / ("d-" + "2" * 32)
    assert json.loads((new / "attempt.json").read_text())["case_id"] == "new-case"
    assert len(workspaces) == 1


@pytest.mark.parametrize("prefix", ["C:\\", "\\\\server\\share\\"])
def test_windows_budget_includes_log_filename_and_utf16_units(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import diagnosis_paths

    monkeypatch.setattr(diagnosis_paths, "_IS_WINDOWS", True)
    # The longest fixed archive suffix is 5 + 50 = 55 UTF-16 units.
    prefix_units = len(prefix.encode("utf-16-le")) // 2
    attempt = PureWindowsPath(prefix + "a" * (192 - prefix_units))
    runtime = PureWindowsPath("C:/tmp/runtime")
    diagnosis_paths.validate_diagnosis_paths(runtime, attempt, case_id="case", source_stage="candidate_failure")
    for extra in ["a", "😀"]:
        too_long = PureWindowsPath(str(attempt) + extra)
        with pytest.raises(diagnosis_paths.DiagnosisPathTooLongError) as exc:
            diagnosis_paths.validate_diagnosis_paths(
                runtime, too_long, case_id="case", source_stage="candidate_failure",
            )
        assert exc.value.path_role == "archive"
        assert exc.value.length == (248 if extra == "a" else 249)
        assert "JIUWENSWARM_DATA_DIR" in str(exc.value)
        assert "candidate_failure" in str(exc.value)


def test_windows_budget_checks_temporary_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import diagnosis_paths

    monkeypatch.setattr(diagnosis_paths, "_IS_WINDOWS", True)
    with pytest.raises(diagnosis_paths.DiagnosisPathTooLongError) as exc:
        diagnosis_paths.validate_diagnosis_paths(
            PureWindowsPath("C:/" + "a" * 230), PureWindowsPath("C:/out"), case_id="case", source_stage="batch",
        )
    assert exc.value.path_role == "runtime"


def test_windows_budget_includes_projected_harness_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import diagnosis_paths

    monkeypatch.setattr(diagnosis_paths, "_IS_WINDOWS", True)
    runtime = PureWindowsPath("C:/" + "a" * 206)
    prompt = runtime / "current_harness/role_001/prompt_001.json"
    assert len(str(prompt)) == 250
    with pytest.raises(diagnosis_paths.DiagnosisPathTooLongError) as exc:
        diagnosis_paths.validate_diagnosis_paths(runtime, PureWindowsPath("C:/out"))
    assert exc.value.path_role == "runtime"
    assert exc.value.length == 250


def test_non_windows_does_not_apply_windows_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import diagnosis_paths

    monkeypatch.setattr(diagnosis_paths, "_IS_WINDOWS", False)
    diagnosis_paths.validate_diagnosis_paths(Path("/tmp") / ("a" * 300), Path("/out") / ("b" * 300))


@pytest.mark.asyncio
async def test_budget_failure_precedes_evidence_copy_and_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import diagnosis_paths

    monkeypatch.setattr(diagnosis_paths, "_IS_WINDOWS", True)
    workspaces = []
    strategy = _strategy(monkeypatch, workspaces)
    output = tmp_path / ("a" * 180) / "failure_analysis" / "retry_001"
    with pytest.raises(diagnosis_paths.DiagnosisPathTooLongError):
        await strategy._per_case_diagnosis(
            [_case(tmp_path, "case")], DeterministicSignals(), None,
            output_dir=output, source_stage="single_harness_candidate_failure",
        )
    assert workspaces == []
    assert not list(tmp_path.rglob("evidence_manifest.json"))
    assert not output.exists()


@pytest.mark.asyncio
async def test_evidence_copy_failure_propagates_and_cleans_runtime(
    diagnosis_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = diagnosis_root
    workspaces = []
    strategy = _strategy(monkeypatch, workspaces)
    runtime_roots = []
    original_mkdtemp = analyzer.tempfile.mkdtemp

    def allocate_runtime(*, prefix: str) -> str:
        root = original_mkdtemp(prefix=prefix)
        runtime_roots.append(Path(root))
        return root

    def fail_copy(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("evidence archive is not writable")

    monkeypatch.setattr(analyzer.tempfile, "mkdtemp", allocate_runtime)
    monkeypatch.setattr(analyzer.shutil, "copytree", fail_copy)
    output = tmp_path / "analysis"
    with pytest.raises(PermissionError, match="evidence archive is not writable"):
        await strategy._per_case_diagnosis(
            [_case(tmp_path, "case")], DeterministicSignals(), None, output_dir=output,
        )
    assert workspaces == []
    assert runtime_roots and not any(path.exists() for path in runtime_roots)
    mapping = next(output.rglob("attempt.json"))
    assert json.loads(mapping.read_text())["case_id"] == "case"


@pytest.mark.asyncio
async def test_short_attempt_results_reach_feedback_and_next_candidate_plan(
    diagnosis_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = diagnosis_root
    from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import (
        compile_optimization_hypotheses,
        load_optimization_hypotheses,
    )
    from openjiuwen.rsi.harness_rsi.member_optimizer.action_planner import MemberActionPlannerAgent
    from openjiuwen.rsi.harness_rsi.member_optimizer.schema import MechanismAttributionReport, RoleAttributionReport
    from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import (
        _compact_analysis_diagnoses,
        _prior_candidate_feedback,
        _refresh_optimization_experience,
    )

    case_id = "T086zh_llm_cache_lifecycle_exhaustive_model_checker"
    case = _case(tmp_path, case_id)
    Path(case.result_path).write_text(json.dumps({
        "case_id": case_id, "status": "failed", "score": 0.0,
        "evaluation": {"method": "script_based", "passed": False, "reason": "missing answer"},
    }))
    Path(case.trace_path).write_text(json.dumps({"input": case.input, "response": ""}))
    eval_ref = tmp_path / "eval_ref.yaml"
    eval_ref.write_text("{}")
    workspaces = []
    strategy = _strategy(monkeypatch, workspaces)
    facade = analyzer.EvaluationResultAnalyzer(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    facade._strategy = strategy
    analysis_ref = await facade.analyze(EvaluationResultAnalysisInvocation(
        eval_ref_path=str(eval_ref), case_results_dir=str(tmp_path), case_traces_dir=str(tmp_path),
        team_skill_ref_path="", harness_refs_path="", output_dir=str(tmp_path / "failure_analysis"),
        source_stage="single_harness_candidate_failure",
    ))
    metadata = yaml.safe_load(Path(analysis_ref).read_text())["metadata"]
    persisted = json.loads(Path(metadata["per_case_diagnoses_path"]).read_text())
    assert persisted["per_case_diagnoses"][0]["case_id"] == case_id
    compact = _compact_analysis_diagnoses(analysis_ref)
    state = {"candidate_gates": [{
        "status": "rejected", "verifier_deltas_by_case": {case_id: {}}, "candidate_failure_diagnoses": compact,
    }]}
    _refresh_optimization_experience(state, tmp_path)
    feedback = _prior_candidate_feedback(state, [{"case_id": case_id}])
    assert feedback["by_case"][case_id][0]["candidate_failure_diagnosis"]["root_cause"] == "answer omitted"
    inputs = []
    original_run = analyzer._run_agent

    async def run_with_feedback(agent: Any, prompt: str, *, max_retries: int) -> str:
        inline = prompt.split("### Inline Diagnosis Input JSON\n", 1)[1]
        payload, _ = json.JSONDecoder().raw_decode(inline)
        inputs.append(payload)
        return await original_run(agent, prompt, max_retries=max_retries)

    monkeypatch.setattr(analyzer, "_run_agent", run_with_feedback)
    residual_ref = await facade.analyze(EvaluationResultAnalysisInvocation(
        eval_ref_path=str(eval_ref), case_results_dir=str(tmp_path), case_traces_dir=str(tmp_path),
        team_skill_ref_path="", harness_refs_path="", output_dir=str(tmp_path / "residual_analysis"),
        source_stage="single_harness_residual_repair", prior_candidate_feedback=feedback,
    ))
    assert inputs[0]["case_facts"]["case_id"] == case_id
    assert "answer omitted" in json.dumps(inputs[0]["prior_candidate_feedback"])
    hypotheses_ref = compile_optimization_hypotheses(
        analysis_ref_path=residual_ref, cases=[{"case_id": case_id, "input": case.input}],
        output_path=tmp_path / "hypotheses.yaml",
    )
    hypotheses = load_optimization_hypotheses(hypotheses_ref)
    assert hypotheses[0]["target_case_ids"] == [case_id]
    message = MemberActionPlannerAgent._build_user_message(
        targets=[], role_attribution_report=RoleAttributionReport(),
        mechanism_attribution_report=MechanismAttributionReport(), optimization_hypotheses=hypotheses,
    )
    assert case_id in message
    assert "the final answer is present" in message
