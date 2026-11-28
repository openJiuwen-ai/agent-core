from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from video_memory.config import load_config
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.evaluation.metrics import evaluate_qa
from video_memory.llm.api_client import make_model_client
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.schemas import AnswerResult, QAItem


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--input-dir", default="outputs/ablation")
    parser.add_argument("--output-dir", default="outputs/ablation_answer_from_existing")
    parser.add_argument("--snapshot-summary", default="outputs/traces/snapshot_summary.json")
    parser.add_argument(
        "--rank-methods",
        nargs="+",
        default=["field", "api"],
        choices=["field", "embedding", "qwen_embedding", "api"],
    )
    parser.add_argument("--max-hops", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--answer-mode", choices=["text", "vision"], default="vision")
    args = parser.parse_args()

    config = load_config(args.config)
    input_runs_dir = Path(args.input_dir) / "runs"
    output_dir = Path(args.output_dir)
    output_runs_dir = output_dir / "runs"
    output_runs_dir.mkdir(parents=True, exist_ok=True)

    frame_index = build_frame_index(config.paths.frames_dir)
    frames_by_key = {frame.frame_key: frame for frame in frame_index.frames}
    qa_by_id = {qa.qa_id: qa for qa in load_qa_items(config.paths.qa_path, frame_index)}
    snapshot_metadata = _load_snapshot_metadata(Path(args.snapshot_summary))

    tasks = [
        {
            "run_path": run_path,
            "qa": qa_by_id[run["qa_id"]],
            "run": run,
            "snapshot_metadata": snapshot_metadata.get(run["qa_id"], run.get("snapshot_metadata", {})),
        }
        for run_path, run in _iter_existing_runs(input_runs_dir)
        if run.get("qa_id") in qa_by_id
        and run.get("rank_method") in set(args.rank_methods)
        and int(run.get("max_hops", -1)) in set(args.max_hops)
    ]

    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for task in tasks:
            results.append(_run_answer_task(config, frames_by_key, task, args.answer_mode))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(_run_answer_task, config, frames_by_key, task, args.answer_mode) for task in tasks]
            for future in as_completed(futures):
                results.append(future.result())

    for result in results:
        path = output_runs_dir / f"{result['qa_id']}__{result['rank_method']}__hop{result['max_hops']}.json"
        _write_json(path, result)

    output = {
        "source_ablation": args.input_dir,
        "task_count": len(tasks),
        "completed_count": len(results),
        "answer_mode": args.answer_mode,
        "rank_methods": args.rank_methods,
        "max_hops": args.max_hops,
        "workers": args.workers,
        "snapshot_summary": args.snapshot_summary,
        "summary": _summarize(results),
        "results": sorted(results, key=lambda item: (item["variant"], item["qa_id"])),
    }
    _write_json(output_dir / "summary.json", output)
    print(
        json.dumps(
            {
                "task_count": len(tasks),
                "summary": output["summary"],
                "summary_path": str(output_dir / "summary.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _iter_existing_runs(runs_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    runs = []
    for path in sorted(runs_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            runs.append((path, json.load(f)))
    return runs


def _run_answer_task(
    config,
    frames_by_key: dict[str, Any],
    task: dict[str, Any],
    answer_mode: str,
) -> dict[str, Any]:
    qa: QAItem = task["qa"]
    run = dict(task["run"])
    selected_node_ids = list(run.get("selected_node_ids", []))
    retrieved_frame_keys = list(run.get("retrieved_frame_keys", []))
    pending_frame_keys = _pending_frame_keys(frames_by_key.values(), qa, task.get("snapshot_metadata", {}))
    answer_frame_keys = sorted(set(retrieved_frame_keys) | set(pending_frame_keys))

    store = SQLiteMemoryStore(run["snapshot_path"])
    selected_nodes = store.get_nodes(selected_node_ids)
    store.close()

    client = make_model_client(config.llm)
    answer_frames = [] if answer_mode == "text" else [frames_by_key[key] for key in answer_frame_keys if key in frames_by_key]
    answer_text = client.answer(qa.question, selected_nodes, answer_frames)

    retrieval_answer = AnswerResult(qa.qa_id, "", selected_node_ids, retrieved_frame_keys)
    answer = AnswerResult(qa.qa_id, answer_text, selected_node_ids, answer_frame_keys)
    retrieval_metrics = evaluate_qa(qa, retrieval_answer)
    answer_metrics = evaluate_qa(qa, answer)

    run["answer_pred"] = answer_text
    run["answer_node_ids"] = [node.node_id for node in selected_nodes]
    run["pending_frame_keys"] = pending_frame_keys
    run["answer_frame_keys"] = answer_frame_keys
    run["retrieval_metrics"] = retrieval_metrics.to_dict()
    run["answer_metrics"] = answer_metrics.to_dict()
    run["metrics"] = answer_metrics.to_dict()
    return run


def _pending_frame_keys(all_frames, qa: QAItem, snapshot_metadata: dict[str, Any]) -> list[str]:
    if qa.qa_time_id is None:
        return []
    built_until = snapshot_metadata.get("built_until_time_id")
    if built_until is None:
        return []
    built_until = int(built_until)
    return sorted(frame.frame_key for frame in all_frames if built_until < frame.time_id <= qa.qa_time_id)


def _load_snapshot_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {item["qa_id"]: item for item in raw.get("snapshots", []) if item.get("qa_id")}


def _summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["variant"]].append(result)

    metric_names = [
        "qa_accuracy",
        "reference_recall",
        "redundant_ratio",
        "evidence_precision",
        "evidence_f1",
        "evidence_unit_coverage",
        "fact_completeness",
        "evidence_sufficiency",
        "valid_evidence_precision",
        "background_ratio",
        "off_target_ratio",
        "conditional_redundant_ratio",
        "retrieved_frame_count",
        "extra_frame_count",
    ]
    rows = []
    for variant, items in sorted(grouped.items()):
        row: dict[str, Any] = {"variant": variant, "count": len(items)}
        for metric in metric_names:
            answer_values = [
                float(item["answer_metrics"][metric])
                for item in items
                if item["answer_metrics"].get(metric) is not None
            ]
            retrieval_values = [
                float(item["retrieval_metrics"][metric])
                for item in items
                if item["retrieval_metrics"].get(metric) is not None
            ]
            row[f"mean_answer_{metric}"] = sum(answer_values) / len(answer_values) if answer_values else None
            row[f"mean_retrieval_{metric}"] = sum(retrieval_values) / len(retrieval_values) if retrieval_values else None
        row["mean_qa_accuracy"] = row["mean_answer_qa_accuracy"]
        rows.append(row)
    return rows


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
