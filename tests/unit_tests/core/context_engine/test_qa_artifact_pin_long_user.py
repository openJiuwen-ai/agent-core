# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for QAArtifactManager long-user pin (P0-1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifactConfig
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage


def _mgr(*, pin_chars: int = 20) -> QAArtifactManager:
    config = QAArtifactConfig(safety_net_pin_user_chars=pin_chars)
    return QAArtifactManager(config, None, CatalogBuilder(config))


def _stub_with_handle(*, handle: str = "fs://pinned", metadata: dict | None = None):
    # Offload stubs may carry filesystem attrs outside UserMessage schema.
    return SimpleNamespace(
        role="user",
        content="[offloaded]",
        metadata=dict(metadata or {}),
        offload_handle=handle,
        offload_type="filesystem",
    )


@pytest.mark.asyncio
async def test_pin_long_user_success_preserves_metadata_and_returns_stub():
    mgr = _mgr(pin_chars=10)
    long_user = UserMessage(
        content="x" * 20,
        metadata={"qa_id": "qa_1", "context_message_id": "cm-1"},
    )
    other = AssistantMessage(content="ok")
    stub = _stub_with_handle()
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(return_value=stub))
    ctx = SimpleNamespace()
    context = MagicMock()

    pinned, ok = await mgr._pin_long_user_messages(ctx, [long_user, other], context=context)

    assert ok is True
    assert pinned[0] is stub
    assert pinned[1] is other
    assert pinned[0].metadata.get("qa_id") == "qa_1"
    assert pinned[0].metadata.get("context_message_id") == "cm-1"
    mgr._pin_offloader.offload_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_pin_long_user_threshold_boundary_skips_below_and_pins_at_or_above():
    mgr = _mgr(pin_chars=10)
    below = UserMessage(content="x" * 9)
    at_threshold = UserMessage(content="y" * 10)
    stub = _stub_with_handle(handle="fs://at")
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(return_value=stub))

    pinned, ok = await mgr._pin_long_user_messages(
        SimpleNamespace(),
        [below, at_threshold],
        context=MagicMock(),
    )

    assert ok is True
    assert pinned[0] is below
    assert pinned[1] is stub
    mgr._pin_offloader.offload_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_pin_long_user_offload_exception_returns_ok_false():
    mgr = _mgr(pin_chars=5)
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(side_effect=RuntimeError("boom")))
    messages = [UserMessage(content="long-" * 10)]

    pinned, ok = await mgr._pin_long_user_messages(
        SimpleNamespace(),
        messages,
        context=MagicMock(),
    )

    assert ok is False
    assert pinned is messages


@pytest.mark.asyncio
async def test_pin_long_user_missing_handle_returns_ok_false():
    mgr = _mgr(pin_chars=5)
    # No filesystem handle on returned message.
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(return_value=UserMessage(content="stub")))
    messages = [UserMessage(content="long-" * 10)]

    pinned, ok = await mgr._pin_long_user_messages(
        SimpleNamespace(),
        messages,
        context=MagicMock(),
    )

    assert ok is False
    assert pinned is messages


@pytest.mark.asyncio
async def test_pin_long_user_skips_already_offloaded_and_disabled_threshold():
    mgr = _mgr(pin_chars=5)
    already = _stub_with_handle(handle="fs://existing")
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock())

    pinned, ok = await mgr._pin_long_user_messages(
        SimpleNamespace(),
        [already],
        context=MagicMock(),
    )
    assert ok is True
    assert pinned[0] is already
    mgr._pin_offloader.offload_messages.assert_not_awaited()

    mgr_disabled = _mgr(pin_chars=0)
    mgr_disabled._pin_offloader = SimpleNamespace(offload_messages=AsyncMock())
    messages = [UserMessage(content="z" * 100)]
    pinned2, ok2 = await mgr_disabled._pin_long_user_messages(
        SimpleNamespace(),
        messages,
        context=MagicMock(),
    )
    assert ok2 is True
    assert pinned2 is messages
    mgr_disabled._pin_offloader.offload_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_long_user_messages_in_context_writes_back_on_success():
    mgr = _mgr(pin_chars=5)
    long_user = UserMessage(content="long-" * 10, metadata={"qa_id": "qa_x"})
    stub = _stub_with_handle()
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(return_value=stub))
    context = MagicMock()
    context.get_messages.return_value = [long_user]

    ok = await mgr.pin_long_user_messages_in_context(SimpleNamespace(), context)

    assert ok is True
    context.set_messages.assert_called_once()
    written = context.set_messages.call_args.args[0]
    assert written[0] is stub
    assert written[0].metadata.get("qa_id") == "qa_x"


@pytest.mark.asyncio
async def test_pin_long_user_messages_in_context_skips_set_messages_on_failure():
    mgr = _mgr(pin_chars=5)
    mgr._pin_offloader = SimpleNamespace(offload_messages=AsyncMock(side_effect=RuntimeError("fail")))
    context = MagicMock()
    context.get_messages.return_value = [UserMessage(content="long-" * 10)]

    ok = await mgr.pin_long_user_messages_in_context(SimpleNamespace(), context)

    assert ok is False
    context.set_messages.assert_not_called()
