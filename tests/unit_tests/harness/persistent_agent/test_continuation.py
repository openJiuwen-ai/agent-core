# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-18 continuation with a real DeepAgent, a local model and the real session state record.

Records are written into the window backend in their stored form, so loading, the stale scene
handling, the log summaries, the chain tracing and the saved record are the real ones; only the
model, the host ports, the workspace grant and the group message reader are doubles. The formal
entry keeps continuation closed (F_20 D-10); the fixture opens it through the internal seam.
"""

import json
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngineConfig
from openjiuwen.core.context_engine.token import NativeTokenizerCounter
from openjiuwen.core.context_engine.token.tokenizer_spec import TokenizerSpec
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.session.checkpointer import NoopCheckpointer
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import ToolInterruptEntry, ToolInterruptionState
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.schema.deep_agent_spec import _TOOL_PROVIDER_REGISTRY, BuiltinToolSpec, ModelSpec

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
SEAMS = import_module(f"{PKG}.execution.seams")
STATE = import_module(f"{PKG}.execution.session_state")
HANDOFF = import_module(f"{PKG}.execution.handoff")
CONTINUATION = import_module(f"{PKG}.execution.continuation")
LOG = import_module(f"{PKG}.execution.execution_log")
RECORD = import_module(f"{PKG}.schema.session_record")
EVENTS = import_module(f"{PKG}.schema.events")
INTEGRATION = import_module(f"{PKG}.schema.integration")
PART = SEAMS.WINDOW_PART_KEY
CLOSED_GATE = SEAMS.continuation_support
RECORD_TOOL = import_module(f"{PKG}.execution.record_tool")
WHEN = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
EARLIER = "execution-2"
HEADER = (
    "【本次执行】类型：续做；request_id：request-1；execution_id：execution-3；recovery_ref：execution-2；"
    "来源：定时任务"
)
TASK = "summarize unhandled claims"


def parts(messages):
    return [(type(message).__name__, message.content, (message.metadata or {}).get(PART)) for message in messages]


def usage():
    return UsageMetadata(input_tokens=3, output_tokens=4)


class LocalModel:
    """Answers by default, or calls one tool for a queued (name, arguments) turn; keeps what each call was sent."""

    model_client_config = None
    model_config = None

    def __init__(self):
        self.seen = []
        self.tools = []
        self.turns = []

    async def invoke(self, messages, **kwargs):
        raise AssertionError("entries always stream the native agent")

    async def stream(self, messages, **kwargs):
        self.seen.append(list(messages))
        self.tools.append([getattr(tool, "name", None) for tool in kwargs.get("tools") or []])
        turn = self.turns.pop(0) if self.turns else "answer"
        if isinstance(turn, tuple):
            name, arguments = turn
            calls = [ToolCall(id=f"call-{len(self.seen)}", type="function", name=name, arguments=arguments)]
            yield AssistantMessageChunk(content="checking ", tool_calls=calls, usage_metadata=usage())
            return
        yield AssistantMessageChunk(content="checked and done", finish_reason="stop", usage_metadata=usage())


class Remember(Tool):
    """A write tool; a continuation must never run it again on its own."""

    def __init__(self):
        super().__init__(
            card=ToolCard(id="remember", name="remember", input_params={"type": "object", "properties": {"value": {}}})
        )
        self.calls = 0

    async def invoke(self, inputs, **kwargs):
        self.calls += 1
        return "stored"

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def ref(execution, **changes):
    data = import_module(f"{PKG}.schema.protocol").InvocationRef.from_execution(execution).model_dump()
    return {**data, **changes}


def link(ctx, execution_id, relation="first", predecessor=None, **changes):
    """The predecessor link the host saved for one Execution of this Request."""
    identity = {name: getattr(ctx.execution, name) for name in ("namespace_id", "agent_id", "session_id", "request_id")}
    return {**identity, "execution_id": execution_id, "predecessor_id": predecessor, "relation": relation, **changes}


class Host:
    """Commit, budget and group chat ports; logs and predecessor links are configurable per Execution."""

    def __init__(self, ctx_ref):
        self.ctx_ref = ctx_ref
        self.events = []
        self.log = ()
        self.log_error = None
        self.logs = {}
        self.log_errors = {}
        self.links = None
        self.link_errors = {}
        self.reads = []
        self.link_reads = []
        self.admits = []
        self.admission = "admitted"

    async def record(self, execution, event):
        self.events.append(event)

    async def read_execution_log(self, execution, execution_id):
        self.reads.append(execution_id)
        error = self.log_errors.get(execution_id, self.log_error)
        if error is not None:
            raise error
        return self.logs.get(execution_id, self.log)

    async def read_execution_link(self, execution, execution_id):
        self.link_reads.append(execution_id)
        if execution_id in self.link_errors:
            raise self.link_errors[execution_id]
        links = self.links if self.links is not None else {EARLIER: link(self.ctx_ref(), EARLIER)}
        return links[execution_id]

    async def admit(self, execution, model_call_id):
        self.admits.append(model_call_id)
        if self.admission == "denied":
            return {**ref(execution, model_call_id=model_call_id), "status": "denied", "reason": "quota used up"}
        return {**ref(execution, model_call_id=model_call_id), "status": "admitted", "reservation_ref": "res-1"}

    async def report(self, execution, model_call_id, model_usage):
        return {**ref(execution, model_call_id=model_call_id), "status": "confirmed", "receipt_ref": "rcpt-1"}

    async def recent_messages(self, *args, **kwargs):
        raise AssertionError("a continuation does not judge participation again")

    async def search_messages(self, *args):
        raise AssertionError("not searched")


class Reader:
    """PA-23 double for the group background of the continuation."""

    def __init__(self):
        self.calls = []

    async def recent_messages(self, context, *, after=None, trigger_message_id=None):
        self.calls.append((after, trigger_message_id))
        said = {
            "message_id": "m-2",
            "sender_id": "u-2",
            "sender_name": "王五",
            "sender_kind": "human",
            "received_at": WHEN,
            "text": "三月的已经处理了",
        }
        return {**ref(context.execution), "status": "found", "messages": [said]}


class Harness:
    def __init__(self, api, model, tool, monkeypatch):
        self.api = api
        self.model = model
        self.tool = tool
        self.monkeypatch = monkeypatch
        self.backend = InMemoryKVStore()

    def spec(self, **overrides):
        fields = dict(
            card=AgentCard(id="business-agent", name="test"),
            model=ModelSpec(
                model_client_config=ModelClientConfig(
                    client_provider="OpenAI", api_key="mock-api-key", api_base="https://example.invalid/v1"
                ),
                model_request_config=ModelRequestConfig(context_window=1_000_000, max_tokens=4_000),
            ),
            enable_sys_operation=False,
            enable_task_loop=False,
            enable_security_rail=False,
            enable_tool_resilience_rail=False,
            auto_create_workspace=False,
            tools=[BuiltinToolSpec(type="test.continuation.remember")],
            continuation_chain_limit=8,
        )
        return self.api.PersistentAgentSpec(**{**fields, **overrides})

    def context(self, spec, *, recovery_ref=EARLIER, group=False, workspace_access=None):
        execution = self.api.ExecutionContext(
            namespace_id="tenant",
            agent_id=spec.card.id,
            session_id="session-a",
            request_id="request-1",
            execution_id="execution-3",
            config_version="v1",
            config_fingerprint=spec.fingerprint(),
            source_ref="source:message-1",
            principal_ref="user",
            startup_scope=frozenset({"read"}),
            source_scope=frozenset({"read"}),
            policy_revision="p1",
            capability_snapshot=spec.enabled_features,
            recovery_ref=recovery_ref,
        )
        holder: list = []
        host = Host(lambda: holder[0])
        ports = self.api.PersistentPorts(
            commit=host,
            budget=host,
            window_backend=self.backend,
            group_chat=host if group else None,
            workspace_access=workspace_access,
        )
        session = create_agent_session(session_id="session-a", card=spec.card, checkpointer=NoopCheckpointer())
        holder.append(self.api.PersistentBuildContext(execution=execution, session=session, ports=ports))
        return holder[0], host

    async def run(self, spec, ctx, request):
        return [event async for event in self.api.execute_persistent_request(spec, ctx, request)]

    def key(self):
        return STATE.WindowStore.state_key("business-agent", "session-a")

    async def store(self, messages, *, outcome="completed", request_id="request-0", interruption=None, **ending):
        record = {
            "schema_version": 1,
            "agent_id": "business-agent",
            "session_id": "session-a",
            "messages": RECORD.encode_messages(messages),
            "interruption": RECORD.encode_interruption(interruption) if interruption is not None else None,
            "auto_confirm": ending.pop("auto_confirm", {}),
            "deepagent_state": None,
            "checkpoint": None,
            "last_group_message_id": ending.pop("last_group_message_id", None),
            "current_window_id": ending.pop("current_window_id", None),
            "window_seq": ending.pop("window_seq", None),
            "last_rollover": ending.pop("last_rollover", None),
            "ending": {"outcome": outcome, "request_id": request_id, "execution_id": "execution-1", **ending},
        }
        await self.backend.set(self.key(), json.dumps(record))

    async def saved(self):
        record = RECORD.parse_record(await self.backend.get(self.key()))
        return record, RECORD.decode_messages(record.messages)


@pytest.fixture
def harness(monkeypatch):
    api = import_module(PKG)
    model, tool = LocalModel(), Remember()
    monkeypatch.setattr(ModelSpec, "build", lambda self: model)
    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, "test.continuation.remember", lambda params, context: tool)
    monkeypatch.setattr(SEAMS, "continuation_support", lambda context: None)
    return Harness(api, model, tool, monkeypatch)


@pytest.fixture
def local_tokenizer(tmp_path):
    """A real local artifact; entry tests exercise core selection without a counter double."""
    from tokenizers import Regex, Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex("."), "isolated")
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    return path


def configured_native_spec(harness, path, representation, registration="spec", **overrides):
    name = "local-native-model"
    tokenizer = TokenizerSpec(
        provider="OpenAI", model=name, source="local", engine="tokenizers", artifact_path=str(path)
    )
    config = ContextEngineConfig(
        model_provider="OpenAI",
        model_name=name,
        tokenizer_spec=tokenizer if registration == "spec" else None,
        tokenizer_registry=[tokenizer] if registration == "registry" else [],
        tokenizer_cache_dir=str(path.parent / "cache"),
    )
    model = ModelSpec(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="mock-api-key", api_base="https://example.invalid/v1"
        ),
        model_request_config=ModelRequestConfig(model=name, context_window=1_000_000, max_tokens=4_000),
    )
    return harness.spec(
        model=model,
        context_engine_config=config.model_dump(mode="json") if representation == "dict" else config,
        **overrides,
    )


@pytest.mark.parametrize("representation", ["object", "dict"])
@pytest.mark.parametrize("registration", ["spec", "registry"])
@pytest.mark.asyncio
async def test_the_published_tokenizer_reaches_the_native_context_of_a_formal_execution(
    harness, local_tokenizer, monkeypatch, representation, registration
):
    """The Spec's JSON roundtrip keeps the tokenizer settings that window management counts with."""
    spec = configured_native_spec(harness, local_tokenizer, representation, registration)
    publication = spec.model_dump_json()
    fingerprint = spec.fingerprint()
    rail = import_module(f"{PKG}.execution.model_rail").PersistentModelCallRail
    final_check, counters = rail.check_final_request, []

    async def probe(self, ctx):
        counters.append(ctx.context.token_counter())
        return await final_check(self, ctx)

    monkeypatch.setattr(rail, "check_final_request", probe)
    ctx, _ = harness.context(spec, recovery_ref=None)
    events = await harness.run(spec, ctx, scheduled())
    assert spec.model_dump_json() == publication and spec.fingerprint() == fingerprint
    assert events[-1].payload.status == "completed"
    assert len(counters) == 1 and isinstance(counters[0], NativeTokenizerCounter)


def logged(ctx, seq, kind, payload, execution_id=EARLIER):
    """An event of an earlier Execution of this Request."""
    source = replace(ctx.execution, execution_id=execution_id, recovery_ref=None)
    return EVENTS.AgentEvent.create(source, event_type=kind, local_seq=seq, occurred_at=WHEN, payload=payload)


def write_intent(ctx, seq, execution_id=EARLIER, op="op_12"):
    payload = {
        "operation_id": op,
        "tool_call_id": f"call-{op}",
        "tool_name": "submit_reminder",
        "params_digest": "a" * 64,
        "args_summary": "单号=BX-0930",
    }
    return logged(ctx, seq, EVENTS.EventType.TOOL_INTENT, payload, execution_id)


def conclusion(ctx, execution_id, text):
    return logged(ctx, 1, EVENTS.EventType.PROGRESS_UPDATED, {"text": text}, execution_id)


def scheduled(**fields):
    return {"kind": "scheduled", "text": TASK, "reply_target": "chat-1", **fields}


def call(call_id):
    return ToolCall(id=call_id, type="function", name="remember", arguments='{"value": "x"}')


def waiting_scene(call_id="call-1"):
    """What a confirmation pause saved: the interrupted call, its request and the remembered choices."""
    state = ToolInterruptionState(
        ai_message=AssistantMessage(content="checking", tool_calls=[call(call_id)]),
        iteration=1,
        original_query=TASK,
        interrupted_tools={
            call_id: ToolInterruptEntry(
                tool_call=call(call_id),
                interrupt_requests={f"{call_id}-0": InterruptRequest(message="approve?", auto_confirm_key="remember")},
            )
        },
        auto_confirm_mapping={f"{call_id}-0": "remember"},
    )
    window = [UserMessage(content=TASK), AssistantMessage(content="checking", tool_calls=[call(call_id)])]
    return window, state


def listed_before_waiting():
    """A share_file listing made before the confirmation pause, as it stands in the saved window."""
    share = ToolCall(id="share-1", type="function", name=HANDOFF.SHARE_FILE, arguments="{}")
    result = HANDOFF.registration_result(HANDOFF.Registration("reports/q3.md", "upload", None))
    return [
        AssistantMessage(content="listing", tool_calls=[share]),
        ToolMessage(content=result, tool_call_id="share-1"),
    ]


def background(messages):
    return next(message.content for message in messages if (message.metadata or {}).get(PART) == "temporary_background")


@pytest.mark.asyncio
async def test_a_continuation_checks_progress_before_the_original_task(harness):
    """PA-18-V-01: the log is read even though a window was saved."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    await harness.store([UserMessage(content="earlier question"), AssistantMessage(content="earlier answer")])
    host.log = (
        logged(ctx, 1, EVENTS.EventType.EXECUTION_STARTED, {"goal": TASK}),
        write_intent(ctx, 2),
        logged(ctx, 3, EVENTS.EventType.PROGRESS_UPDATED, {"text": "已核对 3 笔报销"}),
    )
    events = await harness.run(spec, ctx, scheduled())
    assert host.reads == [EARLIER]
    assert [str(event.event_type) for event in events][0] == "execution.started"
    assert events[-1].payload.status == "completed"
    (first,) = harness.model.seen
    assert isinstance(first[0], SystemMessage)
    shown = parts(first[1:])
    assert shown[:3] == [
        ("UserMessage", "earlier question", None),
        ("AssistantMessage", "earlier answer", None),
        ("UserMessage", HEADER, SEAMS.EXECUTION_HEADER),
    ]
    assert shown[3][2] == SEAMS.TEMPORARY_BACKGROUND and shown[4] == ("UserMessage", TASK, None)
    record_text = shown[3][1]
    assert record_text.startswith("【续做记录】本次接续执行 execution-2。")
    assert "[已登记，无结果] submit_reminder(单号=BX-0930)（写操作 op_12，调用 call-op_12）" in record_text
    assert "[结论] 已核对 3 笔报销" in record_text
    assert record_text.endswith(CONTINUATION._RULES), "the rules close the background, right before the task"
    _, saved = await harness.saved()
    saved_parts = [part for _, _, part in parts(saved)]
    assert SEAMS.TEMPORARY_BACKGROUND not in saved_parts, "the log summary and rules are not kept"
    assert ("UserMessage", HEADER, SEAMS.EXECUTION_HEADER) in parts(saved), "the header is kept"


@pytest.mark.parametrize("bad", ["has space", "a\nb", "a\x00b", "x" * 513])
@pytest.mark.asyncio
async def test_an_invalid_recovery_ref_is_rejected_before_any_event(harness, bad):
    """PA-18-V-02."""
    spec = harness.spec()
    ctx, host = harness.context(spec, recovery_ref=bad)
    with pytest.raises(BaseError) as exc_info:
        await harness.run(spec, ctx, scheduled())
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR
    assert host.events == [] and host.reads == [] and harness.model.seen == []


@pytest.mark.asyncio
async def test_a_continue_instruction_follows_the_original_task_input(harness):
    """PA-18-V-03."""
    spec = harness.spec()
    ctx, _ = harness.context(spec)
    await harness.run(spec, ctx, scheduled(continue_instruction="只处理三月的报销"))
    (first,) = harness.model.seen
    assert parts(first[-1:]) == [("UserMessage", f"{TASK}\n\n【继续指示】只处理三月的报销", None)]


@pytest.mark.asyncio
async def test_a_stale_waiting_scene_is_settled_without_replaying_the_call(harness):
    """PA-18-V-04: confirmed, resumed, then crashed; the continuation loads the older waiting record."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    window, state = waiting_scene()
    window = [window[0], *listed_before_waiting(), window[1]]
    assert HANDOFF.rebuild_registrations(window), "a confirmation resume would still hand this listing off"
    await harness.store(
        window,
        outcome="awaiting_confirmation",
        request_id="request-1",
        interruption=state,
        reply_target="chat-1",
        auto_confirm={"remember": True},
    )
    events = await harness.run(spec, ctx, scheduled())
    assert harness.tool.calls == 0, "the interrupted call is not replayed"
    assert not {"tool.intent", "tool.completed"} & {str(event.event_type) for event in events}
    assert events[-1].payload.status == "completed"
    (first,) = harness.model.seen
    shown = parts(first[1:])
    assert shown[2:5] == [
        ("ToolMessage", shown[2][1], None),
        ("AssistantMessage", "checking", None),
        ("ToolMessage", STATE.PLACEHOLDER_TEXT, INTEGRATION.STOPPED_TOOL_PLACEHOLDER),
    ], "the PA-10 placeholder answers the call before anything is appended; the real result stays"
    assert first[5].tool_call_id == "call-1"
    assert shown[5] == ("UserMessage", HEADER, SEAMS.EXECUTION_HEADER)
    assert STATE.PLACEHOLDER_TEXT in background(first), "the rules say how to read the placeholder"
    record, saved = await harness.saved()
    assert record.interruption is None and record.ending.outcome == "completed"
    assert record.auto_confirm == {"remember": True}, "choices remembered in this Session stay"
    assert parts(saved)[4] == ("ToolMessage", STATE.PLACEHOLDER_TEXT, INTEGRATION.STOPPED_TOOL_PLACEHOLDER)
    assert HANDOFF.rebuild_registrations(saved) == [], "the listing before the crash is evidence only"
    assert events[-1].payload.output_refs, "the answer is still handed off"


@pytest.mark.asyncio
async def test_another_requests_waiting_scene_is_refused_and_left_as_it_is(harness):
    """PA-18-V-05."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    window, state = waiting_scene()
    await harness.store(
        window, outcome="awaiting_confirmation", request_id="request-0", interruption=state, reply_target="chat-0"
    )
    before = await harness.backend.get(harness.key())
    with pytest.raises(BaseError) as exc_info:
        await harness.run(spec, ctx, scheduled())
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR
    assert "another Request is waiting" in str(exc_info.value)
    assert await harness.backend.get(harness.key()) == before, "the other Request's scene is not saved over"
    assert harness.model.seen == [] and harness.tool.calls == 0
    assert [str(event.event_type) for event in host.events] == ["execution.started"], "no ending is recorded"


@pytest.mark.asyncio
async def test_a_missing_log_still_continues_and_asks_the_initiator_first(harness):
    """PA-18-V-06."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    host.log_error = PermissionError("denied")
    events = await harness.run(spec, ctx, scheduled())
    assert events[-1].payload.status == "completed"
    record_text = background(harness.model.seen[0])
    assert LOG._MISSING in record_text and record_text.endswith(CONTINUATION._RULES)
    assert "先向发起人确认" not in record_text, "whether to ask is the Agent's call (Q-09)"
    assert "denied" not in record_text


@pytest.mark.asyncio
async def test_a_group_continuation_does_not_judge_participation_again(harness, monkeypatch):
    """PA-18-V-07."""
    reader = Reader()
    monkeypatch.setattr(SEAMS, "group_message_reader", lambda context: reader)
    spec = harness.spec()
    ctx, _ = harness.context(spec, group=True)
    source = {
        "message_id": "m-1",
        "sender_id": "u-1",
        "sender_name": "李四",
        "sender_kind": "human",
        "received_at": WHEN,
        "text": TASK,
    }
    request = {"kind": "group_message", "text": TASK, "reply_target": "chat-1", "source": source}
    events = await harness.run(spec, ctx, request)
    kinds = [str(event.event_type) for event in events]
    assert "participation.decided" not in kinds and kinds[0] == "execution.started"
    assert events[0].payload.decision_event_id is None
    assert reader.calls == [(None, "m-1")], "the group background is read as for a new Request"
    (first,) = harness.model.seen
    shown = parts(first[1:])
    assert [part for _, _, part in shown] == [
        SEAMS.EXECUTION_HEADER,
        SEAMS.TEMPORARY_BACKGROUND,
        SEAMS.TEMPORARY_BACKGROUND,
        None,
    ], "header, group messages, then the continuation record, and the task input last"
    assert shown[1][1].startswith("【最近群消息】") and shown[2][1].startswith("【续做记录】")


@pytest.mark.parametrize("missing", ["gate", "link_reader", "chain_limit"])
@pytest.mark.asyncio
async def test_continuation_stays_closed_until_its_integrations_are_in(harness, monkeypatch, missing):
    """PA-18-V-10: the formal entry refuses a continuation before any event (F_20 D-10)."""
    spec = harness.spec(continuation_chain_limit=None) if missing == "chain_limit" else harness.spec()
    ctx, host = harness.context(spec)
    if missing == "gate":
        monkeypatch.setattr(SEAMS, "continuation_support", CLOSED_GATE)
    if missing == "link_reader":
        monkeypatch.delattr(Host, "read_execution_link")
    with pytest.raises(BaseError) as exc_info:
        await harness.run(spec, ctx, scheduled())
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert host.events == [] and host.reads == [] and harness.model.seen == []


def chain_of_three(ctx, host):
    """execution-0 (first) <- execution-1 (confirmation resume) <- execution-2 (continuation) <- this one."""
    host.links = {
        "execution-2": link(ctx, "execution-2", "continuation", "execution-1"),
        "execution-1": link(ctx, "execution-1", "confirmation_resume", "execution-0"),
        "execution-0": link(ctx, "execution-0"),
    }
    host.logs = {
        "execution-0": (write_intent(ctx, 1, "execution-0", "op_old"),),
        "execution-1": (conclusion(ctx, "execution-1", "审批已同意"),),
        "execution-2": (conclusion(ctx, "execution-2", "预算不足，暂停"),),
    }


@pytest.mark.asyncio
async def test_the_chain_is_read_back_and_shown_in_causal_order(harness):
    """PA-18-V-11: a write intent two interruptions back is still in front of the Agent."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    chain_of_three(ctx, host)
    await harness.run(spec, ctx, scheduled())
    assert host.reads == ["execution-2", "execution-1", "execution-0"]
    text = background(harness.model.seen[0])
    assert (
        "执行链：execution-0（首次执行） → execution-1（确认恢复） → execution-2（续做） → execution-3（本次续做）"
        in text
    )
    order = [
        text.index(f"执行 {name}（请求 request-1）的执行日志摘要")
        for name in ("execution-0", "execution-1", "execution-2")
    ]
    assert order == sorted(order), "earliest first, by the links and not by time"
    assert "[已登记，无结果] submit_reminder(单号=BX-0930)（写操作 op_old" in text
    assert "更早执行未查完" not in text and "之前是否还有执行" not in text and "停止追溯" not in text


@pytest.mark.asyncio
async def test_the_chain_limit_is_stated_and_never_taken_for_the_start(harness):
    """PA-18-V-12."""
    spec = harness.spec(continuation_chain_limit=2)
    ctx, host = harness.context(spec)
    chain_of_three(ctx, host)
    await harness.run(spec, ctx, scheduled())
    assert host.reads == ["execution-2", "execution-1"]
    text = background(harness.model.seen[0])
    assert "更早执行未查完：已读取 2 次执行（追溯上限），execution-0 及更早的执行未读取。" in text
    assert "首次执行" not in text


@pytest.mark.parametrize(
    "broken",
    [
        "read_error",
        "self",
        "cycle",
        "active",
        "other_request",
        "wrong_target",
        "first_with_predecessor",
        "resume_without_predecessor",
    ],
)
@pytest.mark.asyncio
async def test_a_link_that_cannot_be_used_stops_tracing_but_keeps_what_was_read(harness, broken):
    """PA-18-V-13: the logs already reached stay; the Agent is told earlier Executions are unknown."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    chain_of_three(ctx, host)
    bad = {
        "self": link(ctx, "execution-1", "continuation", "execution-1"),
        "cycle": link(ctx, "execution-1", "continuation", "execution-2"),
        "active": link(ctx, "execution-1", "continuation", "execution-3"),
        "other_request": link(ctx, "execution-1", "confirmation_resume", "execution-0", request_id="request-9"),
        "wrong_target": link(ctx, "execution-0"),
        "first_with_predecessor": link(ctx, "execution-1", "first", "execution-0"),
        "resume_without_predecessor": link(ctx, "execution-1", "confirmation_resume"),
    }
    if broken == "read_error":
        host.link_errors["execution-1"] = TimeoutError("host busy")
    else:
        host.links["execution-1"] = bad[broken]
    events = await harness.run(spec, ctx, scheduled())
    assert events[-1].payload.status == "completed"
    assert host.reads == ["execution-2", "execution-1"], "execution-1's own log is still read"
    text = background(harness.model.seen[0])
    assert "[结论] 审批已同意" in text and "[结论] 预算不足，暂停" in text
    assert ("停止追溯" if broken in ("cycle", "active") else "无法确认 execution-1 之前") in text
    assert "更早的执行" in text and "首次执行" not in text and "host busy" not in text


@pytest.mark.asyncio
async def test_a_missing_log_with_a_sound_link_goes_on_to_earlier_executions(harness):
    """PA-18-V-14."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    chain_of_three(ctx, host)
    host.log_errors["execution-1"] = PermissionError("denied")
    await harness.run(spec, ctx, scheduled())
    assert host.reads == ["execution-2", "execution-1", "execution-0"]
    text = background(harness.model.seen[0])
    assert f"上一次执行 execution-1：{LOG._MISSING}" in text
    assert "（写操作 op_old" in text, "the earliest pending write is still found"


class WorkspaceAccess:
    def __init__(self, root):
        self.root = root

    async def resolve_read_scope(self, context):
        return WorkspaceReadScope(
            status="allowed",
            agent_id=context.agent_id,
            session_id=context.session_id,
            workspace_root=self.root,
            allowed_roots=(self.root,),
            private_sessions_root=self.root / "sessions",
            current_session_root=self.root / "sessions" / context.session_id,
            source_ref="workspace://business-agent",
            policy_revision=context.policy_revision,
        )


@pytest.mark.asyncio
async def test_files_listed_before_the_crash_are_not_handed_off_but_new_listings_are(harness, tmp_path):
    """PA-18-V-15: the real file handoff path; old listings are evidence, new ones and the answer go out."""
    root = tmp_path / "workspace"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "q3.md").write_bytes(b"listed before the crash")
    (root / "reports" / "q4.md").write_bytes(b"listed after checking")
    spec = harness.spec(enable_sys_operation=True, enabled_features=frozenset({"construction", "workspace_read"}))
    ctx, _ = harness.context(spec, workspace_access=WorkspaceAccess(root))
    window, state = waiting_scene()
    await harness.store(
        [window[0], *listed_before_waiting(), window[1]],
        outcome="awaiting_confirmation",
        request_id="request-1",
        interruption=state,
        reply_target="chat-1",
    )
    harness.model.turns = [("share_file", json.dumps({"file_path": "reports/q4.md", "share_mode": "upload"}))]
    events = await harness.run(spec, ctx, scheduled())
    handed = [event.payload for event in events if event.event_type == "output.proposed"]
    assert [(item.share_mode, item.file_path) for item in handed] == [("message", None), ("upload", "reports/q4.md")]
    assert events[-1].payload.status == "completed"
    assert events[-1].payload.output_refs == tuple(item.output_id for item in handed)


# Each final request is admitted once, then the model is called; no model capacity is configured or checked.


def paused(events):
    ending = events[-1]
    assert str(ending.event_type) == "execution.paused"
    return ending.payload


@pytest.mark.asyncio
async def test_each_model_call_is_admitted_once_then_called(harness):
    """PA-18-V-18."""
    spec = harness.spec()
    ctx, host = harness.context(spec, recovery_ref=None)
    events = await harness.run(spec, ctx, scheduled())
    assert events[-1].payload.status == "completed"
    assert len(host.admits) == len(harness.model.seen) == 1
    denied_spec = harness.spec()
    denied_ctx, denied_host = harness.context(denied_spec, recovery_ref=None)
    denied_host.admission = "denied"
    seen_before = len(harness.model.seen)
    payload = paused(await harness.run(denied_spec, denied_ctx, scheduled()))
    assert payload.reason == "budget_denied" and len(harness.model.seen) == seen_before, "a denied call never runs"


NO_WINDOW = ModelSpec(
    model_client_config=ModelClientConfig(
        client_provider="OpenAI", api_key="mock-api-key", api_base="https://x.invalid"
    )
)


@pytest.mark.parametrize(
    ("overrides", "recovery_ref"),
    [
        pytest.param({"model": NO_WINDOW}, None, id="new_task_without_model_limits"),
        pytest.param({"model": NO_WINDOW}, EARLIER, id="continuation_without_model_limits"),
        pytest.param(
            {"model": NO_WINDOW, "enabled_features": frozenset({"construction", "window_management"})},
            None,
            id="window_management_without_legacy_limits",
        ),
        pytest.param(
            {"model_input_limit_tokens": 1, "capacity_safety_margin_tokens": 0}, None, id="limits_left_to_windows"
        ),
    ],
)
@pytest.mark.asyncio
async def test_formal_execution_needs_no_model_capacity(harness, overrides, recovery_ref):
    """PA-18-V-30 / V-31: execution and window construction do not require legacy capacity fields."""
    spec = harness.spec(**overrides)
    ctx, host = harness.context(spec, recovery_ref=recovery_ref)
    events = await harness.run(spec, ctx, scheduled())
    assert events[-1].payload.status == "completed"
    assert harness.model.seen and len(host.admits) == len(harness.model.seen)


# F_20 D-08: read_execution_record is declared for every Execution of the Spec and answers in a continuation.


@pytest.mark.asyncio
async def test_the_record_tool_is_declared_for_every_execution_of_a_continuing_spec(harness):
    """PA-18-V-20: tool definitions stay the same from the first Execution to its continuation."""
    spec = harness.spec()
    new_task, _ = harness.context(spec, recovery_ref=None)
    await harness.run(spec, new_task, scheduled())
    continuing, _ = harness.context(spec)
    await harness.run(spec, continuing, scheduled())
    first, second = harness.model.tools
    assert "read_execution_record" in first and first == second
    plain = harness.spec(continuation_chain_limit=None)
    plain_ctx, _ = harness.context(plain, recovery_ref=None)
    await harness.run(plain, plain_ctx, scheduled())
    assert "read_execution_record" not in harness.model.tools[-1]


def tool_results(events):
    return [event.payload for event in events if str(event.event_type) == "tool.completed"]


@pytest.mark.asyncio
async def test_the_record_tool_reads_a_verified_execution_and_refuses_others(harness):
    """PA-18-V-21: only the traced chain is readable; the read is not a write and records no intent."""
    spec = harness.spec()
    ctx, host = harness.context(spec)
    chain_of_three(ctx, host)
    harness.model.turns = [
        ("read_execution_record", json.dumps({"execution_id": "execution-0"})),
        ("read_execution_record", json.dumps({"execution_id": "execution-9"})),
    ]
    events = await harness.run(spec, ctx, scheduled())
    kinds = [str(event.event_type) for event in events]
    assert "tool.intent" not in kinds, "the record tool is on the read-only list"
    read, refused = tool_results(events)
    assert read.status == "succeeded" and refused.status == "failed"
    window = harness.model.seen[2]
    results = [message for message in window if isinstance(message, ToolMessage)]
    page = json.loads(str(results[-2].content))
    assert page["execution_id"] == "execution-0" and page["next_cursor"] is None
    text = "\n".join(segment["text"] for segment in page["segments"])
    assert text.startswith("上一次执行 execution-0（请求 request-1）的执行日志整理："), (
        "the full summary, not the brief"
    )
    assert "不在本次续做已核验的执行链中" in results[-1].content
    assert host.reads.count("execution-0") == 2, "the tool checks the whole log again"


@pytest.mark.asyncio
async def test_the_record_tool_is_unavailable_outside_a_continuation(harness):
    spec = harness.spec()
    ctx, host = harness.context(spec, recovery_ref=None)
    harness.model.turns = [("read_execution_record", json.dumps({"execution_id": EARLIER}))]
    events = await harness.run(spec, ctx, scheduled())
    (refused,) = tool_results(events)
    assert refused.status == "failed" and host.reads == []
    result = [message for message in harness.model.seen[1] if isinstance(message, ToolMessage)][-1]
    assert "只有续做执行可以读取执行记录" in result.content
