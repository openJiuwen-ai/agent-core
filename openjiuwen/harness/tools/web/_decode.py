# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Response body charset detection and decoding.

Mirrors the original synchronous decoder but takes raw bytes plus a
Content-Type string (instead of a ``requests.Response``), so it works with the
aiohttp transport. ``requests``' ``apparent_encoding`` is reproduced with
``charset_normalizer`` (the very library ``requests`` uses internally).
"""

from __future__ import annotations

import re

_CHARSET_HEADER_RE = re.compile(r"charset=([^\s;]+)", flags=re.IGNORECASE)
_CHARSET_META_RE = re.compile(
    br"""<meta[^>]+charset=["']?\s*([A-Za-z0-9._-]+)""",
    flags=re.IGNORECASE,
)
_MOJIBAKE_MARKERS = ("mojibake", "Ã", "Â", "â", "ï¿½")


def _extract_declared_charset(content_type: str, head_bytes: bytes) -> str:
    """Extract charset from a Content-Type header or HTML meta tags."""
    header_match = _CHARSET_HEADER_RE.search(content_type or "")
    if header_match:
        return header_match.group(1).strip().strip("\"'")

    meta_match = _CHARSET_META_RE.search((head_bytes or b"")[:4096])
    if meta_match:
        try:
            return meta_match.group(1).decode("ascii", errors="ignore").strip()
        except Exception:
            return ""
    return ""


def _score_decoded_text(value: str) -> float:
    """Score decoded text quality to avoid mojibake."""
    if not value:
        return float("-inf")
    score = 0.0
    score -= value.count("�") * 8
    for marker in _MOJIBAKE_MARKERS:
        score -= value.count(marker) * 3
    score += len(re.findall(r"[一-鿿]", value)) * 0.15
    score += len(re.findall(r"[A-Za-z0-9]", value)) * 0.02
    score -= len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)) * 5
    return score


def _detect_apparent_encoding(raw: bytes) -> str:
    """Detect encoding via charset_normalizer (requests' apparent_encoding equivalent)."""
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None:
            return (best.encoding or "").lower()
    except Exception:
        return ""
    return ""


def _decode_response_text(raw: bytes, *, content_type: str) -> str:
    """Decode response bytes with charset detection and a utf-8 fast path.

    Tries the declared charset, a charset_normalizer guess, and a fixed set of
    common encodings, scoring each successful decode to pick the least
    mojibake-prone result.

    The utf-8 fast path returns immediately when the body declares a utf-8
    family charset, has no BOM, and decodes cleanly (no U+FFFD). This is
    provably non-divergent: it is gated on the *declared* charset, so pages
    declaring a legacy charset or carrying a BOM still fall through to the full
    scoring path and decode exactly as before.

    Args:
        raw: Raw response body bytes.
        content_type: The response Content-Type header value.

    Returns:
        The best-effort decoded text (never raises; falls back to
        utf-8/replace).
    """
    if not raw:
        return ""

    declared = (_extract_declared_charset(content_type, raw) or "").lower()

    if declared in {"utf-8", "utf8", "utf-8-sig"} and not raw.startswith(b"\xef\xbb\xbf"):
        try:
            text = raw.decode("utf-8", errors="strict")
            if "�" not in text:
                return text
        except UnicodeDecodeError:
            pass

    apparent = _detect_apparent_encoding(raw)

    candidates: list[str] = []
    if declared and declared not in {"iso-8859-1", "latin-1", "latin1"}:
        candidates.append(declared)
    candidates.extend(
        [
            "utf-8-sig",
            "utf-8",
            apparent,
            "gbk",
            "gb18030",
            "big5",
            "shift_jis",
            "cp1252",
            "iso-8859-1",
        ]
    )

    decoded_candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for enc in candidates:
        enc = (enc or "").strip().lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            text = raw.decode(enc, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        decoded_candidates.append((_score_decoded_text(text), text))

    if decoded_candidates:
        decoded_candidates.sort(key=lambda item: item[0], reverse=True)
        return decoded_candidates[0][1]

    return raw.decode("utf-8", errors="replace")
