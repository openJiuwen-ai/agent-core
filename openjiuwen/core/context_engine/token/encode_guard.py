# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bound the cost of a single tiktoken encode, and cache exact counts so the hot
path never repeats work.

tiktoken applies BPE per pre-token piece and, up to 0.12, merged oversized
pieces quadratically: measured on 0.12.0 / ``cl100k_base``, one separator-free
run cost roughly 4x per doubling of its length — 16K chars 0.46s, 64K chars
7.0s, and a 400K-char run never returned within minutes. Tool results can carry
exactly such blobs (one repeated character, a rule line, or a long
``errors="replace"`` run from reading a binary file as text) and the context
engine counts tokens on the event loop, so a single blob was enough to stop
every websocket heartbeat and blow up the turn.

tiktoken 0.14.0 fixed that merge: identical token counts, 128K-char run in
0.020s, 400K-char blob in 0.080s. So this guard is no longer the primary
defence — it is kept as a backstop for environments still pinned to an older
tiktoken, and so that a future regression cannot silently bring the stall back.
``test_token_encode_guard.py`` carries a canary that fails if upstream turns
superlinear on oversized pieces again.

Ordinary content never trips the guard: text, JSON, logs, minified code and CJK
paragraphs keep every piece far below :data:`MAX_PIECE_CHARS`. Inputs above the
limit are counted with the character estimate (biased high, see
:func:`estimate_tokens`) instead of being encoded.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Callable

from openjiuwen.core.common.logging import logger

__all__ = [
    "CHARS_PER_TOKEN",
    "MAX_PIECE_CHARS",
    "CountCache",
    "count_with_guard",
    "estimate_tokens",
    "is_pathological",
    "longest_piece_length",
]

MAX_PIECE_CHARS = 16_384
"""Longest separator-free run that is still encoded; longer means estimate.

Sized against the *old* quadratic merge, not today's: a regressed tiktoken
spends ~0.5s on a piece of this length (measured 0.46s on 0.12.0), which is the
worst case this backstop tolerates. On 0.14.0 the same piece encodes in ~0.002s,
so realistic content keeps its exact count.
"""

CHARS_PER_TOKEN = 3
"""ASCII characters per estimated token; every non-ASCII code point counts as one."""

_PIECE_DELIMITER = "\n"


def _separator_codes() -> list[int]:
    """Code points that end a tiktoken pre-token piece in practice.

    Whitespace and punctuation break pieces. Letters and digits do not, but
    tiktoken caps digit runs at three characters, which keeps them cheap on its
    own; only unbroken letter/symbol runs can grow without bound.

    tiktoken breaks a letter run at any non-letter, so non-ASCII punctuation and
    spaces bound a piece exactly like ASCII ones do. They are listed explicitly
    (General Punctuation, CJK Symbols and Punctuation, and the full-width forms
    except the full-width letters and digits) because otherwise a long CJK
    paragraph looks like a single unbounded piece here while tiktoken actually
    splits it at every ``，``/``。``.
    """
    return [
        *range(0x00, 0x21),
        0x7F,
        *range(0x21, 0x30),
        *range(0x3A, 0x41),
        *range(0x5B, 0x61),
        *range(0x7B, 0x7F),
        *range(0x2000, 0x2070),
        *range(0x3000, 0x3040),
        *range(0xFF01, 0xFF10),
        *range(0xFF1A, 0xFF21),
        *range(0xFF3B, 0xFF41),
        *range(0xFF5B, 0xFF66),
    ]


_PIECE_SEPARATOR_TABLE = str.maketrans({code: _PIECE_DELIMITER for code in _separator_codes()})


def longest_piece_length(text: str) -> int:
    """Return the length of the longest separator-free run, i.e. the longest piece.

    ``str.translate`` plus ``str.split`` keep this at C speed and linear; the
    scan stops as soon as the limit is reached.
    """
    if not text:
        return 0
    longest = 0
    for piece in text.translate(_PIECE_SEPARATOR_TABLE).split(_PIECE_DELIMITER):
        if len(piece) > longest:
            longest = len(piece)
            if longest >= MAX_PIECE_CHARS:
                break
    return longest


def is_pathological(text: str) -> bool:
    """Return whether encoding ``text`` is refused for cost reasons."""
    return longest_piece_length(text) >= MAX_PIECE_CHARS


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a text that is not encoded, biased so it never under-counts.

    ASCII keeps the documented ``CHARS_PER_TOKEN`` ratio, which already leans
    high (English runs 3.5-4 characters per token). Every non-ASCII code point
    counts as a full token, because BPE needs roughly one token per CJK, emoji
    or full-width character: ``len // 3`` would under-count such text 2-3x, and
    a low count lets an oversized payload slip past the offload and compaction
    thresholds, which is the unsafe direction.
    """
    ascii_chars = len(text.encode("ascii", "ignore"))
    return ascii_chars // CHARS_PER_TOKEN + (len(text) - ascii_chars)


class CountCache:
    """Bounded LRU cache of exact counts, keyed by the counted text.

    Every turn re-counts the whole history for offload thresholds and
    compression metrics; without a cache that is a full encode per message per
    turn. Both an entry count and a retained-character budget bound memory, and
    a text larger than the whole budget is simply not cached.
    """

    def __init__(self, *, max_entries: int = 2_048, max_chars: int = 4_000_000) -> None:
        self._entries: OrderedDict[str, int] = OrderedDict()
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._chars = 0
        self._lock = Lock()

    def get(self, text: str) -> int | None:
        with self._lock:
            count = self._entries.get(text)
            if count is None:
                return None
            self._entries.move_to_end(text)
            return count

    def put(self, text: str, count: int) -> None:
        if len(text) > self._max_chars:
            return
        with self._lock:
            if self._entries.pop(text, None) is not None:
                self._chars -= len(text)
            self._entries[text] = count
            self._chars += len(text)
            while self._entries and (len(self._entries) > self._max_entries or self._chars > self._max_chars):
                evicted_text, _ = self._entries.popitem(last=False)
                self._chars -= len(evicted_text)


def count_with_guard(text: str, *, cache: CountCache, encode: Callable[[str], int]) -> int:
    """Count ``text`` once, with the pathological-input guard and the exact-count cache.

    ``encode`` performs the real encoding; an error it raises propagates to the
    caller, which keeps its own fallback out of this cache. Whatever ``encode``
    returns is cached as the exact count.
    """
    cached = cache.get(text)
    if cached is not None:
        return cached
    if is_pathological(text):
        logger.debug("token encode skipped for oversized pre-token piece: chars=%s", len(text))
        count = estimate_tokens(text)
    else:
        count = encode(text)
    cache.put(text, count)
    return count
