# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stopping a remote A2A stream early must close its underlying stream."""

import pytest

from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a.a2a_remote_client import A2ARemoteClient
from types import SimpleNamespace


class _Stream:
    def __init__(self) -> None:
        self.closed = False
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._first:
            raise StopAsyncIteration
        self._first = False
        return AgentResult()

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, stream: _Stream) -> None:
        self._stream = stream

    def stream(self, inputs):
        return self._stream


@pytest.mark.asyncio
async def test_remote_stream_early_aclose_closes_inner_stream() -> None:
    inner = _Stream()
    client = object.__new__(A2ARemoteClient)
    client.client = _Client(inner)
    client.config = SimpleNamespace(id="remote-1")

    stream = client.stream({})
    await stream.__anext__()
    await stream.aclose()

    assert inner.closed is True
