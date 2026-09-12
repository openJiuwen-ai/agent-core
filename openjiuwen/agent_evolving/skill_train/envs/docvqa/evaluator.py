# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DocVQA scoring: Average Normalized Levenshtein Similarity (ANLS).

Pipeline:
  1. Pull the model answer from ``<answer>...</answer>`` (else last non-empty line).
  2. Flatten gold labels (string / list / nested dict / JSON literals).
  3. Score prediction vs each gold with ANLS; keep the best.

Public surface: :func:`extract_answer`, :func:`evaluate`.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from typing import Any

_ANLS_CUTOFF = 0.5
DEFAULT_ANLS_THRESHOLD = _ANLS_CUTOFF  # public alias
_ANSWER_TAG = re.compile(r"<answer>(.*?)</answer>", re.IGNORECASE | re.DOTALL)
_NESTED_GOLD_KEYS = ("answers", "ground_truth", "answer")
_DICT_VALUE_KEYS = ("text", "answer", "value")


def _collapse_ws(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _edit_distance(left: str, right: str) -> int:
    """Wagner–Fischer distance; shorter string drives the outer loop."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) > len(right):
        left, right = right, left

    prev = list(range(len(right) + 1))
    for i, ch_l in enumerate(left, start=1):
        curr = [i]
        for j, ch_r in enumerate(right, start=1):
            cost_ins = curr[j - 1] + 1
            cost_del = prev[j] + 1
            cost_sub = prev[j - 1] + (0 if ch_l == ch_r else 1)
            curr.append(min(cost_ins, cost_del, cost_sub))
        prev = curr
    return prev[-1]


def _anls_pair(pred: str, gold: str, cutoff: float) -> float:
    """ANLS for one (prediction, gold) pair after whitespace normalization."""
    p = _collapse_ws(pred)
    g = _collapse_ws(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    denom = max(len(p), len(g))
    ratio = _edit_distance(p, g) / denom
    if ratio >= cutoff:
        return 0.0
    return 1.0 - ratio


def _try_parse_literal(text: str) -> Any | None:
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def _flatten_golds(raw: Any) -> list[str]:
    """Recursively collect gold answer strings from heterogeneous payloads."""
    if raw is None:
        return [""]
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return [""]
        nested = _try_parse_literal(stripped)
        if nested is not None:
            return _flatten_golds(nested)
        return [stripped]
    if isinstance(raw, dict):
        for key in _NESTED_GOLD_KEYS:
            if key in raw:
                return _flatten_golds(raw[key])
        return [str(raw)]
    if isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray)):
        collected: list[str] = []
        for entry in raw:
            if isinstance(entry, dict):
                matched = False
                for key in _DICT_VALUE_KEYS:
                    if key in entry:
                        collected.extend(_flatten_golds(entry[key]))
                        matched = True
                        break
                if not matched:
                    collected.append(str(entry))
            else:
                collected.append(str(entry))
        return collected or [""]
    return [str(raw)]


def extract_answer(text: str) -> str:
    """Prefer the last ``<answer>`` block; otherwise the last non-empty line."""
    # Keep G.FMT.04-style exclusive end: match group is content between tags.
    hits = _ANSWER_TAG.findall(text)
    if hits:
        return hits[-1].strip()
    nonempty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if nonempty:
        return nonempty[-1]
    return text.strip()


def evaluate(prediction_text: str, gold_answers: Any) -> dict:
    """Return ANLS score plus parsed prediction / gold strings."""
    predicted = extract_answer(prediction_text)
    golds = _flatten_golds(gold_answers)
    best = 0.0
    for gold in golds:
        best = max(best, _anls_pair(predicted, gold, _ANLS_CUTOFF))
    return {
        "anls": best,
        "predicted_answer": predicted,
        "gold_answers": golds,
    }
