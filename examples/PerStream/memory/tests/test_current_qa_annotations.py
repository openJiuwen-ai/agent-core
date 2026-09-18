from pathlib import Path

from video_memory.data.frame_index import build_frame_index
from video_memory.data.qa_loader import load_qa_items
from video_memory.evaluation.metrics import evaluate_qa
from video_memory.schemas import AnswerResult


ROOT = Path(__file__).resolve().parents[1]


def test_current_canonical_references_are_available_sufficient_and_minimal() -> None:
    frame_index = build_frame_index(ROOT / "decoded_frames_renumbered")
    qa_items = load_qa_items(ROOT / "qa.json", frame_index)

    assert len(qa_items) == 11
    for qa in qa_items:
        canonical = qa.reference_sets[0]
        assert canonical
        assert len(canonical) == qa.minimum_evidence_size
        for frame_key in canonical:
            frame = frame_index.require(frame_key)
            assert qa.qa_time_id is not None
            assert frame.time_id <= qa.qa_time_id

        result = evaluate_qa(
            qa,
            AnswerResult(
                qa_id=qa.qa_id,
                answer=qa.answer,
                selected_node_ids=[],
                retrieved_frame_keys=canonical,
            ),
        )
        assert result.qa_accuracy == 1.0
        assert result.evidence_unit_coverage == 1.0
        assert result.fact_completeness == 1.0
        assert result.evidence_sufficiency == 1.0
        assert result.conditional_redundant_ratio == 0.0


def test_high_risk_qa_v2_shapes() -> None:
    frame_index = build_frame_index(ROOT / "decoded_frames_renumbered")
    by_id = {qa.qa_id: qa for qa in load_qa_items(ROOT / "qa.json", frame_index)}

    expected = {
        "aitw_general_Q3": (2, 4, 1),
        "aitw_general_Q7": (9, 13, 6),
        "aitw_general_Q8": (6, 9, 3),
        "aitw_general_Q10": (6, 5, 5),
        "aitw_general_Q9": (15, 15, 15),
        "aitw_general_Q11": (9, 9, 9),
    }
    for qa_id, (frame_count, unit_count, fact_count) in expected.items():
        qa = by_id[qa_id]
        result = evaluate_qa(qa, AnswerResult(qa_id, qa.answer, [], qa.reference_sets[0]))
        assert qa.evidence_schema_version == 2
        assert len(qa.reference_sets[0]) == frame_count
        assert result.evidence_unit_count == unit_count
        assert result.required_fact_count == fact_count


def test_text_evidence_items_use_text_frames() -> None:
    frame_index = build_frame_index(ROOT / "decoded_frames_renumbered")
    by_id = {qa.qa_id: qa for qa in load_qa_items(ROOT / "qa.json", frame_index)}

    assert frame_index.require(by_id["aitw_general_Q4"].reference_sets[0][0]).modality == "txt"
    assert frame_index.require(by_id["aitw_general_Q6"].reference_sets[0][0]).modality == "txt"
