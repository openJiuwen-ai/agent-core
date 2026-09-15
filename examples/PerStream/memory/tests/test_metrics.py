import pytest

from video_memory.evaluation.metrics import evaluate_qa
from video_memory.schemas import AnswerResult, QAItem


def test_metrics_with_multiple_reference_sets() -> None:
    qa = QAItem(
        qa_id="q1",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f1", "f2"], ["f3"]],
        required_facts=[{"fact_id": "fact", "support_sets": [["f1", "f2"], ["f3"]]}],
    )
    answer = AnswerResult(
        qa_id="q1",
        answer="A",
        selected_node_ids=["n1"],
        retrieved_frame_keys=["f3", "f4"],
    )
    result = evaluate_qa(qa, answer)
    assert result.qa_accuracy == 1.0
    assert result.reference_recall == 1.0
    assert result.redundant_ratio == 0.5
    assert result.best_reference_set == ["f3"]
    assert result.evidence_unit_coverage == 1.0
    assert result.fact_completeness == 1.0
    assert result.evidence_sufficiency == 1.0
    assert result.valid_evidence_precision == 0.5
    assert result.conditional_redundant_ratio == 0.5
    assert result.matched_sufficient_frames == ["f3"]


def test_v2_atomic_fact_accepts_or_alternatives() -> None:
    qa = QAItem(
        qa_id="q-or",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f1"]],
        required_facts=[
            {
                "fact_id": "atomic",
                "fact_type": "atomic",
                "support_sets": [["f1"], ["f2"]],
            }
        ],
    )
    answer = AnswerResult("q-or", "A", ["n1"], ["f2"])

    result = evaluate_qa(qa, answer)

    assert result.reference_recall == 0.0  # Legacy canonical-frame diagnostic.
    assert result.evidence_unit_coverage == 1.0
    assert result.fact_completeness == 1.0
    assert result.evidence_sufficiency == 1.0
    assert result.valid_evidence_precision == 1.0
    assert result.conditional_redundant_ratio == 0.0
    assert result.matched_sufficient_frames == ["f2"]


def test_v2_comparison_requires_all_candidate_units() -> None:
    qa = QAItem(
        qa_id="q-and",
        question="Q?",
        answer="D",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["h1", "h2"]],
        required_facts=[
            {
                "fact_id": "hotel_comparison",
                "fact_type": "comparison",
                "candidates": [
                    {"unit_id": "hotel_1", "support_sets": [["h1"]]},
                    {"unit_id": "hotel_2", "support_sets": [["h2"], ["h2_alt"]]},
                ],
            }
        ],
    )

    partial = evaluate_qa(qa, AnswerResult("q-and", "D", ["n1"], ["h1"]))
    assert partial.qa_accuracy == 1.0
    assert partial.evidence_unit_coverage == 0.5
    assert partial.fact_completeness == 0.0
    assert partial.evidence_sufficiency == 0.0
    assert partial.conditional_redundant_ratio is None

    complete = evaluate_qa(qa, AnswerResult("q-and", "D", ["n1"], ["h1", "h2_alt"]))
    assert complete.evidence_unit_coverage == 1.0
    assert complete.fact_completeness == 1.0
    assert complete.evidence_sufficiency == 1.0
    assert complete.valid_evidence_precision == 1.0
    assert complete.matched_sufficient_frames == ["h1", "h2_alt"]


def test_v2_separates_valid_alternatives_background_and_redundancy() -> None:
    qa = QAItem(
        qa_id="q-redundancy",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f1"]],
        required_facts=[{"fact_id": "fact", "support_sets": [["f1"], ["f2"]]}],
        background_frame_keys=["bg"],
    )
    answer = AnswerResult("q-redundancy", "A", ["n1"], ["f1", "f2", "bg", "noise"])

    result = evaluate_qa(qa, answer)

    assert result.evidence_sufficiency == 1.0
    assert result.valid_evidence_precision == 0.5
    assert result.background_ratio == 0.25
    assert result.off_target_ratio == 0.25
    assert result.conditional_redundant_ratio == 0.75
    assert result.matched_sufficient_frames == ["f1"]


def test_missing_required_facts_raises_instead_of_scoring_zero() -> None:
    qa = QAItem(
        qa_id="q-nofacts",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f1"]],
    )
    with pytest.raises(ValueError, match="required_facts"):
        evaluate_qa(qa, AnswerResult("q-nofacts", "A", ["n1"], ["f1"]))


def test_unit_without_support_sets_raises() -> None:
    qa = QAItem(
        qa_id="q-badunit",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f1"]],
        required_facts=[{"fact_id": "fact", "supporting_frames": ["f1"]}],
    )
    with pytest.raises(ValueError, match="support_sets"):
        evaluate_qa(qa, AnswerResult("q-badunit", "A", ["n1"], ["f1"]))
