# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Text encoding guards for run artifacts."""

from __future__ import annotations

import unicodedata
from typing import Any

_MOJIBAKE_MARKERS = set(
    "\u7487\u8702\u8d1f\u93c2\u62cc\u5158\u5a67\u612c\u504d\u9473"
    "\u6212\u7d12\u6d93\u6c2c\u57d7\u6d63\u6ec5\u7d89\u6924\u70b9"
    "\u6d63\u95c2\u820d\u67ca\u9352\u6735\u7d94\u9462\u4ecb\u7d12"
    "\u701a\u8235\u7c2e\u934c\u3128\u6d7c\u4f77\u7b1f\u95c8\u9598"
    "\u9428\u509b\u878d\u8d44\u94bb\u70b9\u94fe"
)
_LATIN_MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\ufffd")
_MOJIBAKE_SEQUENCES = (
    "\u7487\u8702",
    "\u6d93\u20ac",
    "\u9286",
    "\u9225",
    "\u934f",
    "\u93c2",
    "\u9428",
    "\u6d63",
    "\u4e47",
)
_SUSPICIOUS_MOJIBAKE_BLOCKS = (
    "ARMENIAN",
    "LATIN EXTENDED",
    "IPA EXTENSIONS",
)
_MARKER_MIN_COUNT = 4
_MARKER_MIN_DENSITY = 0.18


def repair_text_mojibake(value: str) -> str:
    """Repair common mojibake while leaving normal text unchanged."""
    text = str(value or "")
    if not text:
        return text

    original_score = _mojibake_score(text)
    if original_score <= 0:
        return text

    best = text
    best_score = original_score
    for encoding in ("gbk", "gb18030", "latin1", "cp1252"):
        for encode_errors in ("strict", "ignore"):
            try:
                raw = text.encode(encoding, errors=encode_errors)
            except (LookupError, UnicodeEncodeError):
                continue
            for decode_errors in ("strict", "ignore"):
                try:
                    candidate = raw.decode("utf-8", errors=decode_errors)
                except UnicodeDecodeError:
                    continue
                candidate = candidate.strip()
                if not candidate or candidate == text:
                    continue
                candidate_score = _mojibake_score(candidate)
                if _better_repair(
                    original=text,
                    candidate=candidate,
                    original_score=best_score,
                    candidate_score=candidate_score,
                ):
                    best = candidate
                    best_score = candidate_score
    return best


def repair_payload_mojibake(value: Any) -> Any:
    """Recursively repair strings inside JSON/YAML-like payloads."""
    if isinstance(value, str):
        return repair_text_mojibake(value)
    if isinstance(value, list):
        return [repair_payload_mojibake(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_payload_mojibake(item) for item in value)
    if isinstance(value, dict):
        return {
            repair_text_mojibake(str(key)) if isinstance(key, str) else key: repair_payload_mojibake(item)
            for key, item in value.items()
        }
    return value


def has_unrepaired_mojibake(value: str) -> bool:
    """Return whether text still looks like mojibake after best-effort repair."""
    text = str(value or "")
    if not text:
        return False
    repaired = repair_text_mojibake(text)
    if repaired != text:
        text = repaired
    return _unrepaired_mojibake_score(text) >= 4


def _better_repair(
    *,
    original: str,
    candidate: str,
    original_score: int,
    candidate_score: int,
) -> bool:
    if candidate_score >= original_score:
        return False
    if _cjk_count(candidate) < 2:
        return False
    return len(candidate) >= max(2, int(len(original) * 0.25))


def _mojibake_score(text: str) -> int:
    score = _dense_marker_score(text)
    score += text.count("\ufffd") * 3
    question_count = text.count("?")
    if question_count >= 2 and _cjk_count(text):
        score += question_count
    score += sum(text.count(marker) * 2 for marker in _LATIN_MOJIBAKE_MARKERS)
    score += sum(text.count(marker) * 4 for marker in _MOJIBAKE_SEQUENCES)
    score += _suspicious_script_score(text)
    return score


def _unrepaired_mojibake_score(text: str) -> int:
    score = sum(text.count(marker) * 4 for marker in _MOJIBAKE_SEQUENCES)
    score += text.count("\ufffd") * 3
    score += _suspicious_script_score(text)
    return score


def _dense_marker_score(text: str) -> int:
    marker_count = sum(1 for char in text if char in _MOJIBAKE_MARKERS)
    if marker_count < _MARKER_MIN_COUNT:
        return 0
    cjk_count = _cjk_count(text)
    if not cjk_count:
        return marker_count
    if marker_count / cjk_count < _MARKER_MIN_DENSITY:
        return 0
    return marker_count


def _suspicious_script_score(text: str) -> int:
    meaningful = [char for char in text if not char.isspace()]
    if not meaningful:
        return 0
    suspicious = 0
    for char in meaningful:
        if ord(char) < 0x80:
            continue
        name = unicodedata.name(char, "")
        if any(block in name for block in _SUSPICIOUS_MOJIBAKE_BLOCKS):
            suspicious += 1
    if suspicious < 4:
        return 0
    density = suspicious / max(1, len(meaningful))
    if density < 0.08:
        return 0
    return suspicious


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


__all__ = [
    "has_unrepaired_mojibake",
    "repair_payload_mojibake",
    "repair_text_mojibake",
]
