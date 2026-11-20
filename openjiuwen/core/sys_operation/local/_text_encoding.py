# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Best-effort text encoding detection for local file reads.

The detector is intentionally conservative: UTF-8 is checked first, and when
several CJK encodings can decode the same bytes, the decoded text with the
fewest private-use/control characters wins. This prevents Big5 text from being
silently returned as GB18030 mojibake while keeping GB18030/GBK as the default
for ambiguous Simplified Chinese files.
"""

from __future__ import annotations

import codecs
import os
import pathlib
import re
from typing import Optional

_SAMPLE_SIZE = 256 * 1024
_FULL_SCAN_LIMIT = 8 * 1024 * 1024
_SCAN_CHUNK_SIZE = 256 * 1024
_QUALITY_SAMPLE_LIMIT = 2 * 1024 * 1024
_CANDIDATE_ENCODINGS = ("gb18030", "big5")

# CJK punctuation, Extension A, Unified Ideographs and compatibility ideographs.
_CJK_CHAR_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u0080-\u009f]")


def _bom_encoding(sample: bytes) -> Optional[str]:
    """Return the BOM-declared encoding, or ``None`` when no BOM is present."""
    if sample.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return None


def _read_sample(file_path: pathlib.Path, size: int) -> bytes:
    with open(file_path, "rb") as file:
        return file.read(size)


def _can_decode_file(file_path: pathlib.Path, encoding: str) -> bool:
    """Return whether ``encoding`` can strictly decode the complete file."""
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        with open(file_path, "rb") as file:
            while True:
                chunk = file.read(_SCAN_CHUNK_SIZE)
                if not chunk:
                    break
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except (UnicodeDecodeError, LookupError, OSError):
        return False
    return True


def _decode_file(file_path: pathlib.Path, encoding: str) -> Optional[str]:
    """Return the strictly decoded text, or ``None`` when decoding fails."""
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        parts: list[str] = []
        with open(file_path, "rb") as file:
            while True:
                chunk = file.read(_SCAN_CHUNK_SIZE)
                if not chunk:
                    break
                parts.append(decoder.decode(chunk))
            parts.append(decoder.decode(b"", final=True))
    except (UnicodeDecodeError, LookupError, OSError):
        return None
    return "".join(parts)


def _text_quality(text: str) -> int:
    """Score decoded text by CJK content and suspicious control characters.

    A wrong CJK decoding (for example Big5 bytes decoded as GB18030) tends to
    produce private-use or control characters. Correct GBK/Big5 text produces
    common CJK characters instead. A non-positive score means the candidate
    should not be trusted as text.
    """
    if len(text) > _QUALITY_SAMPLE_LIMIT:
        half = _QUALITY_SAMPLE_LIMIT // 2
        text = text[:half] + text[-half:]
    return (
        len(_CJK_CHAR_RE.findall(text)) * 2
        - len(_PRIVATE_USE_RE.findall(text)) * 20
        - len(_CONTROL_CHAR_RE.findall(text)) * 10
    )


def _choose_candidate(candidates: dict[str, str]) -> Optional[str]:
    """Pick the most plausible CJK candidate, preferring GB18030 on a tie."""
    if not candidates:
        return None
    if len(candidates) == 1:
        encoding, text = next(iter(candidates.items()))
        return encoding if _text_quality(text) > 0 else None

    scores = {encoding: _text_quality(text) for encoding, text in candidates.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _normalize_encoding(encoding: str) -> str:
    try:
        return codecs.lookup(encoding).name
    except (LookupError, TypeError):
        return "utf-8"


def detect_text_encoding(file_path: str | os.PathLike[str]) -> str:
    """Detect a text file's encoding without modifying its bytes.

    Detection order is BOM, strict UTF-8, then a quality-based choice between
    GB18030/GBK and Big5. GB18030 is preferred on a tie because the primary
    case is Simplified Chinese on Windows. ``charset-normalizer`` is only a
    sample-based fallback for large files, and its result must still look like
    CJK text.

    Args:
        file_path: Path to the file to inspect.

    Returns:
        A Python codec name. ``utf-8`` is returned when no candidate looks like
        text, so the caller still reports the original decode error.
    """
    path = pathlib.Path(file_path)
    try:
        size = path.stat().st_size
    except OSError:
        return "utf-8"
    if size == 0:
        return "utf-8"

    try:
        sample = _read_sample(path, _SAMPLE_SIZE)
    except OSError:
        return "utf-8"

    bom_encoding = _bom_encoding(sample)
    if bom_encoding is not None:
        return bom_encoding

    if size <= _FULL_SCAN_LIMIT:
        if _can_decode_file(path, "utf-8"):
            return "utf-8"

        candidates: dict[str, str] = {}
        for encoding in _CANDIDATE_ENCODINGS:
            text = _decode_file(path, encoding)
            if text is not None:
                candidates[encoding] = text
        best = _choose_candidate(candidates)
        return _normalize_encoding(best) if best is not None else "utf-8"

    # Large files: trust UTF-8 when the sample contains non-ASCII UTF-8 bytes.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if any(byte >= 0x80 for byte in sample):
            return "utf-8"

    candidates = {}
    for encoding in _CANDIDATE_ENCODINGS:
        try:
            candidates[encoding] = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
    best = _choose_candidate(candidates)
    if best is not None:
        return _normalize_encoding(best)

    # Last resort: charset-normalizer, but only when the decoded sample looks
    # like CJK text. This avoids turning arbitrary binary into "successful" text.
    try:
        from charset_normalizer import from_bytes

        match = from_bytes(sample).best()
        if match is not None and match.encoding:
            encoding = _normalize_encoding(match.encoding)
            try:
                decoded = sample.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                decoded = None
            if decoded is not None and _text_quality(decoded) > 0:
                return encoding
    except Exception:  # noqa: BLE001 - detection must never fail the read
        pass
    return "utf-8"
