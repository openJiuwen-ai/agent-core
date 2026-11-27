# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared windows through real ContextEngine, Session and storage entry points."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import (
    ContextEngine, ContextEngineConfig, ConversationBinding, ConversationKey, ConversationSnapshot, StringLengthCounter,
)
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.processor.compressor.micro_compact_processor import (
    MICRO_COMPACT_CLEARED_MARKER, MicroCompactProcessorConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloaderConfig
from openjiuwen.core.context_engine.processor.offloader.tool_result_window_processor import ToolResultWindowProcessorConfig
from openjiuwen.core.context_engine.processor.forked.compressor.base import PrefixCompactProcessor
from openjiuwen.core.context_engine.processor.forked.compressor.round_level_compressor import (
    RoundLevelCompressorConfig as ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import CompressionResult
from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
    MessageSummaryOffloaderConfig as ForkedMessageSummaryOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.llm import (
    AssistantMessage, ModelClientConfig, ModelRequestConfig, SystemMessage, ToolCall, ToolMessage, UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


@pytest_asyncio.fixture(params=["in_memory", "sqlite", "shelve"])
async def conversation(request, tmp_path):
    if request.param == "in_memory":
        checkpointer = InMemoryCheckpointer()
    else:
        checkpointer = await PersistenceCheckpointerProvider().create({
            "db_type": request.param, "db_path": str(tmp_path / "conversation"),
        })
    binding = ConversationBinding(ConversationKey("product", "main"), checkpointer.conversation_store())
    await binding.store.create(binding.key)
    yield checkpointer, binding
    if request.param == "sqlite":
        await checkpointer._kv_store.engine.dispose()


def engine(**kwargs):
    return ContextEngine(ContextEngineConfig(enable_openrouter_model_context_window_tokens=False, **kwargs))


async def session(conversation, executor="agent", *, binding=None, context_id="default_context_id"):
    checkpointer, main = conversation
    value = create_agent_session(session_id=executor, card=AgentCard(id=executor), checkpointer=checkpointer)
    value.bind_conversation(binding or main, context_id=context_id)
    await value.pre_run()
    return value


def contents(messages):
    return [message.content for message in messages]


async def write(ctx, messages):
    """Append and flush, standing in for a run that reached one of its save points."""
    added = await ctx.add_messages(messages)
    await ctx.save_conversation()
    return added


def tool_rounds(count=3, content_size=30):
    messages = [UserMessage(content="question")]
    for index in range(count):
        messages.extend([
            AssistantMessage(tool_calls=[ToolCall(id=str(index), name="web_search", type="function", arguments="{}")]),
            ToolMessage(tool_call_id=str(index), content=f"result-{index}:" + "x" * content_size),
        ])
    return messages


class SharedBlockingCompressorConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    started: asyncio.Event
    release: asyncio.Event


@ContextEngine.register_processor()
class SharedBlockingCompressor(ContextProcessor):
    async def on_add_messages(self, context, messages_to_add, **kwargs):
        if not kwargs.get("force"):
            return None, messages_to_add
        original_size = len(context.get_messages())
        self.config.started.set()
        await self.config.release.wait()
        context.set_messages([UserMessage(content="summary"), *context.get_messages()[original_size:]])
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=[0]), messages_to_add

    def load_state(self, state):
        return

    def save_state(self):
        return {}


@ContextEngine.register_processor()
class SharedGetRewriteProcessor(ContextProcessor):
    async def trigger_get_context_window(self, context, window):
        return True

    async def on_get_context_window(self, context, window, **kwargs):
        context.set_messages([UserMessage(content="GET processor result")])
        window.context_messages = context.get_messages()
        return ContextEvent(event_type=self.processor_type(), messages_to_modify=[0]), window

    def load_state(self, state):
        return

    def save_state(self):
        return {}


class SharedGetRewriteProcessorConfig(BaseModel):
    pass


@pytest.mark.asyncio
async def test_switch_between_engines_refreshes_cached_window_and_keeps_executor_config(conversation):
    _, binding = conversation
    agent_engine = engine(context_window_tokens=1000)
    leader_engine = engine(context_window_tokens=2000)
    agent_session = await session(conversation)
    first = await agent_engine.create_context(session=agent_session)
    await first.add_messages(UserMessage(content="A", metadata={"context_message_id": "stable-A"}))
    await agent_engine.save_contexts(agent_session)
    assert agent_session.get_state("context") == {}
    leader = await leader_engine.create_context(session=await session(conversation, "leader"))
    await write(leader, [UserMessage(content="B"), AssistantMessage(content="D")])
    first.set_messages([UserMessage(content="stale unsaved cache")])
    rebound = await agent_engine.create_context(session=await session(conversation),
                                                history_messages=[UserMessage(content="stale UI history")])
    assert rebound is first
    assert contents(rebound.get_messages()) == ["A", "B", "D"]
    assert rebound.get_messages()[0].metadata["context_message_id"] == "stable-A"
    tool = ToolInfo(name="current-tool", description="current permission", parameters={"type": "object"})
    window = await rebound.get_context_window(system_messages=[SystemMessage(content="current system")], tools=[tool])
    assert contents(window.context_messages) == ["A", "B", "D"]
    assert contents(window.system_messages) == ["current system"]
    assert window.tools == [tool]
    assert rebound.context_window_tokens() == 1000
    assert leader.context_window_tokens() == 2000
    assert contents((await binding.store.read(binding.key)).messages) == ["A", "B", "D"]


@pytest.mark.asyncio
async def test_loading_copies_the_record_once_and_saving_does_not_copy_it(conversation, monkeypatch):
    from openjiuwen.core.context_engine import conversation as loader
    from openjiuwen.core.context_engine.schema import conversation as schema

    _, binding = conversation
    await binding.store.commit(binding.key, ConversationSnapshot(messages=tool_rounds()))
    copies = []
    for module in (loader, schema):
        monkeypatch.setattr(
            module, "deepcopy", lambda value, real=module.deepcopy: copies.append(1) or real(value),
        )
    ctx = await engine().create_context(session=await session(conversation))
    assert contents(ctx.get_messages()) == contents(tool_rounds())
    assert len(copies) == 1
    await write(ctx, UserMessage(content="next"))
    assert len(copies) == 1
    assert contents((await binding.store.read(binding.key)).messages)[-1] == "next"


@pytest.mark.asyncio
async def test_old_checkpoint_and_explicit_history_never_seed_missing_shared_record(conversation):
    checkpointer, _ = conversation
    missing = ConversationBinding(ConversationKey("missing", "main"), checkpointer.conversation_store())
    value = await session(conversation, binding=missing)
    value.update_state({"context": {"default_context_id": {"messages": [UserMessage(content="checkpoint")]}}})
    ctx_engine = engine()
    with pytest.raises(BaseError) as error:
        await ctx_engine.create_context(session=value, history_messages=[UserMessage(content="history")])
    assert error.value.status == StatusCode.CONTEXT_CONVERSATION_NOT_FOUND
    assert ctx_engine.get_context(session_id=value.get_session_id()) is None
    with pytest.raises(BaseError):
        await missing.store.read(missing.key)


@pytest.mark.asyncio
async def test_pending_tool_calls_persist_but_cannot_be_resumed_as_an_idle_window(conversation):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation))
    await write(ctx, tool_rounds(1)[:-1])
    assert (await binding.store.read(binding.key)).messages[-1].tool_calls[0].id == "0"
    with pytest.raises(BaseError, match="conversation_tool_call_unclosed"):
        await engine().create_context(session=await session(conversation, "leader"))
    await write(ctx, ToolMessage(tool_call_id="0", content="done"))
    restored = await engine().create_context(session=await session(conversation, "leader"))
    assert restored.get_messages()[-1].content == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_real_compression_is_shared_and_old_tool_results_do_not_reappear(conversation, active):
    _, binding = conversation
    ctx_engine = engine()
    value = await session(conversation)
    config = MicroCompactProcessorConfig(trigger_threshold=1, keep_recent_per_tool=1)
    processors = [("MicroCompactProcessor", config)]
    ctx = await ctx_engine.create_context(session=value, processors=[] if active else processors)
    await write(ctx, tool_rounds())
    if active:
        await ctx_engine.create_context(session=value, processors=processors)
        assert await ctx_engine.compress_context(session=value) == "compressed"
    snapshot = await binding.store.read(binding.key)
    results = [message.content for message in snapshot.messages if isinstance(message, ToolMessage)]
    assert results[:2] == [MICRO_COMPACT_CLEARED_MARKER] * 2
    assert results[-1].startswith("result-2:")
    restored = await engine().create_context(session=await session(conversation, "leader"),
                                             history_messages=tool_rounds())
    assert contents(restored.get_messages()) == contents(snapshot.messages)
    assert not any(str(message.content).startswith("result-0:") for message in restored.get_messages())


@pytest.mark.asyncio
@pytest.mark.parametrize("processor_type", ["MessageOffloader", "ToolResultWindowProcessor"])
async def test_real_offloader_uses_shared_buffers_and_survives_executor_eviction(conversation, tmp_path, processor_type):
    _, binding = conversation
    ctx_engine = engine()
    ctx_engine._workspace = SimpleNamespace(root_path=str(tmp_path / "private-executor"))
    value = await session(conversation)
    config = MessageOffloaderConfig(
        messages_threshold=1, large_message_threshold=20, trim_size=5, messages_to_keep=None,
        keep_last_round=False, protected_tool_names=[],
    ) if processor_type == "MessageOffloader" else ToolResultWindowProcessorConfig(
        tool_names=["web_search"], keep_last_k=1, trim_size=5,
    )
    ctx = await ctx_engine.create_context(session=value, processors=[(processor_type, config)])
    await write(ctx, tool_rounds(3, content_size=100))
    snapshot = await binding.store.read(binding.key)
    placeholder = next(message for message in snapshot.messages if isinstance(message, OffloadToolMessage))
    assert placeholder.offload_type == "in_memory"
    assert "private-executor" not in placeholder.content
    original = snapshot.offload_messages[placeholder.offload_handle][0]
    assert original.content == "result-0:" + "x" * 100
    assert not (tmp_path / "private-executor").exists()
    await ctx_engine.clear_context(session_id=value.get_session_id())
    await conversation[0].release(value.get_session_id())
    restored = await engine().create_context(session=await session(conversation, "leader"))
    assert restored.save_state()["offload_messages"][placeholder.offload_handle][0] == original
    assert restored.get_messages()[-1].tool_call_id == "2"


@pytest.mark.asyncio
async def test_partial_save_flushes_sync_mutations_without_overwriting_local_contexts(conversation):
    _, binding = conversation
    value = await session(conversation)
    value.update_state({"task_plan": "keep", "context": {"uncached-local": {"messages": [UserMessage(content="local")]}}})
    ctx_engine = engine()
    ctx = await ctx_engine.create_context(session=value)
    await write(ctx, UserMessage(content="A"))
    ctx.set_messages([UserMessage(content="replace")])
    assert contents((await binding.store.read(binding.key)).messages) == ["A"]
    await ctx_engine.save_contexts(value, context_ids=["default_context_id"])
    await value.commit()
    assert contents((await binding.store.read(binding.key)).messages) == ["replace"]
    assert value.get_state("context")["uncached-local"]["messages"][0].content == "local"
    restored = await session(conversation)
    assert restored.get_state("task_plan") == "keep"
    assert "default_context_id" not in restored.get_state("context")
    ctx.pop_messages()
    await ctx_engine.save_contexts(value)
    assert (await binding.store.read(binding.key)).messages == []
    await ctx.add_messages(UserMessage(content="clear me"))
    await ctx.clear_messages()
    assert (await binding.store.read(binding.key)).messages == []


@pytest.mark.asyncio
async def test_usage_is_recomputed_by_current_counter_even_when_cached_messages_match(conversation):
    _, binding = conversation
    await binding.store.commit(binding.key, ConversationSnapshot(messages=[
        UserMessage(content="small"),
        AssistantMessage(content="answer", usage_metadata=UsageMetadata(input_tokens=900000, output_tokens=1,
                                                                        total_tokens=900001)),
    ]))
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value, token_counter=StringLengthCounter())
    window = await ctx.get_context_window()
    assert window.statistic.total_tokens < 1000
    await ctx_engine.create_context(session=value)
    assert not ContextUtils.has_valid_usage_metadata(ctx.get_messages()[-1])
    assert (await ctx.get_context_window()).statistic.total_tokens < 1000


@pytest.mark.asyncio
async def test_failed_or_uncertain_commit_blocks_further_work_until_refresh(conversation, monkeypatch):
    _, binding = conversation
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value)
    original_commit = binding.store.commit
    writes = []

    async def commit_then_lose_ack(key, snapshot):
        writes.append(contents(snapshot.messages))
        await original_commit(key, snapshot)
        raise OSError("ack lost after durable write")

    with monkeypatch.context() as patch:
        patch.setattr(binding.store, "commit", commit_then_lose_ack)
        await ctx.add_messages(UserMessage(content="side effect result"))
        assert writes == []
        with pytest.raises(BaseError) as failure:
            await ctx.get_context_window()
        assert isinstance(failure.value.cause, OSError)
        for operation in (ctx.get_context_window(), ctx.add_messages(UserMessage(content="must not append")),
                          ctx_engine.save_contexts(value)):
            with pytest.raises(BaseError, match="successful refresh"):
                await operation
        assert writes == [["side effect result"]]
    assert contents((await binding.store.read(binding.key)).messages) == ["side effect result"]
    await ctx_engine.create_context(session=value)
    await write(ctx, UserMessage(content="next operation"))
    assert contents((await binding.store.read(binding.key)).messages) == ["side effect result", "next operation"]


@pytest.mark.asyncio
async def test_failed_read_disables_cached_window_and_never_writes_it_back(conversation, monkeypatch):
    _, binding = conversation
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value)
    await write(ctx, UserMessage(content="A"))

    async def fail_read(key):
        raise OSError("provider unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(binding.store, "read", fail_read)
        with pytest.raises(BaseError) as failure:
            await ctx_engine.create_context(session=value)
        assert isinstance(failure.value.cause, OSError)
        with pytest.raises(BaseError):
            await ctx.get_context_window()
        with pytest.raises(BaseError):
            await ctx_engine.save_contexts(value)
    assert contents((await binding.store.read(binding.key)).messages) == ["A"]
    await ctx_engine.create_context(session=value)
    assert contents((await ctx.get_context_window()).context_messages) == ["A"]


@pytest.mark.asyncio
async def test_filesystem_offload_is_explicitly_rejected_for_shared_execution(conversation):
    _, binding = conversation
    messages = tool_rounds(1)
    messages[-1] = OffloadToolMessage(tool_call_id="0", content="private file", offload_type="filesystem",
                                     offload_handle="/private/executor/file.json")
    await binding.store.commit(binding.key, ConversationSnapshot(messages=messages))
    with pytest.raises(BaseError, match="filesystem_offload"):
        await engine().create_context(session=await session(conversation))
    await binding.store.commit(binding.key, ConversationSnapshot())
    ctx = await engine().create_context(session=await session(conversation))
    ctx.set_messages(messages)
    with pytest.raises(BaseError, match="provider-owned offload"):
        await ctx.save_conversation()
    assert (await binding.store.read(binding.key)).messages == []


@pytest.mark.asyncio
async def test_binding_normalization_and_private_conversations_are_isolated(conversation):
    checkpointer, binding = conversation
    private = ConversationBinding(ConversationKey(binding.key.session_id, "child"), binding.store)
    await binding.store.create(private.key)
    value = await session(conversation, context_id="a.b")
    value.bind_conversation(binding, context_id="a.b")
    ctx_engine = engine()
    ctx = await ctx_engine.create_context(context_id="a.b", session=value)
    await write(ctx, UserMessage(content="main"))
    child = await engine().create_context(session=await session(conversation, "child-executor", binding=private))
    await write(child, UserMessage(content="private X"))
    assert contents((await binding.store.read(binding.key)).messages) == ["main"]
    assert contents((await private.store.read(private.key)).messages) == ["private X"]
    unbound = create_agent_session(session_id=value.get_session_id(), card=AgentCard(id="agent"), checkpointer=checkpointer)
    with pytest.raises(BaseError, match="explicit conversation binding"):
        await ctx_engine.create_context(context_id="a.b", session=unbound)
    assert ctx.get_session_ref() is value


@pytest.mark.asyncio
async def test_get_window_processor_changes_are_committed_before_returning_to_model(conversation):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation),
                                        processors=[("SharedGetRewriteProcessor", SharedGetRewriteProcessorConfig())])
    await ctx.add_messages(UserMessage(content="before GET"))
    window = await ctx.get_context_window()
    assert contents(window.context_messages) == ["GET processor result"]
    assert contents((await binding.store.read(binding.key)).messages) == ["GET processor result"]


@pytest.mark.asyncio
async def test_appends_reach_the_record_at_a_model_request_or_the_final_save_only(conversation, monkeypatch):
    _, binding = conversation
    value = await session(conversation)
    ctx_engine = engine()
    ctx = await ctx_engine.create_context(session=value)
    commit = AsyncMock(wraps=binding.store.commit)
    monkeypatch.setattr(binding.store, "commit", commit)
    await ctx.add_messages(UserMessage(content="question"))
    assert commit.await_count == 0
    await ctx.get_context_window()
    assert commit.await_count == 1
    call, result = tool_rounds(1)[1:]
    await ctx.add_messages(call)
    # A process that dies here leaves a record another executor can still load.
    other = await engine().create_context(session=await session(conversation, "leader"))
    assert contents(other.get_messages()) == ["question"]
    await ctx.add_messages(result)
    assert commit.await_count == 1
    await ctx.get_context_window()
    assert commit.await_count == 2
    await ctx.add_messages(AssistantMessage(content="answer"))
    assert commit.await_count == 2
    await ctx_engine.save_contexts(value)
    assert commit.await_count == 3
    assert contents((await binding.store.read(binding.key)).messages)[-1] == "answer"


@pytest.mark.asyncio
async def test_real_react_run_writes_before_its_model_request_and_at_the_end(conversation, monkeypatch):
    _, binding = conversation
    agent, llm = react_agent("agent", monkeypatch, ["answer-A"])
    writes = []
    original_commit = binding.store.commit

    async def record_commit(key, snapshot):
        writes.append((llm.call_count, contents(snapshot.messages)))
        await original_commit(key, snapshot)

    monkeypatch.setattr(binding.store, "commit", record_commit)
    assert (await agent.invoke({"query": "A"}, session=await session(conversation)))["result_type"] == "answer"
    assert writes == [(0, ["A"]), (1, ["A", "answer-A"])]


@pytest.mark.asyncio
async def test_unchanged_reads_and_final_flushes_do_not_rewrite_the_shared_record(conversation, monkeypatch):
    _, binding = conversation
    value = await session(conversation)
    ctx_engine = engine()
    ctx = await ctx_engine.create_context(session=value)
    commit = AsyncMock(wraps=binding.store.commit)
    monkeypatch.setattr(binding.store, "commit", commit)
    await ctx.add_messages(UserMessage(content="saved once"))
    for _ in range(2):
        assert contents((await ctx.get_context_window()).context_messages) == ["saved once"]
        await ctx_engine.save_contexts(value)
        await ctx.save_conversation()
    assert await ctx.compress_context() == "noop"
    assert commit.await_count == 1
    assert contents((await binding.store.read(binding.key)).messages) == ["saved once"]


@pytest.mark.asyncio
async def test_offload_only_edits_and_clear_are_saved_once(conversation, monkeypatch):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation))
    commit = AsyncMock(wraps=binding.store.commit)
    monkeypatch.setattr(binding.store, "commit", commit)
    ctx.offload_messages("original", [UserMessage(content="offloaded source")])
    await ctx.save_conversation()
    await ctx.save_conversation()
    assert contents((await binding.store.read(binding.key)).offload_messages["original"]) == ["offloaded source"]
    assert commit.await_count == 1
    await ctx.clear_messages()
    await ctx.save_conversation()
    assert (await binding.store.read(binding.key)).offload_messages == {}
    assert commit.await_count == 2


@pytest.mark.asyncio
async def test_offload_only_restore_is_flushed_without_a_second_message_authority(conversation):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation))
    ctx.load_state({ctx.context_id(): {"messages": [], "offload_messages": {
        "restored": [UserMessage(content="restored source")],
    }}})
    await ctx.save_conversation()
    assert contents((await binding.store.read(binding.key)).offload_messages["restored"]) == ["restored source"]


@pytest.mark.asyncio
async def test_edits_during_a_save_are_not_marked_as_already_persisted(conversation, monkeypatch):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation))
    original_commit = binding.store.commit
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_commit(key, snapshot):
        entered.set()
        await release.wait()
        await original_commit(key, snapshot)

    monkeypatch.setattr(binding.store, "commit", blocked_commit)
    ctx.set_messages([UserMessage(content="before save")])
    saving = asyncio.create_task(ctx.save_conversation())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        ctx.set_messages([UserMessage(content="edited during save")])
    finally:
        release.set()
        await asyncio.wait_for(saving, 3)
    assert contents((await binding.store.read(binding.key)).messages) == ["before save"]
    await ctx.save_conversation()
    assert contents((await binding.store.read(binding.key)).messages) == ["edited during save"]


@pytest.mark.asyncio
async def test_append_during_compression_is_flushed_by_existing_processor_lock_holder(conversation):
    _, binding = conversation
    started, release = asyncio.Event(), asyncio.Event()
    ctx = await engine().create_context(session=await session(conversation), processors=[(
        "SharedBlockingCompressor", SharedBlockingCompressorConfig(started=started, release=release),
    )])
    await write(ctx, UserMessage(content="compress me"))
    task = asyncio.create_task(ctx.compress_context())
    try:
        await asyncio.wait_for(started.wait(), 5)
        await ctx.add_messages(UserMessage(content="arrived during compression"))
        assert contents((await binding.store.read(binding.key)).messages) == ["compress me"]
    finally:
        release.set()
        assert await asyncio.wait_for(task, 5) == "compressed"
    assert contents((await binding.store.read(binding.key)).messages) == ["summary", "arrived during compression"]


@pytest.mark.asyncio
async def test_append_during_compression_commit_waits_and_is_not_lost(conversation, monkeypatch):
    _, binding = conversation
    ctx = await engine().create_context(session=await session(conversation), processors=[(
        "MicroCompactProcessor", MicroCompactProcessorConfig(trigger_threshold=100, keep_recent_per_tool=1),
    )])
    await ctx.add_messages(tool_rounds())
    original_commit = binding.store.commit
    committing, release = asyncio.Event(), asyncio.Event()

    async def block_first_commit(key, snapshot):
        if not committing.is_set():
            committing.set()
            await release.wait()
        await original_commit(key, snapshot)

    with monkeypatch.context() as patch:
        patch.setattr(binding.store, "commit", block_first_commit)
        compression = asyncio.create_task(ctx.compress_context())
        append = None
        try:
            await asyncio.wait_for(committing.wait(), 5)
            append = asyncio.create_task(ctx.add_messages(UserMessage(content="arrived during commit")))
            await asyncio.sleep(0)
            assert not append.done()
        finally:
            release.set()
            assert await asyncio.wait_for(compression, 5) == "compressed"
            if append is not None:
                await asyncio.wait_for(append, 5)
    assert (await binding.store.read(binding.key)).messages[-1].tool_call_id == "2"
    await ctx.save_conversation()
    assert (await binding.store.read(binding.key)).messages[-1].content == "arrived during commit"


@pytest.mark.asyncio
async def test_cancelled_commit_requires_refresh_and_releases_processor_lock(conversation, monkeypatch):
    _, binding = conversation
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value)
    started = asyncio.Event()

    async def cancelled_commit(key, snapshot):
        started.set()
        await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(binding.store, "commit", cancelled_commit)
        await ctx.add_messages(UserMessage(content="not committed"))
        save = asyncio.create_task(ctx.save_conversation())
        await asyncio.wait_for(started.wait(), 5)
        save.cancel()
        with pytest.raises(asyncio.CancelledError):
            await save
        with pytest.raises(BaseError, match="successful refresh"):
            await ctx.get_context_window()
    await asyncio.wait_for(ctx_engine.create_context(session=value), 5)
    assert ctx.get_messages() == []
    assert (await binding.store.read(binding.key)).messages == []


@pytest.mark.asyncio
async def test_cache_identity_preserves_session_and_context_components(conversation):
    _, binding = conversation
    second_binding = ConversationBinding(ConversationKey("other-product", "main"), binding.store)
    await binding.store.create(second_binding.key)
    ctx_engine = engine()
    first_session = await session(conversation, "a_b", context_id="c")
    second_session = await session(conversation, "a", context_id="b_c", binding=second_binding)
    first = await ctx_engine.create_context(session=first_session, context_id="c")
    second = await ctx_engine.create_context(session=second_session, context_id="b_c")
    assert first is not second
    await write(first, UserMessage(content="first"))
    await write(second, UserMessage(content="second"))
    assert contents((await binding.store.read(binding.key)).messages) == ["first"]
    assert contents((await second_binding.store.read(second_binding.key)).messages) == ["second"]
    await ctx_engine.clear_context(session_id="a_b")
    assert ctx_engine.get_context(session_id="a", context_id="b_c") is second
    await ctx_engine.save_contexts(second_session)


@pytest.mark.asyncio
async def test_cancelled_compression_requires_refresh_before_next_model_window(conversation):
    _, binding = conversation
    started, release = asyncio.Event(), asyncio.Event()
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value, processors=[(
        "SharedBlockingCompressor", SharedBlockingCompressorConfig(started=started, release=release),
    )])
    await write(ctx, UserMessage(content="original"))
    task = asyncio.create_task(ctx.compress_context())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(BaseError, match="successful refresh"):
        await ctx.get_context_window()
    assert contents((await binding.store.read(binding.key)).messages) == ["original"]
    await asyncio.wait_for(ctx_engine.create_context(session=value), 5)
    assert contents((await ctx.get_context_window()).context_messages) == ["original"]


def react_agent(name, monkeypatch, answers):
    agent = ReActAgent(AgentCard(id=name, name=name)).configure(ReActAgentConfig(
        model_name="mock-model", model_provider="OpenAI",
        prompt_template=[{"role": "system", "content": f"current rules for {name}"}],
        context_engine_config=ContextEngineConfig(enable_openrouter_model_context_window_tokens=False),
    ))
    llm = MockLLMModel()
    llm.set_responses([create_text_response(answer) for answer in answers])
    monkeypatch.setattr(agent, "_get_llm", lambda: llm)
    return agent, llm


@pytest.mark.asyncio
async def test_real_react_requests_follow_shared_window_across_cached_executors(conversation, monkeypatch):
    _, binding = conversation
    agent, llm = react_agent("agent", monkeypatch, ["answer-A", "answer-C"])
    other, other_llm = react_agent("other-executor", monkeypatch, ["D"])
    assert (await agent.invoke({"query": "A"}, session=await session(conversation)))["result_type"] == "answer"
    assert (await other.invoke({"query": "B"}, session=await session(conversation, "other-executor")))["result_type"] == "answer"
    old = agent.context_engine.get_context(session_id="agent")
    old.set_messages([UserMessage(content="stale cache must not reach model")])
    assert (await agent.invoke({"query": "C"}, session=await session(conversation)))["result_type"] == "answer"
    request = llm.call_history[-1]
    assert contents([message for message in request if message.role != "system"]) == ["A", "answer-A", "B", "D", "C"]
    assert contents([message for message in request if message.role == "system"]) == ["current rules for agent"]
    assert other_llm.call_count == 1 and llm.call_count == 2
    assert contents((await binding.store.read(binding.key)).messages) == ["A", "answer-A", "B", "D", "C", "answer-C"]


def budgeted_agent(name, monkeypatch, window_tokens, answers):
    """A real ReAct executor whose compressor triggers at 80% of its own context window."""
    agent = ReActAgent(AgentCard(id=name, name=name)).configure(ReActAgentConfig(
        model_name="mock-model", model_provider="OpenAI",
        prompt_template=[{"role": "system", "content": f"current rules for {name}"}],
        context_engine_config=ContextEngineConfig(
            enable_openrouter_model_context_window_tokens=False, context_window_tokens=window_tokens,
        ),
        context_processors=[("RoundLevelCompressor", ForkedRoundLevelCompressorConfig(
            trigger_context_ratio=0.8, keep_recent_messages=0,
            model=ModelRequestConfig(model="summary-model"),
            model_client=ModelClientConfig(client_provider="OpenAI", api_key="test", api_base="https://model.invalid"),
        ))],
    ))
    llm = MockLLMModel()
    llm.set_responses([create_text_response(answer) for answer in answers])
    monkeypatch.setattr(agent, "_get_llm", lambda: llm)
    return agent, llm


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["main", "member"])
async def test_smaller_model_compresses_before_its_first_request_and_larger_model_keeps_the_summary(
    conversation, monkeypatch, refactored_context_processors, window,
):
    _, binding = conversation
    if window == "member":
        binding = binding.child(kind="team_member", identity="worker")
        await binding.store.create(binding.key)
    large, large_llm = budgeted_agent("large", monkeypatch, 200_000, ["large-1", "large-2"])
    small, small_llm = budgeted_agent("small", monkeypatch, 5_000, ["small-1"])
    compressions = []

    async def summarize(self, *, context, request):
        # The compression model is the system boundary; record which executor compressed and when.
        compressions.append((context.context_window_tokens(), small_llm.call_count))
        return CompressionResult(response=SimpleNamespace(
            content="SUMMARY OF EARLIER WORK", parser_content=None, usage_metadata=None,
        ))

    monkeypatch.setattr(PrefixCompactProcessor, "_invoke_compression", summarize)
    history = []
    for index in range(6):
        history += [UserMessage(content=f"question-{index} " + "q" * 1500),
                    AssistantMessage(content=f"answer-{index} " + "a" * 1500)]
    await binding.store.commit(binding.key, ConversationSnapshot(messages=history))
    counter = StringLengthCounter()
    assert 5_000 * 0.8 < counter.count_messages(history) < 200_000 * 0.8

    def originals(messages):
        return [message for message in messages if str(message.content).startswith(("question-", "answer-"))]

    async def run(agent, query):
        result = await agent.invoke({"query": query}, session=await session(conversation, agent.card.id, binding=binding))
        assert result["result_type"] == "answer"

    # Within the large budget the history is sent as it is.
    await run(large, "first")
    assert compressions == []
    assert len(originals(large_llm.call_history[-1])) == 12

    # The smaller executor compresses once, before its first model request.
    await run(small, "second")
    assert compressions == [(5_000, 0)]
    request = small_llm.call_history[-1]
    assert originals(request) == []
    assert counter.count_messages(request) <= 5_000
    record = (await binding.store.read(binding.key)).messages
    assert originals(record) == [] and "SUMMARY OF EARLIER WORK" in str(record[0].content)

    # Back on the large executor: its cached window is replaced by the summary and nothing is expanded or recompressed.
    await run(large, "third")
    assert compressions == [(5_000, 0)]
    request = large_llm.call_history[-1]
    assert originals(request) == [] and any("SUMMARY OF EARLIER WORK" in str(message.content) for message in request)
    record = (await binding.store.read(binding.key)).messages
    assert originals(record) == []
    assert contents(record)[-3:] == ["small-1", "third", "large-2"]


@pytest.mark.asyncio
async def test_input_storage_failure_prevents_actual_react_model_call(conversation, monkeypatch):
    _, binding = conversation
    agent, llm = react_agent("agent", monkeypatch, ["must not be generated"])
    commits = []

    async def fail_commit(key, snapshot):
        commits.append(snapshot)
        raise OSError("storage unavailable before model")

    with monkeypatch.context() as patch:
        patch.setattr(binding.store, "commit", fail_commit)
        with pytest.raises(BaseError):
            await agent.invoke({"query": "A"}, session=await session(conversation))
    assert llm.call_count == 0 and len(commits) == 1
    assert (await binding.store.read(binding.key)).messages == []


@pytest.mark.asyncio
async def test_shared_refresh_preserves_previous_request_for_kv_invalidation(conversation):
    ctx_engine = engine()
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value)
    await ctx.add_messages(UserMessage(content="original request prefix"))
    systems = [SystemMessage(content="unchanged system prompt")]
    previous = await ctx.get_context_window(system_messages=systems)
    assert ctx.detect_context_window_change(previous) is None
    other = await engine().create_context(session=await session(conversation, "other-executor"))
    other.set_messages([UserMessage(content="shared summary")])
    await other.save_conversation()
    await ctx_engine.create_context(session=await session(conversation))
    current = await ctx.get_context_window(system_messages=systems)
    change = ctx.detect_context_window_change(current)
    assert change is not None and change.has_change
    assert change.msg_start == 1
    assert contents(change.old_messages) == ["unchanged system prompt", "original request prefix"]


@pytest.mark.asyncio
async def test_cached_refresh_preserves_real_offloader_idle_timer(conversation, monkeypatch, refactored_context_processors):
    ctx_engine = engine(context_window_tokens=100)
    value = await session(conversation)
    ctx = await ctx_engine.create_context(session=value, processors=[(
        "MessageSummaryOffloader", ForkedMessageSummaryOffloaderConfig(
            ttl_seconds=5, add_message_threshold_ratio=10,
        ),
    )])
    processor = ctx._processors[0]
    now = [100.0]
    monkeypatch.setattr(processor._rule_pipeline, "_time_func", lambda: now[0])
    original_trigger = processor.trigger_get_context_window
    decisions = []

    async def observe_trigger(*args, **kwargs):
        decision = await original_trigger(*args, **kwargs)
        decisions.append(decision)
        return decision

    monkeypatch.setattr(processor, "trigger_get_context_window", observe_trigger)
    await ctx.add_messages(tool_rounds(1, content_size=500))
    await ctx.get_context_window()
    assert decisions == [False]
    assert ctx.last_context_window_access_at() == 100
    now[0] = 110.0
    await ctx_engine.create_context(session=await session(conversation))
    await ctx.get_context_window()
    assert decisions == [False, True]
    assert ctx.last_context_window_access_at() == 110
    _, binding = conversation
    saved = await binding.store.read(binding.key)
    offloaded = next(message for message in saved.messages if isinstance(message, OffloadToolMessage))
    assert offloaded.offload_type == "in_memory"
    assert saved.offload_messages[offloaded.offload_handle][0].content == "result-0:" + "x" * 500
