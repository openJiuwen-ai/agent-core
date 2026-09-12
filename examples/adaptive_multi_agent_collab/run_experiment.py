"""CLI for the compact Version 24 adaptive-collaboration pilot."""
from __future__ import annotations

import argparse, asyncio, csv, json, logging, os, shlex, time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from string import Template
from typing import Any, Sequence
from openjiuwen.core.common.logging import llm_logger, logger, prompt_logger
from .config import DEFAULT_ARTIFACT_ROOT, ExperimentConfig
from .evaluation import evaluate_trajectories
from .experiment import AdaptiveCollaborationExperiment, load_dataset_splits
from .openjiuwen_client import OpenJiuwenClient
from .prompts import PROMPT_HASH, ROLE_PROMPTS
from .schemas import JsonlCallCache, Trajectory
from .weighting import (WeightingConfig, evaluate_weighting_loss, load_weighting_checkpoint,
                        prepare_weighting_examples, train_weighting_model)

for _logger in (llm_logger, logger, prompt_logger):
    _logger.set_level(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent
PYTHON = "/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python"
MODULE = "examples.adaptive_multi_agent_collab.run_experiment"
STATUS_MOCK = "SYNTHETIC OFFLINE MOCK OUTPUTS - NOT EXPERIMENTAL EVIDENCE"
STATUS_REAL, STATUS_PARTIAL = "REAL LLM OUTPUTS", "PARTIAL REAL OUTPUTS"


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("smoke", "generate", "train", "evaluate", "report", "all"))
    parser.add_argument("--offline-mock", action="store_true")
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    for name in ("train-size", "val-size", "test-size"):
        parser.add_argument(f"--{name}", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--max-api-calls", type=int, default=650)
    parser.add_argument("--request-timeout", type=float, default=90.0)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    return parser.parse_args(argv)


def _config(args: argparse.Namespace) -> ExperimentConfig:
    smoke, artifact_root = args.command == "smoke", args.artifact_root.resolve()
    if not artifact_root.is_relative_to(ROOT):
        raise ValueError(f"--artifact-root must remain under {ROOT}")
    return ExperimentConfig(
        train_size=args.train_size if args.train_size is not None else (2 if smoke else 30),
        val_size=args.val_size if args.val_size is not None else (1 if smoke else 10),
        test_size=args.test_size if args.test_size is not None else (2 if smoke else 20),
        seed=args.seed, concurrency=args.concurrency, max_api_calls=args.max_api_calls,
        request_timeout=args.request_timeout, offline_mock=args.offline_mock,
        force_regenerate=args.force_regenerate, artifact_root=artifact_root,
        epochs=args.epochs, patience=args.patience, learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
    )


def _resume(config: ExperimentConfig, command: str | None = None) -> str:
    command = command or ("smoke" if (config.train_size, config.val_size, config.test_size) == (2, 1, 2) else "all")
    args = [PYTHON, "-m", MODULE, command, "--train-size", str(config.train_size),
            "--val-size", str(config.val_size), "--test-size", str(config.test_size),
            "--seed", str(config.seed), "--concurrency", str(config.concurrency),
            "--max-api-calls", str(config.max_api_calls), "--request-timeout", str(config.request_timeout),
            "--epochs", str(config.epochs), "--patience", str(config.patience),
            "--learning-rate", str(config.learning_rate), "--weight-decay", str(config.weight_decay),
            "--artifact-root", str(config.artifact_root)]
    if config.offline_mock:
        args.append("--offline-mock")
    return shlex.join(args)


def _directories(config: ExperimentConfig) -> dict[str, Path]:
    paths = {name: config.mode_root / name for name in ("cache", "checkpoints", "results")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _safe_error(exc: BaseException) -> str:
    text, secret = f"{type(exc).__name__}: {exc}", os.environ.get("API_KEY")
    return text.replace(secret, "[REDACTED]") if secret else text


def _read_trajectories(path: Path, fingerprint: str | None = None) -> list[Trajectory]:
    latest: dict[str, Trajectory] = {}
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = Trajectory.from_dict(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if fingerprint is None or item.run_fingerprint == fingerprint:
            latest[item.example.example_id] = item
    return list(latest.values())


def _cache_diagnostics(path: Path, fingerprint: str) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "records": 0, "valid_records": 0, "invalid_records": 0,
        "malformed_lines": 0, "attempt_errors": 0, "stages": {}, "parse_methods": {},
    }
    if not path.exists():
        return diagnostics
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            diagnostics["malformed_lines"] += 1
            continue
        settings = record.get("key", {}).get("generation_settings", {})
        if settings.get("run_fingerprint") != fingerprint:
            continue
        diagnostics["records"] += 1
        field = "valid_records" if record.get("valid") else "invalid_records"
        diagnostics[field] += 1
        diagnostics["attempt_errors"] += len(record.get("attempt_errors") or [])
        stage = str(record.get("key", {}).get("stage", "unknown"))
        method = str(record.get("parsed", {}).get("parse_method", "unknown"))
        diagnostics["stages"][stage] = diagnostics["stages"].get(stage, 0) + 1
        diagnostics["parse_methods"][method] = diagnostics["parse_methods"].get(method, 0) + 1
    return diagnostics


def _manifest(config: ExperimentConfig) -> dict[str, Any]:
    path = config.mode_root / "cache" / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}; run generate first")
    return json.loads(path.read_text(encoding="utf-8"))


def _partitions(config: ExperimentConfig, strict: bool = True) -> tuple[dict[str, list[Trajectory]], dict[str, Any]]:
    manifest = _manifest(config)
    fingerprint = manifest.get("run_fingerprint")
    if not fingerprint:
        raise RuntimeError("Legacy manifest lacks provenance; rerun generate")
    trajectories = {item.example.example_id: item for item in _read_trajectories(
        config.mode_root / "cache" / "trajectories.jsonl", fingerprint)}
    selected = manifest["selected_ids"]
    partitions = {name: [trajectories[item] for item in ids if item in trajectories]
                  for name, ids in selected.items()}
    missing = [item for ids in selected.values() for item in ids if item not in trajectories]
    failed = [item.example.example_id for values in partitions.values() for item in values if item.failure]
    if strict and (missing or failed or manifest.get("completed_examples") != manifest.get("expected_examples")):
        raise RuntimeError(f"cached trajectory set is incomplete: missing={len(missing)}, failed={len(failed)}; "
                           f"resume with: {manifest.get('resume_command', _resume(config))}")
    return partitions, manifest


async def _generate(
    config: ExperimentConfig, resume_command: str,
) -> tuple[dict[str, list[Trajectory]], dict[str, Any]]:
    paths, splits = _directories(config), load_dataset_splits(config)
    manifest_path = paths["cache"] / "manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    client = OpenJiuwenClient.from_environment(
        offline_mock=config.offline_mock, concurrency=config.concurrency,
        max_api_calls=config.max_api_calls, request_timeout=config.request_timeout,
        max_retries=config.max_retries, backoff_base=config.backoff_base, seed=config.seed)
    if previous.get("base_client_identity") == client.base_client_identity():
        client.apply_generation_adjustments(previous.get("generation_adjustments", []))
    cache = JsonlCallCache(paths["cache"] / "calls.jsonl", config.mode, config.force_regenerate)
    experiment, started = AdaptiveCollaborationExperiment(config, client, cache), time.perf_counter()
    await experiment.generate(splits)
    fingerprint = experiment.run_fingerprint
    trajectories = {item.example.example_id: item for item in _read_trajectories(
        paths["cache"] / "trajectories.jsonl", fingerprint)}
    selected = {name: [item.example_id for item in values] for name, values in splits.items()}
    expected = sum(map(len, selected.values()))
    complete = sum(identifier in trajectories and not trajectories[identifier].failure
                   for ids in selected.values() for identifier in ids)
    status = STATUS_MOCK if config.offline_mock else (STATUS_REAL if complete == expected else STATUS_PARTIAL)
    manifest = {
        "cache_schema_version": 2, "data_status": status,
        "dataset": "synthetic" if config.offline_mock else "tau/commonsense_qa",
        "selected_ids": selected, "sizes": {name: len(values) for name, values in selected.items()},
        "source_splits": ({"train": "synthetic_train", "validation": "synthetic_validation",
                           "test": "synthetic_test"} if config.offline_mock else
                          {"train": "train", "validation": "train", "test": "validation"}),
        "seed": config.seed, "provider": client.provider, "model_name": client.model_name,
        "prompt_hash": PROMPT_HASH, "run_fingerprint": fingerprint,
        "base_client_identity": client.base_client_identity(),
        "non_secret_fingerprint": client.non_secret_fingerprint(),
        "generation_settings": client.effective_generation_settings(),
        "generation_adjustments": list(client.generation_adjustments),
        "encoder": "deterministic normalized signed hashing (BLAKE2b), 96 dimensions",
        "routing": ["g_01", "g_12", "g_20"],
        "api_key_present": bool(os.environ.get("API_KEY")) if not config.offline_mock else None,
        "api_attempts_this_run": client.budget.count, "cache_valid_records": len(cache.records),
        "completed_examples": complete, "expected_examples": expected,
        "generation_seconds": time.perf_counter() - started,
        "resume_command": _resume(config, resume_command),
    }
    _json(manifest_path, manifest)
    return _partitions(config, strict=False)


def _weighting_config(config: ExperimentConfig) -> WeightingConfig:
    return WeightingConfig(
        query_dim=config.query_dim, history_dim=config.history_dim,
        agent_embedding_dim=config.agent_embedding_dim, hidden_dim=config.hidden_dim,
        dropout=config.dropout, learning_rate=config.learning_rate,
        weight_decay=config.weight_decay, epochs=config.epochs,
        patience=config.patience, seed=config.seed)


def _train(config: ExperimentConfig, partitions: dict[str, list[Trajectory]],
           manifest: dict[str, Any]) -> dict[str, Any]:
    paths, weighting = _directories(config), _weighting_config(config)
    train, validation = (prepare_weighting_examples(partitions[name], weighting)
                         for name in ("train", "validation"))
    result = train_weighting_model(train, validation, paths["checkpoints"] / "weighting.pt", weighting)
    with (paths["results"] / "training_history.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("epoch", "train_loss", "validation_loss"))
        writer.writeheader(); writer.writerows(result.history)
    summary = {
        "device": result.device, "best_epoch": result.best_epoch,
        "best_train_loss": result.best_train_loss,
        "best_validation_loss": result.best_validation_loss,
        "training_seconds": result.training_seconds, "checkpoint": result.checkpoint_path,
        "configuration": asdict(weighting), "train_examples": len(train),
        "validation_examples": len(validation),
        "selection": "minimum validation cross-entropy; held-out test unused",
        "data_status": manifest["data_status"],
    }
    _json(paths["results"] / "training_summary.json", summary)
    return summary


def _evaluate(config: ExperimentConfig, partitions: dict[str, list[Trajectory]],
              manifest: dict[str, Any]) -> dict[str, Any]:
    paths = _directories(config)
    model, payload = load_weighting_checkpoint(paths["checkpoints"] / "weighting.pt")
    evaluation = evaluate_trajectories(partitions["test"], model, seed=config.seed)
    test = prepare_weighting_examples(partitions["test"], model.config)
    training_path = paths["results"] / "training_summary.json"
    training = json.loads(training_path.read_text(encoding="utf-8")) if training_path.exists() else {}
    training["test_loss"] = evaluate_weighting_loss(model, test) if test else None
    training.setdefault("best_epoch", payload.get("best_epoch"))
    rows = evaluation["predictions"]
    changes = [{"example_id": row["example_id"], "uniform": row["collaboration_uniform"],
                "learned": row["collaboration_learned"],
                "effect": ("helped" if row["collaboration_learned"] == row["gold"] else
                           "hurt" if row["collaboration_uniform"] == row["gold"] else "neither")}
               for row in rows if row["collaboration_uniform"] != row["collaboration_learned"]]
    generation_keys = ("generation_settings", "generation_adjustments", "api_attempts_this_run",
                       "cache_valid_records", "completed_examples", "expected_examples",
                       "generation_seconds", "resume_command")
    generation = {key: manifest.get(key) for key in generation_keys}
    generation["current_fingerprint_cache"] = _cache_diagnostics(
        paths["cache"] / "calls.jsonl", manifest["run_fingerprint"])
    summary = {
        "data_status": manifest["data_status"], "manifest": str(paths["cache"] / "manifest.json"),
        "run_fingerprint": manifest["run_fingerprint"],
        "dataset": manifest["dataset"], "sizes": manifest["sizes"], "seed": manifest["seed"],
        "provider": manifest["provider"], "model_name": manifest["model_name"],
        "encoder": manifest["encoder"], "generation": generation,
        "training": training, "evaluation": evaluation, "learned_weight_changes": changes,
        "identical_terminal_answers": sum(len(set(x for x in row["terminal_answers"] if x)) == 1 for row in rows),
        "runtime_failures": [{"example_id": item.example.example_id, "error": item.failure}
                             for item in partitions["test"] if item.failure],
    }
    with (paths["results"] / "predictions.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    metrics = evaluation["methods"]
    fields = ["method", *sorted({key for value in metrics.values() for key in value})]
    with (paths["results"] / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for method, values in metrics.items():
            writer.writerow({"method": method, **values})
    _json(paths["results"] / "summary.json", summary)
    return summary


def _plots(summary: dict[str, Any], results: Path) -> list[str]:
    errors: list[str] = []
    try:
        matplotlib_cache = results.parents[2] / "tmp" / "matplotlib"
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        methods = summary["evaluation"]["methods"]
        names, accuracy = list(methods), [methods[name].get("accuracy") or 0 for name in methods]
        plt.figure(figsize=(8, 4)); plt.bar(names, accuracy); plt.ylim(0, 1)
        plt.xticks(rotation=20, ha="right"); plt.ylabel("Accuracy"); plt.tight_layout()
        plt.savefig(results / "accuracy_comparison.png"); plt.close()
        with (results / "training_history.csv").open(encoding="utf-8") as stream:
            history = list(csv.DictReader(stream))
        plt.figure(figsize=(7, 4))
        plt.plot([float(x["epoch"]) for x in history], [float(x["train_loss"]) for x in history], label="train")
        plt.plot([float(x["epoch"]) for x in history], [float(x["validation_loss"]) for x in history], label="validation")
        plt.xlabel("Epoch"); plt.ylabel("Cross-entropy"); plt.legend(); plt.tight_layout()
        plt.savefig(results / "training_curve.png"); plt.close()
        transitions = summary["evaluation"].get("transitions", {})
        labels = sorted({label for counts in transitions.values() for label in counts})
        bottom = [0.0] * 3; plt.figure(figsize=(8, 4))
        for label in labels:
            values = [transitions.get(str(agent), {}).get(label, 0) for agent in range(3)]
            plt.bar(["Agent 0", "Agent 1", "Agent 2"], values, bottom=bottom, label=label)
            bottom = [left + right for left, right in zip(bottom, values)]
        if labels:
            plt.legend(fontsize=7)
        plt.ylabel("Transitions"); plt.tight_layout()
        plt.savefig(results / "answer_transitions.png"); plt.close()
        weights = summary["evaluation"]["weights"]["average_by_agent"]
        plt.figure(figsize=(6, 4))
        bars = plt.bar(["Agent 0", "Agent 1", "Agent 2"], weights)
        plt.bar_label(bars, labels=[f"{value:.4f}" for value in weights])
        plt.ylim(0, 1); plt.ylabel("Average learned weight"); plt.tight_layout()
        plt.savefig(results / "average_agent_weights.png"); plt.close()
    except Exception as exc:
        errors.append(_safe_error(exc))
    return errors


def _report(config: ExperimentConfig, summary: dict[str, Any] | None = None) -> Path:
    results = _directories(config)["results"]
    summary = summary or json.loads((results / "summary.json").read_text(encoding="utf-8"))
    evaluation, training, generation = summary["evaluation"], summary["training"], summary.get("generation", {})
    methods = "\n".join(
        f"| {name} | {value.get('correct')} / {value.get('evaluated')} | {value.get('accuracy')} | "
        f"{value.get('bootstrap_95_ci')} | {value.get('average_calls')} | {value.get('average_total_tokens')} | "
        f"{value.get('average_wall_latency', value.get('average_latency'))} | "
        f"{value.get('average_provider_latency')} | {value.get('average_total_cost')} |"
        for name, value in evaluation["methods"].items())
    parsing = {name: {"fallback_rate": value.get("parse_fallback_rate"),
                      "failure_rate": value.get("parse_failure_rate"), "failed_predictions": value.get("failed")}
               for name, value in evaluation["methods"].items()}
    usage_details = {
        name: {key: value.get(key) for key in (
            "average_input_tokens", "average_output_tokens", "average_total_tokens",
            "average_cached_tokens", "tie_rate")}
        for name, value in evaluation["methods"].items()
    }
    cases = {"successful": {}, "harmful": {}}
    for agent, groups in evaluation.get("transition_examples", {}).items():
        cases["successful"][agent] = groups.get("incorrect -> correct", [])
        cases["harmful"][agent] = groups.get("correct -> incorrect", [])
    plot_errors = _plots(summary, results)
    cache_diagnostics = generation.get("current_fingerprint_cache", {})
    jdump = lambda value: json.dumps(value, indent=2)
    context: defaultdict[str, Any] = defaultdict(lambda: None, summary)
    for prefix, values in (
        ("training", training), ("generation", generation),
        ("weights", evaluation["weights"]), ("cache", cache_diagnostics),
    ):
        context.update({f"{prefix}_{key}": value for key, value in values.items()})
    context.update({
        "methods": methods, "role_prompts_json": jdump(ROLE_PROMPTS),
        "agents_json": jdump(evaluation["agents"]), "agreement_json": jdump(evaluation["agreement"]),
        "oracles_json": jdump(evaluation["oracles"]),
        "transitions_json": jdump(evaluation.get("transitions", {})),
        "transition_percentages_json": jdump(evaluation.get("transition_percentages", {})),
        "cases_json": jdump(cases),
        "learned_weight_changes_json": jdump(summary.get("learned_weight_changes", [])),
        "runtime_failures_json": jdump(summary.get("runtime_failures", [])),
        "usage_details": usage_details, "parsing": parsing, "plot_errors": plot_errors or "none",
    })
    template = Template((ROOT / "report_template.md").read_text(encoding="utf-8"))
    report = template.substitute(context)
    path = results / "report.md"
    path.write_text(report, encoding="utf-8")
    return path


def _progress_report(config: ExperimentConfig, manifest: dict[str, Any], error: str | None = None) -> Path:
    results, status = _directories(config)["results"], manifest["data_status"]
    value = {"data_status": status, "dataset": manifest["dataset"], "sizes": manifest["sizes"],
             "completed_examples": manifest["completed_examples"],
             "expected_examples": manifest["expected_examples"],
             "api_attempts_this_run": manifest["api_attempts_this_run"],
             "error": error, "resume_command": manifest["resume_command"]}
    _json(results / "summary.json", value)
    path = results / "report.md"
    path.write_text(
        f"# Adaptive collaboration progress\n\n**{status}**\n\n"
        f"Completed {value['completed_examples']} / {value['expected_examples']} trajectories; "
        f"API attempts this run: {value['api_attempts_this_run']}.\n\n"
        f"Error: `{error or 'generation incomplete'}`\n\nResume:\n```bash\n{value['resume_command']}\n```\n",
        encoding="utf-8")
    return path


def _failure_report(config: ExperimentConfig, error: str) -> None:
    paths = _directories(config)
    try:
        manifest = _manifest(config)
    except Exception:
        manifest = {}
    completed = int(manifest.get("completed_examples", 0) or 0)
    status = STATUS_MOCK if config.offline_mock else (STATUS_PARTIAL if completed else "NOT RUN")
    resume = manifest.get("resume_command", _resume(config))
    _json(paths["results"] / "summary.json", {
        "data_status": status, "error": error, "completed_cached_trajectories": completed,
        "resume_command": resume})
    (paths["results"] / "report.md").write_text(
        f"# Adaptive collaboration run\n\n**{status}**\n\nError: `{error}`\n\n"
        f"Resume:\n```bash\n{resume}\n```\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> None:
    config, started = _config(args), time.perf_counter()
    if args.command == "report":
        path = _report(config)
        print(json.dumps({"status": "report regenerated", "report": str(path)})); return
    if args.command in {"smoke", "generate", "all"}:
        partitions, manifest = await _generate(config, args.command)
        if manifest["data_status"] not in {STATUS_REAL, STATUS_MOCK}:
            path = _progress_report(config, manifest)
            print(json.dumps({"status": manifest["data_status"], "report": str(path),
                              "resume": manifest["resume_command"]})); return
    else:
        partitions, manifest = _partitions(config)
    if args.command == "generate":
        print(json.dumps({"status": manifest["data_status"], "resume": manifest["resume_command"]})); return
    training = _train(config, partitions, manifest) if args.command in {"smoke", "train", "all"} else None
    if args.command == "train":
        print(json.dumps({"status": manifest["data_status"], "best_epoch": training["best_epoch"]})); return
    summary_path = _directories(config)["results"] / "summary.json"
    try:
        previous = json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        previous = {}
    summary = _evaluate(config, partitions, manifest)
    previous_wall = previous.get("command_wall_seconds") if (
        args.command == "evaluate"
        and previous.get("run_fingerprint") == manifest["run_fingerprint"]
    ) else None
    summary["command_wall_seconds"] = previous_wall or time.perf_counter() - started
    _json(summary_path, summary)
    if args.command == "evaluate":
        print(json.dumps({"status": summary["data_status"], "evaluated": summary["sizes"]["test"]})); return
    report = _report(config, summary)
    print(json.dumps({"status": summary["data_status"], "report": str(report),
                      "best_epoch": (training or summary["training"]).get("best_epoch")}))


def main(argv: Sequence[str] | None = None) -> None:
    args, config = _arguments(argv), None
    try:
        config = _config(args)
        asyncio.run(_run(args))
    except BaseException as exc:
        error = _safe_error(exc)
        if config is not None:
            _failure_report(config, error)
        print(error)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
