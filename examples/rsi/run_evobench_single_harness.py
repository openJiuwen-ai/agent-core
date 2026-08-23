# coding: utf-8
"""Run the RSI single-Harness loop on a frozen local no-key Evo-Bench suite."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.rsi.evobench.launcher import resolve_evobench_root
from examples.rsi.evobench.domain_runner import materialize_domain_suites
from examples.rsi.evobench.rsi_evaluator import (
    EvoBenchRSIEvaluator,
    EvoBenchRSIEvaluatorConfig,
)
from examples.rsi.evobench.rsi_optimizer import PolicyHarnessRSIOptimizer
from examples.rsi.evobench.subset import main as run_local_subset
from openjiuwen.rsi.config import load_auto_coordinating_harness_config
from openjiuwen.rsi.single_harness import (
    IterativeSingleHarnessRequest,
    SingleHarnessIterativeOptimizationOrchestrator,
)


DEFAULT_SOURCE_RUN = Path(".evobench_runs/local_claw20/local_claw20_v1")
DEFAULT_OUTPUT_ROOT = Path(".evobench_runs/rsi_claw20")
DEFAULT_RUN_MODEL = Path(".local/rsi/models/token_plan_deepseek_v4_flash_single_harness.yaml")
DEFAULT_OPTIMIZATION_MODEL = Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml")


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = _parse_args(argv)
    source_run = Path(args.source_run).expanduser().resolve()
    suite_path, _ = _ensure_source_run(args, source_run)

    run_dir = Path(args.output_dir).expanduser().resolve() / _safe_name(args.run_name)
    if run_dir.exists() and not args.resume:
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = _write_dataset(
        suite_path,
        run_dir / "dataset" / "cases.json",
        task_ids=args.task_id,
        limit=args.limit,
    )
    dataset_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    selected_cases = dataset_payload["cases"]
    execution_mode = _resolve_execution_mode(args.execution_mode, selected_cases)
    selected_case_count = len(selected_cases)
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")
    dataset_id = f"evobench_validation_{selected_case_count}"
    harness_refs_path = _write_seed_refs(
        resolve_evobench_root(args.evobench_root),
        run_dir / "harnesses" / "harness_refs.yaml",
    )
    config_path = _write_config(
        run_dir=run_dir,
        run_model=Path(args.run_model_config_ref).expanduser().resolve(),
        optimization_model=Path(args.optimization_model_config_ref).expanduser().resolve(),
        batch_size=args.batch_size,
        max_epochs=args.max_epochs,
        sibling_candidate_count=args.sibling_candidate_count,
        max_issue_attempts=args.max_issue_attempts,
        # The control-plane limit includes the initial candidate round. The
        # user-facing flag counts feedback-driven repairs after that attempt.
        max_repair_rounds=args.max_repair_rounds + 1,
        improver_policy_ref=args.improver_policy_ref,
    )
    config = load_auto_coordinating_harness_config(str(config_path))
    evaluator = EvoBenchRSIEvaluator(
        EvoBenchRSIEvaluatorConfig(
            evobench_root=args.evobench_root,
            policy_model_config=args.run_model_config_ref,
            judge_model_config=args.optimization_model_config_ref,
            rollout_concurrency=args.rollout_concurrency,
            existing_official_result=None,
            execution_mode=execution_mode,
            env_file=args.env_file,
            e2b_template=args.e2b_template,
            apex_template=args.apex_template,
        )
    )
    optimizer = PolicyHarnessRSIOptimizer(config.member_optimizer)
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        config,
        evaluator=evaluator,
        member_optimizer=optimizer,
    )

    async def _run() -> Any:
        result = await orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs_path),
                output_dir=str(run_dir / "single_harness_optimization"),
                dataset_id=dataset_id,
                resume=args.resume,
                baseline_eval_ref_path="",
                # The benchmark evolves H after every batch. Each batch source
                # execution is the paired pre-intervention reference; do not
                # spend a separate full-suite H0 pass before optimization.
                auto_full_baseline=False,
            )
        )
        return result

    result = asyncio.run(_run())
    print("BASELINE_MODE=paired_batch_source_evaluations")
    print(f"SINGLE_HARNESS_STATE={result.state_path}")
    print(f"SINGLE_HARNESS_REPORT={result.report_path}")
    print(f"BEST_HARNESS_REFS={result.best_harness_refs_path}")
    print(f"PUBLISHED_HARNESS_REFS={result.published_harness_refs_path}")
    print(f"BEST_STRICT_TASK_PASS_RATE={result.best_score}")
    print(f"BEST_PASS_HAT_K={result.best_score}")
    print(f"RUN_DIR={run_dir}")
    return 0


def _write_dataset(
    suite_path: Path,
    output_path: Path,
    *,
    task_ids: list[str] | None = None,
    limit: int = 0,
) -> Path:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    tasks = suite.get("validation", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"Evo-Bench suite has no validation tasks: {suite_path}")
    requested = {str(task_id) for task_id in (task_ids or []) if str(task_id)}
    available = {str(task.get("id", "")): task for task in tasks if isinstance(task, dict)}
    missing = sorted(requested - set(available))
    if missing:
        raise ValueError(f"Evo-Bench tasks not present in the frozen suite: {missing}")
    selected = [available[task_id] for task_id in task_ids or []] if requested else tasks
    if limit > 0:
        selected = selected[:limit]
    cases = []
    for task in selected:
        if not isinstance(task, dict):
            raise ValueError("Evo-Bench validation task must be a mapping")
        task_id = str(task.get("id", "") or "")
        domain = str(task.get("domain", "")).lower()
        if not task_id or domain not in {"general", "office"}:
            raise ValueError(f"RSI local no-key suite requires General or Office tasks: {task_id or '<missing>'}")
        source = task_id.split("-", 1)[0]
        if source not in {"claw", "gdpval", "apex"}:
            raise ValueError(f"RSI suite rejects unsupported task source: {task_id}")
        cases.append(
            {
                "case_id": task_id,
                "task_id": task_id,
                "input": str(task.get("prompt", "") or ""),
                "domain": domain,
                "source": source,
                "task_type": str((task.get("metadata") or {}).get("task_type", domain)),
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"dataset_id": "evobench_local_no_key_validation", "cases": cases}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return output_path


def _ensure_source_run(args: argparse.Namespace, source_run: Path) -> tuple[Path, Path | None]:
    """Run the frozen local H0 once when the requested source run is absent."""
    explicit_suite = str(getattr(args, "suite_path", "") or "").strip()
    if explicit_suite:
        suite_path = Path(explicit_suite).expanduser().resolve()
        if not suite_path.is_file():
            raise FileNotFoundError(suite_path)
        return suite_path, None

    domain = str(getattr(args, "domain", "") or "").strip().lower()
    if domain:
        suites = materialize_domain_suites(resolve_evobench_root(args.evobench_root))
        return suites[("validation", domain)], None

    suite_path = source_run / "suite.json"
    h0_result = source_run / "evaluation" / "result.json"
    if suite_path.is_file() and h0_result.is_file():
        return suite_path, h0_result
    if not args.auto_baseline:
        missing = [str(path) for path in (suite_path, h0_result) if not path.is_file()]
        raise FileNotFoundError(f"missing Evo-Bench H0 artifacts: {missing}")

    command = [
        "run",
        "--resume-partial",
        "--run-name",
        source_run.name,
        "--output-dir",
        str(source_run.parent),
        "--task-count",
        str(args.baseline_task_count),
        "--sample-seed",
        str(args.baseline_sample_seed),
        "--rollout-concurrency",
        str(args.rollout_concurrency),
        "--policy-model-config",
        str(Path(args.run_model_config_ref).expanduser().resolve()),
        "--judge-model-config",
        str(Path(args.optimization_model_config_ref).expanduser().resolve()),
    ]
    if str(args.evobench_root).strip():
        command.extend(["--evobench-root", str(args.evobench_root)])
    exit_code = run_local_subset(command)
    if exit_code:
        raise RuntimeError(f"automatic Evo-Bench H0 failed with exit code {exit_code}")
    for path in (suite_path, h0_result):
        if not path.is_file():
            raise RuntimeError(f"automatic Evo-Bench H0 did not produce: {path}")
    return suite_path, h0_result


def _write_seed_refs(evobench_root: Path, output_path: Path) -> Path:
    harness = (evobench_root / "policy_harness_seed").resolve()
    if not harness.is_dir():
        raise FileNotFoundError(harness)
    _write_yaml(
        output_path,
        {
            "version": 1,
            "harness_refs": {"policy_harness": str(harness)},
            "roles": [
                {
                    "role": "policy_harness",
                    "member_name": "policy_harness",
                    "description": "Official Evo-Bench PolicyHarness.",
                    "harness_ref_path": str(harness),
                }
            ],
        },
    )
    return output_path


def _write_config(  # pylint: disable=huawei-too-many-arguments
    *,
    run_dir: Path,
    run_model: Path,
    optimization_model: Path,
    batch_size: int,
    max_epochs: int,
    sibling_candidate_count: int,
    max_issue_attempts: int,
    max_repair_rounds: int,
    improver_policy_ref: str,
) -> Path:
    for path in (run_model, optimization_model):
        if not path.is_file():
            raise FileNotFoundError(path)
    path = run_dir / "single_harness.yaml"
    _write_yaml(
        path,
        {
            "workspace_dir": str(run_dir / "workspace"),
            "max_epochs": max_epochs,
            "model_configs": {
                "evaluation": str(run_model),
                "analysis": str(optimization_model),
                "member_optimization": str(optimization_model),
            },
            "data_loader": {
                "file_pattern": "cases.json",
                "batch_size": batch_size,
                "batch_balance_keys": ["domain", "source", "task_type"],
            },
            "evaluator": {
                "backend": "single_harness",
                "evaluation_method": "evobench-claw-official",
            },
            "evaluation_result_analyzer": {
                # Preserve every per-case diagnosis (up to six) through
                # deterministic aggregation. Candidate evaluation remains
                # bounded separately by max_issue_attempts_per_batch.
                "max_issues": max(20, batch_size * 6),
                "evidence_limit_per_issue": 3,
            },
            "member_optimizer": {
                "max_roles_per_run": 1,
                "max_actions_per_plan": 2,
                "max_issue_attempts_per_batch": max_issue_attempts,
                "max_repair_rounds_per_batch": max_repair_rounds,
                "sibling_candidate_count": sibling_candidate_count,
                "improver_policy_ref": improver_policy_ref,
                "min_attribution_confidence": 0.1,
                "allowed_action_groups": ["prompt"],
                "allowed_prompt_surfaces": ["prompt_section"],
                "candidate_min_target_behavior_delta": 0.0,
            },
            "scheduling": {
                "evaluation_strategy": "hybrid",
                "coordination_strategy": "team_first_single_pass",
                "promotion_policy": "epoch_full_evaluation",
                "full_evaluation_enabled": True,
            },
        },
    )
    return path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", default="rsi_claw20_v1")
    parser.add_argument("--source-run", default=str(DEFAULT_SOURCE_RUN))
    parser.add_argument("--suite-path", default="")
    parser.add_argument("--domain", choices=("general", "office"), default=None)
    parser.add_argument("--execution-mode", choices=("auto", "local", "e2b"), default="auto")
    parser.add_argument("--env-file", default=".local/rsi/evobench.env")
    parser.add_argument("--e2b-template", default="evobench-20260808")
    parser.add_argument("--apex-template", default="evobench-apex-spec")
    parser.add_argument(
        "--auto-baseline",
        action="store_true",
        help="Run the local no-key H0 automatically when --source-run has no completed evaluation.",
    )
    parser.add_argument("--baseline-task-count", type=int, default=40)
    parser.add_argument("--baseline-sample-seed", type=int, default=20260812)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--evobench-root", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Cases optimized sequentially per batch; the epoch still ends with one full-suite checkpoint.",
    )
    parser.add_argument("--max-epochs", type=int, default=1)
    parser.add_argument(
        "--sibling-candidate-count",
        type=int,
        default=1,
        help=(
            "Harness candidates generated per issue. The single-harness optimization default is 1; "
            "values above 1 are reserved for explicit candidate-feedback experiments."
        ),
    )
    parser.add_argument(
        "--max-issue-attempts",
        type=int,
        default=8,
        help="Maximum distinct issue cohorts evaluated per batch; 0 means unlimited.",
    )
    parser.add_argument(
        "--max-repair-rounds",
        type=int,
        default=1,
        help="Feedback-driven repair rounds after the initial candidate attempt.",
    )
    parser.add_argument("--rollout-concurrency", type=int, default=5)
    parser.add_argument("--improver-policy-ref", default="")
    parser.add_argument("--run-model-config-ref", default=str(DEFAULT_RUN_MODEL))
    parser.add_argument("--optimization-model-config-ref", default=str(DEFAULT_OPTIMIZATION_MODEL))
    return parser.parse_args(argv)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _safe_name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return clean.strip("_") or "rsi_claw20"


def _resolve_execution_mode(requested: str, cases: list[dict[str, Any]]) -> str:
    if requested != "auto":
        return requested
    return "e2b" if any(str(case.get("domain", "")).lower() == "office" for case in cases) else "local"


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


if __name__ == "__main__":
    raise SystemExit(main())
