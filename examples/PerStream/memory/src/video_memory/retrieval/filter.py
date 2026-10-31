from __future__ import annotations

from video_memory.schemas import MemoryNode, QAParseResult


def filter_nodes(nodes: list[MemoryNode], parsed: QAParseResult) -> list[MemoryNode]:
    result: list[MemoryNode] = []
    allowed_types = set(parsed.qa_types)

    for node in nodes:
        if allowed_types and node.node_type not in allowed_types:
            continue
        if parsed.time_range is not None and not _overlaps(node.time_ids, parsed.time_range):
            continue
        result.append(node)

    return result


def _overlaps(time_ids: list[int], time_range: tuple[int, int]) -> bool:
    if not time_ids:
        return False
    start, end = time_range
    return any(start <= time_id <= end for time_id in time_ids)

