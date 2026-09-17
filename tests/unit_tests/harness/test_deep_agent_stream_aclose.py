# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent stream aclose must release subagent controls."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig


@pytest.mark.asyncio
async def test_task_loop_stream_aclose_releases_subagent_controls() -> None:
    """GeneratorExit / aclose must release SubagentControl like CancelledError."""
    with patch(
        "openjiuwen.harness.deep_agent.schedule_image_support_probe",
        MagicMock(),
    ):
        agent = DeepAgent(AgentCard(name="deep", description="test")).configure(
            DeepAgentConfig(enable_task_loop=True)
        )
    release = AsyncMock()
    cancel_deep = AsyncMock()
    cancel_stream = AsyncMock()
    agent._release_session_subagent_controls = release
    agent._cancel_session_deep_tasks = cancel_deep
    agent._cancel_stream_process_task = cancel_stream

    loop_started = asyncio.Event()

    async def _hanging_task_loop(_ctx, _session):
        loop_started.set()
        await asyncio.Event().wait()
        yield {"output": "unused", "result_type": "answer"}

    class _HangSession:
        def get_session_id(self) -> str:
            return "sess-aclose"

        async def stream_iterator(self):
            await loop_started.wait()
            yield "chunk"
            await asyncio.Event().wait()
            yield "never"

        async def close_stream(self) -> None:
            return None

    agent._run_task_loop = _hanging_task_loop  # type: ignore[method-assign]
    session = _HangSession()
    ctx = MagicMock()

    agen = agent._run_task_loop_stream(ctx, session, None)
    assert await agen.__anext__() == "chunk"
    # Background _stream_process is still running; aclose must release controls.
    await agen.aclose()

    release.assert_awaited_once()
    assert release.await_args.kwargs.get("reason") == "stream_cancelled"
    cancel_deep.assert_awaited()
    cancel_stream.assert_awaited()
