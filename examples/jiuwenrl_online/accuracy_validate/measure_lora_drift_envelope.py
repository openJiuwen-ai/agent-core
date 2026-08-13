# coding: utf-8

"""Measure and validate repeated online-RL LoRA numeric drift.

This script is intentionally separate from normal pytest ST because the default
baseline mode runs 10 full PPO training jobs and can take a long time.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

from st_utils import (
    AGENT_CORE_ROOT,
    FIXTURE_DIR,
    ST_DIR,
    adapter_model_file,
    adapter_manifest,
    compare_adapter_tensors,
    run_direct_training,
)


DEFAULT_FIXTURE = FIXTURE_DIR / "a5_training_trajectories.json"
DEFAULT_MODEL_PATH = "/data1/lll/models/Qwen3-4B-Thinking-2507"
DEFAULT_MODEL_NAME = "Qwen3-4B-Thinking-2507"
DATA_DIR = ST_DIR / "data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated fixed-trajectory PPO training and compute pairwise LoRA drift.",
    )
    parser.add_argument(
        "--mode",
        choices=("baseline", "validate"),
        default="baseline",
        help="baseline writes observed GPU drift; validate checks against a baseline JSON.",
    )
    parser.add_argument("--runs", type=int, default=int(os.getenv("ST_TEST_LORA_DRIFT_RUNS", "10")))
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--model-path", default=os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH))
    parser.add_argument("--model-name", default=os.getenv("MODEL_NAME", DEFAULT_MODEL_NAME))
    parser.add_argument("--work-dir", default=os.getenv("ST_TEST_LORA_DRIFT_WORK_DIR", ""))
    parser.add_argument("--output", default=os.getenv("ST_TEST_LORA_DRIFT_OUTPUT", ""))
    parser.add_argument("--baseline-json", default=os.getenv("ST_TEST_LORA_DRIFT_BASELINE", ""))
    parser.add_argument(
        "--reference-adapter-dir",
        action="append",
        default=[],
        help=(
            "Reference adapter dir for validate mode. Can be repeated. "
            "If omitted, validate mode uses adapter dirs recorded in --baseline-json."
        ),
    )
    parser.add_argument("--train-gpu", default=os.getenv("TRAIN_GPU", "6,7"))
    parser.add_argument("--ppo-config-path", default=os.getenv("PPO_CONFIG_PATH", ""))
    parser.add_argument("--ppo-samples-per-step", default=os.getenv("ONLINE_RL_PPO_SAMPLES_PER_STEP", "4"))
    parser.add_argument("--train-threshold", default=os.getenv("TRAIN_THRESHOLD", "4"))
    parser.add_argument(
        "--max-abs-margin-ratio",
        type=float,
        default=float(os.getenv("ST_TEST_LORA_MAX_ABS_MARGIN_RATIO", "0.10")),
        help="Suggested max_abs threshold margin over observed baseline maximum.",
    )
    parser.add_argument(
        "--mean-abs-margin-ratio",
        type=float,
        default=float(os.getenv("ST_TEST_LORA_MEAN_ABS_MARGIN_RATIO", "0.10")),
        help="Suggested mean_abs threshold margin over observed baseline maximum.",
    )
    parser.add_argument(
        "--absolute-epsilon",
        type=float,
        default=float(os.getenv("ST_TEST_LORA_DRIFT_ABS_EPS", "1e-6")),
        help="Small additive slack for thresholds.",
    )
    parser.add_argument(
        "--skip-ray-stop",
        action="store_true",
        help="Do not run ray stop --force between training runs.",
    )
    return parser.parse_args()


def _float_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def _stop_ray() -> None:
    subprocess.run(
        ["bash", "-lc", "ray stop --force >/dev/null 2>&1 || true"],
        check=False,
        cwd=str(AGENT_CORE_ROOT),
    )


def _configure_env(args: argparse.Namespace) -> None:
    os.environ["MODEL_PATH"] = args.model_path
    os.environ["MODEL_NAME"] = args.model_name
    os.environ["TRAIN_GPU"] = args.train_gpu
    os.environ["ONLINE_RL_PPO_SAMPLES_PER_STEP"] = str(args.ppo_samples_per_step)
    os.environ["TRAIN_THRESHOLD"] = str(args.train_threshold)
    os.environ.setdefault("PYTHONHASHSEED", os.getenv("ST_TEST_SEED", "20260713"))
    os.environ.setdefault("ONLINE_RL_DETERMINISTIC_SEED", os.getenv("ST_TEST_SEED", "20260713"))
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    os.environ.setdefault("ONLINE_RL_DEVICE_BACKEND", "cuda")
    os.environ.setdefault("ONLINE_RL_VISIBLE_DEVICES_ENV", "CUDA_VISIBLE_DEVICES")
    if args.ppo_config_path:
        os.environ["PPO_CONFIG_PATH"] = args.ppo_config_path


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("_")


def _default_output(args: argparse.Namespace, work_dir: Path) -> Path:
    if args.output:
        return Path(args.output).resolve()
    if args.mode == "baseline":
        return (
            DATA_DIR
            / f"{_safe_name(args.model_name)}_gpu_lora_drift_baseline.json"
        ).resolve()
    return (work_dir / f"lora_drift_{args.mode}.json").resolve()


def _adapter_tensor_stats(adapter_dir: Path) -> dict[str, Any]:
    import torch

    model_file = adapter_model_file(adapter_dir)
    if model_file.suffix == ".safetensors":
        from safetensors.torch import load_file

        state = load_file(str(model_file), device="cpu")
    else:
        state = torch.load(str(model_file), map_location="cpu")
    tensor_count = 0
    numel = 0
    abs_sum = 0.0
    abs_max = 0.0
    l2_sum = 0.0
    for tensor in state.values():
        t = tensor.detach().float()
        if t.numel() == 0:
            continue
        abs_t = t.abs()
        tensor_count += 1
        numel += int(t.numel())
        abs_sum += float(abs_t.sum().item())
        abs_max = max(abs_max, float(abs_t.max().item()))
        l2_sum += float((t * t).sum().item())
    return {
        "tensor_count": tensor_count,
        "numel": numel,
        "adapter_abs_max": abs_max,
        "adapter_abs_mean": abs_sum / numel if numel else 0.0,
        "adapter_l2": l2_sum ** 0.5,
    }


def _run_adapters(args: argparse.Namespace, work_dir: Path) -> list[dict[str, Any]]:
    fixture = Path(args.fixture).resolve()
    adapters = []
    for idx in range(args.runs):
        run_name = f"drift_{idx:02d}"
        if not args.skip_ray_stop:
            _stop_ray()
        adapter_dir = run_direct_training(fixture=fixture, work_dir=work_dir, run_name=run_name)
        manifest = adapter_manifest(adapter_dir)
        tensor_stats = _adapter_tensor_stats(adapter_dir)
        adapters.append(
            {
                "index": idx,
                "run_name": run_name,
                "adapter_dir": str(adapter_dir),
                "manifest": manifest,
                "tensor_stats": tensor_stats,
            }
        )
        print(
            f"[lora-drift] finished run {idx + 1}/{args.runs}: "
            f"{manifest['adapter_model_sha256']} {adapter_dir}",
            flush=True,
        )
    if not args.skip_ray_stop:
        _stop_ray()
    return adapters


def _compare_all(adapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for left, right in itertools.combinations(adapters, 2):
        diff = compare_adapter_tensors(Path(left["adapter_dir"]), Path(right["adapter_dir"]))
        comparisons.append(
            {
                "left_index": left["index"],
                "right_index": right["index"],
                "left_sha256": left["manifest"]["adapter_model_sha256"],
                "right_sha256": right["manifest"]["adapter_model_sha256"],
                "tensor_diff": diff,
            }
        )
    return comparisons


def _compare_against_references(
    adapters: list[dict[str, Any]],
    reference_adapter_dirs: list[str],
) -> list[dict[str, Any]]:
    comparisons = []
    references = [Path(item).resolve() for item in reference_adapter_dirs if item]
    for adapter in adapters:
        adapter_dir = Path(adapter["adapter_dir"])
        for ref_idx, reference_dir in enumerate(references):
            diff = compare_adapter_tensors(adapter_dir, reference_dir)
            comparisons.append(
                {
                    "adapter_index": adapter["index"],
                    "reference_index": ref_idx,
                    "adapter_dir": str(adapter_dir),
                    "reference_adapter_dir": str(reference_dir),
                    "tensor_diff": diff,
                }
            )
    return comparisons


def _reference_dirs_from_baseline(baseline: dict[str, Any]) -> list[str]:
    refs = []
    for item in baseline.get("adapters") or []:
        adapter_dir = item.get("adapter_dir")
        if adapter_dir and Path(adapter_dir).exists():
            refs.append(str(adapter_dir))
    return refs


def _summarize(args: argparse.Namespace, adapters: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    max_abs_values = [float(item["tensor_diff"]["max_abs"]) for item in comparisons]
    mean_abs_values = [float(item["tensor_diff"]["mean_abs"]) for item in comparisons]
    different_tensor_values = [int(item["tensor_diff"]["different_tensors"]) for item in comparisons]
    adapter_abs_max_values = [float(item["tensor_stats"]["adapter_abs_max"]) for item in adapters]
    adapter_abs_mean_values = [float(item["tensor_stats"]["adapter_abs_mean"]) for item in adapters]
    adapter_l2_values = [float(item["tensor_stats"]["adapter_l2"]) for item in adapters]
    max_abs_stats = _float_stats(max_abs_values)
    mean_abs_stats = _float_stats(mean_abs_values)
    adapter_abs_max_stats = _float_stats(adapter_abs_max_values)
    adapter_abs_mean_stats = _float_stats(adapter_abs_mean_values)
    adapter_l2_stats = _float_stats(adapter_l2_values)
    suggested_thresholds = {
        "max_abs": max_abs_stats["max"] * (1.0 + args.max_abs_margin_ratio) + args.absolute_epsilon,
        "mean_abs": mean_abs_stats["max"] * (1.0 + args.mean_abs_margin_ratio) + args.absolute_epsilon,
        "adapter_abs_max": adapter_abs_max_stats["max"] * (1.0 + args.max_abs_margin_ratio) + args.absolute_epsilon,
        "adapter_abs_mean": adapter_abs_mean_stats["max"] * (1.0 + args.mean_abs_margin_ratio) + args.absolute_epsilon,
        "adapter_l2": adapter_l2_stats["max"] * (1.0 + args.mean_abs_margin_ratio) + args.absolute_epsilon,
    }
    return {
        "schema_version": 1,
        "mode": args.mode,
        "runs": len(adapters),
        "pair_count": len(comparisons),
        "model": {
            "name": args.model_name,
            "path": args.model_path,
        },
        "fixture": str(Path(args.fixture).resolve()),
        "train_gpu": args.train_gpu,
        "ppo_samples_per_step": str(args.ppo_samples_per_step),
        "train_threshold": str(args.train_threshold),
        "seed": os.getenv("ONLINE_RL_DETERMINISTIC_SEED"),
        "threshold_policy": {
            "max_abs_margin_ratio": args.max_abs_margin_ratio,
            "mean_abs_margin_ratio": args.mean_abs_margin_ratio,
            "absolute_epsilon": args.absolute_epsilon,
        },
        "observed": {
            "max_abs": max_abs_stats,
            "mean_abs": mean_abs_stats,
            "adapter_abs_max": adapter_abs_max_stats,
            "adapter_abs_mean": adapter_abs_mean_stats,
            "adapter_l2": adapter_l2_stats,
            "different_tensors": {
                "min": min(different_tensor_values) if different_tensor_values else 0,
                "max": max(different_tensor_values) if different_tensor_values else 0,
            },
        },
        "suggested_thresholds": suggested_thresholds,
        "adapters": adapters,
        "comparisons": comparisons,
    }


def _summarize_cross_platform(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    max_abs_values = [float(item["tensor_diff"]["max_abs"]) for item in comparisons]
    mean_abs_values = [float(item["tensor_diff"]["mean_abs"]) for item in comparisons]
    return {
        "pair_count": len(comparisons),
        "max_abs": _float_stats(max_abs_values),
        "mean_abs": _float_stats(mean_abs_values),
        "comparisons": comparisons,
    }


def _validate(report: dict[str, Any], baseline: dict[str, Any], cross_report: dict[str, Any] | None) -> dict[str, Any]:
    thresholds = baseline.get("suggested_thresholds") or {}
    max_abs_threshold = float(thresholds.get("max_abs", 0.0))
    mean_abs_threshold = float(thresholds.get("mean_abs", 0.0))
    adapter_abs_max_threshold = float(thresholds["adapter_abs_max"])
    adapter_abs_mean_threshold = float(thresholds["adapter_abs_mean"])
    adapter_l2_threshold = float(thresholds["adapter_l2"])
    max_abs_observed = float(report["observed"]["max_abs"]["max"])
    mean_abs_observed = float(report["observed"]["mean_abs"]["max"])
    adapter_abs_max_observed = float(report["observed"]["adapter_abs_max"]["max"])
    adapter_abs_mean_observed = float(report["observed"]["adapter_abs_mean"]["max"])
    adapter_l2_observed = float(report["observed"]["adapter_l2"]["max"])
    if report["pair_count"] > 0:
        internal_passed = max_abs_observed <= max_abs_threshold and mean_abs_observed <= mean_abs_threshold
    else:
        internal_passed = True
    adapter_stats_passed = (
        adapter_abs_max_observed <= adapter_abs_max_threshold
        and adapter_abs_mean_observed <= adapter_abs_mean_threshold
        and adapter_l2_observed <= adapter_l2_threshold
    )
    cross_passed = None
    cross_observed = None
    if cross_report is not None:
        cross_observed = {
            "max_abs": float(cross_report["max_abs"]["max"]),
            "mean_abs": float(cross_report["mean_abs"]["max"]),
        }
        cross_passed = (
            cross_observed["max_abs"] <= max_abs_threshold
            and cross_observed["mean_abs"] <= mean_abs_threshold
        )
    passed = internal_passed and adapter_stats_passed
    if cross_passed is not None:
        passed = passed and cross_passed
    validation = {
        "passed": passed,
        "internal_passed": internal_passed,
        "adapter_stats_passed": adapter_stats_passed,
        "cross_platform_passed": cross_passed,
        "thresholds": {
            "max_abs": max_abs_threshold,
            "mean_abs": mean_abs_threshold,
            "adapter_abs_max": adapter_abs_max_threshold,
            "adapter_abs_mean": adapter_abs_mean_threshold,
            "adapter_l2": adapter_l2_threshold,
        },
        "internal_observed_max": {
            "max_abs": max_abs_observed,
            "mean_abs": mean_abs_observed,
        },
        "adapter_stats_observed_max": {
            "adapter_abs_max": adapter_abs_max_observed,
            "adapter_abs_mean": adapter_abs_mean_observed,
            "adapter_l2": adapter_l2_observed,
        },
        "cross_platform_observed_max": cross_observed,
    }
    report["validation"] = validation
    return validation


def main() -> None:
    args = parse_args()
    if args.mode == "baseline" and args.runs < 2:
        raise SystemExit("--runs must be >= 2 in baseline mode")
    if args.mode == "validate" and args.runs < 1:
        raise SystemExit("--runs must be >= 1 in validate mode")
    if args.mode == "validate" and not args.baseline_json:
        args.baseline_json = str(
            DATA_DIR / f"{_safe_name(args.model_name)}_gpu_lora_drift_baseline.json"
        )
    if args.mode == "validate" and not Path(args.baseline_json).exists():
        raise SystemExit(f"baseline JSON not found: {args.baseline_json}")

    _configure_env(args)
    work_dir = Path(args.work_dir or f"/tmp/jiuwen_lora_drift_{args.mode}").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    output = _default_output(args, work_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    baseline = None
    reference_adapter_dirs = list(args.reference_adapter_dir)
    if args.mode == "validate":
        baseline = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        if not reference_adapter_dirs:
            reference_adapter_dirs = _reference_dirs_from_baseline(baseline)

    adapters = _run_adapters(args, work_dir)
    comparisons = _compare_all(adapters)
    report = _summarize(args, adapters, comparisons)
    cross_report = None
    if reference_adapter_dirs:
        cross_report = _summarize_cross_platform(
            _compare_against_references(adapters, reference_adapter_dirs)
        )
        report["cross_platform"] = cross_report

    if args.mode == "validate":
        validation = _validate(report, baseline or {}, cross_report)
        print(f"[lora-drift] validation={json.dumps(validation, ensure_ascii=False)}", flush=True)

    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[lora-drift] report={output}", flush=True)

    if args.mode == "validate" and not report["validation"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
