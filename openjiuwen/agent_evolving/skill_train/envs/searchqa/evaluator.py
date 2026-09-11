# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA scoring: exact match, token F1, and bidirectional substring EM."""

from __future__ import annotations

import string
from collections import Counter

_ARTICLES = frozenset({"a", "an", "the"})
_PUNCT = frozenset(string.punctuation)


def _canonical(text: str) -> str:
    """SQuAD-style normalize: lower, drop punctuation/articles, collapse space."""
    lowered = str(text).lower()
    without_punct = "".join(ch for ch in lowered if ch not in _PUNCT)
    tokens = [tok for tok in without_punct.split() if tok not in _ARTICLES]
    return " ".join(tokens)


def _pull_answer(response: str) -> str:
    """Prefer the last ``<answer>...</answer>`` span; else last non-empty line."""
    lower = response.lower()
    open_at = lower.rfind("<answer>")
    close_at = lower.rfind("</answer>")
    if open_at != -1 and close_at != -1 and close_at > open_at:
        start = open_at + len("<answer>")
        return response[start:close_at].strip()
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    return lines[-1] if lines else response.strip()


def _token_f1(pred_norm: str, gold_norm: str) -> float:
    pred_tokens = pred_norm.split()
    gold_tokens = gold_norm.split()
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0
    overlap = Counter(pred_tokens) & Counter(gold_tokens)
    shared = sum(overlap.values())
    if shared == 0:
        return 0.0
    precision = shared / len(pred_tokens)
    recall = shared / len(gold_tokens)
    return 2.0 * precision * recall / (precision + recall)


def _score_one(prediction: str, gold_answers: list[str]) -> tuple[float, float, float]:
    """Return ``(em, f1, sub_em)`` against the best matching gold."""
    pred_norm = _canonical(prediction)
    pred_tokens = pred_norm.split()
    if not pred_tokens:
        # Empty prediction: F1 is 1 only when some gold also normalizes empty.
        has_empty_gold = any(not _canonical(g).split() for g in gold_answers)
        em = 1.0 if has_empty_gold else 0.0
        f1 = 1.0 if has_empty_gold else 0.0
        sub = 1.0 if any(pred_norm in _canonical(g) or _canonical(g) in pred_norm for g in gold_answers) else 0.0
        return em, f1, sub

    best_em = 0.0
    best_f1 = 0.0
    best_sub = 0.0
    for gold in gold_answers:
        gold_norm = _canonical(gold)
        if pred_norm == gold_norm:
            best_em = 1.0
        best_f1 = max(best_f1, _token_f1(pred_norm, gold_norm))
        if gold_norm in pred_norm or pred_norm in gold_norm:
            best_sub = 1.0
    return best_em, best_f1, best_sub


def exact_match(prediction: str, gold_answers: list[str]) -> float:
    """Public EM helper used by unit tests and callers."""
    em, _f1, _sub = _score_one(str(prediction), list(gold_answers))
    return em


def f1_score(prediction: str, gold_answers: list[str]) -> float:
    """Public token-F1 helper used by unit tests and callers."""
    _em, f1, _sub = _score_one(str(prediction), list(gold_answers))
    return f1


def evaluate(prediction_text: str, gold_answers: list[str]) -> dict:
    """Score one model response against a list of gold answers."""
    answer = _pull_answer(prediction_text)
    em, f1, sub = _score_one(answer, gold_answers)
    return {
        "em": em,
        "f1": f1,
        "sub_em": sub,
        "predicted_answer": answer,
        "gold_answers": gold_answers,
    }
