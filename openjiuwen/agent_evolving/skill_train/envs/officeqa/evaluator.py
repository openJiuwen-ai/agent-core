# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeQA scoring helpers (exact match + token F1).

Normalization keeps digits, decimal points, minus signs, and percent signs so
numeric answers stay comparable after lowercasing and punctuation stripping.
"""

from __future__ import annotations

import re
import string
from collections import Counter

_KEEP_PUNCT = frozenset("0123456789.-%")
_DROP_UNITS = re.compile(
    r"\b(million|millions|billion|billions|dollars|dollar|nominal)\b",
)


def _fold_answer(raw: str) -> str:
    """Lowercase, strip noise tokens, and collapse whitespace."""
    folded = raw.lower().strip().replace(",", "")
    kept: list[str] = []
    for ch in folded:
        if ch not in string.punctuation or ch in _KEEP_PUNCT:
            kept.append(ch)
    cleaned = _DROP_UNITS.sub(" ", "".join(kept))
    return " ".join(cleaned.split())


def normalize_answer(text: str) -> str:
    """Normalize an answer string for EM / F1 comparison."""
    return _fold_answer(text)


def exact_match(prediction: str, gold: str) -> float:
    """Binary EM after :func:`normalize_answer`."""
    return float(_fold_answer(prediction) == _fold_answer(gold))


def token_f1(prediction: str, gold: str) -> float:
    """Harmonic mean of token precision/recall on folded answers."""
    left = _fold_answer(prediction).split()
    right = _fold_answer(gold).split()
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    overlap = sum((Counter(left) & Counter(right)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(left)
    recall = overlap / len(right)
    return 2.0 * precision * recall / (precision + recall)


def evaluate(prediction: str, gold: str) -> dict:
    """Score one prediction against a single gold string."""
    trimmed = prediction.strip()
    return {
        "em": exact_match(trimmed, gold),
        "f1": token_f1(trimmed, gold),
        "predicted_answer": trimmed,
        "gold_answer": gold,
    }
