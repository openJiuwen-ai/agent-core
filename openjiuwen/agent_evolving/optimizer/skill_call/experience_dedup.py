# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic experience de-duplication (LLM skip_reason is not reliable)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    ParsedExperienceDraft,
)
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.core.common.logging import logger

_PUNCT_RE = re.compile(r"[\s\-_/\\|.,;:!?，。；：、！？·•\"'`“”‘’()\[\]{}<>]+")
_SUMMARY_RATIO = 0.70
_SUMMARY_JACCARD = 0.35
_CAUSE_RATIO = 0.80
_CONTENT_RATIO = 0.82
_MIN_CAUSE_LEN = 12
_MIN_CONTENT_LEN = 24
_NGRAM_SIZE = 3
_SHARED_SPAN = 10
_SIGNAL_SUBSTRING = 8
_SIGNAL_RATIO = 0.72
_SIGNAL_JACCARD = 0.35

Fingerprint = Tuple[str, str, str]


def collect_existing_records(ctx: EvolutionContext) -> List[EvolutionRecord]:
    records: List[EvolutionRecord] = []
    for bucket in (
        ctx.existing_desc_records,
        ctx.existing_body_records,
        ctx.existing_script_records,
    ):
        records.extend(bucket or [])
    return records


def filter_uncovered_signals(
    signals: Sequence[EvolutionSignal],
    existing: Sequence[EvolutionRecord],
) -> List[EvolutionSignal]:
    """Keep only signals that are not already represented by persisted experiences."""
    if not existing:
        return list(signals)
    kept: List[EvolutionSignal] = []
    for signal in signals:
        if _signal_already_covered(signal, existing):
            logger.info(
                "[SkillExperience] skip already-covered signal type=%s excerpt=%s",
                signal.signal_type,
                (signal.excerpt or "")[:80],
            )
            continue
        kept.append(signal)
    return kept


def filter_duplicate_records(
    records: Sequence[EvolutionRecord],
    existing: Sequence[EvolutionRecord],
) -> List[EvolutionRecord]:
    """Drop new records that duplicate existing or earlier in-batch experiences.

    Real merge_target values (an existing record id) are updates and are kept.
    Fake or empty merge_target still goes through semantic de-duplication.
    """
    existing_ids = _existing_ids(existing)
    fingerprints = _fingerprints_from_records(existing)
    kept: List[EvolutionRecord] = []
    dropped = 0
    for record in records:
        if _is_real_merge(record.change.merge_target, existing_ids):
            kept.append(record)
            fingerprints.append(_fingerprint_from_record(record))
            continue
        if _matches_any(_fingerprint_from_record(record), fingerprints):
            dropped += 1
            logger.info(
                "[SkillExperience] drop duplicate experience summary=%s existing_count=%s",
                (record.summary or "")[:80],
                len(fingerprints),
            )
            continue
        kept.append(record)
        fingerprints.append(_fingerprint_from_record(record))
    if dropped:
        logger.info("[SkillExperience] dropped %s duplicate experience record(s)", dropped)
    return kept


def filter_duplicate_drafts(
    drafts: Sequence[ParsedExperienceDraft],
    existing: Sequence[EvolutionRecord],
) -> List[ParsedExperienceDraft]:
    existing_ids = _existing_ids(existing)
    fingerprints = _fingerprints_from_records(existing)
    kept: List[ParsedExperienceDraft] = []
    for draft in drafts:
        if _is_real_merge(draft.patch.merge_target, existing_ids):
            kept.append(draft)
            fingerprints.append(_fingerprint_from_draft(draft))
            continue
        if _matches_any(_fingerprint_from_draft(draft), fingerprints):
            logger.info(
                "[SkillExperience] drop duplicate draft summary=%s",
                (draft.summary or "")[:80],
            )
            continue
        kept.append(draft)
        fingerprints.append(_fingerprint_from_draft(draft))
    return kept


def filter_duplicate_candidates(
    candidates: Sequence[Dict[str, Any]],
    existing: Sequence[EvolutionRecord],
) -> List[Dict[str, Any]]:
    existing_ids = _existing_ids(existing)
    fingerprints = _fingerprints_from_records(existing)
    kept: List[Dict[str, Any]] = []
    for candidate in candidates:
        if _is_real_merge(candidate.get("merge_target"), existing_ids):
            kept.append(dict(candidate))
            fingerprints.append(_fingerprint_from_mapping(candidate))
            continue
        if _matches_any(_fingerprint_from_mapping(candidate), fingerprints):
            logger.info(
                "[SkillExperience] drop duplicate analyzer candidate summary=%s",
                str(candidate.get("summary") or "")[:80],
            )
            continue
        kept.append(dict(candidate))
        fingerprints.append(_fingerprint_from_mapping(candidate))
    return kept


def _existing_ids(existing: Sequence[EvolutionRecord]) -> set[str]:
    return {str(record.id) for record in existing if getattr(record, "id", None)}


def _is_real_merge(merge_target: object, existing_ids: set[str]) -> bool:
    target = str(merge_target or "").strip()
    return bool(target) and target in existing_ids


def _fingerprints_from_records(records: Sequence[EvolutionRecord]) -> List[Fingerprint]:
    return [_fingerprint_from_record(record) for record in records]


def _fingerprint_from_record(record: EvolutionRecord) -> Fingerprint:
    return _fingerprint(
        record.summary or record.change.summary,
        record.root_cause,
        record.change.content,
    )


def _fingerprint_from_draft(draft: ParsedExperienceDraft) -> Fingerprint:
    return _fingerprint(draft.summary, draft.root_cause, draft.patch.content)


def _fingerprint_from_mapping(data: Dict[str, Any]) -> Fingerprint:
    keywords = data.get("keywords") or []
    keyword_blob = " ".join(str(item).strip() for item in keywords if str(item).strip())
    summary = str(data.get("summary") or "")
    if keyword_blob:
        summary = f"{summary} {keyword_blob}".strip()
    return _fingerprint(
        summary,
        str(data.get("root_cause") or ""),
        str(data.get("content") or ""),
    )


def _fingerprint(summary: Optional[str], root_cause: Optional[str], content: Optional[str]) -> Fingerprint:
    return (_fold(summary), _fold(root_cause), _fold(content)[:180])


def _fold(value: Optional[str]) -> str:
    return _PUNCT_RE.sub("", str(value or "").strip().lower())


def _signal_already_covered(
    signal: EvolutionSignal,
    existing: Sequence[EvolutionRecord],
) -> bool:
    context = signal.context or {}
    needles = [
        _fold(signal.excerpt),
        _fold(context.get("user_message")),
    ]
    needles = [needle for needle in needles if needle]
    if not needles:
        return False
    for record in existing:
        haystacks = [
            _fold(record.context),
            _fold(record.summary or record.change.summary),
            _fold(record.root_cause),
            _fold(record.change.content)[:240],
        ]
        for needle in needles:
            for haystack in haystacks:
                if _text_overlap(
                    needle,
                    haystack,
                    substring_min=_SIGNAL_SUBSTRING,
                    ratio=_SIGNAL_RATIO,
                    jaccard=_SIGNAL_JACCARD,
                ):
                    return True
    return False


def _matches_any(candidate: Fingerprint, existing: Iterable[Fingerprint]) -> bool:
    summary, cause, content = candidate
    if not summary and not cause and not content:
        return False
    for other in existing:
        if _pair_duplicate(candidate, other):
            return True
    return False


def _both_at_least(left: str, right: str, min_len: int) -> bool:
    return len(left) >= min_len and len(right) >= min_len


def _pair_duplicate(left: Fingerprint, right: Fingerprint) -> bool:
    summary, cause, content = left
    other_summary, other_cause, other_content = right
    if _text_overlap(summary, other_summary, substring_min=16):
        return True
    if _both_at_least(cause, other_cause, _MIN_CAUSE_LEN):
        if _text_overlap(cause, other_cause, substring_min=16, ratio=_CAUSE_RATIO, jaccard=0.40):
            return True
    if _both_at_least(content, other_content, _MIN_CONTENT_LEN):
        if content == other_content or _ratio(content, other_content) >= _CONTENT_RATIO:
            return True
        if _shared_span(content, other_content, _SHARED_SPAN + 6):
            return True
    return False


def _text_overlap(
    left: str,
    right: str,
    *,
    substring_min: int,
    ratio: float = _SUMMARY_RATIO,
    jaccard: float = _SUMMARY_JACCARD,
) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    if _contains(left, right, min_len=substring_min):
        return True
    if _shared_span(left, right, _SHARED_SPAN):
        return True
    if _ratio(left, right) >= ratio:
        return True
    if _ngram_jaccard(left, right) >= jaccard:
        return True
    return False


def _contains(left: str, right: str, min_len: int = 16) -> bool:
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter) >= min_len and shorter in longer


def _shared_span(left: str, right: str, min_len: int) -> bool:
    if not left or not right or min_len <= 0:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < min_len:
        return False
    for index in range(len(shorter) - min_len + 1):
        if shorter[index:index + min_len] in longer:
            return True
    return False


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _ngram_jaccard(left: str, right: str, size: int = _NGRAM_SIZE) -> float:
    left_grams = _ngrams(left, size)
    right_grams = _ngrams(right, size)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _ngrams(value: str, size: int) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index:index + size] for index in range(len(value) - size + 1)}
