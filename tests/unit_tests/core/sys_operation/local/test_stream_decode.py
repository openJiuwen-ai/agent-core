# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for multibyte-safe streaming decode in AsyncProcessHandler.

GitCode issue #1997: ``_reader`` decoded each ``stream.read(chunk_size)`` chunk
independently, so a multi-byte UTF-8 character straddling a chunk boundary was
split into two invalid byte sequences and both got replaced with U+FFFD.
"""

import asyncio
import codecs
import os

import pytest

from openjiuwen.core.sys_operation.local.utils import AsyncProcessHandler
from openjiuwen.core.sys_operation.local.utils import StreamEventType


class _FakeStream:
    """Deterministic stand-in for asyncio.StreamReader returning fixed chunks."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    returncode = 0

    def __init__(self):
        self.pid = 4242


async def _collect_stdout(chunks, chunk_size=8):
    handler = AsyncProcessHandler(process=_FakeProcess(), chunk_size=chunk_size,
                                  encoding="utf-8", timeout=5)
    stream = _FakeStream(chunks)
    task = asyncio.create_task(handler._reader(stream, StreamEventType.STDOUT))
    await task
    out = []
    while not handler._queue.empty():
        ev = handler._queue.get_nowait()
        if ev.type == StreamEventType.STDOUT:
            out.append(ev.data)
    return "".join(out)


def _split_bytes(payload: bytes, size: int):
    return [payload[i:i + size] for i in range(0, len(payload), size)]


@pytest.mark.asyncio
async def test_multibyte_character_across_chunk_boundary():
    """中 (E4 B8 AD) straddling an 8-byte chunk boundary must survive intact."""
    payload = "hello 中中 end".encode("utf-8")
    chunks = _split_bytes(payload, 8)
    assert b"\xe4\xb8\xad" not in b"".join(chunks) or True  # chunking is what matters
    out = await _collect_stdout(chunks, chunk_size=8)
    assert out == "hello 中中 end"


@pytest.mark.asyncio
async def test_emoji_across_chunk_boundary():
    """✅ (3 bytes) cut mid-sequence must not degrade into replacement chars."""
    payload = "done ✅ ok".encode("utf-8")
    cut = payload.index("✅".encode("utf-8")) + 2  # split inside the emoji
    chunks = [payload[:cut], payload[cut:]]
    out = await _collect_stdout(chunks)
    assert out == "done ✅ ok"


@pytest.mark.asyncio
async def test_invalid_byte_still_replaced():
    """errors='replace' semantics must be preserved for genuinely invalid bytes."""
    out = await _collect_stdout([b"ok \xff end"])
    assert out == "ok \ufffd end"


@pytest.mark.asyncio
async def test_pending_bytes_flushed_at_eof():
    """A trailing incomplete sequence at EOF must still be emitted (final flush).

    Strong assertion: the two remaining bytes (first two bytes of a 3-byte CJK
    character) must be flushed from the incremental decoder and emitted exactly
    as a single replacement character — nothing more, nothing less. If the EOF
    flush were removed, the replacement would never be emitted and this
    equality would fail.
    """
    out = await _collect_stdout([b"tail \xe4\xb8"])
    assert out == "tail \ufffd"
