from pathlib import Path

from video_memory.memory.generator import build_memory_nodes
from video_memory.schemas import FrameRecord, FrameWindow


def _frame(local_id: int) -> FrameRecord:
    return FrameRecord(
        frame_id=f"{local_id:06d}",
        frame_key=f"evt_{local_id}",
        global_frame_id=local_id,
        event_id="evt",
        local_frame_id=local_id,
        time_id=local_id,
        modality="png",
        path=Path(f"{local_id}.png"),
    )


WINDOW = FrameWindow(window_id="window_000000", frame_keys=["evt_0", "evt_1"], start_time_id=0, end_time_id=1)
FRAMES = [_frame(0), _frame(1)]


def test_cited_frames_are_bound_and_time_ids_derived() -> None:
    nodes, edges, rejected = build_memory_nodes(
        WINDOW,
        FRAMES,
        [{"node_type": "detail", "description_text": "A price is shown.", "related_frame_ids": ["evt_1"]}],
    )

    assert rejected == []
    assert [node.node_id for node in nodes] == ["window_000000_node_000"]
    assert nodes[0].time_ids == [1]
    assert [(edge.node_id, edge.frame_key) for edge in edges] == [("window_000000_node_000", "evt_1")]


def test_unknown_frame_ids_are_dropped_not_spread_across_the_window() -> None:
    """A node citing frames outside its window must not be bound to all of them.

    node_frame_edges are what every evidence metric is computed from, so
    inventing edges the model never claimed corrupts the ground truth silently.
    """
    nodes, edges, rejected = build_memory_nodes(
        WINDOW,
        FRAMES,
        [
            {"node_type": "detail", "description_text": "Cited a later window.", "related_frame_ids": ["evt_9"]},
            {"node_type": "detail", "description_text": "Cited nothing.", "related_frame_ids": []},
            {"node_type": "detail", "description_text": "Cited a global id.", "related_frame_ids": ["000001"]},
        ],
    )

    assert nodes == []
    assert edges == []
    assert [item.cited_frame_keys for item in rejected] == [["evt_9"], [], ["000001"]]
    assert {item.window_id for item in rejected} == {"window_000000"}
    assert rejected[0].known_frame_keys == ["evt_0", "evt_1"]
    assert [item.node_index for item in rejected] == [0, 1, 2]


def test_partially_valid_citations_keep_only_the_real_frames() -> None:
    nodes, edges, rejected = build_memory_nodes(
        WINDOW,
        FRAMES,
        [{"node_type": "summary", "description_text": "Mixed.", "related_frame_ids": ["evt_0", "evt_42"]}],
    )

    assert rejected == []
    assert [edge.frame_key for edge in edges] == ["evt_0"]
    assert nodes[0].time_ids == [0]


def test_blank_descriptions_are_skipped_without_being_reported() -> None:
    nodes, edges, rejected = build_memory_nodes(
        WINDOW,
        FRAMES,
        [{"node_type": "detail", "description_text": "   ", "related_frame_ids": ["evt_0"]}],
    )

    assert (nodes, edges, rejected) == ([], [], [])


def test_unknown_node_type_falls_back_to_detail() -> None:
    nodes, _, _ = build_memory_nodes(
        WINDOW,
        FRAMES,
        [{"node_type": "not_a_type", "description_text": "Something.", "related_frame_ids": ["evt_0"]}],
    )

    assert nodes[0].node_type == "detail"
