from __future__ import annotations

from video_memory.schemas import RetrievedContext


def select_nodes(
    node_scores: dict[str, float],
    node_to_frames: dict[str, list[str]],
    final_node_threshold: float,
    min_k: int,
    max_k: int,
) -> RetrievedContext:
    ordered = sorted(node_scores.items(), key=lambda item: item[1], reverse=True)
    selected = [(node_id, score) for node_id, score in ordered if score >= final_node_threshold]

    if len(selected) < min_k:
        selected = ordered[:min_k]
    if max_k > 0:
        selected = selected[:max_k]

    selected_node_ids = [node_id for node_id, _ in selected]
    retrieved_frames = sorted({frame for node_id in selected_node_ids for frame in node_to_frames.get(node_id, [])})
    return RetrievedContext(
        selected_node_ids=selected_node_ids,
        retrieved_frame_keys=retrieved_frames,
        node_scores={node_id: score for node_id, score in selected},
    )

