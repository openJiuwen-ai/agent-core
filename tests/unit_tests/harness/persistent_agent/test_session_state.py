# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-10 session state record: encoding, keys, window tidying and record validation.

Entry-level behaviour with a real DeepAgent lives in test_entries.py; these tests pin the record
format and the error categories without running a model.
"""

import json
from importlib import import_module
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, ExecutionError
from openjiuwen.core.common.exception.errors import ValidationError as FrameworkValidationError
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    INTERRUPTION_KEY,
    ToolInterruptEntry,
    ToolInterruptionState,
)
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRequest

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
STATE = import_module(f"{PKG}.execution.session_state")
RECORD = import_module(f"{PKG}.schema.session_record")
INTEGRATION = import_module(f"{PKG}.schema.integration")
PART = INTEGRATION.WINDOW_PART_KEY
SECRET = "SECRET-BODY-TEXT"


def call(call_id, name="remember"):
    return ToolCall(id=call_id, type="function", name=name, arguments='{"value": "x"}')


def tool_result(call_id, content="done"):
    return ToolMessage(content=content, tool_call_id=call_id)


def ending(outcome="completed", **overrides):
    fields = dict(
        outcome=outcome,
        request_id="request-1",
        reply_target="chat-1",
        output_refs=(),
        last_group_message_id="m-9",
    )
    fields.update(overrides)
    return INTEGRATION.ExecutionEnding(**fields)


def interruption(*requests, call_id="call-1"):
    requests = requests or (InterruptRequest(message="approve?", auto_confirm_key="remember"),)
    return ToolInterruptionState(
        ai_message=AssistantMessage(content="checking", tool_calls=[call(call_id)]),
        iteration=1,
        original_query="please summarize",
        interrupted_tools={
            call_id: ToolInterruptEntry(
                tool_call=call(call_id),
                interrupt_requests={f"{call_id}-{index}": request for index, request in enumerate(requests)},
            )
        },
        auto_confirm_mapping={f"{call_id}-0": "remember"},
    )


def tampered(entry_call=None, ai_call=None):
    """A waiting scene whose interrupt entry or ai_message disagrees with the window's call-1."""
    state = interruption(call_id="call-1")
    if entry_call is not None:
        state.interrupted_tools["call-1"].tool_call = entry_call
    if ai_call is not None:
        state.ai_message.tool_calls = [ai_call]
    return state


def context(agent_id="agent:1", session_id="session%a", execution_id="execution-1"):
    session = create_agent_session(session_id=session_id, card=AgentCard(id=agent_id, name="test"))
    execution = SimpleNamespace(agent_id=agent_id, session_id=session_id, execution_id=execution_id)
    return SimpleNamespace(execution=execution, session=session, binding=SimpleNamespace(agent_ref=None))


def window(session):
    return session.get_state("context")["default_context_id"]["messages"]


def store(backend=None):
    backend = backend or InMemoryKVStore()
    return STATE.ExecutionStateStore(STATE.WindowStore(backend)), backend


async def skip_sync(_context):
    return None


def record(**overrides):
    data = {
        "schema_version": 1,
        "agent_id": "agent:1",
        "session_id": "session%a",
        "messages": [UserMessage(content=SECRET).model_dump(mode="json")],
        "interruption": None,
        "auto_confirm": {},
        "deepagent_state": None,
        "ending": {"outcome": "completed", "request_id": "request-0", "execution_id": "execution-0"},
    }
    data.update(overrides)
    return data


def assert_invalid(exc_info):
    error = exc_info.value
    assert isinstance(error, FrameworkValidationError)
    assert error.status == StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR
    assert SECRET not in str(error), "diagnostics never carry record text"


def assert_runtime(exc_info):
    error = exc_info.value
    assert isinstance(error, ExecutionError)
    assert error.status == StatusCode.DEEPAGENT_RUNTIME_ERROR


def test_key_prefix_encodes_each_identity_on_its_own():
    """PA-10-V-19."""
    assert STATE.session_key_prefix("agent", "session") == "pa:agent:session:"
    assert STATE.session_key_prefix("a:b", "c") != STATE.session_key_prefix("a", "b:c")
    assert STATE.session_key_prefix("a%3Ab", "c") != STATE.session_key_prefix("a:b", "c")
    assert STATE.session_key_prefix("代理", "会话 1") == "pa:%E4%BB%A3%E7%90%86:%E4%BC%9A%E8%AF%9D%201:"
    assert STATE.WindowStore.state_key("a:b", "c") == "pa:a%3Ab:c:state"
    assert import_module(PKG).session_key_prefix is STATE.session_key_prefix, "the host imports it from the package"


class FailingBackend(InMemoryKVStore):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def get(self, key):
        raise self.error

    async def set(self, key, value):
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ValueError("backend down"), RECORD.invalid_record("a backend may raise a validation error too")],
)
async def test_backend_faults_are_runtime_errors_whatever_they_raise(error):
    """PA-10-V-09: a backend fault is never taken for an invalid record."""
    window_store = STATE.WindowStore(FailingBackend(error))
    with pytest.raises(BaseError) as exc_info:
        await window_store.load_state("agent", "session")
    assert_runtime(exc_info)
    assert exc_info.value.__cause__ is error
    with pytest.raises(BaseError) as exc_info:
        await window_store.save_state("agent", "session", "{}")
    assert_runtime(exc_info)


def test_messages_round_trip_exactly():
    """PA-10-V-11."""
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="task", metadata={PART: "execution_header"}),
        AssistantMessage(
            content="checking",
            tool_calls=[call("call-1")],
            reasoning_content="why",
            usage_metadata=UsageMetadata(input_tokens=3, output_tokens=4),
        ),
        tool_result("call-1", "remembered"),
        UserMessage(content=[{"type": "text", "text": "image result"}]),
    ]
    restored = RECORD.decode_messages(json.loads(json.dumps(RECORD.encode_messages(messages))))
    assert restored == messages
    assert [type(message) for message in restored] == [type(message) for message in messages]


def test_a_tool_call_round_trip_keeps_what_runs_but_not_the_stream_index():
    """PA-10-V-11: the message format keeps id, type, name, arguments and response_item_id, not index."""
    sent = ToolCall(id="call-1", type="function", name="remember", arguments="{}", index=0, response_item_id="item-1")
    message = AssistantMessage(content="checking", tool_calls=[sent], metadata={PART: "x"})
    restored = RECORD.decode_messages(json.loads(json.dumps(RECORD.encode_messages([message]))))[0]
    kept = restored.tool_calls[0]
    assert (kept.id, kept.type, kept.name, kept.arguments, kept.response_item_id) == (
        "call-1",
        "function",
        "remember",
        "{}",
        "item-1",
    )
    assert kept.index is None and restored.metadata == {PART: "x"}


def test_message_subclasses_are_not_saved():
    class NotedUserMessage(UserMessage):
        note: str = "dropped by UserMessage"

    with pytest.raises(BaseError) as exc_info:
        RECORD.encode_messages([NotedUserMessage(content="x")])
    assert_runtime(exc_info)


def test_interruption_round_trip_keeps_request_types_and_fields():
    """PA-10-V-17: an answer request keeps its questions, so a reply can be told from a permission."""
    ask = AskUserRequest(message="choose", questions=[{"question": "which file?", "options": ["a", "b"]}])
    permission = InterruptRequest(message="approve?", auto_confirm_key="remember", metadata={"risk": "write"})
    state = interruption(permission, ask)
    restored = RECORD.decode_interruption(json.loads(json.dumps(RECORD.encode_interruption(state))))
    requests = list(restored.interrupted_tools["call-1"].interrupt_requests.values())
    assert [type(request) for request in requests] == [InterruptRequest, AskUserRequest]
    assert requests == [permission, ask]
    assert requests[1].questions == ask.questions
    assert restored.ai_message == state.ai_message
    assert restored.auto_confirm_mapping == state.auto_confirm_mapping
    assert (restored.iteration, restored.original_query) == (1, "please summarize")


def test_unlisted_interrupt_request_types_are_not_saved():
    class CustomRequest(InterruptRequest):
        extra: str = "would be lost"

    with pytest.raises(BaseError) as exc_info:
        RECORD.encode_interruption(interruption(CustomRequest(message="x")))
    assert_runtime(exc_info)


def test_unknown_request_tag_is_an_invalid_record():
    encoded = RECORD.encode_interruption(interruption())
    next(iter(encoded["interrupted_tools"]["call-1"]["interrupt_requests"].values()))["type"] = "module.Custom"
    with pytest.raises(BaseError) as exc_info:
        RECORD.decode_interruption(encoded)
    assert_invalid(exc_info)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff not json",
        "[1, 2]",
        json.dumps(record(schema_version=2)),
        json.dumps(record(unexpected="field")),
        json.dumps(record(auto_confirm={"remember": "yes"})),
        json.dumps({key: value for key, value in record().items() if key != "ending"}),
    ],
    ids=["not-json", "not-object", "unsupported-version", "extra-field", "loose-type", "missing-field"],
)
def test_malformed_records_are_invalid(raw):
    """PA-10-V-10."""
    with pytest.raises(BaseError) as exc_info:
        RECORD.parse_record(raw)
    assert_invalid(exc_info)


def test_stopped_call_placeholders_follow_their_group_and_are_idempotent():
    """PA-10-V-08: real and native interruption results stay; only missing ones get the placeholder."""
    native_interrupted = tool_result("call-b", "[Interrupted] Tool 'remember' execution was cancelled by user.")
    messages = [
        UserMessage(content="task"),
        AssistantMessage(content="", tool_calls=[call("call-a"), call("call-b"), call("call-c")]),
        tool_result("call-a"),
        native_interrupted,
        UserMessage(content="[Request cancelled by user]"),
    ]
    patched = STATE.patch_stopped_calls(messages)
    assert [getattr(message, "tool_call_id", None) for message in patched] == [
        None,
        None,
        "call-a",
        "call-b",
        "call-c",
        None,
    ]
    placeholder = patched[4]
    assert placeholder.content == "结果未取得，实际效果需核实"
    assert placeholder.metadata == {PART: "stopped_tool_placeholder"}
    assert STATE.patch_stopped_calls(patched) == patched, "patching again changes nothing"
    assert STATE.unpaired_tool_calls(patched) == []


def test_results_pair_only_with_their_own_group():
    """A call id reused by a later group is not answered by the earlier result."""
    reused = [
        UserMessage(content="first task"),
        AssistantMessage(content="", tool_calls=[call("call-1")]),
        tool_result("call-1"),
        UserMessage(content="second task"),
        AssistantMessage(content="", tool_calls=[call("call-1")]),
    ]
    assert STATE.unpaired_tool_calls(reused) == ["call-1"]
    patched = STATE.patch_stopped_calls(reused)
    assert patched[-1].tool_call_id == "call-1" and patched[-1].metadata == {PART: "stopped_tool_placeholder"}
    assert STATE.unpaired_tool_calls(patched) == []


def test_tidy_window_drops_background_and_marks_the_end():
    """PA-10-V-03 at the window level: history and the one-line header stay as they are."""
    header = UserMessage(content="【本次执行】类型：新任务", metadata={PART: "execution_header"})
    messages = [
        UserMessage(content="old"),
        header,
        UserMessage(content="background", metadata={PART: "temporary_background"}),
        UserMessage(content="task"),
        AssistantMessage(content="answer"),
    ]
    done = ending(output_refs=("out_1",), answer_text="answer", answer_segments=1)
    tidied = STATE.tidy_window(messages, done, "execution-1")
    assert tidied[:-1] == [messages[0], header, messages[3], messages[4]], "an unchanged answer is not repeated"
    marker = tidied[-1]
    assert marker.metadata == {PART: "execution_end"}
    assert marker.content == (
        "【执行结束】request_id：request-1；execution_id：execution-1；结局：已完成；"
        "交出候选：最终回答 1 段（以结束记录为准）；群消息已扫描到：m-9"
    )
    paused = STATE.tidy_window(messages, ending("budget_denied", last_group_message_id=None), "execution-1")
    assert paused[-1].content.endswith("结局：暂停（budget_denied）；交出候选：无；群消息已扫描到：无")
    waiting = STATE.tidy_window(messages, ending("awaiting_confirmation"), "execution-1")
    assert waiting == [messages[0], header, messages[3], messages[4]], "a waiting scene gets no marker"


@pytest.mark.asyncio
async def test_load_restores_state_and_the_waiting_scene():
    state = interruption()
    window_messages = [UserMessage(content="task"), AssistantMessage(content="checking", tool_calls=[call("call-1")])]
    data = record(
        messages=RECORD.encode_messages(window_messages),
        interruption=RECORD.encode_interruption(state),
        auto_confirm={"remember": True},
        deepagent_state={"iteration": 2, "plan_mode": {"mode": "plan"}},
        ending={
            "outcome": "awaiting_confirmation",
            "request_id": "request-0",
            "execution_id": "execution-0",
            "reply_target": "chat-0",
            "last_group_message_id": "m-3",
        },
    )
    state_store, backend = store()
    await backend.set(STATE.WindowStore.state_key("agent:1", "session%a"), json.dumps(data))
    ctx = context()
    loaded = await state_store.load(ctx)
    assert loaded.waiting == INTEGRATION.WaitingScene("request-0", "chat-0")
    assert loaded.last_group_message_id == "m-3"
    assert window(ctx.session) == window_messages, "a waiting scene's unpaired call is legitimate"
    assert ctx.session.get_state(INTERRUPTION_KEY) == state
    assert ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) == {"remember": True}
    assert ctx.session.get_state("deepagent") == {"iteration": 2, "plan_mode": {"mode": "plan"}}


@pytest.mark.asyncio
async def test_a_waiting_call_is_compared_without_its_stream_index():
    """The message format drops ToolCall.index, so an entry that kept it still matches the window."""
    state_store, backend = store()
    indexed = ToolCall(id="call-1", type="function", name="remember", arguments='{"value": "x"}', index=0)
    data = record(
        messages=RECORD.encode_messages([AssistantMessage(content="checking", tool_calls=[call("call-1")])]),
        interruption=RECORD.encode_interruption(tampered(entry_call=indexed)),
        ending={"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
    )
    await backend.set(STATE.WindowStore.state_key("agent:1", "session%a"), json.dumps(data))
    loaded = await state_store.load(context())
    assert loaded.waiting is not None


@pytest.mark.asyncio
async def test_missing_record_starts_an_empty_window():
    state_store, _ = store()
    ctx = context()
    assert await state_store.load(ctx) == INTEGRATION.LoadedRecord()
    assert ctx.session.get_state("context") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "cancelled", "error"])
async def test_loading_an_interrupted_record_patches_in_memory_only(outcome):
    """PA-10-V-08: records of stopped, cancelled or failed Executions are repaired on load, never written."""
    messages = [UserMessage(content="task"), AssistantMessage(content="", tool_calls=[call("call-1")])]
    data = record(
        messages=RECORD.encode_messages(messages),
        ending={"outcome": outcome, "request_id": "request-0", "execution_id": "execution-0"},
    )
    state_store, backend = store()
    key = STATE.WindowStore.state_key("agent:1", "session%a")
    raw = json.dumps(data)
    await backend.set(key, raw)
    ctx = context()
    await state_store.load(ctx)
    loaded = window(ctx.session)
    assert STATE.unpaired_tool_calls(loaded) == []
    assert loaded[-1].metadata == {PART: "stopped_tool_placeholder"}
    assert await backend.get(key) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"agent_id": "agent:2"},
        {"session_id": "SESSION%a"},
        {"messages": [{"role": "robot", "content": SECRET}]},
        {"messages": [{"role": "assistant", "content": SECRET, "tool_calls": "not a list"}]},
        {
            "messages": RECORD.encode_messages([AssistantMessage(content=SECRET, tool_calls=[call("call-x")])]),
            "ending": {"outcome": "completed", "request_id": "r", "execution_id": "e"},
        },
        {
            "messages": RECORD.encode_messages([AssistantMessage(content=SECRET, tool_calls=[call("call-x")])]),
            "interruption": RECORD.encode_interruption(interruption(call_id="call-1")),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
        },
        {"interruption": RECORD.encode_interruption(interruption())},
        {"deepagent_state": {"task_plan": {"tasks": "not a list"}}},
        {"messages": [{"role": ["user"], "content": SECRET}]},
        {"messages": [{"role": {"name": "user"}, "content": SECRET}]},
        {
            "messages": RECORD.encode_messages(
                [
                    AssistantMessage(content=SECRET, tool_calls=[call("call-x")]),
                    UserMessage(content="next"),
                    tool_result("call-x"),
                ]
            ),
            "ending": {"outcome": "stopped", "request_id": "r", "execution_id": "e"},
        },
        {
            "messages": RECORD.encode_messages(
                [AssistantMessage(content=SECRET, tool_calls=[call("call-1")]), tool_result("call-1")]
            ),
            "interruption": RECORD.encode_interruption(interruption(call_id="call-1")),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
        },
        {
            "messages": RECORD.encode_messages(
                [
                    AssistantMessage(content=SECRET, tool_calls=[call("call-0")]),
                    UserMessage(content="next"),
                    AssistantMessage(content="checking", tool_calls=[call("call-1")]),
                ]
            ),
            "interruption": RECORD.encode_interruption(interruption(call_id="call-1")),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
        },
        {
            "messages": RECORD.encode_messages(
                [
                    AssistantMessage(content=SECRET, tool_calls=[call("call-1"), call("call-2")]),
                    tool_result("call-2"),
                ]
            ),
            "interruption": RECORD.encode_interruption(interruption(call_id="call-1")),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
        },
        {
            "messages": RECORD.encode_messages([AssistantMessage(content=SECRET, tool_calls=[call("call-1")])]),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
            "interruption": RECORD.encode_interruption(tampered(entry_call=call("call-1", name="delete_file"))),
        },
        {
            "messages": RECORD.encode_messages([AssistantMessage(content=SECRET, tool_calls=[call("call-1")])]),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
            "interruption": RECORD.encode_interruption(
                tampered(entry_call=ToolCall(id="call-1", type="function", name="remember", arguments='{"value": "y"}'))
            ),
        },
        {
            "messages": RECORD.encode_messages([AssistantMessage(content=SECRET, tool_calls=[call("call-1")])]),
            "ending": {"outcome": "awaiting_confirmation", "request_id": "r", "execution_id": "e"},
            "interruption": RECORD.encode_interruption(tampered(ai_call=call("call-1", name="delete_file"))),
        },
    ],
    ids=[
        "agent-mismatch",
        "session-case-differs",
        "unknown-role",
        "broken-message",
        "unpaired-after-completion",
        "waiting-call-not-interrupted",
        "interruption-without-waiting",
        "broken-deepagent-state",
        "list-role",
        "object-role",
        "result-after-a-later-message",
        "waiting-call-already-answered",
        "waiting-with-an-earlier-unpaired-group",
        "waiting-batch-differs-from-interruption",
        "waiting-entry-runs-another-tool",
        "waiting-entry-has-other-arguments",
        "waiting-ai-message-differs-from-window",
    ],
)
async def test_invalid_records_are_rejected_without_touching_session_or_backend(change, monkeypatch):
    """PA-10-V-10: identity is compared exactly; nothing is written into the Session or the backend."""
    errors = []
    monkeypatch.setattr(STATE.logger, "error", lambda message, *args: errors.append(message % args))
    state_store, backend = store()
    key = STATE.WindowStore.state_key("agent:1", "session%a")
    raw = json.dumps(record(**change))
    await backend.set(key, raw)
    ctx = context()
    with pytest.raises(BaseError) as exc_info:
        await state_store.load(ctx)
    assert_invalid(exc_info)
    assert ctx.session.get_state("context") is None
    assert await backend.get(key) == raw
    assert len(errors) == 1 and "session%a" in errors[0] and SECRET not in errors[0]


KEY = STATE.WindowStore.state_key("agent:1", "session%a")


async def saved(backend):
    raw = await backend.get(KEY)
    return None if raw is None else json.loads(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "cancelled", "error"])
async def test_save_patches_interrupted_endings_and_writes_one_record(outcome, monkeypatch):
    monkeypatch.setattr(STATE.ExecutionStateStore, "_sync_native", staticmethod(skip_sync))
    state_store, backend = store()
    ctx = context()
    ctx.session.update_state(
        {
            "context": {
                "default_context_id": {
                    "messages": [
                        UserMessage(content="task"),
                        AssistantMessage(content="", tool_calls=[call("call-a"), call("call-b")]),
                        tool_result("call-a", "uploaded"),
                    ]
                }
            }
        }
    )
    await state_store.save(ctx, ending(outcome))
    data = await saved(backend)
    assert set(data) == {
        "schema_version",
        "agent_id",
        "session_id",
        "messages",
        "interruption",
        "auto_confirm",
        "deepagent_state",
        "ending",
    }
    messages = RECORD.decode_messages(data["messages"])
    assert [getattr(message, "content", None) for message in messages[2:4]] == [
        "uploaded",
        "结果未取得，实际效果需核实",
    ]
    assert messages[-1].metadata == {PART: "execution_end"}
    assert data["ending"]["outcome"] == outcome and data["ending"]["execution_id"] == "execution-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, outcome",
    [
        ({"context": {"default_context_id": {"messages": [], "offload_messages": {"h": ["x"]}}}}, "completed"),
        ({"context": {"default_context_id": {"messages": []}, "other": {"messages": []}}}, "completed"),
        ({"context": {"default_context_id": {"messages": []}}}, "awaiting_confirmation"),
        (
            {
                "context": {
                    "default_context_id": {"messages": [AssistantMessage(content="", tool_calls=[call("call-x")])]}
                }
            },
            "completed",
        ),
        ({"__interrupt_auto_confirm__": {"remember": "yes"}}, "completed"),
        (
            {
                "context": {
                    "default_context_id": {
                        "messages": [
                            AssistantMessage(content="", tool_calls=[call("call-x")]),
                            UserMessage(content="next"),
                            tool_result("call-x"),
                        ]
                    }
                }
            },
            "stopped",
        ),
        (
            {
                "context": {
                    "default_context_id": {"messages": [AssistantMessage(content="", tool_calls=[call("call-1")])]}
                },
                "__react_agent_interruption__": tampered(entry_call=call("call-1", name="delete_file")),
            },
            "awaiting_confirmation",
        ),
    ],
    ids=[
        "offloaded-messages",
        "second-window",
        "waiting-without-interruption",
        "unpaired-on-completion",
        "bad-choice",
        "stray-result-on-stop",
        "waiting-entry-runs-another-tool",
    ],
)
async def test_save_checks_fail_as_runtime_errors_and_write_nothing(state, outcome, monkeypatch):
    """PA-10-V-12 and the other save-time checks: nothing is dropped or overwritten silently."""
    monkeypatch.setattr(STATE.ExecutionStateStore, "_sync_native", staticmethod(skip_sync))
    state_store, backend = store()
    ctx = context()
    ctx.session.update_state(state)
    with pytest.raises(BaseError) as exc_info:
        await state_store.save(ctx, ending(outcome))
    assert_runtime(exc_info)
    assert await saved(backend) is None


class FailingEngine:
    def __init__(self, has_context):
        self.has_context = has_context
        self.saved = 0

    def get_context(self, context_id, session_id):
        return object() if self.has_context else None

    async def save_contexts(self, session):
        self.saved += 1
        raise RuntimeError("native context save failed")


class NativeAgent:
    def __init__(self, engine):
        self.react_agent = SimpleNamespace(context_engine=engine)
        self.saved_states = 0

    def save_state(self, session):
        self.saved_states += 1


@pytest.mark.asyncio
async def test_sync_is_skipped_without_a_native_context_and_fails_the_save_when_it_breaks():
    """PA-10-V-16: no empty snapshot overwrites the loaded window; a failed sync writes nothing."""
    keep = context()
    agent = NativeAgent(FailingEngine(has_context=False))
    keep.binding.agent_ref = lambda: agent
    keep.session.update_state({"context": {"default_context_id": {"messages": [UserMessage(content="kept")]}}})
    state_store, backend = store()
    await state_store.save(keep, ending())
    assert agent.react_agent.context_engine.saved == 0 and agent.saved_states == 1
    assert RECORD.decode_messages((await saved(backend))["messages"])[0].content == "kept"

    broken = context()
    broken.binding.agent_ref = lambda: NativeAgent(FailingEngine(has_context=True))
    state_store, backend = store()
    with pytest.raises(BaseError) as exc_info:
        await state_store.save(broken, ending())
    assert_runtime(exc_info)
    assert await saved(backend) is None


def test_offloading_processors_are_found_by_their_registered_type():
    config = SimpleNamespace(
        context_processors=[
            ("MessageOffloader", None),
            ("DialogueCompressor", None),
            ("ToolResultBudgetProcessor", None),
        ]
    )
    agent = SimpleNamespace(react_agent=SimpleNamespace(config=config))
    assert STATE.offloading_processors(agent) == ["MessageOffloader", "ToolResultBudgetProcessor"]
    assert STATE.offloading_processors(SimpleNamespace(react_agent=None)) == []


def test_offloader_subclasses_registered_under_new_names_are_found(monkeypatch):
    from openjiuwen.core.context_engine.context_engine import ContextEngine
    from openjiuwen.core.context_engine.processor.compressor.dialogue_compressor import DialogueCompressor
    from openjiuwen.core.context_engine.processor.forked.offloader.message_offloader import (
        MessageSummaryOffloader as ForkedSummaryOffloader,
    )
    from openjiuwen.core.context_engine.processor.offloader.message_offloader import MessageOffloader

    class HostOffloader(MessageOffloader):
        pass

    class HostForkedOffloader(ForkedSummaryOffloader):
        pass

    class HostCompressor(DialogueCompressor):
        pass

    for processor in (HostOffloader, HostForkedOffloader, HostCompressor):
        monkeypatch.setitem(ContextEngine._PROCESSOR_MAP, processor.processor_type(), processor)
    config = SimpleNamespace(
        context_processors=[("HostOffloader", None), ("HostForkedOffloader", None), ("HostCompressor", None)]
    )
    agent = SimpleNamespace(react_agent=SimpleNamespace(config=config))
    assert STATE.offloading_processors(agent) == ["HostForkedOffloader", "HostOffloader"]


def test_tidy_window_keeps_a_rewritten_answer_and_says_what_was_handed_off():
    """PA-08-V-10: the answer as handed off stays in the window when after_invoke changed it."""
    messages = [UserMessage(content="task"), AssistantMessage(content="native answer")]
    handed = ending(
        output_refs=("out_a", "out_b", "out_f"),
        answer_text="rewritten answer",
        answer_segments=2,
        shared_files=(("reports/q3.md", "upload", "out_f"),),
    )
    tidied = STATE.tidy_window(messages, handed, "execution-1")
    kept = tidied[-2]
    assert kept.metadata == {PART: "handoff_text"}
    assert kept.content == "【实际交出的最终回答】\nrewritten answer"
    assert "交出候选：最终回答 2 段、文件 reports/q3.md（upload，out_f）（以结束记录为准）" in tidied[-1].content

    failed = ending("error", answer_text="rewritten answer", handoff_failure="reports/q3.md: not_found")
    tidied = STATE.tidy_window(messages, failed, "execution-1")
    assert tidied[-2].metadata == {PART: "handoff_text"}, "the answer is kept for a later handoff"
    assert "交出候选：无；未交接原因：reports/q3.md: not_found；群消息已扫描到：m-9" in tidied[-1].content


@pytest.mark.asyncio
async def test_new_records_carry_no_pending_answer(monkeypatch):
    """PA-08-V-14: a saved record keeps version 1 and has no pending_answer key."""
    monkeypatch.setattr(STATE.ExecutionStateStore, "_sync_native", staticmethod(skip_sync))
    state_store, backend = store()
    ctx = context()
    await state_store.load(ctx)
    await state_store.save(ctx, ending(answer_text="answer", answer_segments=1, output_refs=("out_1",)))
    written = await saved(backend)
    assert written["schema_version"] == 1 and set(written["ending"]) == {
        "outcome",
        "request_id",
        "execution_id",
        "reply_target",
        "output_refs",
        "last_group_message_id",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [SECRET, None], ids=["text", "null"])
async def test_a_record_with_the_retired_pending_answer_key_is_invalid(value):
    """PA-08-V-14: the strict record model rejects the retired key, even when it is null; nothing is changed."""
    state_store, backend = store()
    data = record(
        ending={"outcome": "error", "request_id": "request-0", "execution_id": "execution-0", "pending_answer": value}
    )
    raw = json.dumps(data)
    await backend.set(KEY, raw)
    with pytest.raises(BaseError) as exc_info:
        await state_store.load(context())
    assert_invalid(exc_info)
    assert await backend.get(KEY) == raw, "the stored record is left as it is"


class WritesThenFails(InMemoryKVStore):
    """A backend whose set raises after the value was stored, e.g. a timeout after the write landed."""

    async def set(self, key, value):
        await super().set(key, value)
        raise RuntimeError("backend write failed")


async def load_previous(backend, monkeypatch):
    monkeypatch.setattr(STATE.ExecutionStateStore, "_sync_native", staticmethod(skip_sync))
    data = record(ending={"outcome": "error", "request_id": "request-0", "execution_id": "execution-0"})
    await InMemoryKVStore.set(backend, KEY, json.dumps(data))
    state_store, _ = store(backend)
    ctx = context()
    await state_store.load(ctx)
    return state_store, ctx


@pytest.mark.asyncio
async def test_a_failed_write_cannot_promise_the_record_is_unchanged(monkeypatch):
    """PA-10-V-09: the entry sees a runtime error, yet the backend may hold the new record."""
    backend = WritesThenFails()
    state_store, ctx = await load_previous(backend, monkeypatch)
    with pytest.raises(BaseError) as exc_info:
        await state_store.save(ctx, ending())
    assert_runtime(exc_info)
    assert (await saved(backend))["ending"]["request_id"] == "request-1", "the write landed before the error"


@pytest.mark.asyncio
async def test_a_save_failing_before_the_write_leaves_the_record(monkeypatch):
    backend = InMemoryKVStore()
    state_store, ctx = await load_previous(backend, monkeypatch)
    loaded = window(ctx.session)
    ctx.session.update_state(
        {"context": {"default_context_id": {"messages": loaded, "offload_messages": {"handle": ["x"]}}}}
    )
    with pytest.raises(BaseError) as exc_info:
        await state_store.save(ctx, ending())
    assert_runtime(exc_info)
    assert (await saved(backend))["ending"]["request_id"] == "request-0", "nothing was written"
