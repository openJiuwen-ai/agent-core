from __future__ import annotations

import re


def exact_or_choice_accuracy(predicted: str, gold: str) -> float:
    pred_choice = _extract_choice(predicted)
    gold_choice = _extract_choice(gold)
    if gold_choice and pred_choice:
        return 1.0 if pred_choice == gold_choice else 0.0
    return 1.0 if _normalize(predicted) == _normalize(gold) else 0.0


def _extract_choice(text: str) -> str | None:
    match = re.search(r"\b([A-D])\b", text.strip(), flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

