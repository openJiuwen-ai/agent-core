"""Tests for the official Evo-Bench RSI launcher."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import yaml

from examples.rsi.evobench import launcher, run_one, subset


def _write_model(path: Path, *, base: str, key: str, model: str) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "model_client_config": {"api_base": base, "api_key": key},
                "model_request_config": {"model": model},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_local_model_requires_jiuwen_sections(tmp_path: Path) -> None:
    path = tmp_path / "model.yaml"
    path.write_text("model: nope\n", encoding="utf-8")

    try:
        launcher.load_local_model(path)
    except ValueError as error:
        assert "Jiuwen client/request sections" in str(error)
    else:
        raise AssertionError("invalid Jiuwen model config was accepted")


def test_generated_model_config_never_contains_credentials(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    launcher.write_evobench_model_config(
        path,
        api_base_env="POLICY_BASE",
        api_key_env="POLICY_KEY",
        model="deepseek-v4-flash",
        role="policy",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["api_base_env"] == "POLICY_BASE"
    assert payload["api_key_env"] == "POLICY_KEY"
    assert payload["temperature"] == 1.0
    assert payload["reasoning_effort"] == "max"
    assert "api_base" not in payload
    assert "api_key" not in payload


def test_smoke_suites_cover_all_five_sources(tmp_path: Path) -> None:
    suite_root = tmp_path / "benchmark" / "suites"
    suite_root.mkdir(parents=True)
    for split in ("validation", "evaluation"):
        tasks = [
            {
                "id": task_id,
                "domain": (
                    "search"
                    if task_id.startswith(("bc-", "hle-"))
                    else "general"
                    if task_id.startswith("claw-")
                    else "office"
                ),
            }
            for task_id in launcher.SMOKE_TASK_IDS[split]
        ]
        (suite_root / f"evobench_{split}.json").write_text(
            json.dumps({split: tasks, "assets_dir": "../assets/gdpval"}),
            encoding="utf-8",
        )

    validation, evaluation = launcher.write_smoke_suites(tmp_path)

    for path, split in ((validation, "validation"), (evaluation, "evaluation")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        ids = [task["id"] for task in payload[split]]
        assert ids == launcher.SMOKE_TASK_IDS[split]
        assert {task["domain"] for task in payload[split]} == {
            "search",
            "office",
            "general",
        }


def test_build_evolve_command_uses_official_protocol(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    prepared = launcher.PreparedRun(
        evobench_root=root,
        output_root=tmp_path / "out",
        policy_config=tmp_path / "policy.json",
        evolver_config=tmp_path / "evolver.json",
        judge_config=tmp_path / "judge.json",
        validation_suite=tmp_path / "validation.json",
        evaluation_suite=tmp_path / "evaluation.json",
        environment={},
    )
    args = argparse.Namespace(
        command="evolve",
        run_name="paper_run",
        max_iterations=None,
        max_steps=None,
        rollout_concurrency=20,
        evaluation_concurrency=20,
        trials_by_domain="general=3",
        sandbox_ttl_minutes=2880,
        sandbox_cpu=1,
        sandbox_memory=4,
        sandbox_disk=4,
        eval_sandbox_cpu=8,
        eval_sandbox_memory=64,
        eval_sandbox_disk=64,
    )

    command = launcher.build_command(args, prepared)

    assert "run-evolve" in command
    assert command[command.index("--max-iterations") + 1] == "20"
    assert command[command.index("--max-steps") + 1] == "1000"
    assert command[command.index("--trials-by-domain") + 1] == "general=3"
    assert "--evaluation-suite" in command


def test_smoke_keeps_two_iterations_for_baseline_then_candidate(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    prepared = launcher.PreparedRun(
        evobench_root=root,
        output_root=tmp_path / "out",
        policy_config=tmp_path / "policy.json",
        evolver_config=tmp_path / "evolver.json",
        judge_config=tmp_path / "judge.json",
        validation_suite=tmp_path / "validation.json",
        evaluation_suite=tmp_path / "evaluation.json",
        environment={},
    )
    args = argparse.Namespace(
        command="smoke",
        run_name="smoke",
        max_iterations=None,
        max_steps=None,
        rollout_concurrency=1,
        evaluation_concurrency=1,
        trials_by_domain="general=3",
        sandbox_ttl_minutes=60,
        sandbox_cpu=1,
        sandbox_memory=4,
        sandbox_disk=4,
        eval_sandbox_cpu=2,
        eval_sandbox_memory=8,
        eval_sandbox_disk=8,
    )

    command = launcher.build_command(args, prepared)

    assert command[command.index("--max-iterations") + 1] == "2"


def test_prepare_reuses_local_model_credentials_only_in_environment(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "Evo-Bench"
    suite_root = root / "benchmark" / "suites"
    suite_root.mkdir(parents=True)
    validation = [{"id": f"v{i}"} for i in range(160)]
    evaluation = [{"id": f"e{i}"} for i in range(448)]
    (suite_root / "evobench_validation.json").write_text(json.dumps({"validation": validation}), encoding="utf-8")
    (suite_root / "evobench_evaluation.json").write_text(json.dumps({"evaluation": evaluation}), encoding="utf-8")
    (root / "evobench").mkdir()
    (root / "evobench" / "cli.py").touch()
    policy = _write_model(tmp_path / "policy.yaml", base="https://policy.test/v1", key="policy-secret", model="ds")
    evolver = _write_model(tmp_path / "evolver.yaml", base="https://evolver.test/v1", key="evolver-secret", model="glm")
    monkeypatch.setattr(launcher, "_wsl_runtime_credentials", lambda: {})
    args = launcher._parse_args(
        [
            "prepare",
            "--evobench-root",
            str(root),
            "--output-dir",
            str(tmp_path / "out"),
            "--policy-model-config",
            str(policy),
            "--evolver-model-config",
            str(evolver),
        ]
    )

    prepared = launcher.prepare_run(args)

    assert prepared.environment["RSI_EVOBENCH_POLICY_API_KEY"] == "policy-secret"
    assert prepared.environment["RSI_EVOBENCH_EVOLVER_API_KEY"] == "evolver-secret"
    assert "policy-secret" not in prepared.policy_config.read_text(encoding="utf-8")
    assert "evolver-secret" not in prepared.evolver_config.read_text(encoding="utf-8")


def test_single_task_suite_selects_one_task_and_uses_wsl_assets(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    suite_root = root / "benchmark" / "suites"
    suite_root.mkdir(parents=True)
    (suite_root / "evobench_validation.json").write_text(
        json.dumps(
            {
                "validation": [{"id": "wanted"}, {"id": "other"}],
                "assets_dir": "../assets/gdpval",
            }
        ),
        encoding="utf-8",
    )

    path = run_one.write_single_task_suite(root, task_id="wanted", output_dir=tmp_path / "out")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [task["id"] for task in payload["validation"]] == ["wanted"]
    assert payload["assets_dir"].endswith("/benchmark/assets/gdpval")
    assert payload["assets_dir"].startswith("/mnt/")


def test_wsl_environment_forwards_secrets_without_argv(monkeypatch) -> None:
    monkeypatch.setenv("WSLENV", "EXISTING/u:POLICY_KEY/old")

    environment = run_one._wsl_subprocess_environment({"POLICY_KEY": "secret", "JUDGE_KEY": "judge-secret"})

    assert environment["POLICY_KEY"] == "secret"
    assert environment["JUDGE_KEY"] == "judge-secret"
    assert environment["WSLENV"] == "EXISTING/u:POLICY_KEY:JUDGE_KEY"


def test_single_task_command_uses_official_seed_harness_and_local_isolation(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    command = run_one.build_wsl_single_task_command(
        root=root,
        suite_path=tmp_path / "suite.json",
        output_dir=tmp_path / "evaluation",
        policy_config=tmp_path / "policy.json",
        judge_config=tmp_path / "judge.json",
    )

    assert "EVOBENCH_EXECUTION_MODE=local" in command
    assert "run-validation-eval" in command
    assert command[command.index("--trials") + 1] == "1"
    assert command[command.index("--policy-harness") + 1].endswith("/policy_harness_seed")


def test_validation20_sampling_is_deterministic_and_local_only() -> None:
    tasks = [{"id": f"claw-{number}", "domain": "general"} for number in range(32)]
    tasks.extend({"id": f"{prefix}-{number}", "domain": "search"} for prefix in ("bc", "hle") for number in range(32))
    tasks.extend(
        {"id": f"{prefix}-{number}", "domain": "office"} for prefix in ("apex", "gdpval") for number in range(32)
    )

    first = subset.select_validation_subset(tasks, seed=42)
    second = subset.select_validation_subset(tasks, seed=42)

    assert [task["id"] for task in first] == [task["id"] for task in second]
    assert len(first) == 20
    assert all(task["id"].startswith("claw-") for task in first)


def test_validation40_uses_all_claw_and_eight_local_office_tasks() -> None:
    tasks = [{"id": f"claw-{number}", "domain": "general"} for number in range(32)]
    tasks.extend({"id": f"{prefix}-{number}", "domain": "search"} for prefix in ("bc", "hle") for number in range(32))
    tasks.extend(
        {"id": f"{prefix}-{number}", "domain": "office"} for prefix in ("apex", "gdpval") for number in range(32)
    )

    selected = subset.select_validation_subset(tasks, seed=42, task_count=40)
    prefixes = Counter(task["id"].split("-", 1)[0] for task in selected)

    assert len(selected) == 40
    assert prefixes == Counter({"claw": 32, "gdpval": 8})
    assert all(task["domain"] in {"general", "office"} for task in selected)


def test_read_env_file_does_not_override_process_environment(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "evobench.env"
    path.write_text(
        "# credentials\nE2B_API_KEY=file-key\nSERPER_API_KEY='search-key'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("E2B_API_KEY", "process-key")

    values = subset.read_env_file(path)

    assert "E2B_API_KEY" not in values
    assert values["SERPER_API_KEY"] == "search-key"


def test_read_env_file_replaces_empty_process_value(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "evobench.env"
    path.write_text("E2B_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("E2B_API_KEY", "")

    assert subset.read_env_file(path)["E2B_API_KEY"] == "file-key"


def test_validation20_command_uses_official_claw_trials(tmp_path: Path) -> None:
    root = tmp_path / "Evo-Bench"
    prepared = {
        "python": root / ".claw-venv" / "bin" / "python",
        "root": root,
        "suite_path": tmp_path / "suite.json",
        "policy_config": tmp_path / "policy.json",
        "judge_config": tmp_path / "judge.json",
        "evaluation_dir": tmp_path / "evaluation",
        "environment": {"EVOBENCH_CLAW_REPO": str(root / "external" / "claw-eval")},
    }

    command = subset.build_validation_command(prepared, concurrency=5)

    assert command[0] == "wsl.exe"
    assert "EVOBENCH_EXECUTION_MODE=local" in command
    assert "EVOBENCH_RESUME_COMPLETED_LOCAL_TRIALS=1" in command
    assert command[command.index("--rollout-concurrency") + 1] == "5"
    assert command[command.index("--trials") + 1] == "1"
    assert command[command.index("--trials-by-domain") + 1] == "general=3"
    assert all("E2B_API_KEY" not in item and "SERPER_API_KEY" not in item for item in command)
