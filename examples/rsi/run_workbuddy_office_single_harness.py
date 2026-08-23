# coding: utf-8
"""Run WorkBuddy Bench Office through the iterative single-harness optimizer."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
import traceback
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.rsi.workbuddy_office import WorkBuddyOfficeEvaluator
from openjiuwen.rsi.config import (
    load_auto_coordinating_harness_config,
)
from openjiuwen.rsi.schema import DatasetArtifact
from openjiuwen.rsi.single_harness import (
    IterativeSingleHarnessRequest,
    SingleHarnessIterativeOptimizationOrchestrator,
)


DEFAULT_OUTPUT_ROOT = Path(".office_runs" if os.name == "nt" else ".local/rsi/workbuddy_office")
DEFAULT_RUN_MODEL = Path(".local/rsi/models/token_plan_deepseek_v4_flash_single_harness.yaml")
DEFAULT_OPTIMIZATION_MODEL = Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml")


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = _parse_args(argv)
    if args.candidate_holdout_cases:
        raise ValueError(
            "--candidate-holdout-cases is no longer supported: Epoch Full Checkpoint is the full-dataset promotion gate"
        )
    if args.disable_full_evaluation:
        raise ValueError("--disable-full-evaluation is incompatible with iterative promotion")
    dataset_root = _resolve_dataset_root(args.dataset_root)
    run_model = Path(args.run_model_config_ref).expanduser().resolve()
    optimization_model = Path(args.optimization_model_config_ref).expanduser().resolve()
    for path in (run_model, optimization_model):
        if not path.is_file():
            raise FileNotFoundError(path)

    run_dir = Path(args.output_dir).expanduser().resolve() / "runs" / _physical_run_name(args.run_name)
    if run_dir.exists() and not args.resume:
        _remove_run_dir(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = _write_dataset(
        dataset_root=dataset_root,
        output_path=run_dir / "dataset" / "cases.json",
        task_ids=args.task_id,
        limit=args.limit,
        difficulty=args.difficulty,
        category=args.category,
        timeout_sec=args.timeout_sec,
        verifier_timeout_sec=args.verifier_timeout_sec,
        python_executable=args.workbuddy_python,
    )
    harness_refs_path = (
        Path(args.harness_refs_path).expanduser().resolve()
        if args.harness_refs_path
        else run_dir / "harnesses" / "harness_refs.yaml"
    )
    if not args.harness_refs_path and (not args.resume or not harness_refs_path.is_file()):
        harness_refs_path = _prepare_office_harness(run_dir / "harnesses")
    if not harness_refs_path.is_file():
        raise FileNotFoundError(harness_refs_path)
    config_path = _write_config(
        run_dir=run_dir,
        run_model=run_model,
        optimization_model=optimization_model,
        batch_size=args.batch_size,
        max_epochs=args.max_epochs,
        sibling_candidate_count=args.sibling_candidate_count,
        improver_policy_ref=args.improver_policy_ref,
        solver_backend=args.solver_backend,
        jiuwenswarm_executable=args.jiuwenswarm_executable,
        jiuwenswarm_python=args.jiuwenswarm_python,
        jiuwenswarm_expected_version=args.jiuwenswarm_expected_version,
        jiuwenswarm_startup_timeout_sec=args.jiuwenswarm_startup_timeout_sec,
        jiuwenswarm_runtime_timeout_sec=args.jiuwenswarm_runtime_timeout_sec,
        jiuwenswarm_runtime_profile=args.jiuwenswarm_runtime_profile,
    )
    config = load_auto_coordinating_harness_config(str(config_path))
    evaluator = WorkBuddyOfficeEvaluator(config.evaluator)
    if args.seed_only:
        cases = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]
        seed_eval_ref = _run_async(
            evaluator.evaluate_batch(
                cases=cases,
                team_skill_ref_path="",
                harness_refs_path=str(harness_refs_path),
                output_dir=str(run_dir / "seed_evaluation"),
                dataset=DatasetArtifact(
                    dataset_id="workbuddy_office_v1",
                    dataset_dir=str(dataset_path.parent),
                    dataset_files=[str(dataset_path)],
                    cases=len(cases),
                ),
            ),
            run_dir,
        )
        print(f"SEED_EVAL_REF={seed_eval_ref}")
    else:
        result = _run_async(
            SingleHarnessIterativeOptimizationOrchestrator(
                config,
                evaluator=evaluator,
            ).run(
                IterativeSingleHarnessRequest(
                    dataset_files=[str(dataset_path)],
                    harness_refs_path=str(harness_refs_path),
                    output_dir=str(run_dir / "single_harness_optimization"),
                    dataset_id="workbuddy_office_v1",
                    resume=args.resume,
                    auto_full_baseline=True,
                )
            ),
            run_dir,
        )
        print(f"SINGLE_HARNESS_STATE={result.state_path}")
        print(f"SINGLE_HARNESS_REPORT={result.report_path}")
        print(f"CURRENT_HARNESS_REFS={result.current_harness_refs_path}")
        print(f"BEST_HARNESS_REFS={result.best_harness_refs_path}")
        print(f"PUBLISHED_HARNESS_REFS={result.published_harness_refs_path}")
        print(f"BEST_SCORE={result.best_score}")
    print(f"RUN_DIR={run_dir}")
    print(f"DATASET_PATH={dataset_path}")
    print(f"HARNESS_REFS_PATH={harness_refs_path}")
    return 0


def _resolve_dataset_root(value: str) -> Path:
    candidates: list[Path] = []
    if value.strip():
        candidates.append(Path(value).expanduser())
    env_root = os.environ.get("WORKBUDDY_OFFICE_DATASET_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root.parent / "workbuddy-bench" / "datasets" / "wb-bench-office-v1.0",
            repo_root.parent.parent / "workbuddy-bench" / "datasets" / "wb-bench-office-v1.0",
            Path("D:/code/workbuddy-bench/datasets/wb-bench-office-v1.0"),
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "dataset.toml").is_file() and (resolved / "tasks").is_dir():
            return resolved
    raise FileNotFoundError("WorkBuddy Office dataset was not found; set WORKBUDDY_OFFICE_DATASET_ROOT")


def _write_dataset(
    *,
    dataset_root: Path,
    output_path: Path,
    task_ids: list[str],
    limit: int,
    difficulty: str,
    category: str,
    timeout_sec: int,
    verifier_timeout_sec: int,
    python_executable: str = "",
) -> Path:
    task_dirs = sorted(path for path in (dataset_root / "tasks").iterdir() if path.is_dir())
    selected: list[Path] = []
    requested = {item.strip() for item in task_ids if item.strip()}
    available = {task_dir.name: task_dir for task_dir in task_dirs}
    missing = sorted(requested - set(available))
    if missing:
        raise ValueError(f"WorkBuddy task ids not found: {', '.join(missing)}")
    candidates = [available[task_id] for task_id in task_ids] if requested else task_dirs
    for task_dir in candidates:
        task_config = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
        metadata = task_config.get("metadata", {})
        if difficulty and str(metadata.get("difficulty", "")) != difficulty:
            continue
        if category and str(metadata.get("category", "")) != category:
            continue
        selected.append(task_dir)
        if limit > 0 and len(selected) >= limit:
            break
    if not selected:
        raise ValueError("no WorkBuddy Office tasks matched the selection")

    cases = [
        _case_from_task(
            task_dir,
            dataset_root=dataset_root,
            timeout_sec=timeout_sec,
            verifier_timeout_sec=verifier_timeout_sec,
            python_executable=python_executable,
        )
        for task_dir in selected
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "dataset_id": "workbuddy_office_v1",
                "source": "workbuddy_office",
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def _case_from_task(
    task_dir: Path,
    *,
    dataset_root: Path,
    timeout_sec: int,
    verifier_timeout_sec: int,
    python_executable: str = "",
) -> dict[str, Any]:
    task_config = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    metadata = task_config.get("metadata", {})
    instruction = (task_dir / "instruction.md").read_text(encoding="utf-8").strip()
    return {
        "case_id": task_dir.name,
        "input": instruction,
        "source": "workbuddy_office",
        "task_type": "office_artifact",
        "dimension": str(metadata.get("category", "office")),
        "difficulty": str(metadata.get("difficulty", "")),
        "metadata": {
            "category": str(metadata.get("category", "")),
            "difficulty": str(metadata.get("difficulty", "")),
            "dataset_id": dataset_root.name,
        },
        "workbuddy_office": {
            "dataset_id": dataset_root.name,
            "task_id": task_dir.name,
            "task_dir": str(task_dir.resolve()),
            "timeout_sec": timeout_sec,
            "verifier_timeout_sec": verifier_timeout_sec,
            "success_score": 1.0,
            **(
                {"python_executable": str(Path(python_executable).expanduser().resolve())}
                if python_executable.strip()
                else {}
            ),
        },
    }


def _prepare_office_harness(output_dir: Path) -> Path:
    harness_dir = output_dir / "office_worker"
    harness_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        harness_dir / "harness_config.yaml",
        {
            "schema_version": "1.0",
            "id": "workbuddy_office_worker",
            "name": "WorkBuddy Office worker",
            "description": "A single harness for producing verifiable office artifacts.",
            "config": {"enable_subagent": False},
        },
    )
    (harness_dir / "config.json").write_text("{}\n", encoding="utf-8")
    (harness_dir / "identity.md").write_text(
        "You are an office task execution agent. Inspect source files and produce the requested editable artifact in the exact output location.\n",
        encoding="utf-8",
    )
    (harness_dir / "soul.md").write_text(
        "Prefer faithful transformation over invention. Preserve source meaning, satisfy exact file and schema requirements, and validate the artifact before finishing.\n",
        encoding="utf-8",
    )
    _write_yaml(harness_dir / "tools" / "tools.yaml", {"tools": []})
    _write_yaml(harness_dir / "mcps" / "mcps.yaml", {"mcps": []})
    _write_yaml(harness_dir / "subagents" / "subagents.yaml", {"subagents": []})
    _write_yaml(harness_dir / "skills" / "skills.yaml", {"skills": ["skills/office_baseline"]})
    _write_yaml(
        harness_dir / "rails" / "rails.yaml",
        {
            "rails": [
                {
                    "type": "core.skill_use",
                    "params": {
                        "skill_mode": "all",
                        "include_tools": False,
                    },
                }
            ]
        },
    )
    skill_dir = harness_dir / "skills" / "office_baseline"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: workbuddy-office-baseline
description: Complete file-based office tasks with inspectable, editable deliverables.
---

# Office Baseline

1. Read the instruction and list the workspace files before deciding how to act.
2. Inspect source content programmatically when it is structured or too large to read manually.
3. Extract exact output paths, names, schemas, ordering, formulas, and preservation constraints.
4. Produce the requested editable artifact with an appropriate installed Python library or local tool.
5. Reopen the generated artifact and verify its structure, values, labels, formulas, and required sections.
6. Do not invent missing source content, inspect hidden tests, or leave extra deliverables unless requested.
""",
        encoding="utf-8",
    )
    harness_refs_path = output_dir / "harness_refs.yaml"
    _write_yaml(
        harness_refs_path,
        {
            "version": 1,
            "harness_refs": {"office_worker": str(harness_dir)},
            "roles": [
                {
                    "role": "office_worker",
                    "member_name": "office_worker",
                    "description": "Single WorkBuddy Office artifact worker.",
                    "harness_ref_path": str(harness_dir),
                }
            ],
        },
    )
    return harness_refs_path


def _write_config(
    *,
    run_dir: Path,
    run_model: Path,
    optimization_model: Path,
    batch_size: int,
    max_epochs: int,
    sibling_candidate_count: int = 1,
    improver_policy_ref: str = "",
    solver_backend: str = "deep_agent",
    jiuwenswarm_executable: str = "",
    jiuwenswarm_python: str = "",
    jiuwenswarm_expected_version: str = "",
    jiuwenswarm_startup_timeout_sec: int = 120,
    jiuwenswarm_runtime_timeout_sec: int = 3600,
    jiuwenswarm_runtime_profile: str = "task86",
) -> Path:
    config_path = run_dir / "single_harness.yaml"
    _write_yaml(
        config_path,
        {
            "workspace_dir": str(run_dir / "workspace"),
            "max_epochs": max_epochs,
            "freeze_team_skill": True,
            "freeze_team_members": False,
            "model_configs": {
                "evaluation": str(run_model),
                "analysis": str(optimization_model),
                "member_optimization": str(optimization_model),
            },
            "data_loader": {
                "file_pattern": "*.json",
                "batch_size": batch_size,
                "batch_balance_keys": ["difficulty", "dimension", "source", "task_type"],
            },
            "evaluator": {
                "backend": "single_harness",
                "evaluation_method": "script_based",
                "success_score": 1.0,
                "case_lifecycle_timeout_sec": 7200,
                "transient_case_retry_limit": 5,
                "solver_backend": solver_backend,
                "jiuwenswarm_executable": jiuwenswarm_executable,
                "jiuwenswarm_python": jiuwenswarm_python,
                "jiuwenswarm_expected_version": jiuwenswarm_expected_version,
                "jiuwenswarm_startup_timeout_sec": jiuwenswarm_startup_timeout_sec,
                "jiuwenswarm_runtime_timeout_sec": jiuwenswarm_runtime_timeout_sec,
                "jiuwenswarm_runtime_profile": jiuwenswarm_runtime_profile,
            },
            "evaluation_result_analyzer": {
                # Diagnostic coverage and candidate-evaluation budget are
                # separate concerns. Preserve up to six causal clusters per
                # case even when only a bounded subset is attempted.
                "max_issues": max(20, batch_size * 6),
                "evidence_limit_per_issue": 3,
            },
            "member_optimizer": {
                "action_group_configs": ["prompt", "skill", "tool", "rail"],
                "max_roles_per_run": 1,
                "max_actions_per_plan": 3,
                "max_repair_rounds_per_batch": 3,
                "sibling_candidate_count": sibling_candidate_count,
                "improver_policy_ref": improver_policy_ref,
                "min_attribution_confidence": 0.1,
                "execution_concurrency": 1,
                "role_execution_concurrency": 1,
                "action_execution_concurrency_per_role": 1,
                "allowed_action_groups": ["prompt", "skill", "tool", "rail"],
                "allowed_prompt_surfaces": ["prompt_section"],
                "candidate_min_score_delta": 0.0,
                "candidate_min_target_behavior_delta": 0.0,
                "candidate_non_target_max_regression": 0.0,
                "candidate_holdout_cases": 0,
                "candidate_holdout_max_regression": 0.0,
            },
            "scheduling": {
                "evaluation_strategy": "hybrid",
                "coordination_strategy": "team_first_single_pass",
                "promotion_policy": "epoch_full_evaluation",
                "full_evaluation_enabled": True,
            },
        },
    )
    return config_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default="office_smoke")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--difficulty", choices=["", "easy", "medium", "hard"], default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--harness-refs-path", default="")
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-epochs", type=int, default=1)
    parser.add_argument(
        "--sibling-candidate-count",
        type=int,
        default=_env_int("WORKBUDDY_SIBLING_CANDIDATE_COUNT", 1),
        help=("Candidates generated from each frozen parent Harness (env: WORKBUDDY_SIBLING_CANDIDATE_COUNT)."),
    )
    parser.add_argument(
        "--improver-policy-ref",
        default=os.environ.get("RSI_IMPROVER_POLICY_REF", ""),
        help="Versioned Improver Policy YAML (env: RSI_IMPROVER_POLICY_REF).",
    )
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--verifier-timeout-sec", type=int, default=1800)
    parser.add_argument(
        "--workbuddy-python",
        default="",
        help="Optional Python executable from the WorkBuddy Bench environment.",
    )
    parser.add_argument(
        "--solver-backend",
        choices=["deep_agent", "jiuwenswarm"],
        default=os.environ.get("WORKBUDDY_SOLVER_BACKEND", "deep_agent"),
        help="Office solver backend (env: WORKBUDDY_SOLVER_BACKEND).",
    )
    parser.add_argument(
        "--jiuwenswarm-executable",
        default=os.environ.get("JIUWENSWARM_EXECUTABLE", ""),
        help=(
            "Optional host path to the JiuwenSwarm bridge script; the bundled "
            "bridge is used by default (env: JIUWENSWARM_EXECUTABLE)."
        ),
    )
    parser.add_argument(
        "--jiuwenswarm-python",
        default=os.environ.get("JIUWENSWARM_PYTHON", ""),
        help=(
            "Optional Python executable inside the WorkBuddy container; defaults to python3 (env: JIUWENSWARM_PYTHON)."
        ),
    )
    parser.add_argument(
        "--jiuwenswarm-expected-version",
        default=os.environ.get("JIUWENSWARM_EXPECTED_VERSION", ""),
        help="Optional required JiuwenSwarm version (env: JIUWENSWARM_EXPECTED_VERSION).",
    )
    parser.add_argument(
        "--jiuwenswarm-startup-timeout-sec",
        type=int,
        default=_env_int("JIUWENSWARM_STARTUP_TIMEOUT_SEC", 120),
        help="JiuwenSwarm startup timeout (env: JIUWENSWARM_STARTUP_TIMEOUT_SEC).",
    )
    parser.add_argument(
        "--jiuwenswarm-runtime-timeout-sec",
        "--jiuwenswarm-run-timeout-sec",
        dest="jiuwenswarm_runtime_timeout_sec",
        type=int,
        default=_env_int("JIUWENSWARM_RUNTIME_TIMEOUT_SEC", 3600),
        help="JiuwenSwarm task timeout (env: JIUWENSWARM_RUNTIME_TIMEOUT_SEC).",
    )
    parser.add_argument(
        "--jiuwenswarm-runtime-profile",
        default=os.environ.get("JIUWENSWARM_RUNTIME_PROFILE", "task86"),
        help="JiuwenSwarm runtime profile (env: JIUWENSWARM_RUNTIME_PROFILE).",
    )
    parser.add_argument(
        "--candidate-holdout-cases",
        type=int,
        default=0,
        help="Deprecated; nonzero values are rejected.",
    )
    parser.add_argument(
        "--disable-full-evaluation",
        action="store_true",
        help="Deprecated; Epoch Full Checkpoint is required.",
    )
    parser.add_argument("--run-model-config-ref", default=str(DEFAULT_RUN_MODEL))
    parser.add_argument(
        "--optimization-model-config-ref",
        default=str(DEFAULT_OPTIMIZATION_MODEL),
    )
    return parser.parse_args(argv)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _run_async(coroutine: Any, run_dir: Path) -> Any:
    try:
        return asyncio.run(coroutine)
    except BaseException as error:
        if not isinstance(error, (KeyboardInterrupt, SystemExit)):
            try:
                error_path = _write_fatal_error(run_dir, error)
                print(f"FATAL_ERROR_LOG={error_path}", file=sys.stderr)
            except OSError as write_error:
                print(
                    f"Failed to persist fatal error: {write_error}",
                    file=sys.stderr,
                )
        raise


def _write_fatal_error(run_dir: Path, error: BaseException) -> Path:
    error_path = run_dir / "fatal_errors.log"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_traceback = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    with error_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"[{datetime.now(timezone.utc).isoformat()}]\n")
        stream.write(rendered_traceback)
        if not rendered_traceback.endswith("\n"):
            stream.write("\n")
        stream.write("\n")
    return error_path


def _physical_run_name(run_name: str) -> str:
    if os.name != "nt" or len(run_name) <= 24:
        return run_name
    prefix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in run_name)
    digest = hashlib.sha256(run_name.encode("utf-8")).hexdigest()[:8]
    return f"{prefix[:12]}-{digest}"


def _remove_run_dir(run_dir: Path) -> None:
    shutil.rmtree(_native_path(run_dir))


def _native_path(path: Path) -> str:
    resolved = str(path.expanduser().resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
