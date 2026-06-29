# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证 context trace 关闭时跳过消息快照构建。

回归性能优化：当 OPENJIUWEN_CONTEXT_TRACE_ENABLED 关闭时，
add_messages / get_context_window 不应再调用 snapshot_messages
（避免对整段消息历史做切片复制与序列化）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent import AgentCard


def _make_processor(messages):
    """构造一个总是 trigger 的 ContextProcessor mock。"""
    proc = MagicMock()
    proc.processor_type.return_value = "test_proc"
    proc.trigger_add_messages = AsyncMock(return_value=True)
    # on_add_messages 返回 (event, messages)；event=None 走无修改分支
    proc.on_add_messages = AsyncMock(return_value=(None, messages))
    proc.trigger_get_context_window = AsyncMock(return_value=True)
    # on_get_context_window 返回 (event, window)；原样回传 window
    proc.on_get_context_window = AsyncMock(side_effect=lambda ctx, window, **kw: (None, window))
    return proc


@pytest.fixture
def engine():
    return ContextEngine(ContextEngineConfig(default_window_message_num=5))


@pytest.fixture
def session():
    return create_agent_session("trace_short_circuit", card=AgentCard(id="t"))


class TestContextTraceShortCircuit:
    """trace 关闭时应跳过 snapshot_messages（避免 O(N) 复制与序列化）。"""

    @pytest.mark.asyncio
    async def test_add_messages_no_snapshot_when_trace_disabled(
        self, engine, session, monkeypatch
    ):
        monkeypatch.setenv("OPENJIUWEN_CONTEXT_TRACE_ENABLED", "false")
        context = await engine.create_context(context_id="ctx", session=session)
        msgs = [UserMessage(content="hello")]
        context._processors = [_make_processor(msgs)]

        with patch(
            "openjiuwen.core.context_engine.context.context.snapshot_messages"
        ) as snap, patch(
            "openjiuwen.core.context_engine.context.context.write_context_trace"
        ):
            await context.add_messages(msgs)

        assert snap.call_count == 0, "trace 关闭时不应构建消息快照"

    @pytest.mark.asyncio
    async def test_add_messages_snapshots_when_trace_enabled(
        self, engine, session, monkeypatch
    ):
        monkeypatch.setenv("OPENJIUWEN_CONTEXT_TRACE_ENABLED", "true")
        context = await engine.create_context(context_id="ctx", session=session)
        msgs = [UserMessage(content="hello")]
        context._processors = [_make_processor(msgs)]

        with patch(
            "openjiuwen.core.context_engine.context.context.snapshot_messages"
        ) as snap, patch(
            "openjiuwen.core.context_engine.context.context.write_context_trace"
        ):
            await context.add_messages(msgs)

        assert snap.call_count > 0, "trace 开启时仍应构建消息快照"

    @pytest.mark.asyncio
    async def test_get_context_window_no_snapshot_when_trace_disabled(
        self, engine, session, monkeypatch
    ):
        monkeypatch.setenv("OPENJIUWEN_CONTEXT_TRACE_ENABLED", "false")
        context = await engine.create_context(context_id="ctx", session=session)
        msgs = [UserMessage(content="hello")]
        context._processors = [_make_processor(msgs)]
        await context.add_messages(msgs)

        with patch(
            "openjiuwen.core.context_engine.context.context.snapshot_messages"
        ) as snap, patch(
            "openjiuwen.core.context_engine.context.context.write_context_trace"
        ):
            await context.get_context_window(system_messages=[], tools=[])

        assert snap.call_count == 0, "trace 关闭时 get_context_window 不应构建快照"
