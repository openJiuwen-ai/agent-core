"""Native checkpoint preparation and lazy legacy offloads."""
import json
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.context.file_offload_store import FileOffloadStore
from openjiuwen.core.context_engine.context.message_buffer import OffloadMessageBuffer
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import ToolMessage, UserMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


def context():
    return SessionModelContext("default_context_id", "durable-test", ContextEngineConfig(), history_messages=[])


@pytest.mark.asyncio
async def test_migrated_offloads_restore_lazily_and_preserve_message_types(tmp_path, monkeypatch):
    original = context()
    message = ToolMessage(content="raw original", tool_call_id="tool-1")
    original.offload_messages("legacy-handle", [message])
    old_state = original.save_state()
    original.persist_offloaded_messages(str(tmp_path))
    state = original.save_state()
    assert old_state["offload_messages"]["legacy-handle"][0].content == "raw original"
    assert state["offload_messages"] == {}
    assert state["offload_directory"] == str(tmp_path)
    read = Mock(side_effect=AssertionError("restoration must not load raw offloads"))
    original_get = FileOffloadStore.get
    monkeypatch.setattr(FileOffloadStore, "get", read)
    restored = context()
    restored.load_state({"default_context_id": state})
    assert restored.save_state()["offload_messages"] == {}
    read.assert_not_called()
    monkeypatch.setattr(FileOffloadStore, "get", original_get)
    messages = await restored._offload_message_buffer.reload("legacy-handle", "in_memory")
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "tool-1" and messages[0].content == "raw original"


@pytest.mark.asyncio
async def test_new_offloads_do_not_accumulate_in_checkpoint(tmp_path):
    buffer = OffloadMessageBuffer(storage_directory=str(tmp_path))
    for index in range(20):
        buffer.offload(f"handle-{index}", "in_memory", [UserMessage(content=f"raw-{index}")])
    assert buffer.get_all() == {}
    assert len(list(tmp_path.glob("*.json"))) == 20
    assert (await buffer.reload("handle-19", "in_memory"))[0].content == "raw-19"


def test_large_raw_offload_is_written_in_bounded_space_and_reload_is_bounded(tmp_path):
    message = UserMessage(content='中文🙂"\\' * 2_000_000)
    store = FileOffloadStore(str(tmp_path))
    tracemalloc.start()
    version = store.put("huge", [message])
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    assert store.path("huge", version).stat().st_size > 8 * 1024 * 1024
    with pytest.raises(ValueError, match="reload budget"):
        store.get("huge", version=version)


def test_offload_publish_failure_retains_original_memory(tmp_path, monkeypatch):
    buffer = OffloadMessageBuffer({"handle": [UserMessage(content="intact")]})
    monkeypatch.setattr("openjiuwen.core.context_engine.context.file_offload_store.os.replace",
                        Mock(side_effect=OSError("injected publish failure")))
    with pytest.raises(OSError):
        buffer.persist(str(tmp_path))
    assert buffer.get_all()["handle"][0].content == "intact"
    assert buffer.storage_directory() is None
    assert not list(tmp_path.glob("*.tmp"))


def test_handle_cannot_escape_storage_directory(tmp_path):
    store = FileOffloadStore(str(tmp_path))
    version = store.put("../../outside", [UserMessage(content="inside")])
    assert store.path("../../outside", version).parent == tmp_path
    assert store.get("../../outside", version=version)[0].content == "inside"


@pytest.mark.asyncio
async def test_reusing_a_handle_cannot_rewrite_an_older_checkpoint(tmp_path):
    original = context()
    original.offload_messages("reused", [UserMessage(content="first version")])
    original.persist_offloaded_messages(str(tmp_path))
    first = original.save_state()
    original.offload_messages("reused", [UserMessage(content="second version")])
    second = original.save_state()
    restored = context()
    restored.load_state({"default_context_id": first})
    assert (await restored._offload_message_buffer.reload("reused", "in_memory"))[0].content == "first version"
    latest = context()
    latest.load_state({"default_context_id": second})
    assert (await latest._offload_message_buffer.reload("reused", "in_memory"))[0].content == "second version"
    latest._offload_message_buffer.clear("reused", "in_memory")
    assert await latest._offload_message_buffer.reload("reused", "in_memory") == []
    assert (await restored._offload_message_buffer.reload("reused", "in_memory"))[0].content == "first version"


@pytest.mark.asyncio
async def test_native_save_hook_runs_before_session_state_install_and_never_commits():
    engine = ContextEngine()
    state = {"context": None}
    session = SimpleNamespace(get_session_id=lambda: "hook-test", get_state=state.get,
                              update_state=state.update, commit=AsyncMock())
    await engine.create_context(session=session, history_messages=[UserMessage(content="effective")])
    calls = []

    async def hook(current_session, states):
        assert current_session is session and state["context"] is None
        calls.append(states["default_context_id"]["messages"][0].content)
        current_session.update_state({"prepared_ref": "immutable-file"})
        return states

    engine.set_persistence_hook(hook)
    await engine.save_contexts(session)
    assert calls == ["effective"]
    assert state["prepared_ref"] == "immutable-file"
    assert state["context"]["default_context_id"]["messages"][0].content == "effective"
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("disk full"), MemoryError("injected pressure")])
async def test_failed_native_hook_does_not_install_partial_context(error):
    engine = ContextEngine()
    state = {}
    session = SimpleNamespace(get_session_id=lambda: "hook-failure", get_state=state.get, update_state=state.update)
    await engine.create_context(session=session, history_messages=[UserMessage(content="old")])
    await engine.save_contexts(session)
    old = state["context"]
    engine.get_context(session_id="hook-failure").set_messages([UserMessage(content="new")])
    engine.set_persistence_hook(AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await engine.save_contexts(session)
    assert state["context"] is old
    assert state["context"]["default_context_id"]["messages"][0].content == "old"


def test_native_hook_registration_is_owned():
    engine = ContextEngine()
    hook = AsyncMock()
    another = AsyncMock()
    engine.set_persistence_hook(hook)
    engine.set_persistence_hook(hook)
    with pytest.raises(ValueError):
        engine.set_persistence_hook(another)
    engine.clear_persistence_hook(another)
    assert engine.persistence_hook is hook
    engine.clear_persistence_hook(hook)
    assert engine.persistence_hook is None


@pytest.mark.asyncio
async def test_consumed_input_metadata_is_installed_with_native_origin():
    from openjiuwen.core.foundation.llm.schema.message import (
        OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER, OPENJIUWEN_MESSAGE_ORIGIN_METADATA,
    )
    target = SimpleNamespace(add_messages=AsyncMock())
    agent = SimpleNamespace(_sync_prompt_attachments=AsyncMock())
    callback = SimpleNamespace(inputs=object())

    async def fire(_event):
        callback.inputs.metadata.update({"request_ids": ["consumed-1"], OPENJIUWEN_MESSAGE_ORIGIN_METADATA: "forged"})

    callback.fire = fire
    await ReActAgent._admit_user_message(agent, callback, target, ["input"], source="query")
    message = target.add_messages.await_args.args[0]
    assert message.metadata["request_ids"] == ["consumed-1"]
    assert message.metadata[OPENJIUWEN_MESSAGE_ORIGIN_METADATA] == OPENJIUWEN_MESSAGE_ORIGIN_EXTERNAL_USER
