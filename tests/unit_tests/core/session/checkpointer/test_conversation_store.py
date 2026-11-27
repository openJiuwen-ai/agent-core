# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Provider-backed shared data and public Session checkpoint separation."""
import asyncio
import base64

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import (
    ConversationBinding, ConversationKey, ConversationSnapshot, SessionConversationStore,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.context_engine.schema.messages import OffloadToolMessage
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import NoopCheckpointer
from openjiuwen.core.session.checkpointer.conversation_store import KVSessionConversationStore
from openjiuwen.core.session.checkpointer.inmemory import InMemoryCheckpointer
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider
from openjiuwen.core.single_agent import AgentCard


@pytest_asyncio.fixture(params=["in_memory", "sqlite", "shelve"])
async def checkpointer(request, tmp_path):
    if request.param == "in_memory":
        instance = InMemoryCheckpointer()
    else:
        instance = await PersistenceCheckpointerProvider().create({
            "db_type": request.param, "db_path": str(tmp_path / "checkpoint"),
        })
    yield instance
    if request.param == "sqlite":
        await instance._kv_store.engine.dispose()


def make_session(checkpointer, *, binding=None, card_id="agent"):
    return create_agent_session(session_id="product", card=AgentCard(id=card_id, name=card_id),
                                checkpointer=checkpointer, conversation_binding=binding)


@pytest.mark.asyncio
async def test_create_is_idempotent_and_snapshots_are_detached(checkpointer):
    store: SessionConversationStore = checkpointer.conversation_store()
    assert checkpointer.conversation_store() is store
    key = ConversationKey("product", "main")
    assert (await store.create(key)).messages == []
    message = UserMessage(content=[{"type": "text", "text": "A"}], metadata={"id": "message-1"})
    await store.commit(key, ConversationSnapshot(messages=[message]))
    message.metadata["id"] = "changed"
    existing = await store.create(key)
    assert existing.messages[0].metadata == {"id": "message-1"}
    existing.messages.clear()
    assert (await store.read(key)).messages[0].content == [{"type": "text", "text": "A"}]


def test_to_state_copies_unless_the_caller_asks_for_its_own_lists():
    message = UserMessage(content="A")
    snapshot = ConversationSnapshot(messages=[message])
    assert snapshot.to_state()["messages"][0] is not message
    assert snapshot.to_state(copy=False)["messages"][0] is message


@pytest.mark.asyncio
async def test_store_detaches_by_serialising_without_copying_the_window_first(checkpointer, monkeypatch):
    """A write runs before every model request; the record must not be copied again around it."""
    from openjiuwen.core.context_engine.schema import conversation as schema

    copies = []
    real = schema.deepcopy
    monkeypatch.setattr(schema, "deepcopy", lambda value: copies.append(1) or real(value))
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    await store.create(key)
    message = UserMessage(content="saved", metadata={"id": "message-1"})
    await store.commit(key, ConversationSnapshot(messages=[message]))
    message.metadata["id"] = "changed after the write"
    first = await store.read(key)
    first.messages[0].metadata["id"] = "changed after the read"
    assert (await store.read(key)).messages[0].metadata == {"id": "message-1"}
    assert copies == []


@pytest.mark.asyncio
async def test_a_late_concurrent_create_keeps_the_first_turn(tmp_path):
    """Two executors may create the same record at once; the loser must not reset it."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
    from tests.unit_tests.core.foundation.store.test_db_based_kv_store import PauseBeforeWrite

    url = f"sqlite+aiosqlite:///{tmp_path / 'kv.db'}"
    engines = [create_async_engine(url), create_async_engine(url)]
    try:
        first_kv, second_kv = (DbBasedKVStore(engine) for engine in engines)
        await first_kv._create_table_if_not_exist()
        first, second = KVSessionConversationStore(first_kv), KVSessionConversationStore(second_kv)
        key = ConversationKey("product", "main")
        pause = PauseBeforeWrite(second_kv)
        late = asyncio.create_task(second.create(key))
        await pause.wait_reached()
        await first.create(key)
        await first.commit(key, ConversationSnapshot(messages=[UserMessage(content="first turn")]))
        pause.proceed.set()
        await late
        assert [message.content for message in (await first.read(key)).messages] == ["first turn"]
    finally:
        for engine in engines:
            await engine.dispose()


@pytest.mark.asyncio
async def test_keys_separate_main_children_sessions_and_delimiters(checkpointer):
    store = checkpointer.conversation_store()
    keys = [ConversationKey("s:a", "b"), ConversationKey("s", "a:b"),
            ConversationKey("s", "main"), ConversationKey("s", "child"),
            ConversationKey("other", "main"), ConversationKey("s%3Aa", "b")]
    for index, key in enumerate(keys):
        await store.create(key)
        await store.commit(key, ConversationSnapshot(messages=[UserMessage(content=str(index))]))
    for index, key in enumerate(keys):
        assert (await store.read(key)).messages[0].content == str(index)


@pytest.mark.asyncio
async def test_pending_tools_and_offload_buffers_roundtrip(checkpointer):
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    await store.create(key)
    pending = AssistantMessage(tool_calls=[{"id": "call", "type": "function", "name": "lookup", "arguments": "{}"}],
                               metadata={"context_message_id": "assistant-1"})
    result = ToolMessage(tool_call_id="earlier-call", content="structured result")
    placeholder = OffloadToolMessage(tool_call_id="earlier-call", content="see buffer",
                                     offload_type="in_memory", offload_handle="buffer")
    snapshot = ConversationSnapshot(messages=[UserMessage(content="A"), placeholder, pending],
                                    offload_messages={"buffer": [result]})
    await store.commit(key, snapshot)
    restored = await store.read(key)
    assert restored == snapshot
    assert isinstance(restored.messages[-1], AssistantMessage)
    assert restored.messages[-1].tool_calls[0].id == "call"
    assert restored.messages[1].offload_handle == "buffer"
    assert restored.offload_messages["buffer"][0].tool_call_id == "earlier-call"


@pytest.mark.asyncio
async def test_missing_read_and_commit_do_not_create_record(checkpointer):
    store = checkpointer.conversation_store()
    key = ConversationKey("missing", "main")
    for operation in (store.read(key), store.commit(key, ConversationSnapshot())):
        with pytest.raises(BaseError) as error:
            await operation
        assert error.value.status == StatusCode.CONTEXT_CONVERSATION_NOT_FOUND


@pytest.mark.asyncio
async def test_bound_checkpoint_preserves_execution_state_excludes_shared_context(checkpointer):
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    binding = ConversationBinding(key, store)
    await store.create(key)
    await store.commit(key, ConversationSnapshot(messages=[UserMessage(content="current shared window")]))
    session = make_session(checkpointer, binding=binding)
    session.bind_conversation(binding, context_id="second-bound-context")
    await session.pre_run()
    session.update_state({"task_plan": "plan", "context": {
        "default_context_id": {"messages": [UserMessage(content="stale executor window")]},
        "auxiliary": {"messages": [UserMessage(content="private auxiliary")]},
        "second-bound-context": {"messages": [UserMessage(content="another stale projection")]},
    }})
    session._inner.state().update_global({"goal": "keep goal"})
    session._inner.state().update({"local_iteration": 7})
    await session.commit()
    # Projection must not delete the live context or mutate other context entries.
    assert session.get_state("context")["default_context_id"]["messages"][0].content == "stale executor window"
    unbound_reader = make_session(checkpointer)
    await unbound_reader.pre_run()
    assert "default_context_id" not in unbound_reader.get_state("context")
    assert "second-bound-context" not in unbound_reader.get_state("context")
    assert unbound_reader.get_state("task_plan") == "plan"
    assert unbound_reader._inner.state().get("local_iteration") == 7
    assert unbound_reader._inner.state().get_global("goal") == "keep goal"
    assert unbound_reader.get_state("context")["auxiliary"]["messages"][0].content == "private auxiliary"
    assert (await store.read(key)).messages[0].content == "current shared window"
    other_executor = make_session(checkpointer, binding=binding, card_id="leader")
    await other_executor.pre_run()
    assert other_executor.get_state("task_plan") is None
    assert other_executor.get_conversation_binding().key == key


@pytest.mark.asyncio
async def test_binding_ignores_old_checkpoint_context_without_importing_it(checkpointer):
    earlier = make_session(checkpointer)
    await earlier.pre_run()
    earlier.update_state({"context": {"default_context_id": {"messages": [UserMessage(content="old")]}},
                          "task_plan": "resume"})
    await earlier.commit()
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    session = make_session(checkpointer, binding=ConversationBinding(key, store))
    await session.pre_run()
    assert session.get_state("context") == {}
    assert session.get_state("task_plan") == "resume"
    with pytest.raises(BaseError) as error:
        await store.read(key)
    assert error.value.status == StatusCode.CONTEXT_CONVERSATION_NOT_FOUND
    # Recovery filtering must not alter the checkpoint stored for unbound callers.
    unbound = make_session(checkpointer)
    await unbound.pre_run()
    assert unbound.get_state("context")["default_context_id"]["messages"][0].content == "old"


@pytest.mark.asyncio
async def test_executor_release_does_not_delete_shared_conversation(checkpointer):
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    await store.create(key)
    await store.commit(key, ConversationSnapshot(messages=[UserMessage(content="survive eviction")]))
    for agent_id in ("agent", None):
        await checkpointer.release("product", agent_id=agent_id)
        assert (await store.read(key)).messages[0].content == "survive eviction"
    # Conversation storage must not collide with release's arbitrary '<id>:' prefix.
    await checkpointer.release("conversation")
    assert (await store.read(key)).messages[0].content == "survive eviction"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "shelve"])
async def test_provider_reopen_reads_durable_conversation(tmp_path, backend):
    conf = {"db_type": backend, "db_path": str(tmp_path / "durable")}
    first = await PersistenceCheckpointerProvider().create(conf)
    key = ConversationKey("product", "main")
    await first.conversation_store().create(key)
    await first.conversation_store().commit(key, ConversationSnapshot(messages=[UserMessage(content="durable")]))
    second = await PersistenceCheckpointerProvider().create(conf)
    try:
        assert (await second.conversation_store().read(key)).messages[0].content == "durable"
    finally:
        if backend == "sqlite":
            await first._kv_store.engine.dispose()
            await second._kv_store.engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_record_is_reported_and_kept_by_read_and_create(checkpointer):
    store = checkpointer.conversation_store()
    kv = store._kv_store
    key = ConversationKey("product", "main")
    storage_key = store._storage_key(key)
    dump_type, data = store._serde.dumps_typed({"schema_version": 99, "messages": [], "offload_messages": {}})
    future_schema = dump_type + ":" + base64.b64encode(data).decode("ascii")
    unknown_codec = store._encode(ConversationSnapshot()).replace("pickle", "unknown", 1)
    for blob in ("broken", "null", "[]", "json:e30=", unknown_codec, future_schema):
        await kv.set(storage_key, blob)
        for operation in (store.read(key), store.create(key)):
            with pytest.raises(BaseError) as error:
                await operation
            assert error.value.status == StatusCode.CONTEXT_MESSAGE_INVALID
            assert await kv.get(storage_key) == blob


@pytest.mark.asyncio
async def test_commit_replaces_the_record_without_decoding_the_stored_one(checkpointer, monkeypatch):
    store = checkpointer.conversation_store()
    key = ConversationKey("product", "main")
    await store.create(key)
    decoded = []
    original = store._serde.loads_typed
    monkeypatch.setattr(store._serde, "loads_typed", lambda data: decoded.append(data) or original(data))
    await store.commit(key, ConversationSnapshot(messages=[UserMessage(content="saved")]))
    assert decoded == []
    assert (await store.read(key)).messages[0].content == "saved"
    assert len(decoded) == 1


@pytest.mark.asyncio
async def test_kv_provider_can_return_text_as_bytes():
    kv = InMemoryKVStore()
    store = KVSessionConversationStore(kv)
    key = ConversationKey("product", "main")
    payload = store._encode(ConversationSnapshot(messages=[UserMessage(content="bytes")])).encode("ascii")
    await kv.set(store._storage_key(key), payload)
    assert (await store.read(key)).messages[0].content == "bytes"


@pytest.mark.asyncio
async def test_storage_failure_is_distinct_from_missing_and_preserves_cause(monkeypatch):
    kv = InMemoryKVStore()
    store = KVSessionConversationStore(kv)
    key = ConversationKey("product", "main")
    await store.create(key)
    failure = OSError("provider unavailable")

    async def fail(*args, **kwargs):
        raise failure

    missing = ConversationKey("new", "main")
    for method, operation in (("get", lambda: store.read(key)), ("exclusive_set", lambda: store.create(missing)),
                               ("set", lambda: store.commit(key, ConversationSnapshot()))):
        with monkeypatch.context() as patch:
            patch.setattr(kv, method, fail)
            with pytest.raises(BaseError) as error:
                await operation()
            assert error.value.status == StatusCode.CONTEXT_EXECUTION_ERROR
            assert error.value.cause is failure
    assert (await store.read(key)).messages == []


@pytest.mark.parametrize("snapshot", [ConversationSnapshot(schema_version=2), ConversationSnapshot(schema_version=True),
                                      ConversationSnapshot(messages=["invalid"]),
                                      ConversationSnapshot(messages=[OffloadToolMessage(tool_call_id="missing", content="buffer",
                                                                                        offload_type="in_memory", offload_handle="missing")]),
                                      ConversationSnapshot(offload_messages={"buffer": "invalid"})])
def test_invalid_snapshot_is_rejected(snapshot):
    with pytest.raises(BaseError):
        snapshot.to_state()
    with pytest.raises(BaseError):
        snapshot.to_state(copy=False)


@pytest.mark.parametrize("session_id,conversation_id", [("", "main"), ("s", " "), (None, "main")])
def test_key_requires_two_explicit_nonempty_ids(session_id, conversation_id):
    with pytest.raises(BaseError):
        ConversationKey(session_id, conversation_id)


@pytest.mark.asyncio
async def test_binding_is_runtime_only_immutable_and_set_before_recovery():
    checkpointer = InMemoryCheckpointer()
    binding = ConversationBinding(ConversationKey("product", "main"), checkpointer.conversation_store())
    session = make_session(checkpointer, binding=binding)
    session.bind_conversation(binding)
    assert session.get_conversation_binding() is binding
    assert "conversation" not in repr(session._inner.state().get_state())
    with pytest.raises(BaseError):
        session.bind_conversation(ConversationBinding(ConversationKey("product", "other"), binding.store))
    await session.pre_run()
    session.bind_conversation(binding)
    with pytest.raises(BaseError):
        session.bind_conversation(binding, context_id="too-late")
    with pytest.raises(BaseError):
        NoopCheckpointer().conversation_store()
