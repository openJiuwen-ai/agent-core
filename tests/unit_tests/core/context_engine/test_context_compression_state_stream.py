# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch
import re

import pytest
from pydantic import BaseModel

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import BaseMessage, UserMessage
from openjiuwen.core.session.stream.base import OutputSchema


class _FakeSession:
    def __init__(self):
        self.chunks: list[OutputSchema] = []

    def get_session_id(self) -> str:
        return "session-1"

    async def write_stream(self, data: OutputSchema):
        self.chunks.append(data)


class _CompressConfig(BaseModel):
    trigger_total_tokens: int = 100
    model: str = "test-compressor-model"


class _ReplacingCompressor(ContextProcessor):
    def __init__(self):
        super().__init__(_CompressConfig())

    async def trigger_add_messages(
            self,
            context,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        return True

    async def on_add_messages(
            self,
            context,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        context.set_messages([UserMessage(content="short")])
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=[0, 1]), []

    def load_state(self, state: Dict[str, Any]) -> None:
        return None

    def save_state(self) -> Dict[str, Any]:
        return {}


class _NoopCompressor(ContextProcessor):
    def __init__(self):
        super().__init__(_CompressConfig())

    async def trigger_add_messages(
            self,
            context,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> bool:
        return True

    async def on_add_messages(
            self,
            context,
            messages_to_add: List[BaseMessage],
            **kwargs: Any,
    ) -> Tuple[ContextEvent | None, List[BaseMessage]]:
        return None, messages_to_add

    def load_state(self, state: Dict[str, Any]) -> None:
        return None

    def save_state(self) -> Dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_active_compression_streams_started_and_completed_state():
    session = _FakeSession()
    context = SessionModelContext(
        "context-1",
        session.get_session_id(),
        ContextEngineConfig(),
        history_messages=[
            UserMessage(content="a" * 80),
            UserMessage(content="b" * 80),
        ],
        processors=[_ReplacingCompressor()],
        token_counter=None,
    )
    setattr(context, "_session_ref", session)

    result = await context.compress_context()

    assert result == "compressed"
    states = [chunk for chunk in session.chunks if chunk.type == "context_compression_state"]
    assert [chunk.payload.status for chunk in states] == ["started", "completed"]

    started = states[0].payload
    assert started.phase == "active_compress"
    assert started.processor == "_ReplacingCompressor"
    assert started.model == "test-compressor-model"
    assert started.before.time
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}", started.before.time)
    assert started.before.messages == 2
    assert started.before.tokens == 40
    assert started.before.context_percent == 0
    assert started.statistic.total_messages == 2
    assert started.statistic.total_tokens == 0
    assert started.statistic.user_messages == 2
    assert started.after is None
    assert started.saved is None
    assert started.duration_ms is None

    completed = states[1].payload
    assert completed.after.time
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}", completed.after.time)
    assert completed.after.messages == 1
    assert completed.after.tokens == 2
    assert completed.after.context_percent == 0
    assert completed.saved.messages == 1
    assert completed.saved.tokens == 38
    assert completed.statistic.total_messages == 1
    assert completed.statistic.total_tokens == 0
    assert completed.statistic.user_messages == 1
    assert completed.summary == "Compressed 2 -> 1 messages, ~40 -> ~2 tokens, saved ~38 tokens (95.0%), modified 2 messages"
    assert completed.duration_ms is not None


@pytest.mark.asyncio
async def test_active_compression_returns_noop_when_processor_does_not_change_context():
    session = _FakeSession()
    context = SessionModelContext(
        "context-1",
        session.get_session_id(),
        ContextEngineConfig(),
        history_messages=[UserMessage(content="unchanged")],
        processors=[_NoopCompressor()],
        token_counter=None,
    )
    setattr(context, "_session_ref", session)

    result = await context.compress_context()

    assert result == "noop"
    states = [chunk for chunk in session.chunks if chunk.type == "context_compression_state"]
    assert [chunk.payload.status for chunk in states] == ["started", "noop"]
    assert states[0].payload.before.context_percent == 0


@pytest.mark.asyncio
async def test_context_percent_uses_model_context_window_mapping():
    session = _FakeSession()
    context = SessionModelContext(
        "context-1",
        session.get_session_id(),
        ContextEngineConfig(model_context_window_tokens={"mapped-model": 200}),
        history_messages=[UserMessage(content="a" * 80)],
        processors=[_NoopCompressor()],
        token_counter=None,
    )
    setattr(context, "_session_ref", session)

    result = await context.compress_context(model_name="mapped-model")

    assert result == "noop"
    states = [chunk for chunk in session.chunks if chunk.type == "context_compression_state"]
    assert states[0].payload.before.tokens == 20
    assert states[0].payload.before.context_percent == 10


@pytest.mark.asyncio
async def test_state_callback_failure_does_not_block_stream_emit():
    session = _FakeSession()
    context = SessionModelContext(
        "context-1",
        session.get_session_id(),
        ContextEngineConfig(),
        history_messages=[UserMessage(content="a" * 80)],
        processors=[_NoopCompressor()],
        token_counter=None,
    )
    setattr(context, "_session_ref", session)

    with patch(
        "openjiuwen.core.context_engine.context.context._fw.trigger",
        new=AsyncMock(side_effect=RuntimeError("callback failed")),
    ):
        result = await context.compress_context()

    assert result == "noop"
    states = [chunk for chunk in session.chunks if chunk.type == "context_compression_state"]
    assert [chunk.payload.status for chunk in states] == ["started", "noop"]
