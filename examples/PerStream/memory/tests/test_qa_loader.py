import json
from pathlib import Path

import pytest

from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items


def test_load_qa_items(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "000001_123_0.png").write_bytes(b"fake")
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(
        json.dumps(
            {
                "video_1": {
                    "qa_list": [
                        {
                            "question": "Q?",
                            "answer": "A",
                            "question_id": "q1",
                            "evidence": {
                                "required_facts": [
                                    {"fact_id": "answer_fact", "support_sets": [["123_0"]]}
                                ],
                                "minimal_sufficient_sets": [["123_0"]],
                            },
                            "timestamp": "123_0",
                            "type": ["Fatual Detail Questions"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    index = build_frame_index(frames)
    items = load_qa_items(qa_path, index)
    assert len(items) == 1
    assert items[0].reference_sets == [["123_0"]]
    assert items[0].qa_time_id == 1


def test_missing_minimal_sufficient_sets_raises(tmp_path: Path) -> None:
    """A QA item without an explicit canonical set must not load silently."""
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "000001_123_0.png").write_bytes(b"fake")
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(
        json.dumps(
            {"video_1": {"qa_list": [{"question_id": "q-legacy", "reference": ["123_0"]}]}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="minimal_sufficient_sets"):
        load_qa_items(qa_path, build_frame_index(frames))


def test_load_qa_items_prefers_audited_evidence(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "000001_123_0.png").write_bytes(b"fake")
    (frames / "000002_123_1.png").write_bytes(b"fake")
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(
        json.dumps(
            {
                "video_1": {
                    "qa_list": [
                        {
                            "question": "Q?",
                            "answer": "A",
                            "question_id": "q1",
                            "reference": ["123_0", "123_1"],
                            "evidence": {
                                "required_facts": [
                                    {
                                        "fact_id": "answer_fact",
                                        "description": "The answer is visible.",
                                        "support_sets": [["123_1"]],
                                    }
                                ],
                                "minimal_sufficient_sets": [["123_1"]],
                                "minimum_evidence_size": 1,
                                "background_frames": ["123_0"],
                            },
                            "timestamp": "123_1",
                            "type": ["Fatual Detail Questions"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    index = build_frame_index(frames)
    item = load_qa_items(qa_path, index)[0]
    assert item.reference_sets == [["123_1"]]
    assert item.required_facts[0]["fact_id"] == "answer_fact"
    assert item.background_frame_keys == ["123_0"]
    assert item.minimum_evidence_size == 1


def test_load_qa_items_reads_v2_scope(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "000001_123_0.png").write_bytes(b"fake")
    (frames / "000002_123_1.png").write_bytes(b"fake")
    qa_path = tmp_path / "qa.json"
    qa_path.write_text(
        json.dumps(
            {
                "video_1": {
                    "qa_list": [
                        {
                            "question": "Q?",
                            "answer": "A",
                            "question_id": "q-v2",
                            "evidence": {
                                "schema_version": 2,
                                "scope": {
                                    "rule": "preceding_thematic_segment",
                                    "start_frame": "123_0",
                                    "end_frame": "123_1",
                                },
                                "required_facts": [
                                    {
                                        "fact_id": "answer_fact",
                                        "fact_type": "atomic",
                                        "support_sets": [["123_1"]],
                                    }
                                ],
                                "minimal_sufficient_sets": [["123_1"]],
                            },
                            "timestamp": "123_1",
                            "type": ["Factual Detail Questions"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    item = load_qa_items(qa_path, build_frame_index(frames))[0]

    assert item.reference_sets == [["123_1"]]
    assert item.evidence_schema_version == 2
    assert item.evidence_scope == {
        "rule": "preceding_thematic_segment",
        "start_frame": "123_0",
        "end_frame": "123_1",
    }
