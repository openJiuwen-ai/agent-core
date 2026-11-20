# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stopping an A2A client stream early must close the SDK stream."""

import pytest

from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a import a2a_client as module
from openjiuwen.extensions.a2a.a2a_client import A2AClient


class _EventStream:
    def __init__(self) -> None:
        self.closed = False
        self._yielded = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return object()

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, event_stream: _EventStream) -> None:
        self.event_stream = event_stream

    def send_message(self, request):
        return self.event_stream


@pytest.mark.asyncio
async def test_stream_early_aclose_closes_sdk_stream(monkeypatch) -> None:
    monkeypatch.setattr(module.A2ATransformer, "to_a2a_request", staticmethod(lambda inputs: object()))
    monkeypatch.setattr(
        module.A2ATransformer,
        "from_a2a_response",
        staticmethod(lambda event: AgentResult()),
    )
    event_stream = _EventStream()
    client = object.__new__(A2AClient)
    client.client = _Client(event_stream)

    stream = client.stream({"conversation_id": "c1"})
    await stream.__anext__()
    await stream.aclose()

    assert event_stream.closed is True
