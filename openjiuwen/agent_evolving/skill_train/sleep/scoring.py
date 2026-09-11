# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Heuristic scoring helpers for sleep replay."""

from __future__ import annotations

import re


def fold_text(text: str) -> str:
    folded = (text or "").lower().strip()
    folded = re.sub(r"[^\w\s]", " ", folded)
    folded = re.sub(r"\s+", " ", folded)
    return folded.strip()


def exact_score(reference: str, response: str) -> float:
    ref = fold_text(reference)
    resp = fold_text(response)
    if not ref:
        return 0.0
    return 1.0 if ref in resp or resp == ref else 0.0


def keyword_soft_score(reference: str, response: str) -> float:
    tokens = [token for token in fold_text(reference).split() if len(token) > 2]
    if not tokens:
        return 0.0
    unique = set(tokens)
    resp = fold_text(response)
    hits = sum(1 for token in unique if token in resp)
    return hits / len(unique)


def score_exact_pair(reference: str, response: str) -> tuple[float, float, str]:
    hard = exact_score(reference, response)
    soft = max(hard, keyword_soft_score(reference, response))
    return hard, soft, f"exact-match={hard}"


def score_rubric_keywords(reference: str, response: str) -> tuple[float, float, str]:
    soft = keyword_soft_score(reference, response)
    hard = 1.0 if soft >= 0.8 else 0.0
    return hard, soft, f"rubric keyword soft={soft:.2f}"
