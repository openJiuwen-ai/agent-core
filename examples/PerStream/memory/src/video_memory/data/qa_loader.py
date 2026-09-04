from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_memory.data.frame_index import FrameIndex
from video_memory.schemas import QAItem


def load_qa_items(path: str | Path, frame_index: FrameIndex | None = None) -> list[QAItem]:
    qa_path = Path(path)
    with qa_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    items: list[QAItem] = []
    for video_id, video_data in raw.items():
        qa_list = video_data.get("qa_list", [])
        for entry in qa_list:
            item = _parse_qa_entry(video_id, entry, frame_index)
            items.append(item)

    return items


def _parse_qa_entry(video_id: str, entry: dict[str, Any], frame_index: FrameIndex | None) -> QAItem:
    qa_time_key = entry.get("timestamp")
    qa_time_id = frame_index.time_for_key(qa_time_key) if frame_index else None
    evidence = entry.get("evidence") or {}
    evidence_sets = evidence.get("minimal_sufficient_sets")
    if not evidence_sets:
        raise ValueError(
            f"{entry.get('question_id', '<no question_id>')}: evidence.minimal_sufficient_sets "
            f"is missing or empty. The first set is the canonical reference."
        )
    reference_sets = [list(reference_set) for reference_set in evidence_sets]

    return QAItem(
        qa_id=entry.get("question_id", ""),
        question=entry.get("question", ""),
        answer=str(entry.get("answer", "")),
        qa_time_key=qa_time_key,
        qa_time_id=qa_time_id,
        reference_sets=reference_sets,
        raw_type=list(entry.get("type") or []),
        reasoning=entry.get("reasoning"),
        video_id=video_id,
        required_facts=[dict(fact) for fact in evidence.get("required_facts") or []],
        background_frame_keys=list(evidence.get("background_frames") or []),
        minimum_evidence_size=evidence.get("minimum_evidence_size"),
        evidence_aggregation=dict(evidence["aggregation"]) if evidence.get("aggregation") else None,
        evidence_scope=dict(evidence["scope"]) if evidence.get("scope") else None,
        evidence_schema_version=int(evidence.get("schema_version", 1)),
    )
