from __future__ import annotations

from dataclasses import dataclass

from video_memory.schemas import QAItem, RetrievedContext


@dataclass(frozen=True)
class SelectionPolicy:
    threshold: float
    min_k: int
    max_k: int
    category: str


def selection_policy(qa: QAItem, rank_method: str, parsed_types: list[str]) -> SelectionPolicy:
    category = _qa_category(qa, parsed_types)
    min_max = {
        "detail": (1, 3),
        "privacy": (1, 3),
        "multi": (2, 6),
        "summary": (4, 10),
        "preference": (6, 15),
    }[category]
    thresholds = {
        "field": {
            "detail": 0.35,
            "privacy": 0.35,
            "multi": 0.30,
            "summary": 0.28,
            "preference": 0.25,
        },
        "embedding": {
            "detail": 0.45,
            "privacy": 0.45,
            "multi": 0.40,
            "summary": 0.35,
            "preference": 0.32,
        },
        "qwen_embedding": {
            "detail": 0.45,
            "privacy": 0.45,
            "multi": 0.40,
            "summary": 0.35,
            "preference": 0.32,
        },
        "api": {
            "detail": 0.65,
            "privacy": 0.65,
            "multi": 0.58,
            "summary": 0.55,
            "preference": 0.50,
        },
    }
    return SelectionPolicy(
        threshold=thresholds.get(rank_method, thresholds["field"])[category],
        min_k=min_max[0],
        max_k=min_max[1],
        category=category,
    )


def adaptive_select_nodes(
    node_scores: dict[str, float],
    node_to_frames: dict[str, list[str]],
    policy: SelectionPolicy,
) -> RetrievedContext:
    ordered = sorted(node_scores.items(), key=lambda item: item[1], reverse=True)
    selected = [(node_id, score) for node_id, score in ordered if score >= policy.threshold]

    if len(selected) < policy.min_k:
        selected = ordered[: policy.min_k]
    if policy.max_k > 0:
        selected = selected[: policy.max_k]

    selected_node_ids = [node_id for node_id, _ in selected]
    retrieved_frames = sorted({frame for node_id in selected_node_ids for frame in node_to_frames.get(node_id, [])})
    return RetrievedContext(
        selected_node_ids=selected_node_ids,
        retrieved_frame_keys=retrieved_frames,
        node_scores={node_id: score for node_id, score in selected},
    )


def _qa_category(qa: QAItem, parsed_types: list[str]) -> str:
    raw = " ".join(qa.raw_type).lower()
    if "privacy" in raw:
        return "privacy"
    if "multi-evidence" in raw:
        return "multi"
    if "summarization" in raw:
        return "summary"
    if "preference" in raw:
        return "preference"
    if parsed_types == ["summary"]:
        return "summary"
    if parsed_types == ["preference"]:
        return "preference"
    return "detail"
