# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Heuristic decode of subprocess stdout/stderr.

Prefer UTF-8. Keep UTF-8 when replacement characters are sparse (for example
``head -c`` cutting a multi-byte character). Fall back to GBK-family encodings
only when UTF-8 looks badly broken.

Never use "strict UTF-8 fail → decode the whole buffer as GBK": a truncated
UTF-8 weather page would then turn 「南京」 into 「鍗椾含」.
"""
from __future__ import annotations

import codecs
from typing import Optional

_REPL = "\ufffd"
_FALLBACKS = ("gb18030", "gbk", "cp936")
# Stream path: wait for a prefix (or EOF) before locking the encoding so a
# split UTF-8 sequence at the first chunk boundary is not treated as GBK.
_LOCK_AFTER_BYTES = 256


def _keep_utf8(n_rep: int, n_chars: int) -> bool:
    if n_rep == 0:
        return True
    if n_chars <= 0:
        return True
    ratio = n_rep / n_chars
    if n_rep <= 2 and ratio < 0.02:
        return True
    return ratio <= 0.01


def _utf8_only_fails_at_tail(data: bytes) -> bool:
    """True when the buffer is valid UTF-8 except an incomplete sequence at the end."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        if exc.start < max(0, len(data) - 3):
            return False
        try:
            data[:exc.start].decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True


def choose_output_encoding(data: bytes) -> str:
    """Pick a codec for *data* without looking at which shell produced it."""
    if not data:
        return "utf-8"
    if _utf8_only_fails_at_tail(data):
        return "utf-8"
    utf8 = data.decode("utf-8", errors="replace")
    n_rep = utf8.count(_REPL)
    if _keep_utf8(n_rep, len(utf8)):
        return "utf-8"
    best_enc, best_rep = "utf-8", n_rep
    for enc in _FALLBACKS:
        try:
            cand = data.decode(enc, errors="replace")
        except LookupError:
            continue
        cand_rep = cand.count(_REPL)
        if cand_rep < best_rep:
            best_enc, best_rep = enc, cand_rep
    return best_enc


def decode_command_output(data: bytes, encoding: Optional[str] = None) -> str:
    """Decode subprocess output.

    Args:
        data: Raw stdout/stderr bytes.
        encoding: If set, lock this codec (caller opted out of the heuristic).
    """
    if not data:
        return ""
    enc = encoding or choose_output_encoding(data)
    return data.decode(enc, errors="replace")


class IncrementalCommandDecoder:
    """Lock encoding after a prefix (or EOF), then decode incrementally."""

    def __init__(self, encoding: Optional[str] = None) -> None:
        self._forced = encoding
        self._locked: Optional[str] = encoding
        self._pending = bytearray()
        self._incremental = (
            codecs.getincrementaldecoder(encoding)(errors="replace") if encoding else None
        )

    def feed(self, chunk: bytes, *, final: bool = False) -> str:
        if self._locked is not None:
            if self._incremental is None:
                self._incremental = codecs.getincrementaldecoder(self._locked)(errors="replace")
            if not chunk and not final:
                return ""
            return self._incremental.decode(chunk, final=final)

        if chunk:
            self._pending.extend(chunk)
        if not final and len(self._pending) < _LOCK_AFTER_BYTES:
            return ""
        return self._flush_lock(final=final)

    def _flush_lock(self, *, final: bool) -> str:
        data = bytes(self._pending)
        self._pending.clear()
        self._locked = self._forced or choose_output_encoding(data)
        self._incremental = codecs.getincrementaldecoder(self._locked)(errors="replace")
        if not data and not final:
            return ""
        return self._incremental.decode(data, final=final)
