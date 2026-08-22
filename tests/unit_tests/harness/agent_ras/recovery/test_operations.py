# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recovery operations: user-visible notice stream contract."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.harness.agent_ras.recovery.operations import emit_user_notice


class TestEmitUserNotice:
    @pytest.mark.asyncio
    async def test_writes_llm_output_with_string_content(self) -> None:
        session = MagicMock()
        session.write_stream = AsyncMock()
        ctx = MagicMock(session=session)

        await emit_user_notice(ctx, "检测到思考循环异常，已执行恢复操作")

        session.write_stream.assert_awaited_once()
        chunk = session.write_stream.await_args.args[0]
        assert chunk.type == "llm_output"
        assert chunk.index == -1
        assert chunk.payload == {
            "content": "\n\n⚠️ 检测到思考循环异常，已执行恢复操作\n\n",
        }

    @pytest.mark.asyncio
    async def test_noop_when_session_missing(self) -> None:
        ctx = MagicMock(session=None)
        await emit_user_notice(ctx, "hello")

    @pytest.mark.asyncio
    async def test_swallows_write_stream_errors(self) -> None:
        session = MagicMock()
        session.write_stream = AsyncMock(side_effect=RuntimeError("stream closed"))
        ctx = MagicMock(session=session)

        await emit_user_notice(ctx, "hello")
