from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from video_memory.evaluation.judge import exact_or_choice_accuracy
from video_memory.schemas import AnswerResult, EvaluationResult, QAItem


def evaluate_qa(qa: QAItem, answer: AnswerResult) -> EvaluationResult:
    retrieved = set(answer.retrieved_frame_keys)
    best_ref = _best_reference_set(retrieved, qa.reference_sets)
    ref_set = set(best_ref)

    intersection = retrieved & ref_set
    recall = len(intersection) / len(ref_set) if ref_set else 0.0
    precision = len(intersection) / len(retrieved) if retrieved else 0.0
    redundant = len(retrieved - ref_set) / len(retrieved) if retrieved else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    facts = _normalized_facts(qa)
    fact_options = [_fact_sufficient_options(fact, retrieved) for fact in facts]
    fact_complete_flags = [bool(options) for options in fact_options]
    complete_fact_count = sum(fact_complete_flags)
    fact_completeness = complete_fact_count / len(facts) if facts else 0.0

    unit_options = [
        _unit_support_sets(unit)
        for fact in facts
        for unit in _fact_units(fact)
    ]
    covered_unit_count = sum(any(option <= retrieved for option in options) for options in unit_options)
    unit_coverage = covered_unit_count / len(unit_options) if unit_options else 0.0
    sufficient = bool(facts) and all(fact_complete_flags)

    valid_frames = _all_valid_frames(facts) | {frame for ref in qa.reference_sets for frame in ref}
    valid_retrieved = retrieved & valid_frames
    background = retrieved & set(qa.background_frame_keys)
    valid_precision = len(valid_retrieved) / len(retrieved) if retrieved else 0.0
    background_ratio = len(background) / len(retrieved) if retrieved else 0.0
    off_target = retrieved - valid_frames - set(qa.background_frame_keys)
    off_target_ratio = len(off_target) / len(retrieved) if retrieved else 0.0

    matched_sufficient = _smallest_sufficient_subset(fact_options) if sufficient else set()
    conditional_redundant = (
        len(retrieved - matched_sufficient) / len(retrieved)
        if sufficient and retrieved
        else None
    )

    return EvaluationResult(
        qa_id=qa.qa_id,
        qa_accuracy=exact_or_choice_accuracy(answer.answer, qa.answer),
        reference_recall=recall,
        redundant_ratio=redundant,
        evidence_precision=precision,
        evidence_f1=f1,
        retrieved_frame_count=len(retrieved),
        reference_frame_count=len(ref_set),
        extra_frame_count=len(retrieved - ref_set),
        best_reference_set=best_ref,
        evidence_unit_coverage=unit_coverage,
        fact_completeness=fact_completeness,
        evidence_sufficiency=float(sufficient),
        valid_evidence_precision=valid_precision,
        background_ratio=background_ratio,
        off_target_ratio=off_target_ratio,
        conditional_redundant_ratio=conditional_redundant,
        evidence_unit_count=len(unit_options),
        covered_evidence_unit_count=covered_unit_count,
        required_fact_count=len(facts),
        complete_fact_count=complete_fact_count,
        valid_evidence_frame_count=len(valid_retrieved),
        background_frame_count=len(background),
        matched_sufficient_frames=sorted(matched_sufficient),
    )


def _best_reference_set(retrieved: set[str], reference_sets: list[list[str]]) -> list[str]:
    if not reference_sets:
        return []
    best = reference_sets[0]
    best_key = (-1.0, -1.0)
    for ref in reference_sets:
        ref_set = set(ref)
        recall = len(retrieved & ref_set) / len(ref_set) if ref_set else 0.0
        precision = len(retrieved & ref_set) / len(retrieved) if retrieved else 0.0
        key = (recall, precision)
        if key > best_key:
            best = ref
            best_key = key
    return best


def _normalized_facts(qa: QAItem) -> list[dict[str, Any]]:
    if not qa.required_facts:
        raise ValueError(
            f"{qa.qa_id}: evidence.required_facts is missing or empty. Every QA item must "
            f"declare its required facts; the evidence metrics cannot be computed without them."
        )
    return qa.required_facts


def _fact_units(fact: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candidates", "events", "units"):
        units = fact.get(key)
        if units:
            return [dict(unit) for unit in units]
    return [fact]


def _unit_support_sets(unit: dict[str, Any]) -> list[frozenset[str]]:
    raw_sets = unit.get("support_sets")
    if not raw_sets:
        identifier = unit.get("unit_id") or unit.get("fact_id") or unit
        raise ValueError(f"Evidence unit has no support_sets: {identifier}")
    return _coerce_support_sets(raw_sets)


def _coerce_support_sets(raw_sets: Iterable[Any]) -> list[frozenset[str]]:
    support_sets: list[frozenset[str]] = []
    for raw_set in raw_sets:
        if isinstance(raw_set, str):
            support_sets.append(frozenset([raw_set]))
        else:
            support_sets.append(frozenset(map(str, raw_set)))
    return [support_set for support_set in support_sets if support_set]


def _fact_sufficient_options(fact: dict[str, Any], retrieved: set[str]) -> list[frozenset[str]]:
    options: list[frozenset[str]] = []

    explicit = _coerce_support_sets(fact.get("complete_support_sets") or [])
    options.extend(option for option in explicit if option <= retrieved)

    units = _fact_units(fact)
    if units == [fact]:
        atomic = _unit_support_sets(fact)
        options.extend(option for option in atomic if option <= retrieved)
    else:
        satisfied_groups: list[list[frozenset[str]]] = []
        for unit in units:
            satisfied = [option for option in _unit_support_sets(unit) if option <= retrieved]
            if not satisfied:
                satisfied_groups = []
                break
            satisfied_groups.append(satisfied)
        if satisfied_groups:
            options.extend(_minimal_unions(satisfied_groups))

    return _remove_supersets(options)


def _all_valid_frames(facts: list[dict[str, Any]]) -> set[str]:
    valid: set[str] = set()
    for fact in facts:
        valid.update(frame for option in _coerce_support_sets(fact.get("complete_support_sets") or []) for frame in option)
        for unit in _fact_units(fact):
            valid.update(frame for option in _unit_support_sets(unit) for frame in option)
    return valid


def _smallest_sufficient_subset(fact_options: list[list[frozenset[str]]]) -> set[str]:
    if not fact_options or any(not options for options in fact_options):
        return set()
    unions = _minimal_unions(fact_options)
    if not unions:
        return set()
    return set(min(unions, key=lambda frames: (len(frames), sorted(frames))))


def _minimal_unions(option_groups: list[list[frozenset[str]]]) -> list[frozenset[str]]:
    unions = [frozenset()]
    for options in option_groups:
        unions = _remove_supersets([current | option for current in unions for option in options])
    return unions


def _remove_supersets(options: Iterable[frozenset[str]]) -> list[frozenset[str]]:
    unique = sorted(set(options), key=lambda option: (len(option), sorted(option)))
    minimal: list[frozenset[str]] = []
    for option in unique:
        if any(existing <= option for existing in minimal):
            continue
        minimal.append(option)
    return minimal
