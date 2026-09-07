# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for SWE-bench runtime path handling."""

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
    CaseExecutionResult,
)
from openjiuwen.rsi.harness_rsi.evaluator.judger.script_based import (
    _judge_swebench,
)

from openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime import (
    _bounded_infrastructure_failure_detail,
    _bounded_test_output_excerpt,
    _cache_official_dependency_files,
    _configure_workspace_git,
    _configured_image,
    _detach_container_symlinks,
    _ensure_configured_image,
    _image_coordinates,
    _infrastructure_failure_reason,
    _native_path,
    _official_command,
    _official_python_prefix,
    _read_json,
    _restore_workspace_symlinks,
    _run_official_command_with_retries,
    SWEbenchInfrastructureError,
    run_official_swebench_evaluation,
    swebench_image_name,
)


def test_swebench_judge_uses_patch_captured_in_solver_container(
    tmp_path: Path,
) -> None:
    captured_patch = tmp_path / "container_model.patch"
    captured_patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    execution_result = CaseExecutionResult(
        response="done",
        execution_status="passed",
        workspace_dir=str(tmp_path / "workspace"),
        metadata={"swebench_model_patch_path": str(captured_patch)},
    )

    with (
        patch(
            "openjiuwen.rsi.harness_rsi.evaluator.judger.script_based.collect_model_patch",
            side_effect=AssertionError("host git diff must not run"),
        ),
        patch(
            "openjiuwen.rsi.harness_rsi.evaluator.judger.script_based.run_official_swebench_evaluation",
            return_value={"score": 1.0, "passed": True, "reason": ""},
        ) as evaluate,
    ):
        result = _judge_swebench(
            case={"case_id": "repo__case-1", "swebench": {}},
            execution_result=execution_result,
            output_dir=str(tmp_path / "case"),
        )

    assert result.passed is True
    assert evaluate.call_args.kwargs["model_patch"] == captured_patch.read_text(encoding="utf-8")


def test_swebench_judge_rejects_missing_captured_patch(tmp_path: Path) -> None:
    execution_result = CaseExecutionResult(
        response="done",
        execution_status="passed",
        workspace_dir=str(tmp_path / "workspace"),
        metadata={
            "swebench_model_patch_path": str(tmp_path / "missing.patch"),
        },
    )

    with pytest.raises(SWEbenchInfrastructureError, match="captured SWE-bench patch"):
        _judge_swebench(
            case={"case_id": "repo__case-1", "swebench": {}},
            execution_result=execution_result,
            output_dir=str(tmp_path / "case"),
        )


def test_swebench_judge_records_empty_patch_as_model_failure(tmp_path: Path) -> None:
    captured_patch = tmp_path / "container_model.patch"
    captured_patch.write_text("", encoding="utf-8")
    execution_result = CaseExecutionResult(
        response="done",
        execution_status="passed",
        workspace_dir=str(tmp_path / "workspace"),
        metadata={"swebench_model_patch_path": str(captured_patch)},
    )

    with patch(
        "openjiuwen.rsi.harness_rsi.evaluator.judger.script_based.run_official_swebench_evaluation",
        side_effect=AssertionError("empty patches must not invoke the official runner"),
    ):
        result = _judge_swebench(
            case={"case_id": "repo__case-1", "swebench": {}},
            execution_result=execution_result,
            output_dir=str(tmp_path / "case"),
        )

    assert result.passed is False
    assert result.score == 0.0
    assert result.metadata["empty_patch"] is True
    assert result.reason == "official SWE-bench evaluation received an empty model patch"


def test_pristine_dependency_manifest_is_cached_for_offline_testspec(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "requirements_dev.txt").write_text(
        "pytest\nhypothesis\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._DEPENDENCY_CACHE_ROOT",
        tmp_path / "cache",
    )

    cache = _cache_official_dependency_files(
        {
            "repo": "sqlfluff/sqlfluff",
            "base_commit": "setup-commit",
            "environment_setup_commit": "setup-commit",
        },
        workspace,
    )

    assert cache is not None
    assert (cache / "requirements_dev.txt").read_text(encoding="utf-8") == ("pytest\nhypothesis\n")
    assert json.loads((cache / "cache.json").read_text(encoding="utf-8"))["files"] == ["requirements_dev.txt"]


def test_official_python_prefix_activates_local_dependency_cache() -> None:
    prefix = _official_python_prefix(
        python_path="/venv/bin/python",
        support_dir="/repo/support",
        dependency_cache_root="/cache/sqlfluff/setup",
    )

    assert prefix == [
        "env",
        "PYTHONPATH=/repo/support",
        "ACH_SWEBENCH_DEPENDENCY_CACHE=/cache/sqlfluff/setup",
        "/venv/bin/python",
    ]


def test_official_runtime_retries_transient_network_setup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=("requests.exceptions.ConnectionError: RemoteDisconnected"),
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._run",
        fake_run,
    )

    completed, used_attempts = _run_official_command_with_retries(
        command=["python", "-m", "swebench.harness.run_evaluation"],
        timeout_sec=60,
        verifier_dir=tmp_path,
        run_id="run-1",
        report_path=tmp_path / "report.json",
        test_output_path=tmp_path / "test_output.txt",
        config={
            "infrastructure_retry_attempts": 3,
            "infrastructure_retry_delay_sec": 0,
        },
    )

    assert completed.returncode == 0
    assert used_attempts == 2
    assert attempts == 2


def test_official_runtime_does_not_retry_semantic_or_unknown_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    attempts = 0

    def fake_run(command, **kwargs):
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="FAILED target test",
            stderr="assertion mismatch",
        )

    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._run",
        fake_run,
    )

    completed, used_attempts = _run_official_command_with_retries(
        command=["python", "-m", "swebench.harness.run_evaluation"],
        timeout_sec=60,
        verifier_dir=tmp_path,
        run_id="run-1",
        report_path=tmp_path / "report.json",
        test_output_path=tmp_path / "test_output.txt",
        config={
            "infrastructure_retry_attempts": 3,
            "infrastructure_retry_delay_sec": 0,
        },
    )

    assert completed.returncode == 1
    assert used_attempts == 1
    assert attempts == 1


def test_bounded_test_output_excerpt_preserves_failure_tail() -> None:
    output = "setup\n" + ("x" * 9000) + "\nFAILED test_next\nE AttributeError: _iter"

    excerpt = _bounded_test_output_excerpt(output)

    assert excerpt.startswith("setup\n")
    assert "characters omitted" in excerpt
    assert excerpt.endswith("FAILED test_next\nE AttributeError: _iter")
    assert len(excerpt) < len(output)


def test_infrastructure_failure_detail_exposes_nested_transport_error() -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="official runner failed",
    )

    detail = _bounded_infrastructure_failure_detail(
        completed=completed,
        test_output="requests.ConnectionError: RemoteDisconnected",
    )

    assert "RemoteDisconnected" in detail


def test_read_json_supports_deep_windows_report_path(tmp_path: Path) -> None:
    report = tmp_path
    while len(str(report / "report.json")) <= 280:
        report /= "deep_evaluation_directory"
    report /= "report.json"
    os.makedirs(_native_path(report.parent), exist_ok=True)
    with open(_native_path(report), "w", encoding="utf-8") as file:
        json.dump({"resolved_ids": ["example__case-1"]}, file)

    assert _read_json(report) == {"resolved_ids": ["example__case-1"]}


def test_configure_workspace_git_disables_host_filemode_noise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)

    _configure_workspace_git(repo)

    expected = {
        "core.autocrlf": "false",
        "core.eol": "lf",
        "core.safecrlf": "false",
        "core.filemode": "false",
    }
    for key, value in expected.items():
        completed = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", key],
            check=True,
            capture_output=True,
            text=True,
        )
        assert completed.stdout.strip() == value


def test_custom_image_coordinates_are_shared_by_solver_and_official_command(
    tmp_path: Path,
) -> None:
    config = {
        "namespace": "openjiuwen-swebench",
        "instance_image_tag": "numpy1-v1",
    }
    instance_id = "pvlib__pvlib-python-1072"

    assert _image_coordinates(config) == ("openjiuwen-swebench", "numpy1-v1")
    assert _configured_image(config, instance_id) == swebench_image_name(
        instance_id,
        namespace="openjiuwen-swebench",
        tag="numpy1-v1",
    )

    command = _official_command(
        config={**config, "python_path": "python"},
        dataset_path=tmp_path / "dataset.json",
        predictions_path=tmp_path / "predictions.jsonl",
        instance_id=instance_id,
        run_id="run-1",
        timeout_sec=60,
    )
    assert command[command.index("--namespace") + 1] == "openjiuwen-swebench"
    assert command[command.index("--instance_image_tag") + 1] == "numpy1-v1"


@pytest.mark.parametrize(
    "host,config,env,expected_python,expected_distro",
    [
        ("nt", {}, {}, "python3", "Ubuntu-24.04"),
        (
            "nt",
            {},
            {"SWEBENCH_WSL_PYTHON": "/opt/swe venv/bin/python", "SWEBENCH_WSL_DISTRO": "Verifier"},
            "/opt/swe venv/bin/python",
            "Verifier",
        ),
        (
            "nt",
            {"python_path": "/explicit/python", "wsl_distro": "Explicit"},
            {"SWEBENCH_WSL_PYTHON": "/env/python", "SWEBENCH_WSL_DISTRO": "Env"},
            "/explicit/python",
            "Explicit",
        ),
        ("posix", {}, {"SWEBENCH_WSL_PYTHON": "/wsl/python"}, "python", None),
    ],
)
def test_official_command_respects_host_verifier_environment(
    tmp_path,
    monkeypatch,
    host,
    config,
    env,
    expected_python,
    expected_distro,
) -> None:
    monkeypatch.delenv("SWEBENCH_WSL_PYTHON", raising=False)
    monkeypatch.delenv("SWEBENCH_WSL_DISTRO", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    dataset = tmp_path / "dataset.json"
    predictions = tmp_path / "predictions.jsonl"
    with (
        patch("openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime.os.name", host),
        patch("openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._wsl_path", return_value="/mnt/test"),
    ):
        command = _official_command(
            config=config,
            dataset_path=dataset,
            predictions_path=predictions,
            instance_id="repo__case-1",
            run_id="check",
            timeout_sec=60,
        )
    assert command[command.index("-m") - 1] == expected_python
    if expected_distro:
        assert command[:4] == ["wsl", "-d", expected_distro, "--"]
    else:
        assert command[0] == "env"


def test_setup_commands_derive_stable_non_latest_image_tag() -> None:
    config = {"verifier_setup_commands": ["python -m pip install 'numpy<2'"]}

    namespace, first_tag = _image_coordinates(config)
    _, second_tag = _image_coordinates(config)

    assert namespace == "swebench"
    assert first_tag == second_tag
    assert first_tag.startswith("ach-")


def test_configured_infrastructure_failure_is_not_a_model_zero() -> None:
    instance_id = "pvlib__pvlib-python-1072"
    reason = _infrastructure_failure_reason(
        config={"infrastructure_failure_patterns": ["AttributeError: `np.Inf` was removed in the NumPy 2.0 release"]},
        instance_id=instance_id,
        completed=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        report={"completed_ids": [instance_id], "error_ids": [], "incomplete_ids": []},
        instance_report={instance_id: {"resolved": False}},
        test_output=(
            "ImportError while loading conftest\nAttributeError: `np.Inf` was removed in the NumPy 2.0 release"
        ),
    )

    assert reason.startswith("configured infrastructure failure matched:")


def test_builtin_binary_compatibility_failure_is_not_a_model_zero() -> None:
    instance_id = "pvlib__pvlib-python-1154"

    reason = _infrastructure_failure_reason(
        config={},
        instance_id=instance_id,
        completed=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        report={"completed_ids": [instance_id], "error_ids": [], "incomplete_ids": []},
        instance_report={instance_id: {"resolved": False}},
        test_output=(
            "ImportError while loading conftest\n"
            "ValueError: numpy.dtype size changed, may indicate binary incompatibility"
        ),
    )

    assert reason.startswith("configured infrastructure failure matched:")


def test_builtin_missing_shared_library_is_not_a_model_zero() -> None:
    instance_id = "pyvista__pyvista-4315"

    reason = _infrastructure_failure_reason(
        config={},
        instance_id=instance_id,
        completed=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        report={"completed_ids": [instance_id], "error_ids": [], "incomplete_ids": []},
        instance_report={instance_id: {"resolved": False}},
        test_output="ImportError: libGL.so.1: cannot open shared object file: No such file",
    )

    assert reason.startswith("configured infrastructure failure matched:")


def test_container_symlinks_are_detached_and_restored(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        stdout = '[["test/fixtures/path_c", "."]]' if command[1] == "exec" and "python" in command[3] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._run",
        fake_run,
    )

    links = _detach_container_symlinks("case-container")
    _restore_workspace_symlinks("case-container", links)

    assert links == [("test/fixtures/path_c", ".")]
    assert calls[-1] == [
        "docker",
        "exec",
        "case-container",
        "ln",
        "-s",
        "--",
        ".",
        "/ach-workspace/test/fixtures/path_c",
    ]


def test_image_build_accepts_nonzero_exit_only_when_target_exists(monkeypatch) -> None:
    image = "local/sweb.eval.x86_64.example_1776_case-1:derived"
    inspect_count = 0

    def fake_run(command, **kwargs):
        nonlocal inspect_count
        if command[:3] == ["docker", "image", "inspect"]:
            inspect_count += 1
            return subprocess.CompletedProcess(
                command,
                1 if inspect_count == 1 else 0,
                stdout="",
                stderr="",
            )
        if command[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="registry metadata refresh failed",
            )
        raise AssertionError(command)

    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._run",
        fake_run,
    )

    _ensure_configured_image(
        config={
            "namespace": "local",
            "instance_image_tag": "derived",
            "base_docker_image": "base/image:latest",
            "verifier_setup_commands": ["true"],
        },
        instance_id="example__case-1",
        image=image,
        timeout_sec=60,
    )

    assert inspect_count == 3


def test_empty_patch_without_instance_report_is_a_model_zero() -> None:
    instance_id = "marshmallow-code__marshmallow-1343"

    reason = _infrastructure_failure_reason(
        config={},
        instance_id=instance_id,
        completed=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        report={
            "submitted_ids": [instance_id],
            "empty_patch_ids": [instance_id],
            "error_ids": [],
            "incomplete_ids": [],
        },
        instance_report={},
        test_output="",
    )

    assert reason == ""


def test_empty_model_patch_skips_official_runtime_and_records_model_zero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    instance_id = "sqlfluff__sqlfluff-1517"
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]", encoding="utf-8")

    def fail_if_run(*args, **kwargs):
        raise AssertionError("official runtime must not run for an empty patch")

    monkeypatch.setattr(
        "openjiuwen.rsi.harness_rsi.evaluator.swebench_runtime._run",
        fail_if_run,
    )

    result = run_official_swebench_evaluation(
        case={
            "case_id": instance_id,
            "swebench": {
                "instance_id": instance_id,
                "official_dataset_path": str(dataset_path),
            },
        },
        model_patch="",
        output_dir=tmp_path / "case",
    )

    assert result["passed"] is False
    assert result["score"] == 0.0
    assert result["empty_patch"] is True
    assert result["exit_code"] == 0
    assert result["report"]["empty_patch_ids"] == [instance_id]
    assert Path(result["report_path"]).is_file()


def test_missing_instance_report_for_nonempty_patch_is_infrastructure_failure() -> None:
    instance_id = "marshmallow-code__marshmallow-1343"

    reason = _infrastructure_failure_reason(
        config={},
        instance_id=instance_id,
        completed=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        report={
            "submitted_ids": [instance_id],
            "empty_patch_ids": [],
            "error_ids": [],
            "incomplete_ids": [],
        },
        instance_report={},
        test_output="",
    )

    assert reason == "official per-instance report is missing"
