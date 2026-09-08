# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset adaptation must not change execution or scoring semantics."""

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openjiuwen.rsi import load_cases
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.data_loader.case_files import (
    copy_dataset_files,
    copy_public_assets,
    file_fingerprint,
    resolve_dataset_file,
    task_input,
    validate_case_fields,
)
from openjiuwen.rsi.harness_rsi.evaluator import case_backend
from openjiuwen.rsi.harness_rsi.evaluator.case_backend import CaseExecutionResult
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger import ScriptBasedJudger
from openjiuwen.rsi.harness_rsi.evaluator.judger.exact_match import ExactMatchJudger
from openjiuwen.rsi.harness_rsi.evaluator.team_evaluator import TeamEvaluator, _evaluation_input_fingerprint
from openjiuwen.rsi.harness_rsi.single_harness.source_evidence import evaluation_context


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _packet(root):
    _write_json(root / "assets" / "data.json", {"amount": 150})
    _write_json(root / "references" / "private.json", {"answer": "PRIVATE_REFERENCE"})
    return _write_json(root / "cases.json", {"cases": [{
        "case_id": "one", "input": "Read assets/data.json.",
        "assets": ["assets/data.json"],
        "reference": {"files": ["references/private.json"]},
    }]})


@pytest.mark.parametrize("reference", [None, {}, {"rubric": ["Verify all facts"]}, {"files": ["private.txt"]}])
@pytest.mark.asyncio
async def test_unsupported_grading_fails_before_task_calls(tmp_path, reference, monkeypatch):
    evaluator = TeamEvaluator(EvaluatorConfig(backend="single_harness"))
    backend = evaluator.case_runner.backend
    execute = AsyncMock()
    monkeypatch.setattr(type(backend), "execute", execute)
    case = {"case_id": "one", "input": "Produce a report"}
    if reference is not None:
        case["reference"] = reference
    with pytest.raises(EvaluationInfrastructureError):
        await evaluator.evaluate_batch([case], "", "", str(tmp_path / "eval"))
    execute.assert_not_awaited()
    assert not (tmp_path / "eval" / "summary.json").exists()


@pytest.mark.parametrize("value", [None, "", "   ", [], 12, {}])
def test_invalid_input_is_rejected(value):
    with pytest.raises(ValueError):
        validate_case_fields({"case_id": "one", "input": value})


def test_missing_input_never_exposes_private_case():
    with pytest.raises(ValueError, match="input is required"):
        case_backend._case_inputs({"case_id": "one", "reference": {"answer": "secret"}})
    assert task_input({"question": "legacy task", "reference": {"answer": "secret"}}) == "legacy task"


@pytest.mark.parametrize("value", [
    "", ".", "./a", "../private.txt", "/tmp/a", "D:/data/a", "C:a", "a:stream", "a/../b", "a//b", "a/",
    "a\\..\\b", "\\\\server\\share\\a",
])
def test_paths_cannot_escape_packet(tmp_path, value):
    with pytest.raises(ValueError, match="must be a relative path"):
        resolve_dataset_file(tmp_path, value)


@pytest.mark.parametrize("value", ["assets/data.json", "assets\\data.json"])
def test_relative_dataset_paths_remain_valid(tmp_path, value):
    _packet(tmp_path)
    assert resolve_dataset_file(tmp_path, value) == (tmp_path / "assets/data.json").resolve()


@pytest.mark.parametrize("content, error_type", [(b"{", json.JSONDecodeError), (b"\xff", UnicodeDecodeError)])
def test_invalid_reference_json_preserves_cause(tmp_path, content, error_type):
    source = _packet(tmp_path)
    (tmp_path / "references/private.json").write_bytes(content)
    with pytest.raises(ValueError, match="invalid reference JSON: private.json") as error:
        load_cases([str(source)])
    assert isinstance(error.value.__cause__, error_type)


def test_private_files_cannot_be_public_even_in_other_cases(tmp_path):
    source = _packet(tmp_path)
    payload = json.loads(source.read_text())
    payload["cases"].append({"case_id": "two", "input": "Other", "assets": ["references/private.json"]})
    _write_json(source, payload)
    with pytest.raises(ValueError, match="private reference"):
        load_cases([str(source)])


def test_missing_asset_and_reference_file_are_rejected(tmp_path):
    for field in ("assets", "reference"):
        case = {"case_id": "one", "input": "Task"}
        case[field] = ["missing.txt"] if field == "assets" else {"files": ["missing.txt"]}
        with pytest.raises(ValueError, match="not found"):
            load_cases([str(_write_json(tmp_path / "cases.json", {"cases": [case]}))])


def test_snapshot_and_candidate_workspaces_are_isolated(tmp_path):
    source = _packet(tmp_path / "original")
    target = tmp_path / "snapshot" / "cases.json"
    target.parent.mkdir()
    shutil.copy2(source, target)
    cases = load_cases([str(source)])
    hashes = copy_dataset_files(cases, source, target)
    assert set(hashes) == {"assets/data.json", "references/private.json"}
    frozen = load_cases([str(target)])[0]
    for name in ("source-run", "candidate-run"):
        workspace = tmp_path / name
        copied = copy_public_assets(frozen, workspace)
        assert [item[0] for item in copied] == ["assets/data.json"]
        assert json.loads((workspace / "assets/data.json").read_text()) == {"amount": 150}
        assert not (workspace / "references").exists()
        (workspace / "assets/data.json").write_text("edited by agent")
    assert json.loads((source.parent / "assets/data.json").read_text()) == {"amount": 150}
    assert json.loads((target.parent / "assets/data.json").read_text()) == {"amount": 150}
    assert case_backend._case_inputs(frozen) == "Read assets/data.json."


def test_reference_cannot_overwrite_dataset_snapshot(tmp_path):
    source = _write_json(tmp_path / "source.json", {"cases": [{
        "case_id": "one", "input": "Task", "reference": {"files": ["cases.json"]},
    }]})
    _write_json(tmp_path / "cases.json", {"answer": 1})
    with pytest.raises(ValueError, match="collides"):
        copy_dataset_files(load_cases([str(source)]), source, tmp_path / "snapshot" / "cases.json")


def test_asset_content_invalidates_both_evaluation_caches(tmp_path):
    source = _packet(tmp_path)
    case = load_cases([str(source)])[0]
    kwargs = {"cases": [case], "team_skill_ref_path": "", "harness_refs_path": ""}
    before = _evaluation_input_fingerprint(**kwargs)
    context = evaluation_context(harness_refs_path="", evaluator_config={}, cases=[case])
    fingerprint = file_fingerprint(case)
    (tmp_path / "assets/data.json").write_text("changed")
    assert _evaluation_input_fingerprint(**kwargs) != before
    assert file_fingerprint(case) != fingerprint
    assert evaluation_context(harness_refs_path="", evaluator_config={}, cases=[case])["cases"] != context["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response, score", [("150", 1.0), ("wrong", 0.0)])
async def test_legacy_and_canonical_answers_have_identical_scores(response, score):
    judger = ScriptBasedJudger()
    legacy = {"case_id": "one", "query": "Task", "expected_output": "150"}
    canonical = {"case_id": "one", "input": "Task", "assets": [], "reference": {"answer": "150"}}
    results = []
    for case in (legacy, canonical):
        judger.validate_case(case)
        results.append(await judger.judge(case=case, execution_result=CaseExecutionResult(response, "passed")))
    assert results[0] == results[1]
    assert results[0].score == score


def _swe_packet(root):
    record = {"instance_id": "repo-1", "repo": "org/repo", "base_commit": "abc", "version": "1",
              "test_patch": "PRIVATE_TEST_PATCH", "FAIL_TO_PASS": "[\"test_fix\"]", "PASS_TO_PASS": "[]"}
    official = _write_json(root / "references/official.json", [record])
    config = {"instance_id": "repo-1", "repo": "org/repo", "base_commit": "abc", "version": "1",
              "test_patch": "PRIVATE_TEST_PATCH", "official_dataset_path": "references/official.json",
              "verifier_setup_commands": ["python -m pip install pytest==4.6.11"],
              "infrastructure_failure_patterns": ["environment broken"], "instance_image_tag": "local-repaired"}
    _write_json(root / "references/verifier.json", {"adapter": "swebench_official", "config": config})
    source = _write_json(root / "cases.json", {"cases": [{
        "case_id": "repo-1", "input": "Fix a regression", "assets": [],
        "reference": {"files": ["references/verifier.json", "references/official.json"]},
    }]})
    return source, {**config, "official_dataset_path": str(official.resolve())}


def test_swe_reference_restores_existing_runtime_config_without_leaking_it(tmp_path):
    source, config = _swe_packet(tmp_path)
    case = load_cases([str(source)])[0]
    assert case["swebench"] == config
    legacy = {"case_id": "repo-1", "input": "Fix a regression", "swebench": config}
    assert case_backend._case_inputs(case) == case_backend._case_inputs(legacy)
    assert "PRIVATE" not in case_backend._case_inputs(case)
    ScriptBasedJudger().validate_case(case)


@pytest.mark.parametrize("mutate", ["unknown", "wrong_id", "undeclared", "conflict"])
def test_swe_reference_fails_closed_on_conflicting_config(tmp_path, mutate):
    source, _ = _swe_packet(tmp_path)
    manifest = tmp_path / "references/verifier.json"
    payload = json.loads(manifest.read_text())
    if mutate == "unknown":
        payload["adapter"] = "not-installed"
    elif mutate == "wrong_id":
        payload["config"]["instance_id"] = "other"
    elif mutate == "undeclared":
        payload["config"]["official_dataset_path"] = "unlisted.json"
        _write_json(tmp_path / "unlisted.json", [])
    else:
        payload["config"]["base_commit"] = "wrong"
    _write_json(manifest, payload)
    with pytest.raises(ValueError):
        load_cases([str(source)])


def test_container_receives_only_public_assets(tmp_path, monkeypatch):
    source = _packet(tmp_path / "dataset")
    case = load_cases([str(source)])[0]
    calls = []
    monkeypatch.setattr(case_backend, "run_docker", lambda command, **_kwargs: calls.append(command))
    case_backend._stage_case_assets(case, tmp_path / "workspace", "solver")
    assert len(calls) == 2
    assert calls[-1][-1] == "solver:/testbed/assets/data.json"
    assert "references" not in str(calls)


def test_attachment_setup_error_is_infrastructure_failure(tmp_path, monkeypatch):
    source = _packet(tmp_path / "dataset")

    def docker_unavailable(*_args, **_kwargs):
        raise RuntimeError("container unavailable")

    monkeypatch.setattr(case_backend, "run_docker", docker_unavailable)
    with pytest.raises(EvaluationInfrastructureError, match="assets could not be prepared"):
        case_backend._stage_case_assets(load_cases([str(source)])[0], tmp_path / "workspace", "solver")


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [0.0, 1.0])
async def test_canonical_swe_uses_the_same_official_verifier(tmp_path, monkeypatch, score):
    from openjiuwen.rsi.harness_rsi.evaluator.judger import script_based

    source, config = _swe_packet(tmp_path / "dataset")
    canonical = load_cases([str(source)])[0]
    legacy = {"case_id": "repo-1", "input": "Fix a regression", "swebench": config}
    observed = []

    def official(**kwargs):
        observed.append((kwargs["case"]["swebench"], kwargs["model_patch"]))
        return {"score": score, "passed": bool(score), "reason": "official result"}

    monkeypatch.setattr(script_based, "collect_model_patch", lambda *_args, **_kwargs: "candidate patch")
    monkeypatch.setattr(script_based, "run_official_swebench_evaluation", official)
    results = []
    for index, case in enumerate((legacy, canonical)):
        results.append(await ScriptBasedJudger().judge(
            case=case, execution_result=CaseExecutionResult("done", "passed", workspace_dir=str(tmp_path)),
            output_dir=str(tmp_path / f"eval-{index}"),
        ))
    assert observed == [(config, "candidate patch")] * 2
    assert [result.score for result in results] == [score, score]
    assert all(result.method == "swebench_official" for result in results)


def test_canonical_swe_packet_survives_relocation(tmp_path):
    result, config = _swe_packet(tmp_path / "original")
    raw = json.loads(result.read_text())
    assert set(raw["cases"][0]) == {"case_id", "input", "assets", "reference"}
    target = tmp_path / "relocated" / "cases.json"
    target.parent.mkdir()
    shutil.copy2(result, target)
    copy_dataset_files(load_cases([str(result)]), result, target)
    relocated = load_cases([str(target)])[0]["swebench"]
    relocated_records = json.loads(Path(relocated["official_dataset_path"]).read_text())
    assert relocated_records == json.loads(Path(config["official_dataset_path"]).read_text())
    assert Path(relocated["official_dataset_path"]).is_relative_to(target.parent)
    assert {key: value for key, value in relocated.items() if key != "official_dataset_path"} == {
        key: value for key, value in config.items() if key != "official_dataset_path"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("judger_type", [ScriptBasedJudger, ExactMatchJudger])
@pytest.mark.parametrize("response, expected, score", [
    ({"output": "150", "result_type": "answer"}, "150", 1.0),
    ({"output": "149", "result_type": "answer"}, "150", 0.0),
    ({"output": "150\n", "result_type": "answer"}, "150", 0.0),
    ({"output": "150", "result_type": "error"}, "150", 0.0),
    ({"output": 150, "result_type": "answer"}, "150", 0.0),
    ({"result_type": "answer"}, "150", 0.0),
    ({"output": "150"}, "150", 0.0),
    (None, "150", 0.0),
    (150, 150, 1.0),
    ({"output": "150"}, {"output": "150"}, 1.0),
])
async def test_native_answer_envelope_keeps_exact_scoring(judger_type, response, expected, score):
    result = await judger_type().judge(
        case={"case_id": "one", "input": "Task", "reference": {"answer": expected}},
        execution_result=CaseExecutionResult(response, "passed"),
    )
    assert result.score == score


def test_declared_files_override_untrusted_case_path(tmp_path):
    from openjiuwen.rsi.harness_rsi.config import DataLoaderConfig
    from openjiuwen.rsi.harness_rsi.data_loader import DataLoader

    source = _packet(tmp_path / "dataset")
    payload = json.loads(source.read_text())
    payload["cases"][0]["case_path"] = str(tmp_path / "unrelated" / "cases.json")
    _write_json(source, payload)
    case = next(DataLoader(DataLoaderConfig()).load_files([str(source)]))[0]
    assert Path(case["case_path"]) == source
