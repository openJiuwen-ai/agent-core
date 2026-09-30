from __future__ import annotations

from video_memory.llm.base import ModelClient
from video_memory.ocr.tesseract import OCRFrameText
from video_memory.schemas import (
    FrameRecord,
    FrameWindow,
    MemoryNode,
    NodeFrameEdge,
    RejectedNode,
)

VALID_NODE_TYPES = {"detail", "summary", "preference"}

GeneratedMemory = tuple[
    list[MemoryNode],
    list[NodeFrameEdge],
    list[OCRFrameText],
    list[RejectedNode],
]


class MemoryGenerator:
    def __init__(self, client: ModelClient) -> None:
        self.client = client

    def generate(self, window: FrameWindow, frames: list[FrameRecord]) -> GeneratedMemory:
        raw_nodes = self.client.generate_memory(window, frames)
        nodes, edges, rejected = build_memory_nodes(window, frames, raw_nodes)
        return nodes, edges, [], rejected


def build_memory_nodes(
    window: FrameWindow,
    frames: list[FrameRecord],
    raw_nodes: list[dict],
) -> tuple[list[MemoryNode], list[NodeFrameEdge], list[RejectedNode]]:
    nodes: list[MemoryNode] = []
    edges: list[NodeFrameEdge] = []
    rejected: list[RejectedNode] = []
    known_frame_keys = {frame.frame_key for frame in frames}

    for index, raw in enumerate(raw_nodes):
        node_type = raw.get("node_type", "detail")
        if node_type not in VALID_NODE_TYPES:
            node_type = "detail"
        description = str(raw.get("description_text", "")).strip()
        if not description:
            continue

        cited = [str(key) for key in raw.get("related_frame_ids", [])]
        related_frame_ids = [key for key in cited if key in known_frame_keys]
        if not related_frame_ids:
            # Binding to every frame in the window would manufacture edges the
            # model never claimed, and node-frame edges are what the evidence
            # metrics are computed from. Drop the node and report it instead.
            rejected.append(
                RejectedNode(
                    window_id=window.window_id,
                    node_index=index,
                    node_type=node_type,
                    description_text=description,
                    cited_frame_keys=cited,
                    known_frame_keys=sorted(known_frame_keys),
                )
            )
            continue

        time_ids = [int(value) for value in raw.get("time_ids", []) if isinstance(value, int)]
        if not time_ids:
            time_ids = [frame.time_id for frame in frames if frame.frame_key in related_frame_ids]

        node = MemoryNode(
            node_id=f"{window.window_id}_node_{index:03d}",
            node_type=node_type,
            description_text=description,
            time_ids=sorted(set(time_ids)),
        )
        nodes.append(node)
        edges.extend(NodeFrameEdge(node.node_id, frame_key) for frame_key in sorted(set(related_frame_ids)))

    return nodes, edges, rejected
