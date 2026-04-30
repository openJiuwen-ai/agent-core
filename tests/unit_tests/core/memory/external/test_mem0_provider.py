# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for canonical Mem0MemoryProvider behavior."""

from __future__ import annotations

import asyncio
import json
import pytest

from openjiuwen.core.memory.external.mem0_provider import Mem0MemoryProvider


class FakeMem0Client:
    def __init__(self):
        self.last_search_kwargs = None
        self.last_add_args = None
        self.last_add_kwargs = None

    def search(self, **kwargs):
        self.last_search_kwargs = kwargs
        return {"results": [{"memory": "remember this", "score": 0.9}]}

    def add(self, *args, **kwargs):
        self.last_add_args = args
        self.last_add_kwargs = kwargs
        return {"ok": True}

    def get_all(self, **kwargs):
        return {"results": [{"memory": "hello"}, {"memory": "world"}]}


@pytest.mark.asyncio
async def test_initialize_requires_api_key():
    provider = Mem0MemoryProvider(api_key="")
    with pytest.raises(ValueError):
        await provider.initialize()


@pytest.mark.asyncio
async def test_prefetch_direct_search_for_rail_usage():
    provider = Mem0MemoryProvider(api_key="k", user_id="u1")
    await provider.initialize()

    fake = FakeMem0Client()
    provider._get_client = lambda: fake  # type: ignore[method-assign]

    result = await provider.prefetch("who am I")

    assert "## Mem0 Memory" in result
    assert "- remember this" in result
    assert fake.last_search_kwargs["query"] == "who am I"
    assert fake.last_search_kwargs["filters"] == {"user_id": "u1"}


@pytest.mark.asyncio
async def test_sync_turn_pushes_user_and_assistant_messages():
    provider = Mem0MemoryProvider(api_key="k", user_id="u1", agent_id="a1")
    await provider.initialize()
    fake = FakeMem0Client()
    provider._get_client = lambda: fake  # type: ignore[method-assign]

    await provider.sync_turn("u-msg", "a-msg")

    assert fake.last_add_args
    sent_messages = fake.last_add_args[0]
    assert sent_messages[0]["role"] == "user"
    assert sent_messages[1]["role"] == "assistant"
    assert fake.last_add_kwargs["user_id"] == "u1"
    assert fake.last_add_kwargs["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_handle_tool_call_search_and_conclude():
    provider = Mem0MemoryProvider(api_key="k", user_id="u1", agent_id="a1")
    await provider.initialize()
    fake = FakeMem0Client()
    provider._get_client = lambda: fake  # type: ignore[method-assign]

    search_out = await provider.handle_tool_call("mem0_search", {"query": "x", "top_k": 2})
    search_data = json.loads(search_out)
    assert "results" in search_data
    assert search_data["count"] == 1

    conclude_out = await provider.handle_tool_call("mem0_conclude", {"conclusion": "new fact"})
    conclude_data = json.loads(conclude_out)
    assert conclude_data["result"] == "Fact stored."


@pytest.mark.asyncio
async def test_shutdown_cancels_prefetch_task():
    provider = Mem0MemoryProvider(api_key="k")
    await provider.initialize()
    task = asyncio.create_task(asyncio.sleep(30))
    provider._prefetch_task = task

    await provider.shutdown()

    assert task.cancelled()
