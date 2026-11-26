# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Host-decided compaction writes a shared conversation record only when its result is installed."""
import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.context_engine import (
    CompactionCandidate,
    CompactionSkipped,
    ContextEngine,
    ContextEngineConfig,
    ConversationBinding,
    ConversationKey,
    ConversationSnapshot,
)
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessorConfig
from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloaderConfig
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.single_agent import AgentCard

HISTORY = [
    UserMessage(content="Keep the public API stable. " * 200),
    AssistantMessage(content="Read the importer and its callers. " * 200),
    UserMessage(content="Write the parser tests next. " * 200),
    AssistantMessage(content="Parser tests are half written."),
]
SUMMARY = "Importer work: API stays stable, parser tests half written."
PROCESSORS = [("FullCompactProcessor", FullCompactProcessorConfig(
    trigger_total_tokens=1, messages_to_keep=1, compression_call_max_tokens=100000,
))]


def _texts(messages):
    """What a reader of the window sees; private message metadata differs between memory and storage."""
    return [(message.role, message.content) for message in messages]


TABLE_HISTORY = [
    UserMessage(content="Load the three tables."),
    *[
        message
        for index in range(3)
        for message in (
            AssistantMessage(
                content="", tool_calls=[ToolCall(id=f"call-{index}", type="function", name="query", arguments="{}")],
            ),
            ToolMessage(content=f"table {index}: " + "row of data " * 4000, tool_call_id=f"call-{index}"),
        )
    ],
    AssistantMessage(content="Loaded."),
    UserMessage(content="Summarise them."),
    AssistantMessage(content="Done."),
]
OFFLOADER = [("MessageOffloader", MessageOffloaderConfig(
    tokens_threshold=200, large_message_threshold=200, trim_size=50, messages_to_keep=2,
))]


def _engine(workspace=None):
    return ContextEngine(
        ContextEngineConfig(enable_tiktoken_counter=False, enable_openrouter_model_context_window_tokens=False),
        workspace=SimpleNamespace(root_path=str(workspace)) if workspace is not None else None,
    )


class _Shared:
    """One executor's live window on a shared record, with a scripted summary model."""

    def __init__(self, checkpointer, binding, engine, session, context):
        self.checkpointer, self.binding, self.engine, self.session, self.context = (
            checkpointer, binding, engine, session, context,
        )
        self.summary_started, self.summary_may_finish = asyncio.Event(), asyncio.Event()
        self.summary_may_finish.set()
        self.commits = []
        shared = self

        class Model:
            async def invoke(self, *args, **kwargs):
                shared.summary_started.set()
                await shared.summary_may_finish.wait()
                return AssistantMessage(content=SUMMARY)

        context._processors[0]._model = Model()
        commit = binding.store.commit

        async def counted(key, snapshot):
            shared.commits.append(len(snapshot.messages))
            return await commit(key, snapshot)

        binding.store.commit = counted

    async def record(self):
        return list((await self.binding.store.read(self.binding.key)).messages)

    def accept_every_candidate(self):
        async def hook(request, next_handler):
            return await next_handler(request)

        self.engine.register_compaction_hook(hook)


async def _open(provider, tmp_path, history, processors, workspace=None):
    if provider == "in_memory":
        checkpointer = InMemoryCheckpointer()
    else:
        checkpointer = await PersistenceCheckpointerProvider().create({
            "db_type": "sqlite", "db_path": str(tmp_path / "conversation"),
        })
    binding = ConversationBinding(ConversationKey("product", "main"), checkpointer.conversation_store())
    await binding.store.create(binding.key)
    await binding.store.commit(binding.key, ConversationSnapshot(messages=list(history)))
    session = create_agent_session(session_id="product", card=AgentCard(id="agent"), checkpointer=checkpointer)
    session.bind_conversation(binding)
    await session.pre_run()
    engine = _engine(workspace)
    context = await engine.create_context(session=session, processors=processors)
    return _Shared(checkpointer, binding, engine, session, context)


@pytest_asyncio.fixture(params=["in_memory", "sqlite"])
async def shared(request, tmp_path):
    opened = await _open(request.param, tmp_path, HISTORY, PROCESSORS)
    yield opened
    if request.param == "sqlite":
        await opened.checkpointer._kv_store.engine.dispose()


@pytest_asyncio.fixture(params=["in_memory", "sqlite"])
async def offloading(request, tmp_path):
    """A shared window whose only compaction processor is the built-in offloader, with a workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    opened = await _open(request.param, tmp_path, TABLE_HISTORY, OFFLOADER, workspace)
    opened.workspace = workspace
    yield opened
    if request.param == "sqlite":
        await opened.checkpointer._kv_store.engine.dispose()


@pytest.mark.asyncio
async def test_rejected_candidate_never_reaches_the_shared_record(shared):
    original = _texts(HISTORY)
    assert _texts(await shared.record()) == _texts(shared.context.get_messages()) == original

    async def hook(request, next_handler):
        assert isinstance(await next_handler(request), CompactionCandidate)
        return CompactionSkipped("keep the full conversation")

    shared.engine.register_compaction_hook(hook)
    result = await shared.engine.compact_context(session=shared.session, trigger="plugin")

    assert result.skip == "keep the full conversation"
    assert shared.commits == []
    assert _texts(await shared.record()) == _texts(shared.context.get_messages()) == original
    # The next run, another executor or a restart starts from the record.
    await shared.context.load_conversation(shared.binding)
    assert _texts(shared.context.get_messages()) == original


@pytest.mark.asyncio
async def test_precompute_cannot_replace_a_newer_shared_record(shared):
    shared.accept_every_candidate()
    shared.summary_may_finish.clear()
    precompute = asyncio.create_task(shared.engine.compact_context(session=shared.session, trigger="precompute"))
    await asyncio.wait_for(shared.summary_started.wait(), 5)
    await shared.context.add_messages(UserMessage(content="Also support Windows paths."))
    await shared.context.save_conversation()
    shared.summary_may_finish.set()
    await asyncio.wait_for(precompute, 5)

    assert shared.commits == [len(HISTORY) + 1]
    assert [message.content for message in await shared.record()][-1] == "Also support Windows paths."


@pytest.mark.asyncio
async def test_accepted_candidate_is_saved_once_and_the_window_stays_usable(shared):
    shared.accept_every_candidate()
    # The install saves while compact_context still owns the processor lock.
    result = await asyncio.wait_for(shared.engine.compact_context(session=shared.session, trigger="manual"), 5)

    assert isinstance(result, CompactionCandidate)
    assert len(shared.commits) == 1
    installed = shared.context.get_messages()
    assert any(SUMMARY in str(message.content) for message in installed)
    assert _texts(await shared.record()) == _texts(installed) != _texts(HISTORY)
    assert not shared.context.compaction_uncertain()

    await shared.context.add_messages(UserMessage(content="What is left to do?"))
    await shared.context.save_conversation()
    assert [message.content for message in await shared.record()][-1] == "What is left to do?"

    # Another executor on the same record reads the installed window.
    other_session = create_agent_session(
        session_id="product", card=AgentCard(id="leader"), checkpointer=shared.checkpointer,
    )
    other_session.bind_conversation(shared.binding)
    await other_session.pre_run()
    other = await _engine().create_context(session=other_session)
    assert _texts(other.get_messages()) == _texts(shared.context.get_messages())


@pytest.mark.asyncio
async def test_a_save_outside_compaction_still_waits_for_the_processor_lock(shared):
    shared.context.set_messages([*HISTORY, UserMessage(content="edited by the host")])
    async with shared.context.processor_guard("holder"):
        waiting = asyncio.create_task(shared.context.save_conversation())
        await asyncio.sleep(0.05)
        assert not waiting.done() and shared.commits == []
    await asyncio.wait_for(waiting, 5)
    assert shared.commits == [len(HISTORY) + 1]


@pytest.mark.asyncio
async def test_accepted_candidate_is_saved_when_the_session_commit_saves_the_contexts_again(shared, monkeypatch):
    shared.accept_every_candidate()
    native_commit = shared.session.commit

    async def commit_that_saves_again(*args, **kwargs):
        # A host checkpointer may save the contexts inside the commit, while the install holds the lock.
        await shared.engine.save_contexts(shared.session)
        return await native_commit(*args, **kwargs)

    monkeypatch.setattr(shared.session, "commit", commit_that_saves_again)
    result = await asyncio.wait_for(shared.engine.compact_context(session=shared.session, trigger="manual"), 5)

    assert isinstance(result, CompactionCandidate)
    assert len(shared.commits) == 1
    assert not shared.context.compaction_uncertain()
    assert _texts(await shared.record()) == _texts(shared.context.get_messages())


@pytest.mark.asyncio
async def test_builtin_offloader_keeps_draft_offloads_inside_the_shared_record(offloading):
    offloading.accept_every_candidate()
    result = await asyncio.wait_for(offloading.engine.compact_context(session=offloading.session, trigger="manual"), 5)

    assert isinstance(result, CompactionCandidate)
    assert not offloading.context.compaction_uncertain()
    assert [path for path in offloading.workspace.rglob("*") if path.is_file()] == []
    installed = offloading.context.get_messages()
    offloaded = [message for message in installed if getattr(message, "offload_handle", None)]
    assert offloaded and {message.offload_type for message in offloaded} == {"in_memory"}

    snapshot = await offloading.binding.store.read(offloading.binding.key)
    assert _texts(snapshot.messages) == _texts(installed) != _texts(TABLE_HISTORY)
    assert set(snapshot.offload_messages) == {message.offload_handle for message in offloaded}
    # Another executor on the same record gets the trimmed window and the offloaded originals.
    other_session = create_agent_session(
        session_id="product", card=AgentCard(id="leader"), checkpointer=offloading.checkpointer,
    )
    other_session.bind_conversation(offloading.binding)
    await other_session.pre_run()
    other = await _engine().create_context(session=other_session)
    assert _texts(other.get_messages()) == _texts(installed)
