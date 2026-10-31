from __future__ import annotations

import argparse
import json
from dataclasses import replace
from typing import Any

from video_memory.config import load_config
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.evaluation.metrics import evaluate_qa
from video_memory.llm.api_client import make_model_client
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.retrieval.filter import filter_nodes
from video_memory.retrieval.qa_parser import QAParser
from video_memory.retrieval.ranker import RuleBasedNodeRanker
from video_memory.retrieval.selector import select_nodes
from video_memory.schemas import AnswerResult, MemoryNode, QAItem


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--qa-id")
    parser.add_argument("--qa-index", type=int, default=0)
    parser.add_argument("--override-qa-time-id", type=int)
    parser.add_argument("--provider", choices=["openrouter", "openai"], default="openrouter")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    config = load_config(args.config)
    config = replace(config, llm=replace(config.llm, provider=args.provider))

    frame_index = build_frame_index(config.paths.frames_dir)
    qa_items = load_qa_items(config.paths.qa_path, frame_index)
    qa = _select_qa(qa_items, args.qa_id, args.qa_index)
    if args.override_qa_time_id is not None:
        qa = replace(qa, qa_time_id=args.override_qa_time_id)

    store = SQLiteMemoryStore(config.paths.memory_db)
    client = make_model_client(config.llm)
    parsed = QAParser(client, recently_window_size=config.retrieval.recently_window_size).parse(
        qa,
        (frame_index.min_time_id(), frame_index.max_time_id()),
    )

    all_nodes = store.list_nodes()
    filtered_nodes = filter_nodes(all_nodes, parsed)
    ranked = RuleBasedNodeRanker().rank(qa.question, parsed.entities, filtered_nodes)
    selected_ranked = ranked[: args.top_k]

    filtered_node_ids = {node.node_id for node in filtered_nodes}
    node_to_frames = _node_to_frames(store, filtered_node_ids)
    reference_frames = sorted({frame_key for refset in qa.reference_sets for frame_key in refset})
    reference_linked_nodes = [
        _node_payload(node, node_to_frames, score=None)
        for node in filtered_nodes
        if set(node_to_frames.get(node.node_id, [])) & set(reference_frames)
    ]

    selected_scores = {item.node_id: item.score for item in selected_ranked}
    context = select_nodes(
        selected_scores,
        node_to_frames,
        final_node_threshold=-1.0,
        min_k=0,
        max_k=args.top_k,
    )
    answer_stub = AnswerResult(
        qa_id=qa.qa_id,
        answer="",
        selected_node_ids=context.selected_node_ids,
        retrieved_frame_keys=context.retrieved_frame_keys,
    )
    metrics = evaluate_qa(qa, answer_stub)

    output: dict[str, Any] = {
        "qa_id": qa.qa_id,
        "question": qa.question,
        "qa_time_id": qa.qa_time_id,
        "qa_time_key": qa.qa_time_key,
        "reference_frames": reference_frames,
        "reference_times": [frame_index.time_for_key(frame_key) for frame_key in reference_frames],
        "parse": parsed.to_dict(),
        "memory_node_count": len(all_nodes),
        "filtered_node_count": len(filtered_nodes),
        "reference_linked_nodes_after_filter": reference_linked_nodes,
        "top_ranked_nodes": [
            _node_payload(
                node=store.get_node(item.node_id),
                node_to_frames=node_to_frames,
                score=item.score,
                rank=index + 1,
                reason=item.reason,
            )
            for index, item in enumerate(selected_ranked)
            if store.get_node(item.node_id) is not None
        ],
        "retrieved_frames": context.retrieved_frame_keys,
        "metrics": metrics.to_dict(),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    store.close()


def _select_qa(items: list[QAItem], qa_id: str | None, qa_index: int) -> QAItem:
    if qa_id is not None:
        for item in items:
            if item.qa_id == qa_id:
                return item
        raise KeyError(f"Unknown qa_id: {qa_id}")
    return items[qa_index]


def _node_to_frames(store: SQLiteMemoryStore, node_ids: set[str]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for edge in store.node_frame_edges(node_ids):
        mapping.setdefault(edge.node_id, []).append(edge.frame_key)
    return {node_id: sorted(frame_keys) for node_id, frame_keys in mapping.items()}


def _node_payload(
    node: MemoryNode | None,
    node_to_frames: dict[str, list[str]],
    score: float | None,
    rank: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if node is None:
        return {}
    payload: dict[str, Any] = {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "description_text": node.description_text,
        "time_ids": node.time_ids,
        "frame_keys": node_to_frames.get(node.node_id, []),
    }
    if rank is not None:
        payload["rank"] = rank
    if score is not None:
        payload["score"] = score
    if reason is not None:
        payload["reason"] = reason
    return payload


if __name__ == "__main__":
    main()

