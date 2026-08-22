# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AgentRASRail soft-stop: generic Termination control-flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import Termination
from openjiuwen.harness.rails.agent_ras_rail import AgentRASRail


def _termination() -> Termination:
    return Termination(
        StatusCode.CALLBACK_EXECUTION_ABORTED,
        msg="Agent RAS stream abort: thinking-loop",
    )


def _make_rail() -> AgentRASRail:
    return AgentRASRail(monitor=MagicMock())


class TestInspectStreamChunk:
    @pytest.mark.asyncio
    async def test_raises_termination_when_abort_armed(self) -> None:
        rail = _make_rail()
        monitor = MagicMock()
        monitor.should_abort_stream = True
        rail._monitor_for = MagicMock(return_value=monitor)

        with pytest.raises(Termination):
            await rail.inspect_stream_chunk(MagicMock(), MagicMock())
        monitor.on_stream_chunk.assert_not_called()

    @pytest.mark.asyncio
    async def test_forwards_chunks_when_not_aborting(self) -> None:
        rail = _make_rail()
        monitor = MagicMock()
        monitor.should_abort_stream = False
        monitor.abnormal_committed = False
        monitor.on_stream_chunk = AsyncMock()
        rail._monitor_for = MagicMock(return_value=monitor)

        chunk = MagicMock(reasoning_content="thinking", content="answer")
        await rail.inspect_stream_chunk(MagicMock(), chunk)
        assert monitor.on_stream_chunk.await_count == 2


class TestOnModelException:
    @pytest.mark.asyncio
    async def test_skips_termination(self) -> None:
        rail = _make_rail()
        monitor = MagicMock()
        rail._monitor_for = MagicMock(return_value=monitor)

        ctx = MagicMock(exception=_termination())
        await rail.on_model_exception(ctx)
        monitor.handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_forwards_real_exception(self) -> None:
        rail = _make_rail()
        monitor = MagicMock()
        monitor.handle = AsyncMock()
        rail._monitor_for = MagicMock(return_value=monitor)

        ctx = MagicMock(exception=RuntimeError("boom"))
        await rail.on_model_exception(ctx)
        monitor.handle.assert_awaited_once()
