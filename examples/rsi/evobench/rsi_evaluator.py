# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Adapt official local Evo-Bench no-key evaluations to RSI artifacts.

The official runner remains the protocol and scoring owner.  This module only
selects the requested validation tasks, invokes ``run-validation-eval``, and
materializes its result and rollout traces in the format consumed by RSI's
evaluation result analyzer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

import yaml

from examples.rsi.evobench.launcher import (
    DEFAULT_E2B_TEMPLATE,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_POLICY_CONFIG,
    _wsl_runtime_credentials,
    load_local_model,
    resolve_evobench_root,
    write_evobench_model_config,
)
from examples.rsi.evobench.run_one import _to_wsl, _wsl_subprocess_environment
from examples.rsi.evobench.subset import DEFAULT_ENV_FILE, read_env_file
from openjiuwen.rsi.evaluation_result_analyzer.evidence_compactor import (
    public_task_contract_snapshot,
)


DEFAULT_JUDGE_SOURCE = Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml")
EVALUATION_METHOD = "evobench-claw-official"
GENERAL_TRIALS = 3
OFFICE_TRIALS = 1
_LOCAL_DOMAIN_TRIALS = {"general": GENERAL_TRIALS, "office": OFFICE_TRIALS}
_CLAW_DIMENSIONS = ("completion", "robustness", "communication", "safety")
_CLAW_REASON_PATTERN = re.compile(
    r"^claw_grader:\s*C=(?P<completion>-?\d+(?:\.\d+)?)\s+"
    r"R=(?P<robustness>-?\d+(?:\.\d+)?)\s+"
    r"M=(?P<communication>-?\d+(?:\.\d+)?)\s+"
    r"S=(?P<safety>-?\d+(?:\.\d+)?)\s*->\s*-?\d+(?:\.\d+)?\s*$"
)


@dataclass(frozen=True, slots=True)
class EvoBenchRSIEvaluatorConfig:
    """Runtime configuration for :class:`EvoBenchRSIEvaluator`."""

    evobench_root: str = ""
    policy_model_config: str = str(DEFAULT_POLICY_CONFIG)
    judge_model_config: str = str(DEFAULT_JUDGE_SOURCE)
    judge_model: str = DEFAULT_JUDGE_MODEL
    rollout_concurrency: int = 5
    existing_official_result: str | None = None
    execution_mode: str = "local"
    env_file: str = str(DEFAULT_ENV_FILE)
    e2b_template: str = DEFAULT_E2B_TEMPLATE
    apex_template: str = "evobench-apex-spec"


class EvoBenchRSIEvaluator:
    """RSI evaluator backed by the official Evo-Bench local Claw protocol."""

    def __init__(self, config: EvoBenchRSIEvaluatorConfig | None = None) -> None:
        self.config = config or EvoBenchRSIEvaluatorConfig()

    async def evaluate_batch(  # pylint: disable=huawei-too-many-arguments
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        dataset: Any = None,
    ) -> str:
        """Evaluate one RSI batch and return its ``eval_ref.yaml`` path."""
        return await evaluate_batch(
            cases,
            team_skill_ref_path,
            harness_refs_path,
            output_dir,
            dataset,
            existing_official_result=self.config.existing_official_result,
            evobench_root=self.config.evobench_root,
            policy_model_config=self.config.policy_model_config,
            judge_model_config=self.config.judge_model_config,
            judge_model=self.config.judge_model,
            rollout_concurrency=self.config.rollout_concurrency,
            execution_mode=self.config.execution_mode,
            env_file=self.config.env_file,
            e2b_template=self.config.e2b_template,
            apex_template=self.config.apex_template,
        )


async def evaluate_batch(  # pylint: disable=huawei-too-many-arguments
    cases: list[dict[str, Any]],
    team_skill_ref_path: str,
    harness_refs_path: str,
    output_dir: str,
    dataset: Any = None,
    *,
    existing_official_result: str | None = None,
    evobench_root: str = "",
    policy_model_config: str = str(DEFAULT_POLICY_CONFIG),
    judge_model_config: str = str(DEFAULT_JUDGE_SOURCE),
    judge_model: str = DEFAULT_JUDGE_MODEL,
    rollout_concurrency: int = 5,
    execution_mode: str = "local",
    env_file: str = str(DEFAULT_ENV_FILE),
    e2b_template: str = DEFAULT_E2B_TEMPLATE,
    apex_template: str = "evobench-apex-spec",
) -> str:
    """Run official Evo-Bench and materialize Analyzer-compatible artifacts.

    ``existing_official_result`` is deliberately restricted to the official H0
    ``policy_harness_seed``.  Candidate harnesses always execute a fresh
    official evaluation, preventing cached H0 evidence from being attributed to
    a proposed harness.
    """
    if not cases:
        raise ValueError("Evo-Bench evaluation requires at least one case")
    execution_mode = str(execution_mode).strip().lower()
    if execution_mode not in {"local", "e2b"}:
        raise ValueError(f"unsupported Evo-Bench execution mode: {execution_mode}")

    root = resolve_evobench_root(evobench_root)
    harness_path = _load_policy_harness(harness_refs_path)
    seed_path = (root / "policy_harness_seed").resolve()
    configured_reuse_path = _official_result_path(existing_official_result)
    # A single evaluator instance is reused by the RSI control loop.  The
    # frozen H0 result is a seed-only cache; every candidate must execute the
    # official protocol instead of failing merely because the cache is set.
    reuse_path = configured_reuse_path if _same_path(harness_path, seed_path) else None

    eval_dir = Path(output_dir).expanduser().resolve()
    eval_dir.mkdir(parents=True, exist_ok=True)
    official_root = eval_dir / "official"
    suite_path, official_tasks = _write_suite(
        root, cases=cases, output_dir=official_root, execution_mode=execution_mode
    )

    if reuse_path is None:
        # The official local runner currently derives task workspaces from the
        # output path.  Keep this directory compact on Windows/WSL so model
        # shell commands do not hit platform path-length limits.
        official_eval_dir = _short_official_eval_dir(eval_dir)
        shutil.rmtree(official_eval_dir, ignore_errors=True)
        official_eval_dir.mkdir(parents=True, exist_ok=True)
        prepared = _prepare_runtime(
            root=root,
            output_dir=official_root,
            policy_model_config=policy_model_config,
            judge_model_config=judge_model_config,
            judge_model=judge_model,
            execution_mode=execution_mode,
            env_file=env_file,
            e2b_template=e2b_template,
            apex_template=apex_template,
        )
        command = _build_command(
            root=root,
            suite_path=suite_path,
            harness_path=harness_path,
            official_eval_dir=official_eval_dir,
            policy_config=prepared["policy_config"],
            judge_config=prepared["judge_config"],
            rollout_concurrency=rollout_concurrency,
            execution_mode=execution_mode,
        )
        completed = await asyncio.to_thread(
            _run_wsl_command if execution_mode == "local" else _run_host_command,
            command,
            root,
            prepared["environment"],
        )
        if completed.returncode:
            raise RuntimeError(f"official Evo-Bench run-validation-eval failed with exit code {completed.returncode}")
        result_path = official_eval_dir / "result.json"
    else:
        result_path = reuse_path
        official_eval_dir = result_path.parent

    official_result = _read_mapping(result_path, label="official Evo-Bench result")
    _validate_result_harness(official_result, expected_path=harness_path)

    selected_results = _select_and_validate_results(
        official_result,
        case_ids=[str(task["id"]) for task in official_tasks],
        official_eval_dir=official_eval_dir,
    )
    return _materialize(
        eval_dir=eval_dir,
        cases=cases,
        official_tasks=official_tasks,
        official_results=selected_results,
        official_result_path=result_path,
        official_eval_dir=official_eval_dir,
        team_skill_ref_path=team_skill_ref_path,
        harness_refs_path=harness_refs_path,
        dataset=dataset,
        reused_official_result=reuse_path is not None,
    )


def _load_policy_harness(harness_refs_path: str) -> Path:
    refs_path = Path(harness_refs_path).expanduser().resolve()
    payload = yaml.safe_load(refs_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"harness refs must be a mapping: {refs_path}")
    refs = payload.get("harness_refs", payload)
    if not isinstance(refs, dict):
        raise ValueError(f"harness_refs must be a mapping: {refs_path}")
    raw_path = refs.get("policy_harness")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"harness refs must contain harness_refs.policy_harness: {refs_path}")
    harness_path = Path(raw_path).expanduser()
    if not harness_path.is_absolute():
        harness_path = refs_path.parent / harness_path
    harness_path = harness_path.resolve()
    if not harness_path.is_dir():
        raise ValueError(f"policy_harness directory not found: {harness_path}")
    return harness_path


def _official_result_path(value: str | None) -> Path | None:
    if not str(value or "").strip():
        return None
    path = Path(str(value)).expanduser().resolve()
    if path.is_dir():
        path = path / "result.json"
    if not path.is_file():
        raise ValueError(f"existing official Evo-Bench result not found: {path}")
    return path


def _short_official_eval_dir(eval_dir: Path) -> Path:
    """Keep E2B/APEX host workspaces below Windows' extended-path boundary."""
    digest = hashlib.sha256(str(eval_dir).casefold().encode("utf-8")).hexdigest()[:16]
    configured_root = os.environ.get("RSI_EVOBENCH_SCRATCH_ROOT", "").strip()
    scratch_root = Path(configured_root).expanduser() if configured_root else Path(eval_dir.anchor) / "evor"
    return (scratch_root / digest).resolve()


def _write_suite(
    root: Path,
    *,
    cases: Sequence[Mapping[str, Any]],
    output_dir: Path,
    execution_mode: str = "local",
) -> tuple[Path, list[dict[str, Any]]]:
    source_path = root / "benchmark" / "suites" / "evobench_validation.json"
    source = _read_mapping(source_path, label="official Evo-Bench validation suite")
    source_tasks = source.get("validation")
    if not isinstance(source_tasks, list):
        raise ValueError(f"official validation suite has no validation task list: {source_path}")
    task_by_id = {
        str(task.get("id")): task for task in source_tasks if isinstance(task, dict) and str(task.get("id", "")).strip()
    }

    case_ids: list[str] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, Mapping):
            raise ValueError(f"Evo-Bench case #{index} must be a mapping")
        case_id = str(case.get("task_id") or case.get("case_id") or case.get("id") or "").strip()
        if not case_id:
            raise ValueError(f"Evo-Bench case #{index} has no case_id/task_id")
        case_ids.append(case_id)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Evo-Bench batch contains duplicate case ids")

    missing = [case_id for case_id in case_ids if case_id not in task_by_id]
    if missing:
        raise ValueError(f"official Evo-Bench validation tasks not found: {missing}")
    selected = [dict(task_by_id[case_id]) for case_id in case_ids]
    unsupported = [
        str(task["id"]) for task in selected if str(task.get("domain", "")).lower() not in _LOCAL_DOMAIN_TRIALS
    ]
    if unsupported:
        raise ValueError(f"Evo-Bench local no-key evaluator does not accept these tasks: {unsupported}")

    payload: dict[str, Any] = {
        "name": "rsi_evobench_batch",
        "description": "RSI batch evaluated with the official Evo-Bench protocol.",
        "validation": selected,
    }
    if source.get("assets_dir"):
        assets_path = Path(str(source["assets_dir"]))
        if not assets_path.is_absolute():
            assets_path = source_path.parent / assets_path
        payload["assets_dir"] = _to_wsl(assets_path) if execution_mode == "local" else str(assets_path.resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_path = output_dir / "suite.json"
    _write_json(suite_path, payload)
    return suite_path, selected


def _prepare_runtime(  # pylint: disable=huawei-too-many-arguments
    *,
    root: Path,
    output_dir: Path,
    policy_model_config: str,
    judge_model_config: str,
    judge_model: str,
    execution_mode: str = "local",
    env_file: str = str(DEFAULT_ENV_FILE),
    e2b_template: str = DEFAULT_E2B_TEMPLATE,
    apex_template: str = "evobench-apex-spec",
) -> dict[str, Any]:
    policy = load_local_model(Path(policy_model_config))
    judge = load_local_model(Path(judge_model_config))
    config_dir = output_dir / "configs"
    policy_config = config_dir / "policy.json"
    judge_config = config_dir / "judge.json"
    write_evobench_model_config(
        policy_config,
        api_base_env="RSI_EVOBENCH_POLICY_API_BASE",
        api_key_env="RSI_EVOBENCH_POLICY_API_KEY",
        model=policy.model,
        role="policy",
    )
    write_evobench_model_config(
        judge_config,
        api_base_env="RSI_EVOBENCH_JUDGE_API_BASE",
        api_key_env="RSI_EVOBENCH_JUDGE_API_KEY",
        model=judge_model,
        role="judge",
    )
    credentials = {
        "RSI_EVOBENCH_POLICY_API_BASE": policy.api_base,
        "RSI_EVOBENCH_POLICY_API_KEY": policy.api_key,
        "RSI_EVOBENCH_JUDGE_API_BASE": judge.api_base,
        "RSI_EVOBENCH_JUDGE_API_KEY": judge.api_key,
    }
    if execution_mode == "e2b":
        environment = dict(os.environ)
        environment.update(read_env_file(Path(env_file).expanduser().resolve()))
        environment.update(_wsl_runtime_credentials())
        environment.update(credentials)
        environment.update(
            {
                "PYTHONUTF8": "1",
                "EVOBENCH_EXECUTION_MODE": "e2b",
                "EVOBENCH_E2B_TEMPLATE": e2b_template,
                "EVOBENCH_E2B_APEX_TEMPLATE": apex_template,
            }
        )
        if not environment.get("E2B_API_KEY", "").strip():
            raise ValueError(f"E2B_API_KEY is missing from environment or {env_file}")
    else:
        environment = _wsl_subprocess_environment(credentials)
        environment.update({"PYTHONUTF8": "1", "EVOBENCH_EXECUTION_MODE": "local"})
    return {
        "policy_config": policy_config,
        "judge_config": judge_config,
        "environment": environment,
        "claw_repo": (root / "external" / "claw-eval").resolve(),
    }


def _build_command(  # pylint: disable=huawei-too-many-arguments
    *,
    root: Path,
    suite_path: Path,
    harness_path: Path,
    official_eval_dir: Path,
    policy_config: Path,
    judge_config: Path,
    rollout_concurrency: int,
    execution_mode: str = "local",
) -> list[str]:
    common = [
        "-m",
        "evobench",
        "run-validation-eval",
        "--suite",
        str(suite_path),
        "--policy-harness",
        str(harness_path),
        "--policy-model-config",
        str(policy_config),
        "--judge-model-config",
        str(judge_config),
        "--output-dir",
        str(official_eval_dir),
        "--rollout-concurrency",
        str(max(1, int(rollout_concurrency))),
        "--trials",
        "1",
        "--trials-by-domain",
        f"general={GENERAL_TRIALS}",
    ]
    if execution_mode == "e2b":
        python = root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            python = Path(sys.executable)
        return [str(python), *common]
    claw_repo = (root / "external" / "claw-eval").resolve()
    return [
        "wsl.exe",
        "EVOBENCH_EXECUTION_MODE=local",
        "PYTHONUTF8=1",
        f"PYTHONPATH={_to_wsl(root)}",
        f"EVOBENCH_CLAW_REPO={_to_wsl(claw_repo)}",
        f"PATH={_to_wsl(root / '.claw-venv' / 'bin')}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        _to_wsl(root / ".claw-venv" / "bin" / "python"),
        "-m",
        "evobench",
        "run-validation-eval",
        "--suite",
        _to_wsl(suite_path),
        "--policy-harness",
        _to_wsl(harness_path),
        "--policy-model-config",
        _to_wsl(policy_config),
        "--judge-model-config",
        _to_wsl(judge_config),
        "--output-dir",
        _to_wsl(official_eval_dir),
        "--rollout-concurrency",
        str(max(1, int(rollout_concurrency))),
        "--trials",
        "1",
        "--trials-by-domain",
        f"general={GENERAL_TRIALS}",
    ]


def _run_wsl_command(
    command: list[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(command, cwd=cwd, env=dict(environment), check=False)  # noqa: S603


def _run_host_command(
    command: list[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(command, cwd=cwd, env=dict(environment), check=False)  # noqa: S603


def _validate_result_harness(result: Mapping[str, Any], *, expected_path: Path) -> None:
    raw_harness = result.get("policy_harness_dir")
    if not isinstance(raw_harness, str) or not raw_harness.strip():
        raise ValueError("official result does not identify policy_harness_dir")
    normalized = raw_harness.replace("\\", "/").rstrip("/").casefold()
    expected_windows = str(expected_path).replace("\\", "/").rstrip("/").casefold()
    expected_wsl = _to_wsl(expected_path).rstrip("/").casefold()
    if normalized not in {expected_windows, expected_wsl}:
        raise ValueError("official result was not produced by the requested policy_harness")


def _select_and_validate_results(
    result: Mapping[str, Any],
    *,
    case_ids: Sequence[str],
    official_eval_dir: Path,
) -> list[dict[str, Any]]:
    raw_tasks = result.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("official Evo-Bench result has no task list")
    task_by_id: dict[str, dict[str, Any]] = {}
    for task in raw_tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id", "")).strip()
        if task_id in task_by_id:
            raise ValueError(f"official Evo-Bench result contains duplicate task: {task_id}")
        if task_id:
            task_by_id[task_id] = task

    missing = [case_id for case_id in case_ids if case_id not in task_by_id]
    if missing:
        raise ValueError(f"official Evo-Bench result is missing requested tasks: {missing}")
    selected = [task_by_id[case_id] for case_id in case_ids]
    for task in selected:
        _validate_task_result(task, official_eval_dir=official_eval_dir)
    return selected


def _validate_task_result(task: Mapping[str, Any], *, official_eval_dir: Path) -> None:
    task_id = str(task.get("task_id", ""))
    domain = str(task.get("domain", "")).lower()
    runtime_errors = task.get("runtime_errors")
    if isinstance(runtime_errors, list) and runtime_errors:
        first_error = str(runtime_errors[0])
        raise RuntimeError(f"official {domain} task {task_id} had an infrastructure/runtime failure: {first_error}")
    trial_count = _trial_count_for_domain(domain, task_id=task_id)
    score = task.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(float(score)):
        raise ValueError(f"official task {task_id} has no finite aggregate score")
    if not isinstance(task.get("pass_hat_k"), bool):
        raise ValueError(f"official task {task_id} has no boolean pass_hat_k")
    for field in ("trial_scores", "trial_passed"):
        values = task.get(field)
        if not isinstance(values, list) or len(values) != trial_count:
            raise ValueError(f"official {domain} task {task_id} must contain {trial_count} {field}")
    for trial_index in range(1, trial_count + 1):
        trajectory_path = _trajectory_path(
            official_eval_dir,
            task_id=task_id,
            trial_index=trial_index,
            trial_count=trial_count,
        )
        if not trajectory_path.is_file():
            raise ValueError(f"official {domain} task {task_id} is missing trajectory {trial_index}/{trial_count}")
        _read_trial_score_detail(
            official_eval_dir,
            task_id=task_id,
            trial_index=trial_index,
            trial_count=trial_count,
            expected_score=task["trial_scores"][trial_index - 1],
            expected_passed=task["trial_passed"][trial_index - 1],
        )


def _trial_count_for_domain(domain: str, *, task_id: str = "") -> int:
    trial_count = _LOCAL_DOMAIN_TRIALS.get(str(domain).lower())
    if trial_count is None:
        raise ValueError(f"official task {task_id or '<unknown>'} has unsupported local domain: {domain}")
    return trial_count


def _materialize(  # pylint: disable=huawei-too-many-arguments
    *,
    eval_dir: Path,
    cases: Sequence[Mapping[str, Any]],
    official_tasks: Sequence[Mapping[str, Any]],
    official_results: Sequence[Mapping[str, Any]],
    official_result_path: Path,
    official_eval_dir: Path,
    team_skill_ref_path: str,
    harness_refs_path: str,
    dataset: Any,
    reused_official_result: bool,
) -> str:
    cases_dir = eval_dir / "cases"
    shutil.rmtree(cases_dir, ignore_errors=True)
    cases_dir.mkdir(parents=True, exist_ok=True)
    case_refs: list[dict[str, Any]] = []
    result_paths: list[str] = []

    for index, (case, official_task, official_result) in enumerate(
        zip(cases, official_tasks, official_results, strict=True),
        start=1,
    ):
        case_ref = _materialize_case(
            case=case,
            official_task=official_task,
            official_result=official_result,
            case_dir=cases_dir / _safe_name(str(official_task["id"]), index=index),
            official_result_path=official_result_path,
            official_eval_dir=official_eval_dir,
            reused_official_result=reused_official_result,
        )
        case_refs.append(case_ref)
        result_paths.append(case_ref["result_path"])

    # The iterative optimizer treats eval-ref case scores as the comparison
    # objective and regards 1.0 as pass. General uses strict Pass^3; Office uses
    # its single official verdict. Native rubric scores remain diagnostic only.
    scores = [float(item["score"]) for item in case_refs]
    passed = sum(bool(item["metadata"]["evaluation_passed"]) for item in case_refs)
    summary_path = eval_dir / "summary.json"
    _write_json(
        summary_path,
        {
            "total_cases": len(case_refs),
            "passed_cases": passed,
            "failed_cases": len(case_refs) - passed,
            "average_score": sum(scores) / len(scores),
            "evaluation_method": EVALUATION_METHOD,
            "case_results": result_paths,
        },
    )
    eval_ref_path = eval_dir / "eval_ref.yaml"
    official_metrics = _official_metrics(official_results)
    eval_ref = {
        "eval_id": "_".join(eval_dir.parts[-3:]),
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "team_name": _evaluation_name(team_skill_ref_path),
        "team_skill_ref_path": team_skill_ref_path,
        "harness_refs_path": harness_refs_path,
        "dataset": _dataset_ref(dataset),
        "eval_dir": str(eval_dir),
        "case_results_dir": str(cases_dir),
        "case_traces_dir": str(cases_dir),
        "summary_path": str(summary_path),
        "official_result_path": str(official_result_path.resolve()),
        "reused_official_result": reused_official_result,
        "official_metrics": official_metrics,
        "cases": case_refs,
    }
    eval_ref_path.write_text(
        yaml.safe_dump(eval_ref, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(eval_ref_path)


def _materialize_case(  # pylint: disable=huawei-too-many-arguments
    *,
    case: Mapping[str, Any],
    official_task: Mapping[str, Any],
    official_result: Mapping[str, Any],
    case_dir: Path,
    official_result_path: Path,
    official_eval_dir: Path,
    reused_official_result: bool,
) -> dict[str, Any]:
    case_id = str(official_task["id"])
    domain = str(official_task.get("domain", "")).lower()
    trial_count = _trial_count_for_domain(domain, task_id=case_id)
    aggregate_mean_score = float(official_result["score"])
    passed = bool(official_result["pass_hat_k"])
    objective_score = 1.0 if passed else 0.0
    case_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = case_dir / "judge" / "normalized_trace.json"
    normalized_traces, trajectory_paths = _normalized_traces(
        case_id=case_id,
        official_eval_dir=official_eval_dir,
        trial_count=trial_count,
    )
    trial_details = _trial_score_details(
        official_eval_dir,
        task_id=case_id,
        expected_scores=official_result["trial_scores"],
        expected_passed=official_result["trial_passed"],
    )
    _write_json(normalized_path, {"case_id": case_id, "traces": normalized_traces})

    reason = str(official_result.get("score_reason") or "official Evo-Bench aggregate")
    evaluation_metadata = {
        "source": "official_evobench_result",
        "domain": domain,
        "metric_family": official_result.get("metric_family"),
        "aggregate_mean_score": aggregate_mean_score,
        "pass_hat_k": passed,
        "pass_at_k": official_result.get("pass_at_k"),
        "trial_count": trial_count,
        "trial_scores": official_result.get("trial_scores"),
        "trial_passed": official_result.get("trial_passed"),
        "trial_details": trial_details,
        "trial_exit_reasons": official_result.get("trial_exit_reasons"),
        "runtime_errors": official_result.get("runtime_errors") or [],
        "trial_worker_diagnostics": official_result.get("trial_worker_diagnostics") or [],
        "trial_policy_violation_present": official_result.get("trial_policy_violation_present") or [],
        "policy_violation": bool(official_result.get("policy_violation")),
        "judge_detail": official_result.get("judge_detail"),
        "judge_evidence": _normalize_judge_evidence(official_result.get("judge_detail")),
        "official_result_path": str(official_result_path.resolve()),
        "official_trajectory_paths": [str(path.resolve()) for path in trajectory_paths],
        "reused_official_result": reused_official_result,
        "analysis_task_contract": public_task_contract_snapshot(official_task),
    }
    workspace_dir = _host_path(official_result.get("workspace_path"))
    result_path = case_dir / "result.json"
    _write_json(
        result_path,
        {
            "case_id": case_id,
            "status": "passed" if passed else "failed",
            "execution_status": "passed",
            "score": aggregate_mean_score,
            "trial_details": trial_details,
            "result": str(official_result.get("final_answer") or ""),
            "error": "",
            "workspace_dir": workspace_dir,
            "evaluation": {
                "method": EVALUATION_METHOD,
                "passed": passed,
                "reason": reason,
                "metadata": evaluation_metadata,
            },
            "metadata": {
                "case_path": str(case.get("case_path") or ""),
                "team_name": "policy_harness",
                "official_task_id": case_id,
            },
        },
    )
    trace_path = case_dir / "trace.json"
    recent_events = _recent_events(normalized_traces)
    _write_json(
        trace_path,
        {
            "case_id": case_id,
            "status": "passed" if passed else "failed",
            "input": str(official_task.get("prompt") or ""),
            "response": str(official_result.get("final_answer") or ""),
            "evaluation": {"passed": passed, "score": aggregate_mean_score, "reason": reason},
            "behavior_trace": {
                "normalized_trace_path": str(normalized_path.resolve()),
                "official_trajectory_paths": [str(path.resolve()) for path in trajectory_paths],
                "trajectory_window_summary": {
                    "window_size": len(recent_events),
                    "event_count": sum(int(trace["message_count"]) for trace in normalized_traces),
                    "failure_signatures": _failure_signatures(official_result),
                    "recent_events": recent_events,
                },
            },
        },
    )
    return {
        "case_id": case_id,
        "case_path": str(case.get("case_path") or ""),
        "trace_path": str(trace_path),
        "result_path": str(result_path),
        "status": "passed" if passed else "failed",
        "score": objective_score,
        "metadata": {
            "execution_status": "passed",
            "evaluation_method": EVALUATION_METHOD,
            "evaluation_passed": passed,
            "aggregate_mean_score": aggregate_mean_score,
            "domain": domain,
            "trial_count": trial_count,
        },
    }


def _official_metrics(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the selected-suite metrics used by the RSI promotion gate."""
    passed = [bool(item.get("pass_hat_k")) for item in results]
    native_scores = [float(item["score"]) for item in results]
    pass_at_k = [bool(item.get("pass_at_k")) for item in results]
    infra_failures = 0
    policy_violations = 0
    for item in results:
        diagnostics = item.get("trial_worker_diagnostics")
        if isinstance(diagnostics, list):
            infra_failures += sum(bool(value) for value in diagnostics)
        violations = item.get("trial_policy_violation_present")
        if isinstance(violations, list):
            policy_violations += sum(bool(value) for value in violations)
        elif item.get("policy_violation"):
            policy_violations += 1
    total = len(results)
    domain_counts: dict[str, int] = {}
    domain_pass_counts: dict[str, int] = {}
    for item in results:
        domain = str(item.get("domain", "unknown")).lower()
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        domain_pass_counts[domain] = domain_pass_counts.get(domain, 0) + int(bool(item.get("pass_hat_k")))
    domain_pass_rates = {domain: domain_pass_counts[domain] / count for domain, count in sorted(domain_counts.items())}
    primary_metric = "pass_hat_k" if set(domain_counts) == {"general"} else "strict_task_pass_rate"
    return {
        "primary_metric": primary_metric,
        "primary_score": sum(passed) / total,
        "pass_hat_k_count": sum(passed),
        "pass_at_k": sum(pass_at_k) / total,
        "pass_at_k_count": sum(pass_at_k),
        "native_mean_score": sum(native_scores) / total,
        "task_count": total,
        "domain_counts": dict(sorted(domain_counts.items())),
        "domain_pass_rates": domain_pass_rates,
        "trials_by_domain": dict(_LOCAL_DOMAIN_TRIALS),
        "infra_failures": infra_failures,
        "policy_violations": policy_violations,
    }


def _normalized_traces(
    *,
    case_id: str,
    official_eval_dir: Path,
    trial_count: int,
) -> tuple[list[dict[str, Any]], list[Path]]:
    traces: list[dict[str, Any]] = []
    paths: list[Path] = []
    for trial_index in range(1, trial_count + 1):
        path = _trajectory_path(
            official_eval_dir,
            task_id=case_id,
            trial_index=trial_index,
            trial_count=trial_count,
        )
        payload = _read_mapping(path, label=f"official trajectory for {case_id} trial {trial_index}")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError(f"official trajectory for {case_id} trial {trial_index} has no messages")
        normalized_messages = _normalize_messages(messages, trial_index=trial_index)
        traces.append(
            {
                "trace_id": f"{case_id}:trial_{trial_index}",
                "member_id": "policy_harness",
                "member_role": "policy_harness",
                "execution_id": str(payload.get("rollout_id") or f"trial_{trial_index}"),
                "step_count": sum(message.get("role") == "assistant" for message in normalized_messages),
                "message_count": len(normalized_messages),
                "messages": normalized_messages,
            }
        )
        paths.append(path)
    return traces, paths


def _normalize_messages(messages: Sequence[Any], *, trial_index: int) -> list[dict[str, Any]]:
    tool_outputs = {
        str(message.get("tool_call_id")): _content(message.get("content"))
        for message in messages
        if isinstance(message, Mapping) and message.get("role") == "tool" and message.get("tool_call_id")
    }
    normalized: list[dict[str, Any]] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        step_pointer = f"trial_{trial_index}:message_{message_index}"
        content = _content(message.get("content"))
        if not content:
            content = _content(message.get("reasoning_content"))
        normalized.append(
            {
                "role": str(message.get("role") or "unknown"),
                "message_index": message_index,
                "content": _excerpt(content, 12_000),
                "step_pointer": step_pointer,
                "tool_calls": _normalize_tool_calls(
                    message.get("tool_calls"),
                    outputs=tool_outputs,
                    step_pointer=step_pointer,
                ),
            }
        )
    return normalized


def _normalize_tool_calls(
    raw_calls: Any,
    *,
    outputs: Mapping[str, str],
    step_pointer: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_calls, list):
        return []
    normalized: list[dict[str, Any]] = []
    for call in raw_calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
        call_id = str(call.get("id") or "")
        normalized.append(
            {
                "name": str(function.get("name") or call.get("name") or ""),
                "input": _content(function.get("arguments", call.get("input", ""))),
                "output": outputs.get(call_id, ""),
                "error": "",
                "step_pointer": step_pointer,
            }
        )
    return normalized


def _recent_events(traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for trace in traces:
        for message in trace.get("messages", []):
            if not isinstance(message, Mapping) or message.get("role") not in {"assistant", "tool"}:
                continue
            tool_names = [str(call.get("name")) for call in message.get("tool_calls", []) if isinstance(call, Mapping)]
            summary = ", ".join(tool_names) if tool_names else str(message.get("content") or "")
            events.append(
                {
                    "event_index": len(events),
                    "event_type": "tool_calls" if tool_names else str(message.get("role")),
                    "summary": _excerpt(summary, 1_200),
                }
            )
    return events[-30:]


def _failure_signatures(result: Mapping[str, Any]) -> list[str]:
    signatures: list[str] = []
    for reason in result.get("trial_exit_reasons") or []:
        text = str(reason).strip()
        if text and text not in {"finished", "assistant_no_tool_call"} and text not in signatures:
            signatures.append(text)
    for error in result.get("runtime_errors") or []:
        text = _excerpt(str(error).strip(), 500)
        if text and text not in signatures:
            signatures.append(text)
    return signatures[:20]


def _trial_score_details(
    official_eval_dir: Path,
    *,
    task_id: str,
    expected_scores: Sequence[Any],
    expected_passed: Sequence[Any],
) -> list[dict[str, Any]]:
    """Read authoritative per-trial score artifacts for one task."""
    trial_count = len(expected_scores)
    if trial_count != len(expected_passed) or trial_count not in set(_LOCAL_DOMAIN_TRIALS.values()):
        raise ValueError(f"official task {task_id} has inconsistent trial arrays")
    return [
        _read_trial_score_detail(
            official_eval_dir,
            task_id=task_id,
            trial_index=trial_index,
            trial_count=trial_count,
            expected_score=expected_scores[trial_index - 1],
            expected_passed=expected_passed[trial_index - 1],
        )
        for trial_index in range(1, trial_count + 1)
    ]


def _read_trial_score_detail(  # pylint: disable=huawei-too-many-locals
    official_eval_dir: Path,
    *,
    task_id: str,
    trial_index: int,
    trial_count: int,
    expected_score: Any,
    expected_passed: Any,
) -> dict[str, Any]:
    score_path = _score_path(
        official_eval_dir,
        task_id=task_id,
        trial_index=trial_index,
        trial_count=trial_count,
    )
    payload = _read_mapping(score_path, label=f"official score for {task_id} trial {trial_index}")
    score = payload.get("score")
    if not _finite_number(score):
        raise ValueError(f"official score for {task_id} trial {trial_index} has no finite score")
    if not isinstance(payload.get("passed"), bool):
        raise ValueError(f"official score for {task_id} trial {trial_index} has no boolean passed")
    score_reason = payload.get("score_reason")
    if not isinstance(score_reason, str) or not score_reason.strip():
        raise ValueError(f"official score for {task_id} trial {trial_index} has no score_reason")
    if not _finite_number(expected_score) or not math.isclose(
        float(score),
        float(expected_score),
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise ValueError(f"official score for {task_id} trial {trial_index} disagrees with result.json")
    if not isinstance(expected_passed, bool) or payload["passed"] is not expected_passed:
        raise ValueError(f"official verdict for {task_id} trial {trial_index} disagrees with result.json")

    judge_detail_present = "judge_detail" in payload
    raw_judge_detail = payload.get("judge_detail")
    if isinstance(raw_judge_detail, Mapping):
        judge_detail: dict[str, Any] | None = dict(raw_judge_detail)
        judge_availability = "available"
    elif not judge_detail_present:
        judge_detail = None
        judge_availability = "not_present"
    elif raw_judge_detail is None:
        judge_detail = None
        judge_availability = "not_available"
    else:
        judge_detail = None
        judge_availability = "invalid"

    dimension_scores = _dimension_scores(score_reason, raw_judge_detail)
    dimension_states = [item["availability"] for item in dimension_scores.values()]
    available_count = dimension_states.count("available")
    if available_count == len(_CLAW_DIMENSIONS):
        dimension_availability = "available"
    elif available_count:
        dimension_availability = "partial"
    elif "invalid" in dimension_states:
        dimension_availability = "invalid"
    else:
        dimension_availability = "not_available"
    return {
        "schema_version": 1,
        "trial_id": f"trial_{trial_index}",
        "trial_index": trial_index,
        "score": float(score),
        "passed": payload["passed"],
        "score_reason": score_reason,
        "judge_detail": judge_detail,
        "dimension_scores": dimension_scores,
        "availability": {
            "score_file": "available",
            "score": "available",
            "passed": "available",
            "score_reason": "available",
            "judge_detail": judge_availability,
            "dimension_scores": dimension_availability,
        },
        "source": {"score_path": str(score_path.resolve())},
    }


def _normalize_judge_evidence(value: Any) -> dict[str, Any]:
    """Normalize aggregate judge feedback without inventing missing criteria."""
    if not isinstance(value, Mapping):
        return {
            "schema_version": 1,
            "availability": "not_available",
            "grading_run_status": "",
            "criteria": [],
        }

    raw_criteria = value.get("criteria")
    if not isinstance(raw_criteria, list):
        return {
            "schema_version": 1,
            "availability": "not_available",
            "grading_run_status": str(value.get("grading_run_status") or ""),
            "criteria": [],
        }

    criteria: list[dict[str, Any]] = []
    invalid_count = 0
    for index, raw in enumerate(raw_criteria, start=1):
        if not isinstance(raw, Mapping):
            invalid_count += 1
            continue
        score = raw.get("score")
        rationale = raw.get("rationale")
        criterion_id = str(raw.get("criterion_id") or raw.get("verifier_id") or f"criterion_{index}")
        criteria.append(
            {
                "criterion_id": criterion_id,
                "verifier_id": str(raw.get("verifier_id") or ""),
                "score": float(score) if _finite_number(score) else None,
                "status": str(raw.get("status") or ""),
                "rationale": str(rationale) if isinstance(rationale, str) else "",
                "source": "official_result.judge_detail.criteria",
            }
        )

    availability = "available" if criteria and not invalid_count else "partial" if criteria else "invalid"
    return {
        "schema_version": 1,
        "availability": availability,
        "grading_run_status": str(value.get("grading_run_status") or ""),
        "criteria": criteria,
    }


def _dimension_scores(score_reason: str, judge_detail: Any) -> dict[str, dict[str, Any]]:
    reason_match = _CLAW_REASON_PATTERN.fullmatch(score_reason.strip())
    dimensions: dict[str, dict[str, Any]] = {}
    for name in _CLAW_DIMENSIONS:
        if isinstance(judge_detail, Mapping) and name in judge_detail:
            value = judge_detail[name]
            if _finite_number(value):
                dimensions[name] = {
                    "availability": "available",
                    "value": float(value),
                    "source": f"score.json.judge_detail.{name}",
                }
            else:
                dimensions[name] = {
                    "availability": "invalid",
                    "value": None,
                    "source": f"score.json.judge_detail.{name}",
                }
        elif reason_match is not None:
            dimensions[name] = {
                "availability": "available",
                "value": float(reason_match.group(name)),
                "source": "score.json.score_reason",
            }
        else:
            dimensions[name] = {
                "availability": "not_available",
                "value": None,
                "source": None,
            }
    return dimensions


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(float(value))


def _trajectory_path(
    official_eval_dir: Path,
    *,
    task_id: str,
    trial_index: int,
    trial_count: int,
) -> Path:
    safe_task_id = _safe_name(task_id)
    if safe_task_id != task_id:
        raise ValueError(f"official Evo-Bench task id is not path safe: {task_id}")
    root = official_eval_dir / "rollouts" / task_id
    return (root / f"trial_{trial_index}" if trial_count > 1 else root) / "trajectory.json"


def _score_path(
    official_eval_dir: Path,
    *,
    task_id: str,
    trial_index: int,
    trial_count: int,
) -> Path:
    safe_task_id = _safe_name(task_id)
    if safe_task_id != task_id:
        raise ValueError(f"official Evo-Bench task id is not path safe: {task_id}")
    root = official_eval_dir / "rollouts" / task_id
    return (root / f"trial_{trial_index}" if trial_count > 1 else root) / "score.json"


def _dataset_ref(dataset: Any) -> dict[str, Any] | None:
    if dataset is None:
        return None
    if isinstance(dataset, Mapping):
        return {
            "dataset_id": dataset.get("dataset_id"),
            "dataset_dir": dataset.get("dataset_dir"),
            "dataset_files": list(dataset.get("dataset_files") or []),
        }
    return {
        "dataset_id": getattr(dataset, "dataset_id", ""),
        "dataset_dir": getattr(dataset, "dataset_dir", ""),
        "dataset_files": list(getattr(dataset, "dataset_files", []) or []),
    }


def _evaluation_name(skill_ref_path: str) -> str:
    if not str(skill_ref_path or "").strip():
        return "single_harness"
    path = Path(skill_ref_path).expanduser()
    return path.parent.name if path.name.lower() == "skill.md" else path.name


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _host_path(value: Any) -> str:
    """Translate a local WSL mount path to its host path for diagnosis."""
    raw = str(value or "").strip()
    if os.name == "nt" and raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5]
        tail = raw[6:].lstrip("/").replace("/", "\\")
        return f"{drive.upper()}:\\{tail}"
    return raw


def _safe_name(value: str, *, index: int | None = None) -> str:
    clean = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    clean = clean.strip("_") or "case"
    return f"c{index:03d}_{clean}" if index is not None else clean


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _excerpt(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _read_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON mapping: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "EvoBenchRSIEvaluator",
    "EvoBenchRSIEvaluatorConfig",
    "evaluate_batch",
]
