# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Best-effort text encoding detection for local file reads.

The detector is intentionally conservative: it prefers a full strict decode of
the file so a mostly-ASCII prefix cannot hide GBK/ANSI bytes later in the file.
Only files above ``_FULL_SCAN_LIMIT`` fall back to sample-based detection.
"""

from __future__ import annotations

import codecs
import os
import pathlib
from typing import Optional

_SAMPLE_SIZE = 256 * 1024
_FULL_SCAN_LIMIT = 8 * 1024 * 1024
_SCAN_CHUNK_SIZE = 256 * 1024
_CANDIDATE_ENCODINGS = ("utf-8", "gb18030", "big5")


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
    except (UnicodeDecodeError, LookupError):
        return False
    return True


def _normalize_encoding(encoding: str) -> str:
    try:
        return codecs.lookup(encoding).name
    except (LookupError, TypeError):
        return "utf-8"


def detect_text_encoding(file_path: str | os.PathLike[str]) -> str:
    """Detect a text file's encoding without modifying its bytes.

    Detection order is BOM, strict UTF-8, GB18030, Big5, then
    ``charset-normalizer``. UTF-8 files keep their existing behavior, while
    GBK/ANSI files are decoded as GB18030 (a superset of GBK/GB2312).

    Args:
        file_path: Path to the file to inspect.

    Returns:
        A Python codec name. ``utf-8`` is returned when no candidate matches,
        so the caller still reports the original decode error.
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
        for encoding in _CANDIDATE_ENCODINGS:
            if _can_decode_file(path, encoding):
                return _normalize_encoding(encoding)
        return "utf-8"

    # Large files: avoid a second full pass. charset-normalizer is more
    # reliable than trying UTF-8 first on a possibly ASCII-only prefix.
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(sample).best()
        if best is not None and best.encoding:
            return _normalize_encoding(best.encoding)
    except Exception:  # noqa: BLE001 - detection must never fail the read
        pass

    for encoding in _CANDIDATE_ENCODINGS:
        try:
            sample.decode(encoding)
            return _normalize_encoding(encoding)
        except UnicodeDecodeError:
            continue
    return "utf-8"
