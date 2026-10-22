# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for durable NativeHarness interrupt checkpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.persistence import (
    PersistenceCheckpointerProvider,
)
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _harness_with_session(session) -> NativeHarness:
    harness = object.__new__(NativeHarness)
    harness._session = session
    return harness


def _interruption_state() -> ToolInterruptionState:
    tool_call = ToolCall(
        id="tool-ask-1",
        type="function",
        name="ask_user",
        arguments="{}",
    )
    return ToolInterruptionState(
        ai_message=AssistantMessage(tool_calls=[tool_call]),
        iteration=2,
        original_query="build a demo",
        interrupted_tools={
            tool_call.id: ToolInterruptEntry(
                tool_call=tool_call,
                interrupt_requests={
                    tool_call.id: InterruptRequest(message="Choose a plan", interrupt_id="intr_plan"),
                },
            ),
        },
    )


@pytest.mark.asyncio
async def test_interrupt_checkpoint_is_committed_before_card_output() -> None:
    order: list[str] = []
    session = AsyncMock()
    session.commit_interrupt.side_effect = lambda: order.append("commit")
    harness = _harness_with_session(session)

    async def write_result(_result, _session) -> None:
        order.append("write")

    harness._write_round_result_to_stream = write_result

    await harness._persist_and_write_round_result({"result_type": "interrupt"})

    assert order == ["commit", "write"]


@pytest.mark.asyncio
async def test_non_interrupt_result_does_not_add_checkpoint_commit() -> None:
    session = AsyncMock()
    harness = _harness_with_session(session)
    harness._write_round_result_to_stream = AsyncMock()

    result = {"result_type": "success", "output": "done"}
    await harness._persist_and_write_round_result(result)

    session.commit_interrupt.assert_not_awaited()
    harness._write_round_result_to_stream.assert_awaited_once_with(result, session)


@pytest.mark.asyncio
async def test_interrupt_checkpoint_failure_prevents_card_output() -> None:
    session = AsyncMock()
    session.commit_interrupt.side_effect = RuntimeError("checkpoint unavailable")
    harness = _harness_with_session(session)
    harness._write_round_result_to_stream = AsyncMock()

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await harness._persist_and_write_round_result({"result_type": "interrupt"})

    harness._write_round_result_to_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_interrupt_checkpoint_restores_after_checkpointer_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_path = str(tmp_path / "checkpoint")
    provider = PersistenceCheckpointerProvider()
    writer_checkpointer = await provider.create(
        {"db_type": "sqlite", "db_path": db_path},
    )
    monkeypatch.setattr(
        CheckpointerFactory,
        "_default_checkpointer",
        writer_checkpointer,
    )

    session_id = "native-interrupt-restart"
    card = AgentCard(id="leader-card", name="leader")
    first = create_agent_session(session_id=session_id, card=card)
    await first.pre_run()
    expected = _interruption_state()
    first.update_state({INTERRUPTION_KEY: expected})

    harness = _harness_with_session(first)
    harness._write_round_result_to_stream = AsyncMock()
    await harness._persist_and_write_round_result({"result_type": "interrupt"})
    await writer_checkpointer._kv_store.engine.dispose()

    reader_checkpointer = await provider.create(
        {"db_type": "sqlite", "db_path": db_path},
    )
    monkeypatch.setattr(
        CheckpointerFactory,
        "_default_checkpointer",
        reader_checkpointer,
    )
    try:
        restored = create_agent_session(session_id=session_id, card=card)
        await restored.pre_run()
        actual = restored.get_state(INTERRUPTION_KEY)

        assert isinstance(actual, ToolInterruptionState)
        assert actual == expected
    finally:
        await reader_checkpointer._kv_store.engine.dispose()
