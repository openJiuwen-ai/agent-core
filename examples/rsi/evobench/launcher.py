"""Run the official Evo-Bench protocol with the RSI experiment model roles.

The launcher deliberately treats Evo-Bench as the protocol owner. It converts
the existing local Jiuwen model YAML files into credential-free Evo-Bench JSON
configs, then delegates execution and scoring to the official package.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import yaml


DEFAULT_OUTPUT_ROOT = Path(".evobench_runs")
DEFAULT_POLICY_CONFIG = Path(".local/rsi/models/token_plan_deepseek_v4_flash_single_harness.yaml")
DEFAULT_EVOLVER_CONFIG = Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml")
DEFAULT_JUDGE_MODEL = "Qwen3.7-Plus"
DEFAULT_E2B_TEMPLATE = "evobench-20260808"
DEFAULT_APEX_TEMPLATE = "evobench-apex-spec"

SMOKE_TASK_IDS = {
    "validation": [
        "bc-en-1176",
        "hle-670980821053a19619c30869",
        "gdpval-1e5a1d7f-12c1-48c6-afd9-82257b3f2409",
        "apex-0b6e147c84754379a4e8f3a9057336f8",
        "claw-T007zh_todo_management",
    ],
    "evaluation": [
        "bc-en-1174",
        "hle-66f6f494e56a5e5bc0b5a7af",
        "gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae",
        "apex-8985fd777093438eb1e1a51af2ca6142",
        "claw-T015zh_kb_search",
    ],
}


@dataclass(frozen=True)
class LocalModel:
    api_base: str
    api_key: str
    model: str


@dataclass(frozen=True)
class PreparedRun:
    evobench_root: Path
    output_root: Path
    policy_config: Path
    evolver_config: Path
    judge_config: Path
    validation_suite: Path
    evaluation_suite: Path
    environment: dict[str, str]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prepared = prepare_run(args)
    _print_prepared(prepared)
    if args.command == "prepare":
        missing = missing_runtime_requirements(prepared)
        print("RUNTIME_READY=" + ("true" if not missing else "false"))
        if missing:
            print("MISSING_RUNTIME_REQUIREMENTS=" + ",".join(missing))
        return 0

    if args.command == "verify":
        command = [
            str(_evobench_python(prepared.evobench_root)),
            "-m",
            "unittest",
            "-v",
            "tests.test_release_artifacts",
            "tests.test_new_policy_harness_seed",
            "tests.test_sandbox_security",
            "tests.test_model_clients",
        ]
        return _run(command, prepared, dry_run=args.dry_run)

    missing = missing_runtime_requirements(prepared)
    if missing and not args.dry_run:
        raise RuntimeError("Evo-Bench formal execution is not ready; missing: " + ", ".join(missing))

    command = build_command(args, prepared)
    _write_run_manifest(args, prepared, command)
    return _run(command, prepared, dry_run=args.dry_run)


def prepare_run(args: argparse.Namespace) -> PreparedRun:
    root = resolve_evobench_root(args.evobench_root)
    _validate_release(root)
    output_root = Path(args.output_dir).expanduser().resolve()
    config_root = output_root / "generated_configs"
    config_root.mkdir(parents=True, exist_ok=True)

    policy = load_local_model(Path(args.policy_model_config))
    evolver = load_local_model(Path(args.evolver_model_config))
    policy_config = config_root / "policy_deepseek_v4_flash.json"
    evolver_config = config_root / "evolver_glm52.json"
    judge_config = config_root / "judge_qwen37_plus.json"

    write_evobench_model_config(
        policy_config,
        api_base_env="RSI_EVOBENCH_POLICY_API_BASE",
        api_key_env="RSI_EVOBENCH_POLICY_API_KEY",
        model=policy.model,
        role="policy",
    )
    write_evobench_model_config(
        evolver_config,
        api_base_env="RSI_EVOBENCH_EVOLVER_API_BASE",
        api_key_env="RSI_EVOBENCH_EVOLVER_API_KEY",
        model=evolver.model,
        role="evolver",
    )
    write_evobench_model_config(
        judge_config,
        api_base_env="RSI_EVOBENCH_JUDGE_API_BASE",
        api_key_env="RSI_EVOBENCH_JUDGE_API_KEY",
        model=args.judge_model,
        role="judge",
    )

    environment = dict(os.environ)
    environment.update(_wsl_runtime_credentials())
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EVOBENCH_EXECUTION_MODE": "e2b",
            "EVOBENCH_E2B_TEMPLATE": args.e2b_template,
            "EVOBENCH_E2B_APEX_TEMPLATE": args.apex_template,
            "EVOBENCH_CLAW_REPO": str((root / "external" / "claw-eval").resolve()),
            "RSI_EVOBENCH_POLICY_API_BASE": policy.api_base,
            "RSI_EVOBENCH_POLICY_API_KEY": policy.api_key,
            "RSI_EVOBENCH_EVOLVER_API_BASE": evolver.api_base,
            "RSI_EVOBENCH_EVOLVER_API_KEY": evolver.api_key,
            "RSI_EVOBENCH_JUDGE_API_BASE": evolver.api_base,
            "RSI_EVOBENCH_JUDGE_API_KEY": evolver.api_key,
        }
    )

    if args.command == "smoke":
        validation_suite, evaluation_suite = write_smoke_suites(root)
    else:
        validation_suite = root / "benchmark" / "suites" / "evobench_validation.json"
        evaluation_suite = root / "benchmark" / "suites" / "evobench_evaluation.json"

    return PreparedRun(
        evobench_root=root,
        output_root=output_root,
        policy_config=policy_config,
        evolver_config=evolver_config,
        judge_config=judge_config,
        validation_suite=validation_suite,
        evaluation_suite=evaluation_suite,
        environment=environment,
    )


def resolve_evobench_root(value: str) -> Path:
    candidates: list[Path] = []
    if value.strip():
        candidates.append(Path(value).expanduser())
    env_root = os.environ.get("EVOBENCH_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    workspace = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            workspace.parent / "Evo-Bench-official" / "Evo-Bench-main",
            workspace.parent / "Evo-Bench",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "evobench" / "cli.py").is_file() and (
            resolved / "benchmark" / "suites" / "evobench_validation.json"
        ).is_file():
            return resolved
    raise FileNotFoundError("Evo-Bench repository not found; pass --evobench-root")


def load_local_model(path: Path) -> LocalModel:
    resolved = path.expanduser().resolve()
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"model config must be a mapping: {resolved}")
    client = payload.get("model_client_config")
    request = payload.get("model_request_config")
    if not isinstance(client, dict) or not isinstance(request, dict):
        raise ValueError(f"model config has no Jiuwen client/request sections: {resolved}")
    values = {
        "api_base": str(client.get("api_base", "")).strip(),
        "api_key": str(client.get("api_key", "")).strip(),
        "model": str(request.get("model", "")).strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError(f"model config {resolved} is missing: {', '.join(missing)}")
    return LocalModel(**values)


def write_evobench_model_config(
    path: Path,
    *,
    api_base_env: str,
    api_key_env: str,
    model: str,
    role: str,
) -> None:
    if role not in {"policy", "evolver", "judge"}:
        raise ValueError(f"unknown Evo-Bench model role: {role}")
    context_window = 256_000 if role == "policy" else 1_000_000
    payload: dict[str, Any] = {
        "provider": "openai-compatible",
        "api_base_env": api_base_env,
        "api_key_env": api_key_env,
        "model": model,
        "temperature": 0.0 if role == "judge" else 1.0,
        "max_output_tokens": 65_536,
        "timeout_seconds": 1_200 if role == "judge" else 600,
        "require_api_key": True,
        "context_window_tokens": context_window,
    }
    if role != "judge":
        payload["reasoning_effort"] = "max"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_smoke_suites(root: Path) -> tuple[Path, Path]:
    suite_root = root / "benchmark" / "suites"
    outputs: dict[str, Path] = {}
    for split in ("validation", "evaluation"):
        source = suite_root / f"evobench_{split}.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        records = {str(task["id"]): task for task in data.get(split, [])}
        missing = [task_id for task_id in SMOKE_TASK_IDS[split] if task_id not in records]
        if missing:
            raise ValueError(f"official {split} suite is missing smoke tasks: {missing}")
        selected = [records[task_id] for task_id in SMOKE_TASK_IDS[split]]
        output = {
            "name": f"rsi_evobench_smoke_{split}",
            "description": "RSI five-source protocol smoke suite; not a paper score.",
            split: selected,
        }
        if data.get("assets_dir"):
            output["assets_dir"] = data["assets_dir"]
        path = suite_root / f".rsi_evobench_smoke_{split}.json"
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        outputs[split] = path
    return outputs["validation"], outputs["evaluation"]


def missing_runtime_requirements(prepared: PreparedRun) -> list[str]:
    missing: list[str] = []
    for name in ("E2B_API_KEY", "SERPER_API_KEY"):
        if not prepared.environment.get(name, "").strip():
            missing.append(name)
    claw = Path(prepared.environment["EVOBENCH_CLAW_REPO"])
    if not (claw / "mock_services").is_dir():
        missing.append("EVOBENCH_CLAW_REPO")
    if not _evobench_python(prepared.evobench_root).is_file():
        missing.append("EVOBENCH_PYTHON")
    return missing


def _wsl_runtime_credentials() -> dict[str, str]:
    """Read benchmark infrastructure credentials already configured in WSL.

    Windows is the host orchestrator for the existing RSI worktree, while the
    user's benchmark infrastructure variables live in their WSL shell. Only
    the two named variables are requested and no value is printed or persisted.
    """
    if os.name != "nt":
        return {}
    if os.environ.get("E2B_API_KEY") and os.environ.get("SERPER_API_KEY"):
        return {}
    command = [
        "wsl.exe",
        "bash",
        "-ic",
        'printf \'%s\\n%s\' "$E2B_API_KEY" "$SERPER_API_KEY"',
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    values = completed.stdout.decode("utf-8", errors="ignore").splitlines()
    if len(values) != 2:
        return {}
    result: dict[str, str] = {}
    for name, value in zip(("E2B_API_KEY", "SERPER_API_KEY"), values, strict=True):
        clean = value.strip().strip("\ufeff")
        if clean:
            result[name] = clean
    return result


def build_command(args: argparse.Namespace, prepared: PreparedRun) -> list[str]:
    python = str(_evobench_python(prepared.evobench_root))
    common = [
        "--policy-model-config",
        str(prepared.policy_config),
        "--judge-model-config",
        str(prepared.judge_config),
        "--trials-by-domain",
        args.trials_by_domain,
        "--rollout-concurrency",
        str(args.rollout_concurrency),
    ]
    run_name = _safe_run_name(args.run_name)
    if args.command == "baseline":
        return [
            python,
            "-m",
            "evobench",
            "run-validation-eval",
            "--suite",
            str(prepared.validation_suite),
            "--policy-harness",
            str(prepared.evobench_root / "policy_harness_seed"),
            "--output-dir",
            str(prepared.output_root / "validation_evals" / run_name),
            *common,
        ]

    max_iterations = args.max_iterations if args.max_iterations is not None else (2 if args.command == "smoke" else 20)
    max_steps = args.max_steps if args.max_steps is not None else (20 if args.command == "smoke" else 1_000)
    run_dir = prepared.output_root / "runs" / run_name
    evaluation_dir = prepared.output_root / "evaluations" / run_name
    return [
        python,
        "-m",
        "evobench",
        "run-evolve",
        "--suite",
        str(prepared.validation_suite),
        "--evaluation-suite",
        str(prepared.evaluation_suite),
        "--seed-policy-harness",
        str(prepared.evobench_root / "policy_harness_seed"),
        "--evolver-model-config",
        str(prepared.evolver_config),
        "--run-dir",
        str(run_dir),
        "--evaluation-output-dir",
        str(evaluation_dir),
        "--max-iterations",
        str(max_iterations),
        "--max-steps",
        str(max_steps),
        "--evaluation-concurrency",
        str(args.evaluation_concurrency),
        "--sandbox-ttl-minutes",
        str(args.sandbox_ttl_minutes),
        "--sandbox-cpu",
        str(args.sandbox_cpu),
        "--sandbox-memory",
        str(args.sandbox_memory),
        "--sandbox-disk",
        str(args.sandbox_disk),
        "--eval-sandbox-cpu",
        str(args.eval_sandbox_cpu),
        "--eval-sandbox-memory",
        str(args.eval_sandbox_memory),
        "--eval-sandbox-disk",
        str(args.eval_sandbox_disk),
        *common,
    ]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "smoke", "baseline", "evolve"])
    parser.add_argument("--evobench-root", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default="rsi_evobench")
    parser.add_argument("--policy-model-config", default=str(DEFAULT_POLICY_CONFIG))
    parser.add_argument("--evolver-model-config", default=str(DEFAULT_EVOLVER_CONFIG))
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--e2b-template", default=DEFAULT_E2B_TEMPLATE)
    parser.add_argument("--apex-template", default=DEFAULT_APEX_TEMPLATE)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--rollout-concurrency", type=int, default=20)
    parser.add_argument("--evaluation-concurrency", type=int, default=20)
    parser.add_argument("--trials-by-domain", default="general=3")
    parser.add_argument("--sandbox-ttl-minutes", type=int, default=2_880)
    parser.add_argument("--sandbox-cpu", type=int, default=1)
    parser.add_argument("--sandbox-memory", type=int, default=4)
    parser.add_argument("--sandbox-disk", type=int, default=4)
    parser.add_argument("--eval-sandbox-cpu", type=int, default=8)
    parser.add_argument("--eval-sandbox-memory", type=int, default=64)
    parser.add_argument("--eval-sandbox-disk", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _validate_release(root: Path) -> None:
    validation = json.loads((root / "benchmark" / "suites" / "evobench_validation.json").read_text(encoding="utf-8"))
    evaluation = json.loads((root / "benchmark" / "suites" / "evobench_evaluation.json").read_text(encoding="utf-8"))
    validation_ids = {str(task["id"]) for task in validation.get("validation", [])}
    evaluation_ids = {str(task["id"]) for task in evaluation.get("evaluation", [])}
    if len(validation_ids) != 160 or len(evaluation_ids) != 448:
        raise ValueError("Evo-Bench release must contain 160 validation and 448 evaluation tasks")
    if validation_ids & evaluation_ids:
        raise ValueError("Evo-Bench validation and evaluation suites are not disjoint")


def _evobench_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _run(command: list[str], prepared: PreparedRun, *, dry_run: bool) -> int:
    print("COMMAND=" + subprocess.list2cmdline(command))
    if dry_run:
        return 0
    completed = subprocess.run(
        command,
        cwd=prepared.evobench_root,
        env=prepared.environment,
        check=False,
    )
    return int(completed.returncode)


def _write_run_manifest(args: argparse.Namespace, prepared: PreparedRun, command: list[str]) -> None:
    run_name = _safe_run_name(args.run_name)
    path = prepared.output_root / "manifests" / f"{run_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol": "Evo-Bench v0.1.0",
        "command": args.command,
        "evobench_root": str(prepared.evobench_root),
        "validation_suite": str(prepared.validation_suite),
        "evaluation_suite": str(prepared.evaluation_suite),
        "policy_config": str(prepared.policy_config),
        "evolver_config": str(prepared.evolver_config),
        "judge_config": str(prepared.judge_config),
        "command_argv": command,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_run_name(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")
    if not clean:
        clean = "rsi_evobench"
    if len(clean) <= 64:
        return clean
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
    return f"{clean[:55]}-{digest}"


def _print_prepared(prepared: PreparedRun) -> None:
    print(f"EVOBENCH_ROOT={prepared.evobench_root}")
    print(f"VALIDATION_SUITE={prepared.validation_suite}")
    print(f"EVALUATION_SUITE={prepared.evaluation_suite}")
    print(f"POLICY_CONFIG={prepared.policy_config}")
    print(f"EVOLVER_CONFIG={prepared.evolver_config}")
    print(f"JUDGE_CONFIG={prepared.judge_config}")


if __name__ == "__main__":
    raise SystemExit(main())
