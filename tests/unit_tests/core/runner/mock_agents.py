#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Mock agents for spawn agent testing."""

import asyncio
from typing import Any, AsyncIterator, List, Optional

from openjiuwen.core.session.stream import StreamMode
from openjiuwen.core.single_agent import AgentCard, BaseAgent, Session


class MockSimpleAgent(BaseAgent):
    """Simple mock agent that sleeps and returns predefined output."""

    def __init__(self, sleep_time: float = 0.1, output: Any = None):
        card = AgentCard(id="mock_simple_agent")
        super().__init__(card)
        self._sleep_time = sleep_time
        self._output = output if output is not None else {"result": "mock_output"}

    def configure(self, config) -> "MockSimpleAgent":
        return self

    async def invoke(
        self,
        inputs: Any,
        session: Session | None = None,
    ) -> Any:
        await asyncio.sleep(self._sleep_time)
        return self._output

    async def stream(
        self,
        inputs: Any,
        session: Session | None = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        await asyncio.sleep(self._sleep_time)
        yield self._output


class MockStreamingAgent(BaseAgent):
    """Mock agent that yields multiple chunks with delays."""

    def __init__(
        self,
        chunks: Optional[list[Any]] = None,
        sleep_between_chunks: float = 0.05,
    ):
        card = AgentCard(id="mock_streaming_agent")
        super().__init__(card)
        self._chunks = chunks if chunks is not None else [
            {"chunk": 1},
            {"chunk": 2},
            {"chunk": 3},
        ]
        self._sleep_between_chunks = sleep_between_chunks

    def configure(self, config) -> "MockStreamingAgent":
        return self

    async def invoke(
        self,
        inputs: Any,
        session: Session | None = None,
    ) -> Any:
        return {"chunks": self._chunks}

    async def stream(
        self,
        inputs: Any,
        session: Session | None = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        for chunk in self._chunks:
            await asyncio.sleep(self._sleep_between_chunks)
            yield chunk


class MockLongRunningAgent(BaseAgent):
    """Mock agent that runs for a specified duration."""

    def __init__(self, duration: float = 5.0, check_interval: float = 0.1):
        card = AgentCard(id="mock_long_running_agent")
        super().__init__(card)
        self._duration = duration
        self._check_interval = check_interval
        self._shutdown_requested = False

    def configure(self, config) -> "MockLongRunningAgent":
        return self

    async def invoke(
        self,
        inputs: Any,
        session: Session | None = None,
    ) -> Any:
        elapsed = 0.0
        while elapsed < self._duration and not self._shutdown_requested:
            await asyncio.sleep(self._check_interval)
            elapsed += self._check_interval
        return {"elapsed": elapsed, "completed": elapsed >= self._duration}

    async def stream(
        self,
        inputs: Any,
        session: Session | None = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        elapsed = 0.0
        while elapsed < self._duration and not self._shutdown_requested:
            await asyncio.sleep(self._check_interval)
            elapsed += self._check_interval
            yield {"elapsed": elapsed}
        yield {"completed": elapsed >= self._duration}


class MockShutdownIgnoringAgent(BaseAgent):
    """Mock agent that ignores shutdown signals and keeps running."""

    def __init__(self, duration: float = 30.0):
        card = AgentCard(id="mock_shutdown_ignoring_agent")
        super().__init__(card)
        self._duration = duration

    def configure(self, config) -> "MockShutdownIgnoringAgent":
        return self

    async def invoke(
        self,
        inputs: Any,
        session: Session | None = None,
    ) -> Any:
        await asyncio.sleep(self._duration)
        return {"result": "should_not_reach_here"}

    async def stream(
        self,
        inputs: Any,
        session: Session | None = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        await asyncio.sleep(self._duration)
        yield {"result": "should_not_reach_here"}
