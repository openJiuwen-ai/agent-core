# coding: utf-8
"""Stream consumer behaviour: runtime_ready future + chunk rendering."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
)
from unittest.mock import patch

import pytest
from rich.console import Console

from openjiuwen.agent_teams.cli.stream_renderer import (
    spawn_stream,
    stop_stream,
)
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

pytestmark = pytest.mark.level0


def _make_spec(team_name: str = "alpha") -> TeamAgentSpec:
    return TeamAgentSpec.model_validate(
        {
            "agents": {"leader": {}},
            "team_name": team_name,
        },
    )


@dataclass
class _FakeChunk:
    type: str
    payload: Any


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]):
        self._chunks = chunks

    def __call__(self, *args, **kwargs):
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[_FakeChunk]:
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_runtime_ready_future_resolves_on_first_runtime_ready_event():
    chunks = [
        _FakeChunk(type="message", payload={"event_type": "team.runtime_ready"}),
        _FakeChunk(type="llm_output", payload={"content": "hi"}),
    ]
    fake = _FakeStream(chunks)

    with patch(
        "openjiuwen.core.runner.runner.Runner.run_agent_team_streaming",
        side_effect=fake,
    ):
        handle = spawn_stream(
            spec=_make_spec(),
            session_id="s1",
            inputs={"query": "go"},
            console=Console(record=True),
        )
        ack = await asyncio.wait_for(handle.runtime_ready, timeout=1.0)
        await handle.task

    assert ack["event_type"] == "team.runtime_ready"


@pytest.mark.asyncio
async def test_on_runtime_ready_callback_fires_with_team_and_session():
    chunks = [
        _FakeChunk(type="message", payload={"event_type": "team.runtime_ready"}),
    ]
    fake = _FakeStream(chunks)
    received: list[tuple[str, str]] = []

    async def _callback(team_name: str, session_id: str, payload: dict[str, Any]) -> None:
        received.append((team_name, session_id))

    with patch(
        "openjiuwen.core.runner.runner.Runner.run_agent_team_streaming",
        side_effect=fake,
    ):
        handle = spawn_stream(
            spec=_make_spec("alpha"),
            session_id="s1",
            inputs={"query": "go"},
            console=Console(record=True),
            on_runtime_ready=_callback,
        )
        await asyncio.wait_for(handle.runtime_ready, timeout=1.0)
        await handle.task

    assert received == [("alpha", "s1")]


@pytest.mark.asyncio
async def test_stop_stream_cancels_pending_task():
    async def _hang(*args, **kwargs):
        async def _gen():
            await asyncio.sleep(10)
            yield _FakeChunk(type="x", payload="never")

        return _gen()

    with patch(
        "openjiuwen.core.runner.runner.Runner.run_agent_team_streaming",
        side_effect=lambda *a, **k: _hang_gen(),
    ):
        handle = spawn_stream(
            spec=_make_spec(),
            session_id="s1",
            inputs={"query": "go"},
            console=Console(record=True),
        )
        # let the consumer enter the iterator
        await asyncio.sleep(0)
        await stop_stream(handle)

    assert handle.task.cancelled() or handle.task.done()
    assert handle.cancelled is True


async def _hang_gen() -> AsyncIterator[_FakeChunk]:
    await asyncio.sleep(10)
    yield _FakeChunk(type="x", payload="never")
