"""Materialize and run Evo-Bench General/Office domain partitions."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

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
from examples.rsi.evobench.subset import DEFAULT_ENV_FILE, DEFAULT_JUDGE_SOURCE, read_env_file


EXPECTED_COUNTS = {
    ("validation", "general"): 32,
    ("validation", "office"): 64,
    ("evaluation", "general"): 64,
    ("evaluation", "office"): 128,
}
EXPECTED_SOURCES = {
    ("validation", "general"): {"claw": 32},
    ("validation", "office"): {"apex": 32, "gdpval": 32},
    ("evaluation", "general"): {"claw": 64},
    ("evaluation", "office"): {"apex": 64, "gdpval": 64},
}


def materialize_domain_suites(root: Path) -> dict[tuple[str, str], Path]:
    """Write four deterministic partitions beside the official release suites."""
    suite_dir = root / "benchmark" / "suites"
    outputs: dict[tuple[str, str], Path] = {}
    all_ids: dict[str, set[str]] = {}
    for split in ("validation", "evaluation"):
        source_path = suite_dir / f"evobench_{split}.json"
        payload = _read_mapping(source_path)
        tasks = payload.get(split)
        if not isinstance(tasks, list):
            raise ValueError(f"official Evo-Bench suite has no {split} list: {source_path}")
        ids = [str(task.get("id") or "") for task in tasks if isinstance(task, Mapping)]
        if not ids or any(not task_id for task_id in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"official Evo-Bench {split} task IDs are missing or duplicated")
        all_ids[split] = set(ids)

        for domain in ("general", "office"):
            selected = [
                dict(task)
                for task in tasks
                if isinstance(task, Mapping) and str(task.get("domain") or "").lower() == domain
            ]
            expected = EXPECTED_COUNTS[(split, domain)]
            if len(selected) != expected:
                raise ValueError(f"expected {expected} {split}/{domain} tasks, found {len(selected)}")
            source_counts = Counter(str(task["id"]).split("-", 1)[0] for task in selected)
            if dict(source_counts) != EXPECTED_SOURCES[(split, domain)]:
                raise ValueError(
                    f"unexpected {split}/{domain} source composition: {dict(sorted(source_counts.items()))}"
                )

            partition = {key: value for key, value in payload.items() if key != split}
            partition.update(
                {
                    "name": f"rsi_evobench_{split}_{domain}",
                    "description": (
                        f"Exact {domain.title()} partition of the official Evo-Bench {split} split ({expected} tasks)."
                    ),
                    split: selected,
                }
            )
            output_path = suite_dir / f"rsi_evobench_{split}_{domain}.json"
            _write_json(output_path, partition)
            outputs[(split, domain)] = output_path

    if all_ids["validation"] & all_ids["evaluation"]:
        raise ValueError("official Evo-Bench validation/evaluation task IDs overlap")
    return outputs


def run_domain(args: argparse.Namespace) -> int:
    root = resolve_evobench_root(args.evobench_root)
    suites = materialize_domain_suites(root)
    _print_suites(suites)
    if args.command == "prepare":
        return 0

    suite_path = suites[(args.split, args.domain)]
    run_root = Path(args.output_dir).expanduser().resolve() / _safe_name(args.run_name)
    evaluation_dir = run_root / "evaluation"
    if evaluation_dir.exists() and any(evaluation_dir.iterdir()):
        raise FileExistsError(f"evaluation output already exists: {evaluation_dir}; choose another --run-name")
    run_root.mkdir(parents=True, exist_ok=True)

    policy = load_local_model(Path(args.policy_model_config))
    judge = load_local_model(Path(args.judge_model_config))
    policy_config = run_root / "configs" / "policy_deepseek_v4_flash.json"
    judge_config = run_root / "configs" / "judge_qwen37_plus.json"
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
    environment = dict(os.environ)
    environment.update(read_env_file(Path(args.env_file)))
    environment.update(_wsl_runtime_credentials())
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EVOBENCH_CLAW_REPO": str((root / "external" / "claw-eval").resolve()),
            "EVOBENCH_E2B_TEMPLATE": args.e2b_template,
            "EVOBENCH_E2B_APEX_TEMPLATE": args.apex_template,
            "RSI_EVOBENCH_POLICY_API_BASE": policy.api_base,
            "RSI_EVOBENCH_POLICY_API_KEY": policy.api_key,
            "RSI_EVOBENCH_JUDGE_API_BASE": judge.api_base,
            "RSI_EVOBENCH_JUDGE_API_KEY": judge.api_key,
        }
    )

    requires_e2b = args.split == "evaluation" or args.domain == "office"
    if requires_e2b:
        if not args.dry_run and not environment.get("E2B_API_KEY", "").strip():
            raise RuntimeError(f"E2B_API_KEY is required for the complete {args.split}/{args.domain} partition")
        environment["EVOBENCH_EXECUTION_MODE"] = "e2b"
        command = _e2b_command(
            root=root,
            split=args.split,
            suite_path=suite_path,
            harness_path=Path(args.frozen_harness).expanduser().resolve()
            if args.frozen_harness
            else root / "policy_harness_seed",
            policy_config=policy_config,
            judge_config=judge_config,
            evaluation_dir=evaluation_dir,
            concurrency=args.rollout_concurrency,
        )
        cwd = root
    else:
        environment["EVOBENCH_EXECUTION_MODE"] = "local"
        environment["EVOBENCH_RESUME_COMPLETED_LOCAL_TRIALS"] = "1"
        command = _local_general_validation_command(
            root=root,
            suite_path=suite_path,
            harness_path=Path(args.frozen_harness).expanduser().resolve()
            if args.frozen_harness
            else root / "policy_harness_seed",
            policy_config=policy_config,
            judge_config=judge_config,
            evaluation_dir=evaluation_dir,
            concurrency=args.rollout_concurrency,
        )
        cwd = root

    _write_json(
        run_root / "manifest.json",
        {
            "split": args.split,
            "domain": args.domain,
            "task_count": EXPECTED_COUNTS[(args.split, args.domain)],
            "source_counts": EXPECTED_SOURCES[(args.split, args.domain)],
            "suite_path": str(suite_path),
            "evaluation_dir": str(evaluation_dir),
            "execution_mode": environment["EVOBENCH_EXECUTION_MODE"],
            "policy_model": policy.model,
            "judge_model": args.judge_model,
            "trials": 3 if args.domain == "general" else 1,
        },
    )
    print(f"RUN_ROOT={run_root}")
    print(f"EVALUATION_DIR={evaluation_dir}")
    print(f"EXECUTION_MODE={environment['EVOBENCH_EXECUTION_MODE']}")
    print("COMMAND=" + subprocess.list2cmdline(command))
    if args.dry_run:
        return 0
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=(
            _wsl_subprocess_environment(
                {
                    name: environment[name]
                    for name in (
                        "RSI_EVOBENCH_POLICY_API_BASE",
                        "RSI_EVOBENCH_POLICY_API_KEY",
                        "RSI_EVOBENCH_JUDGE_API_BASE",
                        "RSI_EVOBENCH_JUDGE_API_KEY",
                    )
                }
            )
            if not requires_e2b
            else environment
        ),
        check=False,
    )
    return int(completed.returncode)


def _local_general_validation_command(
    *,
    root: Path,
    suite_path: Path,
    harness_path: Path,
    policy_config: Path,
    judge_config: Path,
    evaluation_dir: Path,
    concurrency: int,
) -> list[str]:
    claw_repo = root / "external" / "claw-eval"
    python = root / ".claw-venv" / "bin" / "python"
    if shutil.which("wsl.exe") is None or subprocess.run(
        ["wsl.exe", "test", "-x", _to_wsl(python)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode:
        raise RuntimeError("WSL Evo-Bench local runtime is not ready")
    return [
        "wsl.exe",
        "EVOBENCH_EXECUTION_MODE=local",
        "PYTHONUTF8=1",
        "EVOBENCH_RESUME_COMPLETED_LOCAL_TRIALS=1",
        f"PYTHONPATH={_to_wsl(root)}",
        f"EVOBENCH_CLAW_REPO={_to_wsl(claw_repo)}",
        f"PATH={_to_wsl(root / '.claw-venv' / 'bin')}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        _to_wsl(python),
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
        _to_wsl(evaluation_dir),
        "--rollout-concurrency",
        str(max(1, concurrency)),
        "--trials",
        "1",
        "--trials-by-domain",
        "general=3",
    ]


def _e2b_command(
    *,
    root: Path,
    split: str,
    suite_path: Path,
    harness_path: Path,
    policy_config: Path,
    judge_config: Path,
    evaluation_dir: Path,
    concurrency: int,
) -> list[str]:
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"Evo-Bench Windows runtime is missing: {python}")
    command = [str(python), "-m", "evobench"]
    if split == "validation":
        command.extend(["run-validation-eval", "--suite", str(suite_path), "--policy-harness", str(harness_path)])
    else:
        command.extend(["run-evaluation", "--suite", str(suite_path), "--frozen-harness", str(harness_path)])
    command.extend(
        [
            "--policy-model-config",
            str(policy_config),
            "--judge-model-config",
            str(judge_config),
            "--output-dir",
            str(evaluation_dir),
            "--rollout-concurrency",
            str(max(1, concurrency)),
            "--trials",
            "1",
            "--trials-by-domain",
            "general=3",
        ]
    )
    return command


def _read_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    clean = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return clean.strip("_") or "evobench_domain"


def _print_suites(suites: Mapping[tuple[str, str], Path]) -> None:
    for split, domain in EXPECTED_COUNTS:
        print(f"SUITE_{split.upper()}_{domain.upper()}={suites[(split, domain)]}")
        print(f"COUNT_{split.upper()}_{domain.upper()}={EXPECTED_COUNTS[(split, domain)]}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--split", choices=("validation", "evaluation"), default="validation")
    parser.add_argument("--domain", choices=("general", "office"), default="general")
    parser.add_argument("--run-name", default="evobench_domain_v1")
    parser.add_argument("--output-dir", default=".evobench_runs/domain_runs")
    parser.add_argument("--evobench-root", default="")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--policy-model-config", default=str(DEFAULT_POLICY_CONFIG))
    parser.add_argument("--judge-model-config", default=str(DEFAULT_JUDGE_SOURCE))
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--frozen-harness", default="")
    parser.add_argument("--rollout-concurrency", type=int, default=8)
    parser.add_argument("--e2b-template", default=DEFAULT_E2B_TEMPLATE)
    parser.add_argument("--apex-template", default="evobench-apex-spec")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run_domain(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
