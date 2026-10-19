# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-11 window archive storage and rollover behavior."""

import hashlib
import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.harness.persistent_agent.execution.control import ExecutionControl
from openjiuwen.harness.persistent_agent.schema.integration import (
    TEMPORARY_BACKGROUND,
    WINDOW_PART_KEY,
    SessionCheckpoint,
)
from openjiuwen.harness.persistent_agent.schema.window import WindowRollover

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
STATE = import_module(f"{PKG}.execution.session_state")
RECORD = import_module(f"{PKG}.schema.session_record")
WINDOW = import_module(f"{PKG}.schema.window")
WINDOW_MANAGER = import_module(f"{PKG}.execution.window_management")


class ExactCounter:
    measurement_source = "native_tokenizer"
    measurement_estimated = False
    measurement_fallback_reason = None

    def measure_messages(self, messages, *, model=""):
        return SimpleNamespace(
            source=self.measurement_source,
            estimated=self.measurement_estimated,
            fallback_reason=self.measurement_fallback_reason,
            tokens=sum(len(message.content) for message in messages),
        )

    def measure_tools(self, tools, *, model=""):
        return SimpleNamespace(
            source=self.measurement_source,
            estimated=self.measurement_estimated,
            fallback_reason=self.measurement_fallback_reason,
            tokens=sum(len(str(tool)) for tool in tools or []),
        )

    def count_messages(self, messages, *, model=""):
        return sum(len(message.content) for message in messages)

    def count_tools(self, tools, *, model=""):
        return sum(len(str(tool)) for tool in tools or [])


def archive_payload(**overrides):
    payload = {
        "old_window_id": "window-1",
        "old_window_seq": 1,
        "new_window_id": "window-2",
        "new_window_seq": 2,
        "trigger_kind": "agent",
        "request_id": "request-1",
        "execution_id": "execution-1",
        "spec_fingerprint": "a" * 64,
        "seed": {"objective": "finish the task", "next_steps": ["verify"]},
        "retained_item_refs": ["message-4"],
        "token_usage": {"input_tokens": 120, "input_limit": 200},
        "messages": RECORD.encode_messages([UserMessage(content="the full previous window")]),
        "candidate_fingerprint": "c" * 64,
        "iteration_seq": 4,
        "last_group_message_id": "group-message-4",
    }
    payload.update(overrides)
    return payload


def message_fingerprint(messages):
    canonical = json.dumps(RECORD.encode_messages(messages), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def window_store(backend=None):
    backend = backend or InMemoryKVStore()
    return STATE.WindowStore(backend), backend


def test_archive_keys_are_session_scoped_and_derived_from_window_sequence():
    assert STATE.WindowStore.archive_key("agent:1", "session/1", 3) == "pa:agent%3A1:session%2F1:archive:3"
    assert STATE.WindowStore.archive_index_key("agent:1", "session/1") == "pa:agent%3A1:session%2F1:archive_index"


@pytest.mark.asyncio
async def test_archive_round_trips_and_is_listed_in_the_rebuildable_index():
    store, backend = window_store()

    archived = await store.archive_window("agent:1", "session-1", archive_payload())

    assert archived.archive_ref == "archive:1"
    loaded = await store.load_archive("agent:1", "session-1", 1)
    assert loaded == archived
    assert loaded.messages == RECORD.encode_messages([UserMessage(content="the full previous window")])
    entries = await store.list_archives("agent:1", "session-1")
    assert [entry.archive_ref for entry in entries] == ["archive:1"]
    assert await backend.get(STATE.WindowStore.archive_index_key("agent:1", "session-1")) is not None


@pytest.mark.asyncio
async def test_identical_archive_retry_is_idempotent():
    store, backend = window_store()
    first = await store.archive_window("agent:1", "session-1", archive_payload())

    second = await store.archive_window("agent:1", "session-1", archive_payload())

    assert second == first
    assert len(await backend.get_by_prefix("pa:agent%3A1:session-1:archive:")) == 1


@pytest.mark.asyncio
async def test_conflicting_archive_does_not_overwrite_the_first_window_snapshot():
    store, _ = window_store()
    first = await store.archive_window("agent:1", "session-1", archive_payload())

    with pytest.raises(BaseError):
        await store.archive_window(
            "agent:1",
            "session-1",
            archive_payload(messages=RECORD.encode_messages([UserMessage(content="conflicting content")])),
        )

    assert await store.load_archive("agent:1", "session-1", 1) == first


@pytest.mark.asyncio
async def test_archive_rejects_an_invalid_handoff_seed_before_persisting():
    store, _ = window_store()

    with pytest.raises(BaseError):
        await store.archive_window(
            "agent:1",
            "session-1",
            archive_payload(seed={"objective": "   "}),
        )


@pytest.mark.asyncio
async def test_corrupt_archive_seed_is_reported_as_an_invalid_record():
    store, backend = window_store()
    await store.archive_window("agent:1", "session-1", archive_payload())
    key = STATE.WindowStore.archive_key("agent:1", "session-1", 1)
    raw = json.loads(await backend.get(key))
    raw["seed"] = {"objective": "   "}
    await backend.set(key, json.dumps(raw))

    with pytest.raises(BaseError):
        await store.load_archive("agent:1", "session-1", 1)


@pytest.mark.asyncio
async def test_archive_index_is_rebuilt_from_the_session_archives():
    store, backend = window_store()
    await store.archive_window("agent:1", "session-1", archive_payload())
    index_key = STATE.WindowStore.archive_index_key("agent:1", "session-1")
    await backend.delete(index_key)

    entries = await store.list_archives("agent:1", "session-1")

    assert [entry.archive_ref for entry in entries] == ["archive:1"]
    rebuilt = json.loads(await backend.get(index_key))
    assert rebuilt["entries"][0]["execution_id"] == "execution-1"


@pytest.mark.asyncio
async def test_failed_state_commit_can_find_only_the_exact_matching_rollover_candidate():
    store, _ = window_store()
    archived = await store.archive_window("agent:1", "session-1", archive_payload())
    checkpoint = SessionCheckpoint(
        request_id="request-1",
        execution_id="execution-1",
        iteration_seq=4,
        kind="react_iteration",
    )

    pending = await store.find_pending_rollover(
        "agent:1",
        "session-1",
        current_window_id="window-1",
        window_seq=1,
        checkpoint=checkpoint,
        request_id="request-1",
        recovery_ref="execution-1",
        spec_fingerprint="a" * 64,
    )

    assert pending == archived


def test_recovery_rebuilds_the_candidate_from_seed_and_retained_complete_group_refs():
    old_messages = [
        UserMessage(content="header", metadata={WINDOW_PART_KEY: "execution_header"}),
        UserMessage(content="original request"),
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="read-1", type="function", name="read_file", arguments="{}")],
        ),
        ToolMessage(content="verified source", tool_call_id="read-1"),
    ]
    seed = WINDOW.WindowSeed(objective="finish task", next_steps=["verify source"])

    recovered = WINDOW_MANAGER.restore_rollover_candidate(
        old_messages,
        seed,
        ["messages:2-3"],
    )

    seed_message = UserMessage(
        content="【窗口交接要点；事实与副作用状态仍需核实】\n"
        + json.dumps(seed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        metadata={WINDOW_PART_KEY: TEMPORARY_BACKGROUND},
    )
    assert recovered == [old_messages[0], seed_message, old_messages[1], *old_messages[2:]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"current_window_id": "other-window"},
        {"request_id": "other-request"},
        {"recovery_ref": "other-execution"},
        {"spec_fingerprint": "b" * 64},
        {
            "checkpoint": SessionCheckpoint(
                request_id="request-1",
                execution_id="execution-1",
                iteration_seq=3,
                kind="react_iteration",
            )
        },
    ],
    ids=["window-id", "request", "execution", "spec", "iteration"],
)
async def test_pending_rollover_with_mismatched_identity_fails_closed(changes):
    store, _ = window_store()
    await store.archive_window("agent:1", "session-1", archive_payload())
    values = {
        "current_window_id": "window-1",
        "window_seq": 1,
        "checkpoint": SessionCheckpoint(
            request_id="request-1",
            execution_id="execution-1",
            iteration_seq=4,
            kind="react_iteration",
        ),
        "request_id": "request-1",
        "recovery_ref": "execution-1",
        "spec_fingerprint": "a" * 64,
    }
    values.update(changes)

    with pytest.raises(BaseError):
        await store.find_pending_rollover("agent:1", "session-1", **values)


@pytest.mark.asyncio
async def test_load_repairs_a_matching_archive_before_exposing_the_active_window():
    backend = InMemoryKVStore()
    state_store = STATE.ExecutionStateStore(STATE.WindowStore(backend))
    old_messages = [
        UserMessage(content="header", metadata={WINDOW_PART_KEY: "execution_header"}),
        UserMessage(content="original request"),
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="read-1", type="function", name="read_file", arguments="{}")],
        ),
        ToolMessage(content="old window result", tool_call_id="read-1"),
    ]
    seed = WINDOW.WindowSeed(objective="finish the task", next_steps=["verify"])
    new_messages = [
        old_messages[0],
        UserMessage(
            content="【窗口交接要点；事实与副作用状态仍需核实】\n"
            + json.dumps(seed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            metadata={WINDOW_PART_KEY: TEMPORARY_BACKGROUND},
        ),
        old_messages[1],
    ]
    current = RECORD.SessionStateRecord(
        agent_id="agent:1",
        session_id="session-1",
        messages=RECORD.encode_messages(old_messages),
        interruption=None,
        auto_confirm={},
        deepagent_state=None,
        checkpoint=RECORD.RecordCheckpoint(
            request_id="request-1", execution_id="execution-1", iteration_seq=4, kind="react_iteration"
        ),
        last_group_message_id=None,
        current_window_id="window-1",
        window_seq=1,
        last_rollover=None,
        ending=None,
    )
    await backend.set(STATE.WindowStore.state_key("agent:1", "session-1"), RECORD.dump_record(current))
    archived = await state_store._store.archive_window(
        "agent:1",
        "session-1",
        archive_payload(
            messages=RECORD.encode_messages(old_messages),
            seed=seed.model_dump(mode="json"),
            retained_item_refs=[],
            candidate_fingerprint=message_fingerprint(new_messages),
            iteration_seq=4,
            last_group_message_id="group-message-4",
        ),
    )
    assert archived.messages == current.messages
    stored_record = RECORD.parse_record(await backend.get(STATE.WindowStore.state_key("agent:1", "session-1")))
    assert archived.messages == RECORD.encode_messages(RECORD.decode_messages(stored_record.messages))
    pending = await state_store._store.find_pending_rollover(
        "agent:1",
        "session-1",
        current_window_id="window-1",
        window_seq=1,
        checkpoint=SessionCheckpoint(
            request_id="request-1", execution_id="execution-1", iteration_seq=4, kind="react_iteration"
        ),
        request_id="request-1",
        recovery_ref="execution-1",
        spec_fingerprint="a" * 64,
    )
    assert pending.messages == RECORD.encode_messages(RECORD.decode_messages(stored_record.messages))
    session = import_module("openjiuwen.core.single_agent").create_agent_session(session_id="session-1")
    context = SimpleNamespace(
        execution=SimpleNamespace(
            agent_id="agent:1",
            session_id="session-1",
            request_id="request-1",
            execution_id="execution-2",
            recovery_ref="execution-1",
            config_fingerprint="a" * 64,
        ),
        session=session,
    )

    loaded = await state_store.load(context)

    stored = RECORD.parse_record(await backend.get(STATE.WindowStore.state_key("agent:1", "session-1")))
    assert loaded.current_window_id == "window-2" and loaded.window_seq == 2
    assert loaded.last_group_message_id == "group-message-4"
    assert loaded.checkpoint.kind == "rollover" and loaded.checkpoint.execution_id == "execution-1"
    assert RECORD.decode_messages(stored.messages) == new_messages
    assert stored.last_rollover.archive_ref == "archive:1"
    assert (session.get_state("context") or {})["default_context_id"]["messages"] == new_messages


@pytest.mark.asyncio
async def test_load_rejects_a_rebuilt_candidate_that_does_not_match_its_fingerprint():
    backend = InMemoryKVStore()
    state_store = STATE.ExecutionStateStore(STATE.WindowStore(backend))
    old_messages = [
        UserMessage(content="header", metadata={WINDOW_PART_KEY: "execution_header"}),
        UserMessage(content="original request"),
    ]
    seed = WINDOW.WindowSeed(objective="finish the task")
    record = RECORD.SessionStateRecord(
        agent_id="agent:1",
        session_id="session-1",
        messages=RECORD.encode_messages(old_messages),
        interruption=None,
        auto_confirm={},
        deepagent_state=None,
        checkpoint=RECORD.RecordCheckpoint(
            request_id="request-1", execution_id="execution-1", iteration_seq=4, kind="react_iteration"
        ),
        last_group_message_id=None,
        current_window_id="window-1",
        window_seq=1,
        last_rollover=None,
        ending=None,
    )
    state_key = STATE.WindowStore.state_key("agent:1", "session-1")
    original_state = RECORD.dump_record(record)
    await backend.set(state_key, original_state)
    await state_store._store.archive_window(
        "agent:1",
        "session-1",
        archive_payload(
            messages=RECORD.encode_messages(old_messages),
            seed=seed.model_dump(mode="json"),
            retained_item_refs=[],
            candidate_fingerprint="f" * 64,
        ),
    )
    session = import_module("openjiuwen.core.single_agent").create_agent_session(session_id="session-1")
    context = SimpleNamespace(
        execution=SimpleNamespace(
            agent_id="agent:1",
            session_id="session-1",
            request_id="request-1",
            execution_id="execution-2",
            recovery_ref="execution-1",
            config_fingerprint="a" * 64,
        ),
        session=session,
    )

    with pytest.raises(BaseError):
        await state_store.load(context)

    assert await backend.get(state_key) == original_state
    assert session.get_state("context") is None


@pytest.mark.asyncio
async def test_archive_for_an_older_window_is_not_treated_as_a_pending_transition():
    store, _ = window_store()
    await store.archive_window("agent:1", "session-1", archive_payload())

    pending = await store.find_pending_rollover(
        "agent:1",
        "session-1",
        current_window_id="window-2",
        window_seq=2,
        checkpoint=None,
        request_id="request-2",
        recovery_ref=None,
        spec_fingerprint="a" * 64,
    )

    assert pending is None


def test_window_seed_is_bounded_and_rejects_unknown_fields():
    seed = WINDOW.WindowSeed(
        objective="完成当前任务",
        completed=["已核对输入"],
        next_steps=["检查结果"],
        constraints_and_decisions=["不丢弃未确认副作用"],
        open_questions=["是否需要确认？"],
        pending_effects=["tool_call:call-1"],
        evidence_refs=["tool_result:call-1"],
    )

    assert seed.objective == "完成当前任务"
    with pytest.raises(Exception):
        WINDOW.WindowSeed(objective="目标", invented_field="not allowed")
    with pytest.raises(Exception):
        WINDOW.WindowSeed(objective="x" * (WINDOW.SEED_OBJECTIVE_MAX_CHARS + 1))
    with pytest.raises(Exception):
        WINDOW.WindowSeed(objective="目标", completed=["x" * (WINDOW.SEED_ITEM_MAX_CHARS + 1)])


@pytest.mark.asyncio
async def test_new_context_only_queues_a_validated_request_for_the_safe_boundary():
    tool_module = import_module(f"{PKG}.tools.new_context")
    control = ExecutionControl()
    tool = tool_module.PersistentNewContextTool(control)

    output = await tool.invoke(
        {
            "objective": "继续实现 PA-11",
            "completed": ["已写入归档"],
            "next_steps": ["提交新窗口状态"],
        }
    )

    assert output.success is True
    assert control.pending_window_switch.trigger_kind == "agent"
    assert control.pending_window_switch.seed.objective == "继续实现 PA-11"
    assert tool.card.name == "new_context"
    assert tool.card.parallel_safe is False
    assert control.rollover_committed is False


@pytest.mark.asyncio
async def test_new_context_rejects_a_second_request_in_the_same_tool_batch():
    tool_module = import_module(f"{PKG}.tools.new_context")
    control = ExecutionControl()
    tool = tool_module.PersistentNewContextTool(control)
    seed = {"objective": "继续", "next_steps": ["验证"]}

    first = await tool.invoke(seed)
    second = await tool.invoke(seed)

    assert first.success is True
    assert second.success is False
    assert control.pending_window_switch.trigger_kind == "agent"


@pytest.mark.asyncio
async def test_rollover_manager_marks_only_a_complete_react_iteration_as_safe():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    request = SimpleNamespace(context_window=1000, max_tokens=100, model_name="test")
    model = SimpleNamespace(model_request_config=request)
    spec = SimpleNamespace(model=model)
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(execution_id="execution-1"),
    )
    manager = manager_module.WindowSwitchManager(
        context=context,
        request_text="task",
        spec=spec,
        state_store=None,
        emitter=None,
        last_group_message_id=None,
    )
    callback_context = SimpleNamespace(extra={"_react_iteration": 3}, inputs=SimpleNamespace())

    await manager.after_react_iteration(callback_context)
    assert control.safe_boundary_pending is True
    assert control.pending_iteration_seq == 3
    assert control.rollover_committed is False


@pytest.mark.asyncio
async def test_complete_iteration_is_checkpointed_before_the_next_model_request():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    request = SimpleNamespace(context_window=1000, max_tokens=100, model_name="test")
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=request))
    execution = SimpleNamespace(request_id="request-1", execution_id="execution-1")
    context = SimpleNamespace(binding=SimpleNamespace(control=control), execution=execution)
    store = SimpleNamespace(checkpoint=AsyncMock())
    manager = manager_module.WindowSwitchManager(
        context, "task", spec, store, None, last_group_message_id="group-message-1"
    )
    callback_context = SimpleNamespace(
        context=SimpleNamespace(
            token_counter=lambda: ExactCounter(),
            context_window_tokens=lambda: 1000,
        ),
        inputs=SimpleNamespace(
            messages=[SystemMessage(content="system"), UserMessage(content="next request")],
            tools=[],
            react_iteration=2,
        ),
        extra={},
    )
    await manager.after_react_iteration(SimpleNamespace(extra={"_react_iteration": 1}, inputs=None))

    result = await manager.before_model_request(callback_context)

    assert result is None
    store.checkpoint.assert_awaited_once()
    checkpoint = store.checkpoint.await_args.args[1]
    assert checkpoint.iteration_seq == 1
    assert checkpoint.kind == "react_iteration"
    assert checkpoint.last_group_message_id == "group-message-1"
    assert control.safe_boundary_pending is False


@pytest.mark.asyncio
async def test_fallback_threshold_uses_the_context_engine_effective_window():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    request = SimpleNamespace(context_window=None, max_tokens=100, model_name="test")
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=request))
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(request_id="request-1", execution_id="execution-1"),
    )
    store = SimpleNamespace(checkpoint=AsyncMock())
    manager = manager_module.WindowSwitchManager(context, "task", spec, store, None, last_group_message_id=None)
    resolved_window = Mock(return_value=2000)
    callback_context = SimpleNamespace(
        context=SimpleNamespace(token_counter=lambda: ExactCounter(), context_window_tokens=resolved_window),
        inputs=SimpleNamespace(
            messages=[SystemMessage(content="system"), UserMessage(content="x" * 994)],
            tools=[],
            react_iteration=2,
        ),
        extra={},
    )
    await manager.after_react_iteration(SimpleNamespace(extra={"_react_iteration": 1}, inputs=None))

    result = await manager.before_model_request(callback_context)

    assert result is None
    store.checkpoint.assert_awaited_once()
    resolved_window.assert_called_once_with()
    assert control.pending_window_switch is None


@pytest.mark.asyncio
async def test_rollover_threshold_refreshes_from_the_current_context_engine_window():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=SimpleNamespace(context_window=4000)))
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(request_id="request-1", execution_id="execution-1"),
    )
    manager = manager_module.WindowSwitchManager(context, "task", spec, None, None, last_group_message_id=None)
    resolved_window = Mock(side_effect=[2000, 1000])
    callback_context = SimpleNamespace(
        context=SimpleNamespace(context_window_tokens=resolved_window),
        inputs=SimpleNamespace(messages=[], tools=[]),
        extra={},
    )

    await manager.before_model_request(callback_context)
    await manager.before_model_request(callback_context)

    assert resolved_window.call_count == 2
    assert manager._context_window == 1000
    assert manager._rollover_threshold == 800


@pytest.mark.asyncio
async def test_missing_effective_context_window_pauses_before_model_request():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=SimpleNamespace(context_window=1000)))
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(request_id="request-1", execution_id="execution-1"),
    )
    manager = manager_module.WindowSwitchManager(context, "task", spec, None, None, last_group_message_id=None)
    callback_context = SimpleNamespace(
        context=SimpleNamespace(context_window_tokens=lambda: None),
        inputs=SimpleNamespace(messages=[], tools=[]),
        extra={},
    )

    result = await manager.before_model_request(callback_context)

    assert result["reason"] == "error"


@pytest.mark.asyncio
async def test_unavailable_window_after_candidate_rebuild_restores_the_old_context():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=SimpleNamespace(context_window=None)))
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(request_id="request-1", execution_id="execution-1"),
    )
    manager = manager_module.WindowSwitchManager(context, "task", spec, None, None, last_group_message_id=None)
    old_messages = [UserMessage(content="old window")]
    set_messages = Mock()
    callback_context = SimpleNamespace(
        context=SimpleNamespace(context_window_tokens=lambda: None, set_messages=set_messages),
        inputs=SimpleNamespace(messages=[UserMessage(content="candidate")], tools=[]),
        extra={},
    )
    manager._prepared = manager_module._PreparedRollover(
        old_messages=tuple(old_messages),
        new_messages=(UserMessage(content="candidate"),),
        seed=WINDOW.WindowSeed(objective="task"),
        trigger_kind="agent",
        retained_item_refs=(),
        iteration_seq=1,
    )
    control.queue_window_switch(
        WINDOW.WindowSwitchRequest(trigger_kind="agent", seed=WINDOW.WindowSeed(objective="task"))
    )

    result = await manager.before_model_request(callback_context)

    assert result["reason"] == "error"
    set_messages.assert_called_once_with(old_messages)
    assert manager._prepared is None
    assert control.pending_window_switch is None


@pytest.mark.asyncio
async def test_required_new_window_content_that_does_not_fit_pauses_without_writing():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    control.safe_boundary_pending = True
    control.pending_iteration_seq = 1
    control.queue_window_switch(
        WINDOW.WindowSwitchRequest(trigger_kind="agent", seed=WINDOW.WindowSeed(objective="必须保留" * 200))
    )
    request = SimpleNamespace(context_window=1000, max_tokens=100, model_name="test")
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=request))
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(execution_id="execution-1"),
    )
    store = SimpleNamespace(checkpoint=AsyncMock(), rollover=AsyncMock())
    manager = manager_module.WindowSwitchManager(context, "task", spec, store, None, last_group_message_id=None)
    old_messages = [
        UserMessage(content="【本次执行】", metadata={"persistent_window_part": "execution_header"}),
        UserMessage(content="task"),
    ]
    system_message = SystemMessage(content="system")
    callback_context = SimpleNamespace(
        context=SimpleNamespace(
            token_counter=lambda: ExactCounter(),
            context_window_tokens=lambda: 1000,
            get_messages=lambda: old_messages,
            set_messages=AsyncMock(),
        ),
        inputs=SimpleNamespace(messages=[system_message, *old_messages], tools=[], react_iteration=1),
        extra={"_final_request_context_window": SimpleNamespace(system_messages=[system_message], tools=[])},
        request_context_rebuild=AsyncMock(),
    )

    result = await manager.before_model_request(callback_context)

    assert result["reason"] == "context_unfit"
    store.checkpoint.assert_not_awaited()
    store.rollover.assert_not_awaited()
    callback_context.request_context_rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_fallback_uses_eighty_percent_context_window_threshold():
    manager_module = import_module(f"{PKG}.execution.window_management")
    control = ExecutionControl()
    control.safe_boundary_pending = True
    control.pending_iteration_seq = 1
    request = SimpleNamespace(context_window=1000, max_tokens=100, model_name="test")
    spec = SimpleNamespace(model=SimpleNamespace(model_request_config=request))
    execution = SimpleNamespace(request_id="request-1", execution_id="execution-1")
    context = SimpleNamespace(binding=SimpleNamespace(control=control), execution=execution)

    class RolloverStore:
        def __init__(self):
            self.transition = None

        async def rollover(self, _context, transition):
            self.transition = transition
            return SimpleNamespace(archive_ref="archive:1", new_window_seq=2)

    class Emitter:
        async def emit(self, *_args):
            return None

    store = RolloverStore()
    manager = manager_module.WindowSwitchManager(context, "task", spec, store, Emitter(), last_group_message_id=None)
    old_messages = [
        UserMessage(content="header", metadata={"persistent_window_part": "execution_header"}),
        UserMessage(content="task"),
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="read-1", type="function", name="read_file", arguments="{}")],
        ),
        ToolMessage(content="optional historical result " + "x" * 760, tool_call_id="read-1"),
    ]
    archived_messages = tuple(old_messages)
    model_context = SimpleNamespace(
        token_counter=lambda: ExactCounter(),
        context_window_tokens=lambda: 1000,
        get_messages=lambda: list(old_messages),
        set_messages=lambda messages: old_messages.__setitem__(slice(None), messages),
    )
    system_message = SystemMessage(content="system")
    callback_context = SimpleNamespace(
        context=model_context,
        inputs=SimpleNamespace(messages=[system_message, *old_messages], tools=[], react_iteration=2),
        extra={"_final_request_context_window": SimpleNamespace(system_messages=[system_message], tools=[])},
        request_context_rebuild=Mock(),
    )
    await manager.after_react_iteration(SimpleNamespace(extra={"_react_iteration": 1}, inputs=None))

    first = await manager.before_model_request(callback_context)

    assert first is None and store.transition is None
    callback_context.request_context_rebuild.assert_called_once()
    candidate_messages = list(model_context.get_messages())
    assert all("optional historical result" not in getattr(message, "content", "") for message in candidate_messages)
    callback_context.inputs.messages = [system_message, *candidate_messages]

    second = await manager.before_model_request(callback_context)

    assert second is None
    assert store.transition.trigger_kind == "capacity_fallback"
    assert store.transition.old_messages == archived_messages
    assert store.transition.new_messages == tuple(candidate_messages)


def test_entry_installs_the_window_manager_only_for_opted_in_persistent_agents():
    entries = import_module(f"{PKG}.execution.entries")
    control = ExecutionControl()
    context = SimpleNamespace(
        binding=SimpleNamespace(control=control),
        execution=SimpleNamespace(execution_id="execution-1"),
    )
    request = SimpleNamespace(context_window=1000, max_tokens=100, model_name="test")
    model = SimpleNamespace(model_request_config=request)
    base = dict(model=model)
    disabled = SimpleNamespace(**base, enabled_features=frozenset({"construction"}))
    enabled = SimpleNamespace(**base, enabled_features=frozenset({"construction", "window_management"}))

    entries._install_window_switch_manager(disabled, context, None, None, "task")
    assert control.window_switch_manager is None

    entries._install_window_switch_manager(enabled, context, None, None, "task")
    manager_type = import_module(f"{PKG}.execution.window_management").WindowSwitchManager
    assert isinstance(control.window_switch_manager, manager_type)


@pytest.mark.asyncio
async def test_rollover_persists_archive_before_new_active_state_and_records_one_checkpoint():
    class TracingStore(InMemoryKVStore):
        def __init__(self):
            super().__init__()
            self.writes = []

        async def exclusive_set(self, key, value):
            self.writes.append(("archive", key))
            return await super().exclusive_set(key, value)

        async def set(self, key, value):
            self.writes.append(("set", key))
            return await super().set(key, value)

    backend = TracingStore()
    state_store = STATE.ExecutionStateStore(STATE.WindowStore(backend))
    agent = SimpleNamespace(save_state=lambda _session: None)
    session = import_module("openjiuwen.core.single_agent").create_agent_session(session_id="session-1")
    old_messages = (UserMessage(content="old full window"),)
    new_messages = (UserMessage(content="new active window"),)
    session.update_state({"context": {"default_context_id": {"messages": list(new_messages)}}})
    context = SimpleNamespace(
        execution=SimpleNamespace(
            agent_id="agent-1",
            session_id="session-1",
            request_id="request-1",
            execution_id="execution-1",
            config_fingerprint="a" * 64,
        ),
        session=session,
        binding=SimpleNamespace(agent_ref=lambda: agent),
    )

    committed = await state_store.rollover(
        context,
        WindowRollover(
            trigger_kind="agent",
            seed=WINDOW.WindowSeed(objective="继续任务"),
            old_messages=old_messages,
            new_messages=new_messages,
            retained_item_refs=("message:0",),
            token_usage={"input_tokens": 120},
            iteration_seq=3,
            last_group_message_id="group-message-9",
        ),
    )

    state_key = STATE.WindowStore.state_key("agent-1", "session-1")
    archive_key = STATE.WindowStore.archive_key("agent-1", "session-1", 1)
    assert backend.writes[0] == ("archive", archive_key)
    assert backend.writes[-1] == ("set", state_key)
    state = json.loads(await backend.get(state_key))
    assert state["checkpoint"]["kind"] == "rollover"
    assert state["checkpoint"]["iteration_seq"] == 3
    assert state["current_window_id"] == committed.new_window_id
    assert state["window_seq"] == committed.new_window_seq == 2
    assert state["last_group_message_id"] == "group-message-9"
    archive = await STATE.WindowStore(backend).load_archive("agent-1", "session-1", 1)
    assert archive.messages == RECORD.encode_messages(list(old_messages))


def test_window_switch_request_does_not_accept_session_or_storage_identity_from_agent():
    request = WINDOW.WindowSwitchRequest.model_validate({"trigger_kind": "agent", "seed": {"objective": "继续任务"}})
    assert request.trigger_kind == "agent"
    with pytest.raises(Exception):
        WINDOW.WindowSwitchRequest.model_validate(
            {
                "trigger_kind": "agent",
                "seed": {"objective": "继续任务"},
                "session_id": "attacker-selected-session",
            }
        )
