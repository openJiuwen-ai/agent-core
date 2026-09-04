"""Prepare and run a reproducible local-only Evo-Bench validation subset."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

from examples.rsi.evobench.launcher import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_POLICY_CONFIG,
    load_local_model,
    resolve_evobench_root,
    write_evobench_model_config,
)
from examples.rsi.evobench.run_one import _to_wsl, _wsl_subprocess_environment

DEFAULT_ENV_FILE = Path(".local/rsi/evobench.env")
DEFAULT_OUTPUT_ROOT = Path(".evobench_runs/local_claw20")
DEFAULT_JUDGE_SOURCE = Path(".local/rsi/models/bailian_glm5_1_single_harness.yaml")
DEFAULT_SAMPLE_SEED = 20_260_812
TASK_COUNT = 20
LOCAL_SOURCE_PREFIXES = ("claw", "gdpval")
_MODEL_ENV_NAMES = (
    "RSI_EVOBENCH_POLICY_API_BASE",
    "RSI_EVOBENCH_POLICY_API_KEY",
    "RSI_EVOBENCH_JUDGE_API_BASE",
    "RSI_EVOBENCH_JUDGE_API_KEY",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    prepared = prepare_subset(args)
    _print_prepared(prepared)
    if args.command == "prepare":
        missing = missing_requirements(prepared)
        print("INFRA_READY=" + str(not missing).lower())
        if missing:
            print("MISSING_REQUIREMENTS=" + ",".join(missing))
        return 0

    report = run_preflight(prepared, live=True)
    _write_json(prepared["run_root"] / "preflight.json", report)
    if not report["ready"]:
        print("PREFLIGHT_READY=false")
        for check in report["checks"]:
            if not check["ok"]:
                print(f"FAILED_CHECK={check['name']}:{check['detail']}")
        return 2
    print("PREFLIGHT_READY=true")
    if args.command == "preflight":
        return 0

    evaluation_dir = prepared["evaluation_dir"]
    if evaluation_dir.exists() and any(evaluation_dir.iterdir()):
        if not args.resume_partial or (evaluation_dir / "result.json").is_file():
            raise FileExistsError(f"evaluation output already exists: {evaluation_dir}; choose another --run-name")
        print(f"RESUMING_PARTIAL_EVALUATION={evaluation_dir}")
    command = build_validation_command(prepared, concurrency=args.rollout_concurrency)
    print("COMMAND=" + subprocess.list2cmdline(command))
    completed = subprocess.run(
        command,
        cwd=prepared["root"],
        env=_wsl_subprocess_environment({name: str(prepared["environment"][name]) for name in _MODEL_ENV_NAMES}),
        check=False,
    )
    return int(completed.returncode)


def prepare_subset(args: argparse.Namespace) -> dict[str, Any]:
    root = resolve_evobench_root(args.evobench_root)
    run_root = Path(args.output_dir).expanduser().resolve() / _safe_name(args.run_name)
    run_root.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(read_env_file(Path(args.env_file)))

    policy = load_local_model(Path(args.policy_model_config))
    judge_source = load_local_model(Path(args.judge_model_config))
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EVOBENCH_EXECUTION_MODE": "local",
            "EVOBENCH_CLAW_REPO": str((root / "external" / "claw-eval").resolve()),
            "RSI_EVOBENCH_POLICY_API_BASE": policy.api_base,
            "RSI_EVOBENCH_POLICY_API_KEY": policy.api_key,
            "RSI_EVOBENCH_JUDGE_API_BASE": judge_source.api_base,
            "RSI_EVOBENCH_JUDGE_API_KEY": judge_source.api_key,
        }
    )

    config_root = run_root / "configs"
    policy_config = config_root / "policy_deepseek_v4_flash.json"
    judge_config = config_root / "judge_qwen37_plus.json"
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
    suite_path = write_validation_subset(
        root,
        output_path=run_root / "suite.json",
        seed=args.sample_seed,
        task_count=args.task_count,
    )
    prepared = {
        "root": root,
        "run_root": run_root,
        "evaluation_dir": run_root / "evaluation",
        "python": root / ".claw-venv" / "bin" / "python",
        "suite_path": suite_path,
        "policy_config": policy_config,
        "judge_config": judge_config,
        "environment": environment,
        "policy_model": policy.model,
        "judge_model": args.judge_model,
    }
    prepared["task_count"] = args.task_count
    _write_manifest(prepared, sample_seed=args.sample_seed)
    return prepared


def select_validation_subset(
    tasks: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SAMPLE_SEED,
    task_count: int = TASK_COUNT,
) -> list[dict[str, Any]]:
    if task_count < 1:
        raise ValueError("task_count must be positive")
    rng = random.Random(seed)
    claw_pool = sorted(
        (task for task in tasks if str(task.get("id", "")).startswith("claw-")),
        key=lambda task: str(task["id"]),
    )
    office_pool = sorted(
        (task for task in tasks if str(task.get("id", "")).startswith("gdpval-")),
        key=lambda task: str(task["id"]),
    )
    if task_count > len(claw_pool) + len(office_pool):
        raise ValueError(
            f"local no-key pool has only {len(claw_pool) + len(office_pool)} tasks "
            f"({len(claw_pool)} claw + {len(office_pool)} gdpval)"
        )
    claw_count = min(task_count, len(claw_pool))
    office_count = task_count - claw_count
    selected = rng.sample(claw_pool, claw_count)
    if office_count:
        selected.extend(rng.sample(office_pool, office_count))
    return sorted(selected, key=lambda task: str(task["id"]))


def write_validation_subset(
    root: Path,
    *,
    output_path: Path,
    seed: int,
    task_count: int = TASK_COUNT,
) -> Path:
    source = root / "benchmark" / "suites" / "evobench_validation.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    selected = select_validation_subset(payload["validation"], seed=seed, task_count=task_count)
    source_counts = Counter(str(task["id"]).split("-", 1)[0] for task in selected)
    result: dict[str, Any] = {
        "name": f"rsi_validation{task_count}_seed_{seed}",
        "description": (
            f"Reproducible local-only sample of {task_count} validation tasks "
            f"({source_counts.get('claw', 0)} Claw + {source_counts.get('gdpval', 0)} GDPval). "
            "APEX and web-search-dependent sources are excluded. Not a paper leaderboard score."
        ),
        "validation": selected,
    }
    if payload.get("assets_dir"):
        result["assets_dir"] = _to_wsl((source.parent / payload["assets_dir"]).resolve())
    _write_json(output_path, result)
    return output_path


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if name and not os.environ.get(name):
            result[name] = value
    return result


def missing_requirements(prepared: Mapping[str, Any]) -> list[str]:
    environment = prepared["environment"]
    missing: list[str] = []
    if shutil.which("wsl.exe") is None:
        missing.append("WSL")
    elif subprocess.run(
        ["wsl.exe", "test", "-x", _to_wsl(Path(prepared["python"]))],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode:
        missing.append("EVOBENCH_CLAW_PYTHON")
    claw = Path(environment["EVOBENCH_CLAW_REPO"])
    if not (claw / "mock_services").is_dir():
        missing.append("EVOBENCH_CLAW_REPO")
    return missing


def run_preflight(prepared: Mapping[str, Any], *, live: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    environment = prepared["environment"]

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    missing = missing_requirements(prepared)
    record("required_credentials_and_runtime", not missing, "ok" if not missing else ",".join(missing))
    suite = json.loads(Path(prepared["suite_path"]).read_text(encoding="utf-8"))
    counts = Counter(str(task["id"]).split("-", 1)[0] for task in suite["validation"])
    task_count = int(prepared.get("task_count", TASK_COUNT))
    record(
        "local_no_key_sampling",
        len(suite["validation"]) == task_count
        and set(counts).issubset(LOCAL_SOURCE_PREFIXES)
        and counts.get("claw", 0) == min(task_count, 32)
        and counts.get("gdpval", 0) == max(0, task_count - 32),
        json.dumps(dict(sorted(counts.items()))),
    )
    if counts.get("gdpval", 0):
        assets_dir = str(suite.get("assets_dir") or "")
        assets_ready = (
            bool(assets_dir)
            and subprocess.run(
                ["wsl.exe", "test", "-d", assets_dir],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )
        record("gdpval_assets", assets_ready, assets_dir or "missing assets_dir")
    import_check = subprocess.run(
        [
            "wsl.exe",
            "EVOBENCH_EXECUTION_MODE=local",
            "PYTHONUTF8=1",
            f"PYTHONPATH={_to_wsl(Path(prepared['root']))}",
            f"EVOBENCH_CLAW_REPO={_to_wsl(Path(environment['EVOBENCH_CLAW_REPO']))}",
            _to_wsl(Path(prepared["python"])),
            "-c",
            "import shutil; import evobench,openai,claw_eval; assert shutil.which('unshare')",
        ],
        cwd=prepared["root"],
        env=_wsl_subprocess_environment({name: str(prepared["environment"][name]) for name in _MODEL_ENV_NAMES}),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    record(
        "wsl_local_runtime",
        import_check.returncode == 0,
        "ok" if import_check.returncode == 0 else import_check.stderr[-500:],
    )
    if live:
        record(
            "policy_model_endpoint",
            *_probe_openai_endpoint(
                environment["RSI_EVOBENCH_POLICY_API_BASE"],
                environment["RSI_EVOBENCH_POLICY_API_KEY"],
                prepared["policy_model"],
            ),
        )
        record(
            "judge_model_endpoint",
            *_probe_openai_endpoint(
                environment["RSI_EVOBENCH_JUDGE_API_BASE"],
                environment["RSI_EVOBENCH_JUDGE_API_KEY"],
                prepared["judge_model"],
            ),
        )
    return {
        "ready": all(check["ok"] for check in checks),
        "live": bool(live and not missing),
        "created_at": time.time(),
        "checks": checks,
    }


def build_validation_command(prepared: Mapping[str, Any], *, concurrency: int) -> list[str]:
    return [
        "wsl.exe",
        "EVOBENCH_EXECUTION_MODE=local",
        "PYTHONUTF8=1",
        "EVOBENCH_RESUME_COMPLETED_LOCAL_TRIALS=1",
        f"PYTHONPATH={_to_wsl(Path(prepared['root']))}",
        f"EVOBENCH_CLAW_REPO={_to_wsl(Path(prepared['environment']['EVOBENCH_CLAW_REPO']))}",
        f"PATH={_to_wsl(Path(prepared['root']) / '.claw-venv' / 'bin')}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        _to_wsl(Path(prepared["python"])),
        "-m",
        "evobench",
        "run-validation-eval",
        "--suite",
        _to_wsl(Path(prepared["suite_path"])),
        "--policy-harness",
        _to_wsl(Path(prepared["root"]) / "policy_harness_seed"),
        "--policy-model-config",
        _to_wsl(Path(prepared["policy_config"])),
        "--judge-model-config",
        _to_wsl(Path(prepared["judge_config"])),
        "--output-dir",
        _to_wsl(Path(prepared["evaluation_dir"])),
        "--rollout-concurrency",
        str(max(1, concurrency)),
        "--trials",
        "1",
        "--trials-by-domain",
        "general=3",
    ]


def _probe_openai_endpoint(api_base: str, api_key: str, expected_model: str) -> tuple[bool, str]:
    req = request.Request(
        api_base.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with request.urlopen(req, timeout=30) as response:  # noqa: S310 - configured API endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, error.HTTPError, ValueError) as exc:
        return False, f"models request failed: {type(exc).__name__}"
    model_ids = {str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)}
    return expected_model in model_ids, f"model_present={str(expected_model in model_ids).lower()}"


def _write_manifest(prepared: Mapping[str, Any], *, sample_seed: int) -> None:
    suite = json.loads(Path(prepared["suite_path"]).read_text(encoding="utf-8"))
    tasks = suite["validation"]
    source_counts = Counter(str(task["id"]).split("-", 1)[0] for task in tasks)
    _write_json(
        Path(prepared["run_root"]) / "manifest.json",
        {
            "protocol": "Evo-Bench v0.1.0 local no-key validation subset",
            "sample_seed": sample_seed,
            "task_count": len(tasks),
            "source_counts": dict(sorted(source_counts.items())),
            "excluded_dependencies": ["E2B_API_KEY", "SERPER_API_KEY"],
            "task_ids": [task["id"] for task in tasks],
            "policy_model": prepared["policy_model"],
            "judge_model": prepared["judge_model"],
            "general_trials": 3,
            "other_trials": 1,
        },
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    clean = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return clean.strip("_") or "validation20"


def _print_prepared(prepared: Mapping[str, Any]) -> None:
    print(f"RUN_ROOT={prepared['run_root']}")
    print(f"SUITE_PATH={prepared['suite_path']}")
    print(f"MANIFEST_PATH={Path(prepared['run_root']) / 'manifest.json'}")
    print(f"EVALUATION_DIR={prepared['evaluation_dir']}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "run"))
    parser.add_argument("--run-name", default="local_claw20_v1")
    parser.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED)
    parser.add_argument("--task-count", type=int, default=TASK_COUNT)
    parser.add_argument("--rollout-concurrency", type=int, default=5)
    parser.add_argument(
        "--resume-partial",
        action="store_true",
        help="Reuse complete local General trials from an interrupted evaluation.",
    )
    parser.add_argument("--evobench-root", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--policy-model-config", default=str(DEFAULT_POLICY_CONFIG))
    parser.add_argument("--judge-model-config", default=str(DEFAULT_JUDGE_SOURCE))
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
