"""Run one official Evo-Bench validation task in WSL local isolation."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from examples.rsi.evobench.launcher import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_POLICY_CONFIG,
    load_local_model,
    resolve_evobench_root,
    write_evobench_model_config,
)

DEFAULT_TASK_ID = "hle-670980821053a19619c30869"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = resolve_evobench_root(args.evobench_root)
    output_dir = Path(args.output_dir).expanduser().resolve() / args.task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_path = write_single_task_suite(root, task_id=args.task_id, output_dir=output_dir)

    policy = load_local_model(Path(args.policy_model_config))
    judge_source = load_local_model(Path(args.judge_model_config))
    policy_config = output_dir / "policy.json"
    judge_config = output_dir / "judge.json"
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
        model=args.judge_model,
        role="judge",
    )

    command = build_wsl_single_task_command(
        root=root,
        suite_path=suite_path,
        output_dir=output_dir / "evaluation",
        policy_config=policy_config,
        judge_config=judge_config,
    )
    environment = {
        "RSI_EVOBENCH_POLICY_API_BASE": policy.api_base,
        "RSI_EVOBENCH_POLICY_API_KEY": policy.api_key,
        "RSI_EVOBENCH_JUDGE_API_BASE": judge_source.api_base,
        "RSI_EVOBENCH_JUDGE_API_KEY": judge_source.api_key,
    }
    print(f"TASK_ID={args.task_id}")
    print(f"SINGLE_TASK_SUITE={suite_path}")
    print(f"OUTPUT_DIR={output_dir / 'evaluation'}")
    if args.dry_run:
        print("WSL_COMMAND=" + " ".join(shlex.quote(item) for item in command))
        return 0
    evaluation_dir = output_dir / "evaluation"
    if evaluation_dir.exists():
        shutil.rmtree(evaluation_dir)
    completed = subprocess.run(
        ["wsl.exe", *command],
        check=False,
        env=_wsl_subprocess_environment(environment),
    )
    if completed.returncode:
        return int(completed.returncode)
    print_single_task_result(output_dir / "evaluation")
    return 0


def write_single_task_suite(root: Path, *, task_id: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = root / "benchmark" / "suites" / "evobench_validation.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    matches = [task for task in data.get("validation", []) if str(task.get("id")) == task_id]
    if not matches:
        raise ValueError(f"Evo-Bench validation task not found: {task_id}")
    payload: dict[str, Any] = {
        "name": f"rsi_single_{task_id}",
        "description": "One-task RSI migration smoke; not a paper score.",
        "validation": matches,
    }
    if data.get("assets_dir"):
        # The generated suite is outside benchmark/suites, so keep the official
        # attachment location explicit rather than retaining a broken relative path.
        payload["assets_dir"] = _to_wsl(source.parent / data["assets_dir"])
    path = output_dir / "suite.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_wsl_single_task_command(
    *,
    root: Path,
    suite_path: Path,
    output_dir: Path,
    policy_config: Path,
    judge_config: Path,
) -> list[str]:
    python = _to_wsl(root / ".claw-venv" / "bin" / "python")
    return [
        "EVOBENCH_EXECUTION_MODE=local",
        "PYTHONUTF8=1",
        f"PYTHONPATH={_to_wsl(root)}",
        f"PATH={_to_wsl(root / '.claw-venv' / 'bin')}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        python,
        "-m",
        "evobench",
        "run-validation-eval",
        "--suite",
        _to_wsl(suite_path),
        "--policy-harness",
        _to_wsl(root / "policy_harness_seed"),
        "--policy-model-config",
        _to_wsl(policy_config),
        "--judge-model-config",
        _to_wsl(judge_config),
        "--output-dir",
        _to_wsl(output_dir),
        "--rollout-concurrency",
        "1",
        "--trials",
        "1",
    ]


def print_single_task_result(output_dir: Path) -> None:
    result_path = output_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    task = (result.get("tasks") or [{}])[0]
    print(f"RESULT_PATH={result_path}")
    print(f"SCORE={float(task.get('score', 0.0)):.4f}")
    print(f"PASSED={str(bool(task.get('passed'))).lower()}")
    print(f"EXIT_REASON={task.get('exit_reason', '')}")
    print(f"TRAJECTORY_PATH={task.get('trajectory_path', '')}")


def _to_wsl(path: Path) -> str:
    resolved = path.expanduser().resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise ValueError(f"expected an absolute Windows path: {resolved}")
    tail = PurePosixPath(*resolved.parts[1:]).as_posix()
    return f"/mnt/{drive}/{tail}"


def _wsl_subprocess_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Pass credentials through WSLENV instead of exposing them in argv."""
    environment = dict(os.environ)
    environment.update(values)
    forwarded = set(values)
    inherited = [
        item for item in environment.get("WSLENV", "").split(":") if item and item.split("/", 1)[0] not in forwarded
    ]
    environment["WSLENV"] = ":".join([*inherited, *values])
    return environment


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--evobench-root", default="")
    parser.add_argument("--output-dir", default=".evobench_runs/single_task")
    parser.add_argument("--policy-model-config", default=str(DEFAULT_POLICY_CONFIG))
    parser.add_argument(
        "--judge-model-config", default=str(Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml"))
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
