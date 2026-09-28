# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P0 regression: the tiktoken first-use load must stay off the event loop.

The first ``tiktoken.get_encoding`` in a process downloads the BPE vocabulary
through a blocking ``requests.get`` without timeout (~40s on blocked networks).
That load used to run synchronously inside ``ContextEngine.create_context`` on
the event loop, stalling the AgentServer loop long enough for the upper API
keepalive to tear down the WebSocket (frontend: 智能体连接不稳定 OA.05000010).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import openjiuwen.core.context_engine.token.tiktoken_counter as tiktoken_counter_module
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.token.tiktoken_counter import (
    TiktokenCounter,
    start_background_warm_up,
    warm_up_default_encoding,
)


class _FakeEncoding:
    def encode(self, text, disallowed_special=()):
        return [0] * (len(text) // 4 + 1)


@pytest.fixture(autouse=True)
def _fake_tiktoken(monkeypatch):
    import tiktoken

    monkeypatch.setattr(tiktoken, "get_encoding", lambda name="cl100k_base": _FakeEncoding())


@pytest.fixture(autouse=True)
def _reset_warm_up_thread():
    tiktoken_counter_module._warm_up_thread = None
    yield
    thread = tiktoken_counter_module._warm_up_thread
    if thread is not None:
        thread.join(timeout=5)
    tiktoken_counter_module._warm_up_thread = None


@pytest.mark.asyncio
async def test_create_context_loads_encoding_off_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen_threads: list[int] = []

    def spy(name: str = "cl100k_base") -> _FakeEncoding:
        seen_threads.append(threading.get_ident())
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return _FakeEncoding()

    import tiktoken

    monkeypatch.setattr(tiktoken, "get_encoding", spy)

    engine = ContextEngine()
    context = await engine.create_context("ctx-offload-check")

    assert context is not None
    assert seen_threads, "tiktoken encoding was never loaded"
    assert all(tid != loop_thread for tid in seen_threads)


@pytest.mark.asyncio
async def test_background_warm_up_is_single_flight(monkeypatch):
    import tiktoken

    calls: list[str] = []

    def slow_get(name: str = "cl100k_base") -> _FakeEncoding:
        calls.append(name)
        time.sleep(0.2)
        return _FakeEncoding()

    monkeypatch.setattr(tiktoken, "get_encoding", slow_get)

    start_background_warm_up()
    start_background_warm_up()
    start_background_warm_up()

    thread = tiktoken_counter_module._warm_up_thread
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(calls) == 1


def test_counter_falls_back_when_encoding_unavailable(monkeypatch):
    import tiktoken

    def boom(name: str = "cl100k_base") -> _FakeEncoding:
        raise RuntimeError("network down")

    monkeypatch.setattr(tiktoken, "get_encoding", boom)

    counter = TiktokenCounter()
    assert counter.count("hello world") == len("hello world") // 4


def test_warm_up_failure_does_not_raise(monkeypatch):
    import tiktoken

    def boom(name: str = "cl100k_base") -> _FakeEncoding:
        raise RuntimeError("network down")

    monkeypatch.setattr(tiktoken, "get_encoding", boom)

    warm_up_default_encoding()
