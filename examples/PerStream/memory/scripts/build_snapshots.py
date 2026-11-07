from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from video_memory.config import AppConfig, load_config
from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.data.windowing import make_windows
from video_memory.llm.api_client import make_model_client
from video_memory.memory.entity_extractor import SpacyEntityExtractor
from video_memory.memory.entity_store import EntityNormalizer, entity_from_mention
from video_memory.memory.generator import MemoryGenerator
from video_memory.memory.ocr_generator import OCRLLMMemoryGenerator, OCRMemoryGenerator
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.ocr.tesseract import TesseractOCR
from video_memory.schemas import FrameWindow, NodeEntityEdge, QAItem, RejectedNode
from video_memory.tracing.trace_logger import TraceLogger


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--output-dir", default="outputs/snapshots")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--limit-windows", type=int)
    parser.add_argument(
        "--snapshot-policy",
        choices=["completed", "covering"],
        default="completed",
        help="completed: only windows with end_time_id <= qa_time_id. covering: include the window covering qa_time_id.",
    )
    parser.add_argument(
        "--override-qa-time-id",
        action="append",
        default=[],
        metavar="QA_ID=TIME_ID",
        help="Override a QA snapshot time. Can be repeated, e.g. --override-qa-time-id aitw_general_Q1=60",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.clear:
        for path in output_dir.glob("*.sqlite"):
            path.unlink()

    summary = build_snapshots(
        config=config,
        output_dir=output_dir,
        limit_windows=args.limit_windows,
        snapshot_policy=args.snapshot_policy,
        qa_time_overrides=_parse_overrides(args.override_qa_time_id),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(_exit_code(summary))


def _exit_code(summary: dict[str, Any]) -> int:
    """Non-zero when the model cited frames that do not exist in their window.

    The build still completes and writes everything it could bind, so the API
    spend is not wasted and the full list of violations is available at once.
    """
    count = int(summary.get("rejected_node_count", 0))
    if not count:
        return 0
    print(
        f"\n{count} generated node(s) were dropped: the model cited frame ids that are "
        f"not in their window. See rejected_nodes above and in the traces.",
        file=sys.stderr,
    )
    return 1


def build_snapshots(
    config: AppConfig,
    output_dir: Path,
    limit_windows: int | None,
    snapshot_policy: str,
    qa_time_overrides: dict[str, int],
) -> dict[str, Any]:
    frame_index = build_frame_index(config.paths.frames_dir)
    qa_items = load_qa_items(config.paths.qa_path, frame_index)
    qa_items = [_with_time_override(item, qa_time_overrides) for item in qa_items]
    qa_items = sorted(
        [item for item in qa_items if item.qa_time_id is not None],
        key=lambda item: (int(item.qa_time_id or 0), item.qa_id),
    )

    all_windows = make_windows(
        frame_index.frames,
        window_size=config.window.window_size,
        stride=config.window.stride,
    )
    windows = all_windows[:limit_windows] if limit_windows is not None else all_windows

    generator = _make_generator(config)
    extractor = SpacyEntityExtractor(
        config.entities.spacy_model,
        allowed_labels=config.entities.allowed_labels,
        conditional_labels=config.entities.conditional_labels,
        blocked_labels=config.entities.blocked_labels,
        blocklist=config.entities.blocklist,
    )
    normalizer = EntityNormalizer(config.entities.aliases)
    store = SQLiteMemoryStore(config.paths.memory_db)
    trace = TraceLogger(config.tracing.traces_dir, config.tracing.enabled)
    store.clear()

    pending = list(qa_items)
    snapshots: list[dict[str, Any]] = []
    rejected_nodes: list[RejectedNode] = []
    total_nodes = 0
    total_edges = 0

    for index, window in enumerate(windows):
        frames = frame_index.subset(window.frame_keys)
        nodes, node_frame_edges, ocr_observations, rejected = generator.generate(window, frames)
        rejected_nodes.extend(rejected)

        store.add_nodes(nodes)
        store.add_node_frame_edges(node_frame_edges)

        node_entity_edges: list[NodeEntityEdge] = []
        entity_trace = []
        for node in nodes:
            for mention in extractor.extract(node.description_text):
                entity = entity_from_mention(mention, normalizer)
                stored_entity = store.add_entity(entity)
                node_entity_edges.append(NodeEntityEdge(node.node_id, stored_entity.entity_id))
                entity_trace.append(
                    {
                        "node_id": node.node_id,
                        "mention": mention.to_dict(),
                        "entity": stored_entity.to_dict(),
                    }
                )

        store.add_node_entity_edges(node_entity_edges)
        total_nodes += len(nodes)
        total_edges += len(node_frame_edges) + len(node_entity_edges)

        trace.write_json(
            f"memory/{window.window_id}.json",
            {
                "window": window.to_dict(),
                "nodes": [node.to_dict() for node in nodes],
                "node_frame_edges": [edge.to_dict() for edge in node_frame_edges],
                "ocr": [item.to_dict() for item in ocr_observations],
                "entities": entity_trace,
                "rejected_nodes": [item.to_dict() for item in rejected],
            },
        )

        ready, pending = _partition_ready_qas(
            pending,
            current_window=window,
            next_window=all_windows[index + 1] if index + 1 < len(all_windows) else None,
            snapshot_policy=snapshot_policy,
        )
        for qa in ready:
            snapshot_path = output_dir / f"{qa.qa_id}.sqlite"
            store.conn.commit()
            shutil.copy2(store.path, snapshot_path)
            snapshots.append(
                {
                    "qa_id": qa.qa_id,
                    "qa_time_id": qa.qa_time_id,
                    "qa_time_key": qa.qa_time_key,
                    "snapshot_path": str(snapshot_path),
                    "built_until_time_id": window.end_time_id,
                    "built_until_window_id": window.window_id,
                    "snapshot_policy": snapshot_policy,
                }
            )

    summary = {
        "frames": len(frame_index),
        "windows_built": len(windows),
        "nodes": total_nodes,
        "entities": len(store.list_entities()),
        "edges": total_edges,
        "rejected_node_count": len(rejected_nodes),
        "rejected_nodes": [item.to_dict() for item in rejected_nodes],
        "snapshots": snapshots,
        "unsnapped_qas": [item.qa_id for item in pending],
        "memory_db": str(config.paths.memory_db),
        "snapshot_dir": str(output_dir),
    }
    trace.write_json("snapshot_summary.json", summary)
    store.close()
    return summary


def _make_generator(config: AppConfig):
    if config.memory.generation_mode == "ocr":
        return OCRMemoryGenerator(
            TesseractOCR(),
            min_chars=config.memory.min_ocr_chars,
        )
    if config.memory.generation_mode == "ocr_llm":
        return OCRLLMMemoryGenerator(
            TesseractOCR(),
            make_model_client(config.llm),
        )
    if config.memory.generation_mode == "vision":
        return MemoryGenerator(make_model_client(config.llm))
    raise ValueError(f"Unsupported memory.generation_mode: {config.memory.generation_mode}")


def _partition_ready_qas(
    pending: list[QAItem],
    current_window: FrameWindow,
    next_window: FrameWindow | None,
    snapshot_policy: str,
) -> tuple[list[QAItem], list[QAItem]]:
    ready: list[QAItem] = []
    later: list[QAItem] = []
    next_boundary = next_window.end_time_id if snapshot_policy == "completed" and next_window else None

    for qa in pending:
        qa_time_id = int(qa.qa_time_id or 0)
        if snapshot_policy == "covering":
            is_ready = current_window.start_time_id <= qa_time_id <= current_window.end_time_id
            if next_window is None and qa_time_id <= current_window.end_time_id:
                is_ready = True
        else:
            is_ready = current_window.end_time_id <= qa_time_id and (
                next_boundary is None or qa_time_id < next_boundary
            )
        if is_ready:
            ready.append(qa)
        else:
            later.append(qa)
    return ready, later


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


if __name__ == "__main__":
    main()
