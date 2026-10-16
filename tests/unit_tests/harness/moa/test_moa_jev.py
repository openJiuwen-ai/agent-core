"""JEV routing through public DeepAgent invocations and the typed core client."""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock

import httpx
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message import SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.llm.system_one import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevSystemOneClient,
    NoulAnswer,
    SystemOneResponse,
    SystemOneUsage,
)
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import SysOperation, SysOperationCard
from openjiuwen.harness.moa import JevConfig, MoAConfig, VerifierConfig
from openjiuwen.harness.moa.state import MoAState, get_moa_snapshot
from openjiuwen.harness.moa.verifier.jev_verifier.verifier import _decode_decision, build_jev_state, decide_reference
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.task import TodoItem, TodoStatus
from openjiuwen.harness.tools.todo import TodoTool
from tests.unit_tests.harness.moa.helpers import Echo, _spec, agent, model, primary


def _response(consult: bool = False, **answer_overrides) -> SystemOneResponse:
    chosen = "CONSULT" if consult else "CONTINUE"
    probabilities = {"CONSULT": 0.9 if consult else 0.1, "CONTINUE": 0.1 if consult else 0.9}
    answer = ChoiceAnswer(choice=chosen, confidence=0.9, probabilities=probabilities)
    return SystemOneResponse(
        model="jev-test",
        answers={"consult_reference": answer.model_copy(update=answer_overrides)},
        usage=SystemOneUsage(input_tokens=2, output_tokens=1),
    )


def _client(*responses) -> JevSystemOneClient:
    client = JevSystemOneClient(api_key="test-key", http_client=AsyncMock(spec=httpx.AsyncClient), max_retries=0)
    client.system_one = AsyncMock(side_effect=responses or None, return_value=_response())
    client.aclose = AsyncMock()
    return client


def _config(client: JevSystemOneClient, reference=None, **routing) -> MoAConfig:
    return MoAConfig(
        mode="jev_routed",
        reference_models=(reference or model(),),
        jev=JevConfig(client=client, **routing),
    )


def _step(index: int, *tool_names: str) -> AssistantMessage:
    return AssistantMessage(
        content=f"step {index}",
        tool_calls=[
            ToolCall(id=f"call-{index}-{offset}", type="function", name=name, arguments='{"value": "result"}')
            for offset, name in enumerate(tool_names or ("echo",))
        ],
    )


def test_jev_config_defaults_are_frozen_keyword_only_and_keep_client_identity():
    client = _client()
    config = JevConfig(client=client)
    assert (config.interval, config.recent_messages, config.timeout) == (3, 5, 30.0)
    assert config.max_jev_calls is config.max_reference_calls is None
    assert config.client is client
    with pytest.raises(FrozenInstanceError):
        config.interval = 1
    with pytest.raises(TypeError):
        JevConfig(client)
    assert replace(config, interval=1).client is client


@pytest.mark.parametrize("field", ["interval", "recent_messages"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3", None])
def test_jev_config_rejects_nonpositive_or_noninteger_cadence(field, value):
    with pytest.raises(BaseError) as error:
        JevConfig(client=_client(), **{field: value})
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.parametrize("value", [0, -0.1, float("nan"), float("inf"), True, "3", None])
def test_jev_config_requires_finite_positive_timeout(value):
    with pytest.raises(BaseError) as error:
        JevConfig(client=_client(), timeout=value)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.parametrize("field", ["max_jev_calls", "max_reference_calls"])
@pytest.mark.parametrize("value", [-1, True, 0.5, "3"])
def test_jev_config_rejects_invalid_budgets(field, value):
    with pytest.raises(BaseError) as error:
        JevConfig(client=_client(), **{field: value})
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.parametrize("client", [None, object()])
def test_jev_config_requires_a_constructed_core_client(client):
    with pytest.raises(BaseError) as error:
        JevConfig(client=client)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"jev": object()},
        {"scorer": AsyncMock(return_value=0.5)},
        {"verifier": VerifierConfig(interval=1)},
        {"verifier": VerifierConfig(max_verifier_calls=0)},
        {"verifier": VerifierConfig(max_reference_calls=0)},
    ],
)
def test_jev_mode_requires_jev_and_rejects_verifier_settings(arguments):
    options = {"jev": JevConfig(client=_client()), **arguments}
    if not arguments:
        options = {}
    with pytest.raises(BaseError):
        MoAConfig(mode="jev_routed", reference_models=(model(),), **options)


@pytest.mark.parametrize("mode", ["aggregator_on_demand", "verifier_routed"])
def test_other_modes_reject_jev_configuration(mode):
    with pytest.raises(BaseError):
        MoAConfig(
            mode=mode,
            reference_models=(model(),),
            scorer=lambda *_: 0.5,
            jev=JevConfig(client=_client()),
        )


def test_explicit_default_verifier_is_compatible_with_jev_mode():
    client = _client()
    config = MoAConfig(
        mode="jev_routed", reference_models=(model(),), jev=JevConfig(client=client), verifier=VerifierConfig()
    )
    assert config.jev.client is client


def test_jev_spec_build_keeps_runtime_client_identity_and_serialized_spec_unchanged():
    client = _client()
    config = _config(client)
    spec = _spec()
    serialized = spec.model_dump_json()
    context = BuildContext(moa=config)
    deep = spec.build(context)
    assert deep.deep_config.moa is config
    assert deep.deep_config.moa.jev.client is client
    assert context.moa is config
    assert spec.model_dump_json() == serialized
    assert "moa" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("consult", [False, True])
async def test_initial_decision_controls_first_call_without_verifier_fallback(consult, monkeypatch):
    scorer = AsyncMock(side_effect=AssertionError("JEV must not run verifier scoring"))
    monkeypatch.setattr("openjiuwen.harness.rails.moa_rail.score_progress", scorer)
    client = _client(_response(consult))
    primary_model = primary(AssistantMessage(content="done"))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="private JEV proposal"))
    deep = agent(primary_model, _config(client, reference))
    result = await deep.invoke("task", session=Session(session_id="jev-initial", card=deep.card))
    assert result["output"] == "done"
    client.system_one.assert_awaited_once()
    scorer.assert_not_awaited()
    assert reference.invoke.await_count == int(consult)
    assert ("private JEV proposal" in str(primary_model.invoke.await_args.kwargs["messages"])) is consult
    assert "private JEV proposal" not in str(
        deep.react_agent.context_engine.get_context(session_id="jev-initial").get_messages()
    )
    client.aclose.assert_not_awaited()
    assert get_moa_snapshot() is None


@pytest.mark.asyncio
async def test_jev_checks_every_three_completed_turns_and_delivers_to_next_turn():
    client = _client(_response(), _response(True), _response(True))
    primary_model = primary(*[_step(i) for i in range(1, 7)], AssistantMessage(content="done"))
    reference = model()
    reference.invoke = AsyncMock(
        side_effect=[AssistantMessage(content="third-turn proposal"), AssistantMessage(content="sixth-turn proposal")]
    )
    deep = agent(primary_model, _config(client, reference), tools=[Echo()])
    await deep.invoke("task", session=Session(session_id="jev-cadence", card=deep.card))
    assert client.system_one.await_count == 3
    assert reference.invoke.await_count == 2
    requests = [str(call.kwargs["messages"]) for call in primary_model.invoke.await_args_list]
    assert ["third-turn proposal" in request for request in requests] == [
        False,
        False,
        False,
        True,
        False,
        False,
        False,
    ]
    assert ["sixth-turn proposal" in request for request in requests] == [False] * 6 + [True]
    third_state = client.system_one.await_args_list[1].kwargs["state"]
    assert [message["role"] for message in third_state["messages"]] == [
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert [message["content"] for message in third_state["messages"] if message["role"] == "assistant"] == [
        "step 2",
        "step 3",
    ]
    assistant = third_state["messages"][-2]
    call = assistant["tool_calls"][0]
    assert call["id"] == "call-3-0"
    assert call.get("name", call.get("function", {}).get("name")) == "echo"
    assert call.get("arguments", call.get("function", {}).get("arguments")) == '{"value": "result"}'
    assert third_state["messages"][-1]["tool_call_id"] == "call-3-0"
    assert "third-turn proposal" not in str(client.system_one.await_args_list[2].kwargs["state"])


@pytest.mark.asyncio
async def test_parallel_tool_batch_counts_as_one_completed_turn():
    client = _client()
    primary_model = primary(_step(1, "echo", "echo"), _step(2), AssistantMessage(content="done"))
    deep = agent(primary_model, _config(client, interval=2), tools=[Echo()])
    await deep.invoke("task", session=Session(card=deep.card))
    assert client.system_one.await_count == 2


def test_state_uses_recent_text_messages_and_detaches_tool_and_todo_data():
    calls = [
        ToolCall(id="tool-1", type="function", name="echo", arguments='{"token": "secret123", "value": "original"}')
    ]
    assistant = AssistantMessage(content="working", tool_calls=calls)
    todo = TodoItem(id="investigate", content="Investigate token=secret123", status=TodoStatus.IN_PROGRESS)
    messages = [
        UserMessage(content="old"),
        SystemMessage(content="system-private"),
        UserMessage(content="reference-private", metadata={"transient": True}),
        UserMessage(content=[{"type": "text", "text": "visible text"}, {"type": "image_url", "image_url": "secret"}]),
        assistant,
        ToolMessage(content='{"password": "result-secret", "value": "result"}', name="echo", tool_call_id="tool-1"),
    ]
    before = [message.model_dump() for message in messages]
    state = build_jev_state(messages, 3, [todo])
    assert [message["role"] for message in state["messages"]] == ["user", "assistant", "tool"]
    assert "visible text" in state["messages"][0]["content"]
    assert "image_url" not in str(state)
    assert "system-private" not in str(state) and "reference-private" not in str(state)
    assert "secret123" not in str(state) and "[redacted]" in str(state)
    assert "result-secret" not in str(state)
    assert json.loads(state["messages"][1]["tool_calls"][0]["arguments"]) == {
        "token": "[redacted]",
        "value": "original",
    }
    assert json.loads(state["messages"][2]["content"]) == {"password": "[redacted]", "value": "result"}
    assert state["todos"][0]["status"] == "in_progress"
    assert before == [message.model_dump() for message in messages]
    assistant.tool_calls[0].name = "changed"
    todo.status = TodoStatus.COMPLETED
    assert "changed" not in str(state)
    assert state["todos"][0]["status"] == "in_progress"
    assert before[-1] == messages[-1].model_dump()


def test_unnamed_tool_results_recover_names_when_assistant_is_outside_latest_k():
    calls = [ToolCall(id=f"call-{index}", type="function", name=f"tool-{index}", arguments="{}") for index in range(6)]
    messages = [
        AssistantMessage(content="run tools", tool_calls=calls),
        *[ToolMessage(content=f"result-{index}", tool_call_id=f"call-{index}") for index in range(6)],
    ]
    before = [message.model_dump() for message in messages]
    state = build_jev_state(messages, 5)
    assert len(state["messages"]) == 5
    assert [message["role"] for message in state["messages"]] == ["tool"] * 5
    assert [message["tool_call_id"] for message in state["messages"]] == [f"call-{index}" for index in range(1, 6)]
    assert [message["name"] for message in state["messages"]] == [f"tool-{index}" for index in range(1, 6)]
    assert [message["content"] for message in state["messages"]] == [f"result-{index}" for index in range(1, 6)]
    assert before == [message.model_dump() for message in messages]


@pytest.mark.asyncio
async def test_k_limits_only_decision_input_and_reference_keeps_consultation_context():
    client = _client(_response(), _response(True))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="context proposal"))
    deep = agent(
        primary(_step(1), AssistantMessage(content="done")),
        _config(client, reference, interval=1, recent_messages=1),
        tools=[Echo()],
    )
    await deep.invoke("original task", session=Session(card=deep.card))
    periodic_messages = client.system_one.await_args.kwargs["state"]["messages"]
    assert len(periodic_messages) == 1 and periodic_messages[0]["role"] == "tool"
    assert periodic_messages[0]["name"] == "echo"
    reference_input = str(reference.invoke.await_args.kwargs["messages"])
    assert "original task" in reference_input and "step 1" in reference_input


@pytest.mark.asyncio
async def test_real_core_client_receives_typed_question_and_preserves_wire_context():
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_response(True).model_dump())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = JevSystemOneClient(api_key="test-key", http_client=http, max_retries=0, model_name="jev-test")
        original = client.system_one
        client.system_one = AsyncMock(wraps=original)
        primary_model = primary(AssistantMessage(content="done"))
        reference = model()
        reference.invoke = AsyncMock(return_value=AssistantMessage(content="wire proposal"))
        deep = agent(primary_model, _config(client, reference))
        await deep.invoke("wire task", session=Session(card=deep.card))
        question = client.system_one.await_args.kwargs["questions"]["consult_reference"]
        assert isinstance(question, ChoiceQuestion)
        assert set(question.criteria) == {"CONSULT", "CONTINUE"}
        assert requests[0]["model"] == "jev-test"
        assert requests[0]["questions"]["consult_reference"]["type"] == "choice"
        assert requests[0]["state"] == {"messages": [{"role": "user", "content": "wire task"}]}
        assert not http.is_closed


@pytest.mark.asyncio
async def test_choice_near_half_confidence_still_consults_without_extra_threshold():
    client = _client(_response(True, confidence=0.501, probabilities={"CONSULT": 0.501, "CONTINUE": 0.499}))
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="proposal"))
    deep = agent(primary(AssistantMessage(content="done")), _config(client, reference))
    await deep.invoke("task", session=Session(card=deep.card))
    reference.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        {"answers": {"consult_reference": {"choice": "CONSULT"}}},
        _response(True, choice="UNKNOWN"),
        _response(True, probabilities={"CONSULT": 1.0}),
        _response(True, probabilities={"CONSULT": 0.9, "CONTINUE": 0.9}),
        _response(True, probabilities={"CONSULT": -0.1, "CONTINUE": 1.1}),
        _response(True, probabilities={"CONSULT": 0.1, "CONTINUE": 0.9}),
        _response(True, confidence=float("nan")),
        _response(True, confidence=float("inf")),
        _response(True, confidence=True),
        _response(True, probabilities={"CONSULT": True, "CONTINUE": False}),
        _response(True, probabilities={"CONSULT": 10**1000, "CONTINUE": 0}),
        _response().model_copy(update={"answers": {"consult_reference": NoulAnswer(noul=1.0)}}),
        _response().model_copy(update={"answers": {"unrelated": _response().answers["consult_reference"]}}),
    ],
)
async def test_invalid_jev_response_continues_without_reference_or_verifier(response, monkeypatch):
    scorer = AsyncMock(side_effect=AssertionError("invalid JEV must not fall back to verifier"))
    monkeypatch.setattr("openjiuwen.harness.rails.moa_rail.score_progress", scorer)
    client = _client(response)
    reference = model()
    reference.invoke = AsyncMock()
    deep = agent(primary(AssistantMessage(content="done")), _config(client, reference))
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    reference.invoke.assert_not_awaited()
    scorer.assert_not_awaited()
    client.system_one.assert_awaited_once()


@pytest.mark.parametrize("response", [None, {}, _response(choice=[]), _response(probabilities=None)])
def test_invalid_jev_response_raises_framework_input_error(response):
    with pytest.raises(BaseError) as error:
        _decode_decision(response)
    assert error.value.status is StatusCode.DEEPAGENT_INPUT_PARAM_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, reason",
    [
        (ValueError("transport failed"), "jev_request_failed"),
        (TimeoutError(), "jev_timeout"),
        (None, "invalid_jev_response"),
        (_response(choice="INVALID"), "invalid_jev_response"),
        (_response(confidence=10**1000), "invalid_jev_response"),
    ],
)
async def test_request_and_decode_failures_keep_distinct_jev_reasons(response, reason):
    state = MoAState()
    config = JevConfig(client=_client(response))
    assert await decide_reference(config, state, [], None, None) is None
    assert state.last_jev_reason == reason
    assert state.jev_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [_response(), RuntimeError("offline"), None])
async def test_retry_does_not_repeat_initial_false_or_failed_decision(initial):
    class RetryOnce(DeepAgentRail):
        async def on_model_exception(self, ctx):
            if ctx.retry_attempt == 0:
                ctx.request_retry()

    client = _client(initial)
    primary_model = primary(RuntimeError("retry"), AssistantMessage(content="done"))
    reference = model()
    reference.invoke = AsyncMock()
    deep = agent(primary_model, _config(client, reference), rails=[RetryOnce()])
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    assert primary_model.invoke.await_count == 2
    client.system_one.assert_awaited_once()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_only_continuation_does_not_repeat_initial_decision():
    class ContinueOnce(DeepAgentRail):
        async def after_model_call(self, ctx):
            if ctx.inputs.react_iteration == 1:
                ctx.request_model_continue()

    client = _client(_response())
    deep = agent(
        primary(AssistantMessage(content="draft"), AssistantMessage(content="done")),
        _config(client),
        rails=[ContinueOnce()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    client.system_one.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("routing", [{"max_jev_calls": 0}, {"max_reference_calls": 0}])
async def test_zero_budget_skips_jev_and_reference(routing):
    client = _client(_response(True))
    reference = model()
    reference.invoke = AsyncMock()
    deep = agent(
        primary(_step(1), AssistantMessage(content="done")),
        _config(client, reference, interval=1, **routing),
        tools=[Echo()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    client.system_one.assert_not_awaited()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_attempt_consumes_decision_budget_and_stops_future_checkpoints():
    client = _client(RuntimeError("offline"))
    reference = model()
    reference.invoke = AsyncMock()
    deep = agent(
        primary(_step(1), _step(2), AssistantMessage(content="done")),
        _config(client, reference, interval=1, max_jev_calls=1),
        tools=[Echo()],
    )
    await deep.invoke("task", session=Session(card=deep.card))
    client.system_one.assert_awaited_once()
    reference.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_response", [AssistantMessage(content="proposal"), RuntimeError("offline")])
async def test_reference_attempt_budget_stops_jev_even_when_reference_failed(reference_response):
    client = _client(_response(True))
    reference = model()
    reference.invoke = AsyncMock(side_effect=[reference_response])
    deep = agent(
        primary(_step(1), _step(2), AssistantMessage(content="done")),
        _config(client, reference, interval=1, max_reference_calls=1),
        tools=[Echo()],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    client.system_one.assert_awaited_once()
    reference.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_continues_and_next_checkpoint_can_consult():
    client = _client()
    cancelled = asyncio.Event()
    calls = 0

    async def decide(**kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        if calls == 1:
            try:
                await asyncio.sleep(3600)
            finally:
                cancelled.set()
        return _response(True)

    client.system_one = AsyncMock(side_effect=decide)
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="after timeout"))
    primary_model = primary(_step(1), AssistantMessage(content="done"))
    deep = agent(primary_model, _config(client, reference, interval=1, timeout=0.02), tools=[Echo()])
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done" and cancelled.is_set()
    assert client.system_one.await_count == 2
    assert "after timeout" not in str(primary_model.invoke.await_args_list[0].kwargs["messages"])
    assert "after timeout" in str(primary_model.invoke.await_args_list[1].kwargs["messages"])


class _MemoryTodo(TodoTool):
    """Registered native todo instance with deterministic in-memory persistence."""

    def __init__(self, todos):
        super().__init__(
            ToolCard(
                id="jev-memory-todo", name="native_todo", description="Update todo", input_params={"type": "object"}
            ),
            SysOperation(SysOperationCard(id="jev-memory-todo-operation")),
        )
        self.todos = todos
        self.load_todos = AsyncMock(side_effect=lambda _session_id: self.todos)

    async def invoke(self, inputs, **kwargs):
        del inputs, kwargs
        self.todos[0].status = TodoStatus.COMPLETED
        return {"message": "todo completed"}


@pytest.mark.asyncio
async def test_timeout_bounds_todo_read_and_spends_one_decision_attempt():
    class CaptureSnapshot(DeepAgentRail):
        def __init__(self):
            super().__init__()
            self.snapshots = []

        async def after_model_call(self, ctx):
            del ctx
            self.snapshots.append(get_moa_snapshot())

    cancelled = asyncio.Event()
    todo = _MemoryTodo([])

    async def stalled(_session_id):
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    todo.load_todos.side_effect = stalled
    client = _client()
    capture = CaptureSnapshot()
    deep = agent(
        primary(AssistantMessage(content="done")),
        _config(client, timeout=0.02, max_jev_calls=1),
        tools=[todo],
        rails=[capture],
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done" and cancelled.is_set()
    client.system_one.assert_not_awaited()
    snapshot = capture.snapshots[0]
    assert snapshot.jev_calls == 1 and snapshot.verifier_calls == 0
    assert snapshot.last_jev_decision is None and snapshot.last_jev_reason == "jev_timeout"


@pytest.mark.asyncio
async def test_todo_input_reads_current_registered_native_state_each_checkpoint():
    todo = _MemoryTodo([TodoItem(id="check", content="Check result", status=TodoStatus.PENDING)])
    client = _client()
    deep = agent(
        primary(_step(1, "native_todo"), AssistantMessage(content="done")),
        _config(client, interval=1),
        tools=[todo],
    )
    await deep.invoke("task", session=Session(session_id="jev-todo", card=deep.card))
    states = [call.kwargs["state"] for call in client.system_one.await_args_list]
    assert states[0]["todos"][0]["status"] == "pending"
    assert states[1]["todos"][0]["status"] == "completed"
    assert [call.args for call in todo.load_todos.await_args_list] == [("jev-todo",), ("jev-todo",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "unreadable"])
async def test_missing_todo_state_uses_messages_only(kind):
    todo = _MemoryTodo([])
    if kind == "unreadable":
        todo.load_todos.side_effect = RuntimeError("cannot read")
    client = _client()
    deep = agent(primary(AssistantMessage(content="done")), _config(client), tools=[todo])
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    assert set(client.system_one.await_args.kwargs["state"]) == {"messages"}
    todo.load_todos.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_stream_delivers_periodic_proposal_once_and_keeps_history_private():
    client = _client(_response(), _response(True), _response())
    primary_model = model()
    requests = []
    outputs = [_step(1), _step(2), AssistantMessage(content="done")]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        output = outputs.pop(0)
        yield AssistantMessageChunk(content=output.content, tool_calls=output.tool_calls)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="stream proposal"))
    deep = agent(primary_model, _config(client, reference, interval=1), tools=[Echo()])
    chunks = [chunk async for chunk in deep.stream("task", session=Session(session_id="jev-stream", card=deep.card))]
    assert chunks and client.system_one.await_count == 3
    assert ["stream proposal" in str(request) for request in requests] == [False, True, False]
    assert "stream proposal" not in str(
        deep.react_agent.context_engine.get_context(session_id="jev-stream").get_messages()
    )
    client.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_loop_keeps_cadence_across_inner_iterations():
    class FollowUpOnce(DeepAgentRail):
        async def after_task_iteration(self, ctx):
            if ctx.inputs.iteration == 1:
                await ctx.agent.follow_up("continue", session=ctx.session)

    client = _client(_response(), _response(True))
    primary_model = model()
    requests = []
    outputs = [_step(1), AssistantMessage(content="first done"), _step(2), AssistantMessage(content="done")]

    async def stream(messages, **kwargs):
        del kwargs
        requests.append(messages)
        output = outputs.pop(0)
        yield AssistantMessageChunk(content=output.content, tool_calls=output.tool_calls)

    primary_model.stream = stream
    reference = model()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content="second-inner proposal"))
    deep = agent(
        primary_model,
        _config(client, reference, interval=2),
        tools=[Echo()],
        rails=[FollowUpOnce()],
        enable_task_loop=True,
    )
    result = await deep.invoke("task", session=Session(card=deep.card))
    assert result["output"] == "done"
    assert client.system_one.await_count == 2
    assert ["second-inner proposal" in str(request) for request in requests] == [False, False, False, True]


@pytest.mark.asyncio
async def test_new_outer_invocation_resets_initial_check_and_budgets_on_same_session():
    client = _client(_response(True), _response(True))
    reference = model()
    reference.invoke = AsyncMock(
        side_effect=[AssistantMessage(content="first proposal"), AssistantMessage(content="fresh proposal")]
    )
    primary_model = primary(AssistantMessage(content="one"), AssistantMessage(content="two"))
    deep = agent(primary_model, _config(client, reference, max_jev_calls=1, max_reference_calls=1))
    session = Session(session_id="jev-reused", card=deep.card)
    await deep.invoke("first task", session=session)
    await deep.invoke("second task", session=session)
    assert client.system_one.await_count == reference.invoke.await_count == 2
    assert "fresh proposal" in str(primary_model.invoke.await_args_list[1].kwargs["messages"])
    assert "first proposal" not in str(primary_model.invoke.await_args_list[1].kwargs["messages"])


@pytest.mark.asyncio
async def test_cancelled_jev_propagates_and_next_invocation_starts_fresh():
    started = asyncio.Event()
    client = _client()

    async def wait_for_cancel(**kwargs):
        del kwargs
        started.set()
        await asyncio.sleep(3600)

    client.system_one = AsyncMock(side_effect=wait_for_cancel)
    primary_model = primary(AssistantMessage(content="fresh"))
    deep = agent(primary_model, _config(client, max_jev_calls=1))
    task = asyncio.create_task(deep.invoke("cancel", session=Session(session_id="jev-cancel", card=deep.card)))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    primary_model.invoke.assert_not_awaited()
    assert get_moa_snapshot() is None
    client.system_one = AsyncMock(return_value=_response())
    result = await deep.invoke("fresh task", session=Session(session_id="jev-fresh", card=deep.card))
    assert result["output"] == "fresh"
    client.system_one.assert_awaited_once()
    client.aclose.assert_not_awaited()
