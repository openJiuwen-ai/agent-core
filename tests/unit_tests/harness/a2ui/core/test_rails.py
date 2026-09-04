# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.harness.a2ui.core.rails.A2uiToolEventRail."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.a2ui.core.rails import A2uiToolEventRail


def _make_ctx(session, tool_name="show_card", tool_args=None, tool_result=None, call_id=None, exception=None):
    tool_call = SimpleNamespace(id=call_id) if call_id is not None else None
    return SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(
            tool_name=tool_name, tool_args=tool_args or {}, tool_result=tool_result, tool_call=tool_call
        ),
        exception=exception,
    )


class TestBeforeToolCall:
    @pytest.mark.asyncio
    async def test_writes_tool_call_chunk(self):
        session = SimpleNamespace(write_stream=AsyncMock())
        rail = A2uiToolEventRail()
        ctx = _make_ctx(session, tool_name="get_current_time", tool_args={"a": 1}, call_id="call-1")

        await rail.before_tool_call(ctx)

        session.write_stream.assert_awaited_once()
        chunk = session.write_stream.await_args.args[0]
        assert chunk.type == "tool_call"
        assert chunk.payload == {"tool_call_id": "call-1", "tool_name": "get_current_time", "tool_args": {"a": 1}}

    @pytest.mark.asyncio
    async def test_tool_call_id_defaults_to_none_when_absent(self):
        session = SimpleNamespace(write_stream=AsyncMock())
        rail = A2uiToolEventRail()
        ctx = _make_ctx(session)

        await rail.before_tool_call(ctx)

        chunk = session.write_stream.await_args.args[0]
        assert chunk.payload["tool_call_id"] is None

    @pytest.mark.asyncio
    async def test_no_session_is_a_no_op(self):
        rail = A2uiToolEventRail()
        ctx = _make_ctx(None)

        await rail.before_tool_call(ctx)  # should not raise


class TestAfterToolCall:
    @pytest.mark.asyncio
    async def test_keeps_raw_tool_result_unstringified(self):
        session = SimpleNamespace(write_stream=AsyncMock())
        rail = A2uiToolEventRail()
        raw_result = {"text": "hi", "genui": [{"version": "v0.9"}]}
        ctx = _make_ctx(session, tool_result=raw_result, call_id="call-2")

        await rail.after_tool_call(ctx)

        chunk = session.write_stream.await_args.args[0]
        assert chunk.type == "tool_result"
        assert chunk.payload["tool_result"] is raw_result
        assert chunk.payload["tool_call_id"] == "call-2"

    @pytest.mark.asyncio
    async def test_no_session_is_a_no_op(self):
        rail = A2uiToolEventRail()
        ctx = _make_ctx(None)

        await rail.after_tool_call(ctx)  # should not raise


class TestOnToolException:
    @pytest.mark.asyncio
    async def test_writes_tool_error_chunk_with_message(self):
        session = SimpleNamespace(write_stream=AsyncMock())
        rail = A2uiToolEventRail()
        ctx = _make_ctx(session, tool_name="fetch_page_image", call_id="call-3", exception=RuntimeError("boom"))

        await rail.on_tool_exception(ctx)

        chunk = session.write_stream.await_args.args[0]
        assert chunk.type == "tool_error"
        assert chunk.payload == {
            "tool_call_id": "call-3",
            "tool_name": "fetch_page_image",
            "tool_args": {},
            "message": "boom",
        }

    @pytest.mark.asyncio
    async def test_missing_exception_falls_back_to_generic_message(self):
        session = SimpleNamespace(write_stream=AsyncMock())
        rail = A2uiToolEventRail()
        ctx = _make_ctx(session, exception=None)

        await rail.on_tool_exception(ctx)

        chunk = session.write_stream.await_args.args[0]
        assert chunk.payload["message"] == "Tool execution failed."

    @pytest.mark.asyncio
    async def test_no_session_is_a_no_op(self):
        rail = A2uiToolEventRail()
        ctx = _make_ctx(None)

        await rail.on_tool_exception(ctx)  # should not raise
