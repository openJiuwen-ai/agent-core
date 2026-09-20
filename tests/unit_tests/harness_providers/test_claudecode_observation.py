# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Code model-request observation tests driven by a fake SDK and receiver."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from openjiuwen.harness_protocol import (
    HarnessEvent,
    HarnessInput,
    HostCapability,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    ModelRequestEvent,
    TurnLifecycleEvent,
)
from openjiuwen.harness_providers.claudecode import ClaudeCodeHarness, ClaudeCodeHarnessConfig
from openjiuwen.harness_providers.claudecode import observation as observation_module
from openjiuwen.harness_providers.telemetry.otlp_receiver import OTEL_RESOURCE_SOURCE_ID
from tests.test_logger import logger
from tests.unit_tests.harness_providers.test_claudecode import _context, _install_fake_sdk, _result, _turn

_OBSERVED = frozenset({HostCapability.MODEL_REQUEST_OBSERVATION})


class _FakeReceiver:
    """Shared-receiver stand-in delivering log events synchronously."""

    def __init__(self, *, grpc_endpoint: str | None = "http://127.0.0.1:4317") -> None:
        self.grpc_endpoint = grpc_endpoint
        self.endpoint = "http://127.0.0.1:4318/v1/traces"
        self.subscribers: dict[int, Callable[[dict[str, Any]], None]] = {}

    async def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> int | None:
        subscriber_id = len(self.subscribers) + 1
        self.subscribers[subscriber_id] = callback
        return subscriber_id

    def unsubscribe(self, subscriber_id: int) -> None:
        self.subscribers.pop(subscriber_id, None)

    def publish(self, event: dict[str, Any]) -> None:
        for callback in tuple(self.subscribers.values()):
            callback(event)


def _install_receiver(monkeypatch: pytest.MonkeyPatch, receiver: _FakeReceiver) -> None:
    monkeypatch.setattr(observation_module, "get_shared_otlp_receiver", lambda: receiver)


def _source_id(client: Any) -> str:
    return client.options.env["OTEL_RESOURCE_ATTRIBUTES"].split("=", 1)[1]


def _body_event(
    receiver: _FakeReceiver,
    client: Any,
    name: str,
    body: dict[str, Any],
    *,
    time_ns: int,
    request_id: str | None = None,
) -> None:
    """Write one body file where the CLI would and publish its log event."""
    body_dir = Path(client.options.env["OTEL_LOG_RAW_API_BODIES"].removeprefix("file:"))
    reference = f"{uuid.uuid4().hex}.json"
    (body_dir / reference).write_text(json.dumps(body), encoding="utf-8")
    attributes: dict[str, Any] = {"body_ref": reference}
    if request_id is not None:
        attributes["request_id"] = request_id
    receiver.publish(
        {
            "signal": "log",
            "name": name,
            "time_ns": time_ns,
            "attributes": attributes,
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: _source_id(client)},
        }
    )


def _tool_events(
    receiver: _FakeReceiver,
    client: Any,
    *,
    tool_use_id: str,
    start_ns: int,
    end_ns: int,
    success: bool,
) -> None:
    """Publish what the CLI states about one tool call."""
    source = _source_id(client)
    receiver.publish(
        {
            "signal": "trace",
            "name": "claude_code.tool",
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
            "attributes": {"tool_use_id": tool_use_id, "tool_name": "Bash"},
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: source},
        }
    )
    receiver.publish(
        {
            "signal": "log",
            "name": "claude_code.tool_result",
            "time_ns": end_ns,
            "attributes": {"tool_use_id": tool_use_id, "success": success, "duration_ms": (end_ns - start_ns) // 1_000_000},
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: source},
        }
    )
    receiver.publish(
        {
            "signal": "log",
            "name": "claude_code.tool_decision",
            "time_ns": start_ns,
            "attributes": {"tool_use_id": tool_use_id, "decision": "accept", "source": "config"},
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: source},
        }
    )


def _api_request_event(receiver: _FakeReceiver, client: Any, *, request_id: str) -> None:
    """Publish the CLI's own accounting of one model request."""
    receiver.publish(
        {
            "signal": "log",
            "name": "claude_code.api_request",
            "time_ns": 1_002_100_000_000,
            "attributes": {
                "request_id": request_id,
                "cost_usd_micros": 33228,
                "effort": "high",
                "speed": "normal",
                "query_source": "sdk",
            },
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: _source_id(client)},
        }
    )


def _request_span(receiver: _FakeReceiver, client: Any, *, request_id: str, start_ns: int, end_ns: int) -> None:
    """Publish the CLI's own per-request span."""
    receiver.publish(
        {
            "signal": "trace",
            "name": "claude_code.llm_request",
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
            "attributes": {"request_id": request_id, "ttft_ms": 400, "attempt": 1, "speed": "normal", "success": True},
            "resource_attributes": {OTEL_RESOURCE_SOURCE_ID: _source_id(client)},
        }
    )


_SYSTEM = [
    # Claude Code's own request metadata, which changes on every request.
    {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1; cc_prompt_id=p-1;"},
    {"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}},
]
_TOOLS = [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}]
# One Claude Code user turn: the CLI's own reminder, a control payload and
# what the host actually said.
_USER = {
    "role": "user",
    "content": [
        {"type": "text", "text": "<system-reminder>be careful</system-reminder>"},
        {"type": "tool_addition", "tool": {"type": "tool_reference", "name": "Bash"}},
        {"type": "text", "text": "list files"},
    ],
}
_FIRST_REPLY = [{"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls"}}]
_TOOL_RESULT = {
    "role": "user",
    "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "a.py", "cache_control": {"type": "ephemeral"}}],
}


def _script(sdk: ModuleType, receiver: _FakeReceiver) -> list[Any]:
    async def first_request(client: Any) -> None:
        body = {
            "model": "claude-x",
            "system": _SYSTEM,
            "tools": _TOOLS,
            "messages": [_USER],
            "max_tokens": 4096,
            "temperature": 0.2,
            "stream": True,
        }
        _body_event(receiver, client, "claude_code.api_request_body", body, time_ns=1_000_000_000_000)

    async def first_response(client: Any) -> None:
        body = {
            "id": "msg-1",
            "model": "claude-x",
            "content": _FIRST_REPLY,
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 30,
                "output_tokens": 4,
                "cache_read_input_tokens": 12,
                "cache_creation_input_tokens": 8,
            },
        }
        _body_event(
            receiver,
            client,
            "claude_code.api_response_body",
            body,
            time_ns=1_002_000_000_000,
            request_id="req-1",
        )
        _request_span(receiver, client, request_id="req-1", start_ns=1_000_100_000_000, end_ns=1_001_900_000_000)
        _api_request_event(receiver, client, request_id="req-1")

    async def side_query(client: Any) -> None:
        body = {"model": "claude-haiku", "messages": [{"role": "user", "content": "title this"}]}
        _body_event(receiver, client, "claude_code.api_request_body", body, time_ns=1_003_500_000_000)

    async def tool_facts(client: Any) -> None:
        _tool_events(
            receiver,
            client,
            tool_use_id="tool-1",
            start_ns=1_002_100_000_000,
            end_ns=1_002_600_000_000,
            success=True,
        )

    async def second_request(client: Any) -> None:
        history = [_USER, {"role": "assistant", "content": _FIRST_REPLY}, _TOOL_RESULT]
        body = {"model": "claude-x", "system": _SYSTEM, "tools": _TOOLS, "messages": history}
        _body_event(receiver, client, "claude_code.api_request_body", body, time_ns=1_003_000_000_000)

    async def second_response(client: Any) -> None:
        body = {
            "id": "msg-2",
            "model": "claude-x",
            "content": [{"type": "text", "text": "a.py"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 40, "output_tokens": 2},
        }
        _body_event(receiver, client, "claude_code.api_response_body", body, time_ns=1_004_000_000_000)

    return [
        sdk.StreamEvent(uuid="s1", session_id="s", event={"type": "message_start"}, parent_tool_use_id=None),
        first_request,
        sdk.AssistantMessage(
            content=[sdk.ToolUseBlock(id="tool-1", name="Bash", input={"command": "ls"})],
            model="claude-x",
            parent_tool_use_id=None,
            error=None,
            usage=None,
            message_id="msg-1",
            stop_reason="tool_use",
            session_id="s",
        ),
        first_response,
        sdk.UserMessage(
            content=[sdk.ToolResultBlock(tool_use_id="tool-1", content="a.py", is_error=False)],
            uuid="um-1",
            parent_tool_use_id=None,
            tool_use_result=None,
        ),
        tool_facts,
        second_request,
        side_query,
        sdk.AssistantMessage(
            content=[sdk.TextBlock(text="a.py")],
            model="claude-x",
            parent_tool_use_id=None,
            error=None,
            usage=None,
            message_id="msg-2",
            stop_reason="end_turn",
            session_id="s",
        ),
        second_response,
        _result(sdk, result="a.py"),
    ]


def _kinds(events: list[HarnessEvent]) -> list[str]:
    result: list[str] = []
    for event in events:
        payload = event.event
        if isinstance(payload, ModelRequestEvent):
            result.append(f"request:{payload.request_id}")
        elif isinstance(payload, ItemLifecycleEvent):
            result.append(f"tool:{event.item_id}:{payload.kind.value}")
        elif isinstance(payload, TurnLifecycleEvent):
            result.append(f"turn:{payload.kind.value}")
    return result


@pytest.mark.asyncio
async def test_request_logs_become_ordered_model_request_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    receiver = _FakeReceiver()
    _install_receiver(monkeypatch, receiver)
    state.scripts.append(_script(sdk, receiver))
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False, cwd="/tmp"))
    await harness.start(_context(host_capabilities=_OBSERVED))
    client = state.clients[0]
    body_dir = Path(client.options.env["OTEL_LOG_RAW_API_BODIES"].removeprefix("file:"))
    assert client.options.env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4317"
    assert OTEL_RESOURCE_SOURCE_ID in json.loads(client.options.settings)["env"]["OTEL_RESOURCE_ATTRIBUTES"]

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)
    logger.info("observed claude events: {}", _kinds(events))

    assert _kinds(events) == [
        "turn:started",
        "request:msg-1",
        "tool:tool-1:started",
        "tool:tool-1:completed",
        "request:msg-2",
        "turn:finished",
    ]
    requests = [event.event for event in events if isinstance(event.event, ModelRequestEvent)]
    first, second = requests
    assert first.input_observed and second.input_observed
    # The billing header is request metadata, not part of the instructions.
    assert [block.content for block in first.system_instructions] == ["You are Claude Code."]
    assert first.data["claude-code"]["billing_header"].startswith("x-anthropic-billing-header:")
    assert first.tool_definitions == ({"name": "Bash", "description": "run", "parameters": {"type": "object"}},)
    assert first.request_parameters == {
        "max_tokens": 4096,
        "temperature": 0.2,
        "stream": True,
        "reasoning_level": "high",
    }
    assert first.response_id == "msg-1" and first.finish_reasons == ("tool_use",)
    # The CLI's own span states the request window and its first-token time.
    assert (first.started_at, first.ended_at) == (1000.1, 1001.9)
    assert first.time_to_first_chunk == 0.4
    assert first.data["claude-code"]["attempt"] == 1
    assert first.data["claude-code"]["api_request_id"] == "req-1"
    # GenAI states the whole prompt as input, with cached input a breakdown.
    assert first.usage.input_tokens == 50 and first.usage.cached_input_tokens == 12
    assert first.usage.total_tokens == 54
    assert [block.kind for block in first.output_message.content] == ["tool_call"]
    assert first.output_message.content[0].data["call_id"] == "tool-1"
    # Each block of a user turn is its own statement; the CLI's control payload
    # states nothing its notice text does not.
    assert [message.role for message in first.input_messages] == [MessageRole.USER, MessageRole.USER]
    assert [block.content for message in first.input_messages for block in message.content] == [
        "<system-reminder>be careful</system-reminder>",
        "list files",
    ]
    assert [message.role for message in second.input_messages] == [
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    # The earlier reply keeps its message id inside the next request's history.
    assert second.input_messages[2].message_id == "msg-1"
    assert second.input_messages[0].message_id == first.input_messages[0].message_id
    tool_started = next(event for event in events if _kinds([event]) == ["tool:tool-1:started"])
    assert "msg-1" in tool_started.causation_ids
    assert isinstance(tool_started.event, ItemLifecycleEvent) and tool_started.event.kind is ItemEventKind.STARTED
    # The CLI's own tool span states the window and the outcome.
    tool_completed = next(event for event in events if _kinds([event]) == ["tool:tool-1:completed"])
    assert (tool_started.timestamp, tool_completed.timestamp) == (1002.1, 1002.6)
    assert tool_completed.event.data["is_error"] is False
    assert tool_completed.event.data["decision"] == "accept"
    assert first.cost is not None and first.cost.micros == 33228
    assert first.data["claude-code"]["query_source"] == "sdk"

    await harness.stop()
    assert not body_dir.exists()
    assert not receiver.subscribers


@pytest.mark.asyncio
async def test_missing_request_logs_fall_back_to_the_sdk_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    receiver = _FakeReceiver()
    _install_receiver(monkeypatch, receiver)
    state.scripts.append(
        [
            sdk.AssistantMessage(
                content=[sdk.ThinkingBlock(thinking="plan"), sdk.TextBlock(text="done")],
                model="claude-x",
                parent_tool_use_id=None,
                error=None,
                usage={"input_tokens": 9, "output_tokens": 3},
                message_id="msg-1",
                stop_reason="end_turn",
                session_id="s",
            ),
            _result(sdk, result="done"),
        ]
    )
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(inherit_process_env=False, cwd="/tmp", request_observation_wait_s=0.05),
    )
    await harness.start(_context(host_capabilities=_OBSERVED))

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == ["turn:started", "request:msg-1", "turn:finished"]
    request = next(event.event for event in events if isinstance(event.event, ModelRequestEvent))
    assert not request.input_observed
    assert request.model == "claude-x"
    assert request.usage.input_tokens == 9
    assert [block.kind for block in request.output_message.content] == ["reasoning", "text"]
    assert request.data["claude-code"]["observation"] == "sdk_stream"
    await harness.stop()


@pytest.mark.asyncio
async def test_remote_transport_reports_requests_without_request_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    receiver = _FakeReceiver()
    _install_receiver(monkeypatch, receiver)
    state.scripts.append(
        [
            sdk.AssistantMessage(
                content=[sdk.ToolUseBlock(id="tool-1", name="Bash", input={})],
                model="claude-x",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-1",
                stop_reason="tool_use",
                session_id="s",
            ),
            _result(sdk),
        ]
    )
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(inherit_process_env=False, cwd="/tmp"),
        transport_factory=lambda options: object(),
    )
    await harness.start(_context(host_capabilities=_OBSERVED))
    assert "OTEL_LOG_RAW_API_BODIES" not in state.clients[0].options.env
    assert not receiver.subscribers

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == ["turn:started", "request:msg-1", "tool:tool-1:started", "turn:finished"]
    await harness.stop()


@pytest.mark.asyncio
async def test_hosts_without_model_request_observation_get_no_request_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    receiver = _FakeReceiver()
    _install_receiver(monkeypatch, receiver)
    state.scripts.append(_script(sdk, receiver)[2:3] + [_result(sdk)])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False, cwd="/tmp"))
    await harness.start(_context())
    assert "OTEL_LOG_RAW_API_BODIES" not in state.clients[0].options.env

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == ["turn:started", "tool:tool-1:started", "turn:finished"]
    await harness.stop()
