from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from video_memory.config import AppConfig, load_config
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.embedding.api_embedder import APIEmbedder
from video_memory.embedding.qwen_encoder import QwenTextEncoder
from video_memory.evaluation.metrics import evaluate_qa
from video_memory.llm.api_client import make_model_client
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.retrieval.adaptive_selector import adaptive_select_nodes, selection_policy
from video_memory.retrieval.embedding_ranker import EmbeddingNodeRanker, QwenEmbeddingNodeRanker
from video_memory.retrieval.field_ranker import FieldAwareRanker
from video_memory.retrieval.filter import filter_nodes
from video_memory.retrieval.propagation import GraphPropagator
from video_memory.retrieval.qa_parser import QAParser
from video_memory.retrieval.ranker import APINodeRanker
from video_memory.schemas import AnswerResult, MemoryNode, QAItem, QAParseResult, RankedNode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--snapshot-dir", default="outputs/snapshots")
    parser.add_argument("--snapshot-summary", default="outputs/traces/snapshot_summary.json")
    parser.add_argument("--output-dir", default="outputs/ablation")
    parser.add_argument("--qa-ids", nargs="+", help="Run only the listed QA IDs.")
    parser.add_argument(
        "--rank-methods",
        nargs="+",
        default=["field", "embedding", "api"],
        choices=["field", "embedding", "qwen_embedding", "api"],
    )
    parser.add_argument("--max-hops", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--answer-mode", choices=["none", "text", "vision"], default="none")
    parser.add_argument("--api-rank-candidates", type=int, default=30)
    parser.add_argument("--qwen-model-path", default="model/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--qwen-checkpoint")
    parser.add_argument("--qwen-device", default="cuda:0")
    parser.add_argument("--qwen-batch-size", type=int, default=1)
    parser.add_argument("--entity-score-threshold", type=float, default=0.25)
    parser.add_argument("--propagation-score-threshold", type=float, default=0.15)
    parser.add_argument("--decay", type=float, default=0.75)
    parser.add_argument(
        "--override-qa-time-id",
        action="append",
        default=[],
        metavar="QA_ID=TIME_ID",
        help="Use the same overrides passed to build_snapshots.py.",
    )
    args = parser.parse_args()

    qwen_ranker = None
    if "qwen_embedding" in args.rank_methods:
        if not args.qwen_checkpoint:
            parser.error("--qwen-checkpoint is required for qwen_embedding")
        if args.workers != 1:
            parser.error("qwen_embedding currently requires --workers 1")
        qwen_encoder = QwenTextEncoder(
            args.qwen_model_path,
            device=args.qwen_device,
            adapter_path=args.qwen_checkpoint,
        )
        qwen_ranker = QwenEmbeddingNodeRanker(qwen_encoder, batch_size=args.qwen_batch_size)

    config = load_config(args.config)
    output_dir = Path(args.output_dir)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    frame_index = build_frame_index(config.paths.frames_dir)
    qa_items = load_qa_items(config.paths.qa_path, frame_index)
    if args.qa_ids:
        requested_qa_ids = set(args.qa_ids)
        qa_items = [item for item in qa_items if item.qa_id in requested_qa_ids]
        missing_qa_ids = requested_qa_ids - {item.qa_id for item in qa_items}
        if missing_qa_ids:
            raise ValueError(f"Unknown QA IDs: {sorted(missing_qa_ids)}")
    qa_items = [_with_time_override(item, _parse_overrides(args.override_qa_time_id)) for item in qa_items]

    parsed_by_qa = _parse_qas(config, qa_items, frame_index.min_time_id(), frame_index.max_time_id())
    snapshot_metadata = _load_snapshot_metadata(Path(args.snapshot_summary))
    tasks = []
    snapshot_dir = Path(args.snapshot_dir)
    for qa in qa_items:
        snapshot_path = snapshot_dir / f"{qa.qa_id}.sqlite"
        if not snapshot_path.exists():
            continue
        for rank_method in args.rank_methods:
            for max_hops in args.max_hops:
                tasks.append(
                    {
                        "qa": qa,
                        "parsed": parsed_by_qa[qa.qa_id],
                        "snapshot_path": snapshot_path,
                        "snapshot_metadata": snapshot_metadata.get(qa.qa_id, {}),
                        "rank_method": rank_method,
                        "max_hops": max_hops,
                    }
                )

    results: list[dict[str, Any]] = []
    if args.workers <= 1:
        for task in tasks:
            results.append(_run_task(config, frame_index.frames, task, args, qwen_ranker))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(_run_task, config, frame_index.frames, task, args, None) for task in tasks]
            for future in as_completed(futures):
                results.append(future.result())

    for result in results:
        path = runs_dir / f"{result['qa_id']}__{result['rank_method']}__hop{result['max_hops']}.json"
        _write_json(path, result)

    summary = _summarize(results)
    output = {
        "task_count": len(tasks),
        "completed_count": len(results),
        "answer_mode": args.answer_mode,
        "rank_methods": args.rank_methods,
        "max_hops": args.max_hops,
        "qa_ids": args.qa_ids,
        "workers": args.workers,
        "snapshot_summary": args.snapshot_summary,
        "summary": summary,
        "results": sorted(results, key=lambda item: (item["variant"], item["qa_id"])),
    }
    _write_json(output_dir / "summary.json", output)
    print(json.dumps({"task_count": len(tasks), "summary": summary, "summary_path": str(output_dir / "summary.json")}, ensure_ascii=False, indent=2))


def _parse_qas(
    config: AppConfig,
    qa_items: list[QAItem],
    min_time_id: int,
    max_time_id: int,
) -> dict[str, QAParseResult]:
    client = make_model_client(config.llm)
    parser = QAParser(client, recently_window_size=config.retrieval.recently_window_size)
    return {qa.qa_id: parser.parse(qa, (min_time_id, max_time_id)) for qa in qa_items}


def _run_task(
    config: AppConfig,
    all_frames: list,
    task: dict[str, Any],
    args: argparse.Namespace,
    qwen_ranker: QwenEmbeddingNodeRanker | None,
) -> dict[str, Any]:
    qa: QAItem = task["qa"]
    parsed: QAParseResult = task["parsed"]
    snapshot_path: Path = task["snapshot_path"]
    snapshot_metadata: dict[str, Any] = task.get("snapshot_metadata", {})
    rank_method: str = task["rank_method"]
    max_hops: int = task["max_hops"]

    store = SQLiteMemoryStore(snapshot_path)
    ranking_parsed = _qwen_parsed(qa, parsed) if rank_method == "qwen_embedding" else parsed
    all_nodes = store.list_nodes()
    filtered_nodes = filter_nodes(all_nodes, ranking_parsed)
    ranked = _rank_nodes(
        config,
        qa,
        ranking_parsed,
        filtered_nodes,
        rank_method,
        args.api_rank_candidates,
        qwen_ranker,
    )

    allowed_node_ids = {node.node_id for node in filtered_nodes}
    node_entity_edges = store.node_entity_edges(allowed_node_ids)
    entities = store.list_entities()
    if max_hops <= 0:
        node_scores = {item.node_id: item.score for item in ranked}
        propagation_steps = []
    else:
        propagation = GraphPropagator(
            max_hops=max_hops,
            entity_score_threshold=args.entity_score_threshold,
            propagation_score_threshold=args.propagation_score_threshold,
            decay=args.decay,
        ).propagate(
            ranked_nodes=ranked,
            allowed_node_ids=allowed_node_ids,
            node_entity_edges=node_entity_edges,
            entities=entities,
            query_entities=[*parsed.entities, qa.question],
        )
        node_scores = propagation.node_scores
        propagation_steps = [step.to_dict() for step in propagation.steps]

    node_to_frames = _node_to_frames(store, allowed_node_ids)
    policy = selection_policy(qa, rank_method, ranking_parsed.qa_types)
    context = adaptive_select_nodes(node_scores, node_to_frames, policy)
    selected_nodes = store.get_nodes(context.selected_node_ids)
    frame_index = {frame.frame_key: frame for frame in all_frames}
    pending_frame_keys = _pending_frame_keys(all_frames, qa, snapshot_metadata)
    answer_frame_keys = sorted(set(context.retrieved_frame_keys) | set(pending_frame_keys))
    answer_frames = [frame_index[key] for key in answer_frame_keys if key in frame_index]
    answer = _answer(config, qa, selected_nodes, answer_frames, context.selected_node_ids, answer_frame_keys, args.answer_mode)
    retrieval_answer = AnswerResult(qa.qa_id, answer.answer, context.selected_node_ids, context.retrieved_frame_keys)
    retrieval_metrics = evaluate_qa(qa, retrieval_answer)
    answer_metrics = evaluate_qa(qa, answer)

    top_ranked = []
    for item in ranked[:20]:
        node = store.get_node(item.node_id)
        top_ranked.append(
            {
                "node_id": item.node_id,
                "score": item.score,
                "reason": item.reason,
                "node_type": node.node_type if node else None,
                "description_text": node.description_text if node else None,
                "time_ids": node.time_ids if node else [],
                "frame_keys": node_to_frames.get(item.node_id, []),
            }
        )

    store.close()
    variant = f"{rank_method}_hop{max_hops}"
    return {
        "qa_id": qa.qa_id,
        "question": qa.question,
        "answer_gold": qa.answer,
        "answer_pred": answer.answer,
        "raw_type": qa.raw_type,
        "parse": parsed.to_dict(),
        "snapshot_path": str(snapshot_path),
        "snapshot_metadata": snapshot_metadata,
        "rank_method": rank_method,
        "max_hops": max_hops,
        "variant": variant,
        "selection_policy": asdict(policy),
        "filtered_node_count": len(filtered_nodes),
        "top_ranked_nodes": top_ranked,
        "selected_node_ids": context.selected_node_ids,
        "retrieved_frame_keys": context.retrieved_frame_keys,
        "pending_frame_keys": pending_frame_keys,
        "answer_frame_keys": answer_frame_keys,
        "node_scores": context.node_scores,
        "propagation_steps": propagation_steps,
        "retrieval_metrics": retrieval_metrics.to_dict(),
        "answer_metrics": answer_metrics.to_dict(),
        "metrics": answer_metrics.to_dict(),
    }


def _rank_nodes(
    config: AppConfig,
    qa: QAItem,
    parsed: QAParseResult,
    filtered_nodes: list[MemoryNode],
    rank_method: str,
    api_rank_candidates: int,
    qwen_ranker: QwenEmbeddingNodeRanker | None,
) -> list[RankedNode]:
    if rank_method == "field":
        return FieldAwareRanker().rank(qa, parsed, filtered_nodes)
    if rank_method == "embedding":
        embedder = APIEmbedder(config.embedding)
        try:
            return EmbeddingNodeRanker(embedder).rank(qa, parsed, filtered_nodes)
        finally:
            embedder.close()
    if rank_method == "qwen_embedding":
        if qwen_ranker is None:
            raise RuntimeError("qwen_embedding ranker was not initialized")
        return qwen_ranker.rank(qa, parsed, filtered_nodes)
    if rank_method == "api":
        client = make_model_client(config.llm)
        prefiltered = [item.node_id for item in FieldAwareRanker().rank(qa, parsed, filtered_nodes)[:api_rank_candidates]]
        node_by_id = {node.node_id: node for node in filtered_nodes}
        candidates = [node_by_id[node_id] for node_id in prefiltered if node_id in node_by_id]
        return APINodeRanker(client).rank(qa.question, parsed.entities, candidates)
    raise ValueError(f"Unknown rank_method: {rank_method}")


def _qwen_parsed(qa: QAItem, parsed: QAParseResult) -> QAParseResult:
    raw_type = " ".join(qa.raw_type).lower()
    if "preference" in raw_type:
        qa_type = "preference"
    elif "summarization" in raw_type:
        qa_type = "summary"
    else:
        qa_type = "detail"
    return replace(parsed, qa_types=[qa_type])


def _answer(
    config: AppConfig,
    qa: QAItem,
    selected_nodes: list[MemoryNode],
    answer_frames: list,
    selected_node_ids: list[str],
    answer_frame_keys: list[str],
    answer_mode: str,
) -> AnswerResult:
    if answer_mode == "none":
        return AnswerResult(qa.qa_id, "", selected_node_ids, answer_frame_keys)
    client = make_model_client(config.llm)
    frames = [] if answer_mode == "text" else answer_frames
    answer = client.answer(qa.question, selected_nodes, frames)
    return AnswerResult(qa.qa_id, answer, selected_node_ids, answer_frame_keys)


def _node_to_frames(store: SQLiteMemoryStore, allowed_node_ids: set[str]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = defaultdict(list)
    for edge in store.node_frame_edges(allowed_node_ids):
        mapping[edge.node_id].append(edge.frame_key)
    return {node_id: sorted(frame_keys) for node_id, frame_keys in mapping.items()}


def _summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["variant"]].append(result)

    rows = []
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


def _pending_frame_keys(all_frames: list, qa: QAItem, snapshot_metadata: dict[str, Any]) -> list[str]:
    qa_time_id = qa.qa_time_id
    if qa_time_id is None:
        return []
    built_until = snapshot_metadata.get("built_until_time_id")
    if built_until is None:
        return []
    built_until = int(built_until)
    return [
        frame.frame_key
        for frame in all_frames
        if built_until < frame.time_id <= qa_time_id
    ]


def _load_snapshot_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {item["qa_id"]: item for item in raw.get("snapshots", []) if item.get("qa_id")}


def _parse_overrides(values: list[str]) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --override-qa-time-id value: {value}")
        qa_id, time_id = value.split("=", 1)
        overrides[qa_id] = int(time_id)
    return overrides


def _with_time_override(item: QAItem, overrides: dict[str, int]) -> QAItem:
    if item.qa_id not in overrides:
        return item
    return replace(item, qa_time_id=overrides[item.qa_id])


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
