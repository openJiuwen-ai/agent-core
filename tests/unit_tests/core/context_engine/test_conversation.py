# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import (
    ContextEngine, ContextEngineConfig, conversation_snapshot,
    export_conversation, replace_conversation,
)
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointer
from openjiuwen.core.single_agent import AgentCard
from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore


@pytest.fixture
def checkpointer(monkeypatch):
    value = PersistenceCheckpointer(MockKVStore())
    monkeypatch.setattr(CheckpointerFactory, "get_checkpointer", lambda: value)
    return value


async def session(agent_id):
    value = create_agent_session(session_id="handoff-unit", card=AgentCard(id=agent_id))
    await value.pre_run()
    return value


def engine():
    return ContextEngine(ContextEngineConfig(enable_openrouter_model_context_window_tokens=False))


@pytest.mark.asyncio
async def test_native_checkpoint_roundtrip_preserves_target_state_and_new_context(checkpointer):
    source, target = await session("source-card"), await session("leader-card")
    source_engine, target_engine = engine(), engine()
    original = [UserMessage(content="A", metadata={"origin": "user", "context_message_id": "stable-id"}),
                AssistantMessage(content="D-generated")]
    await source_engine.create_context(session=source, history_messages=original)
    await source_engine.save_contexts(source)
    await source.commit()
    target.update_state({"deepagent": {"iteration": 12}, "trajectory_member_turn": 7,
                         "team_prompt_context": {"old": True}})
    await target_engine.create_context(session=target, history_messages=[UserMessage(content="stale")])
    marker = {"id": "switch-1", "source_agent_id": "source-card", "target_agent_id": "leader-card"}
    restored_source = await session("source-card")
    snapshot = export_conversation(restored_source)
    await replace_conversation(target, target_engine, snapshot, marker, reset_state_keys=("team_prompt_context",))
    snapshot["messages"][0].content = "mutation must stay local"
    restored = await session("leader-card")
    context = await target_engine.create_context(session=restored)
    assert [m.content for m in context.get_messages()] == ["A", "D-generated"]
    assert context.get_messages()[0].metadata["context_message_id"] == "stable-id"
    assert restored.get_state("deepagent") == {"iteration": 12}
    assert restored.get_state("trajectory_member_turn") == 7
    assert restored.get_state("team_prompt_context") is None
    assert restored.get_state("context_handoff") == marker
    assert export_conversation(await session("source-card"))["messages"][0].content == "A"


@pytest.mark.asyncio
async def test_live_export_reads_uncommitted_context_and_returns_independent_messages(checkpointer):
    source = await session("live-source")
    source_engine = engine()
    context = await source_engine.create_context(session=source, history_messages=[UserMessage(content="saved")])
    await source_engine.save_contexts(source)
    await source.commit()
    await context.add_messages([AssistantMessage(content="live")])

    snapshot = export_conversation(source, source_engine)
    assert [message.content for message in snapshot["messages"]] == ["saved", "live"]
    assert [message.content for message in export_conversation(await session("live-source"))["messages"]] == ["saved"]
    snapshot["messages"][0].content = "local mutation"
    assert context.get_messages()[0].content == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [OSError, asyncio.CancelledError])
async def test_failed_commit_rolls_back_context_and_session_without_deep_state_changes(checkpointer, error_type):
    target = await session("target")
    target_engine = engine()
    context = await target_engine.create_context(session=target, history_messages=[UserMessage(content="old")])
    await target_engine.save_contexts(target)
    target.update_state({"team_prompt_context": {"old": True}, "deepagent": {"iteration": 12},
                         "context_handoff": {"id": "previous"}})
    await target.commit()
    before = deepcopy(target.get_state())
    target.commit = AsyncMock(side_effect=error_type("disk failed"))
    with pytest.raises(error_type):
        await replace_conversation(target, target_engine, {"messages": [UserMessage(content="new")]},
                                   {"id": "switch"}, reset_state_keys=("team_prompt_context",))
    assert context.get_messages()[0].content == "old"
    assert target.get_state() == before
    reloaded = await engine().create_context(session=target)
    assert reloaded is not context
    assert reloaded.get_messages()[0].content == "old"
    restored = await session("target")
    recovered = await engine().create_context(session=restored)
    assert recovered.get_messages()[0].content == "old"
    assert restored.get_state() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [OSError, asyncio.CancelledError])
async def test_uncertain_commit_recovers_replacement_and_receipt_together(checkpointer, error_type):
    target = await session("uncertain-target")
    target_engine = engine()
    context = await target_engine.create_context(session=target, history_messages=[UserMessage(content="old")])
    await target_engine.save_contexts(target)
    target.update_state({"team_prompt_context": {"old": True}, "deepagent": {"iteration": 12},
                         "context_handoff": {"id": "previous"}})
    await target.commit()
    before = deepcopy(target.get_state())
    original_commit = target.commit

    async def commit_then_raise():
        await original_commit()
        raise error_type("acknowledgment lost")

    target.commit = commit_then_raise
    marker = {"id": "switch"}
    with pytest.raises(error_type):
        await replace_conversation(target, target_engine, {"messages": [UserMessage(content="new")]},
                                   marker, reset_state_keys=("team_prompt_context",))
    assert context.get_messages()[0].content == "old"
    assert target.get_state() == before
    restored = await session("uncertain-target")
    recovered = await engine().create_context(session=restored)
    assert recovered.get_messages()[0].content == "new"
    assert restored.get_state("context_handoff") == marker
    assert restored.get_state("team_prompt_context") is None
    assert restored.get_state("deepagent") == before["deepagent"]


def test_offloaded_tool_result_keeps_pair_and_independent_payload():
    state = {"messages": [
        AssistantMessage(content="", tool_calls=[{"id": "tool-1", "type": "function",
                                                  "function": {"name": "read", "arguments": "{}"}}]),
        OffloadToolMessage(content="summary", tool_call_id="tool-1", offload_handle="h", offload_type="in_memory"),
    ], "offload_messages": {"h": [ToolMessage(content="full result", tool_call_id="tool-1")]}}
    snapshot = conversation_snapshot(state)
    state["offload_messages"]["h"][0].content = "changed"
    assert snapshot["offload_messages"]["h"][0].content == "full result"


@pytest.mark.parametrize("messages,error", [
    ([AssistantMessage(content="", tool_calls=[{"id": "unclosed", "type": "function",
                                                "function": {"name": "read", "arguments": "{}"}}])], "unclosed"),
    ([ToolMessage(content="orphan", tool_call_id="missing")], "unpaired"),
    ([OffloadToolMessage(content="file", tool_call_id="t", offload_handle="private-file",
                         offload_type="filesystem")], "not_portable"),
])
def test_incomplete_or_inaccessible_snapshots_fail_explicitly(messages, error):
    with pytest.raises(ValueError, match=error):
        conversation_snapshot({"messages": messages})
