# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Codex model-request observation tests driven by a fake SDK and rollout reader."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from openjiuwen.harness_protocol import (
    HarnessEvent,
    HarnessInput,
    HostCapability,
    ItemLifecycleEvent,
    MessageRole,
    ModelRequestEvent,
    TurnLifecycleEvent,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.codex import CodexHarness, CodexHarnessConfig
from openjiuwen.harness_providers.codex import observation as observation_module
from tests.test_logger import logger
from tests.unit_tests.harness_providers.test_codex import (
    _Status,
    _context,
    _install_fake_sdk,
    _item,
    _notification,
    _turn,
    _turn_completed,
)

_OBSERVED = frozenset({HostCapability.MODEL_REQUEST_OBSERVATION})


class _FakeRolloutReader:
    """Rollout reader stand-in whose records the script delivers directly."""

    instances: list["_FakeRolloutReader"] = []

    def __init__(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self.callback = callback
        self.root = Path("/tmp/openjiuwen-codex-rollout-test")
        self.closed = False

    @classmethod
    async def start(cls, callback: Callable[[dict[str, Any]], None]) -> "_FakeRolloutReader":
        reader = cls(callback)
        cls.instances.append(reader)
        return reader

    async def aclose(self) -> None:
        self.closed = True


def _install_reader(monkeypatch: pytest.MonkeyPatch) -> type[_FakeRolloutReader]:
    _FakeRolloutReader.instances = []
    monkeypatch.setattr(observation_module, "CodexRolloutTraceReader", _FakeRolloutReader)
    return _FakeRolloutReader


def _record(event_type: str, **payload: Any) -> Callable[[Any], Any]:
    """Script step delivering one rollout record stamped with the current time."""
    resolved = payload.pop("resolved_payloads", {})

    async def deliver(_handle: Any) -> None:
        _FakeRolloutReader.instances[-1].callback(
            {
                "wall_time_unix_ms": int(time.time() * 1000),
                "thread_id": "thread-1",
                "codex_turn_id": "codex-turn-1",
                "payload": {"type": event_type, **payload},
                "resolved_payloads": resolved,
            }
        )
        return None

    return deliver


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


_SHELL_CALL = {"type": "function_call", "id": "fc-1", "call_id": "cmd-1", "name": "shell", "arguments": '{"command":"ls"}'}
_USER_INPUT = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list files"}]}
# Codex offers its tools as an input item instead of a request field.
_TOOL_CATALOGUE = {
    "type": "additional_tools",
    "id": "at-1",
    "role": "developer",
    "tools": [
        {
            "type": "namespace",
            "name": "functions",
            "tools": [{"type": "custom", "name": "shell", "description": "run", "parameters": {"type": "object"}}],
        },
    ],
}


def _command(**fields: Any) -> dict[str, Any]:
    return _item(id="cmd-1", type="commandExecution", command="ls", cwd="/w", **fields).__dict__


@pytest.mark.asyncio
async def test_rollout_inferences_become_ordered_model_request_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    reader_type = _install_reader(monkeypatch)
    state.scripts.append(
        [
            _record("codex_turn_started"),
            _record(
                "inference_started",
                inference_call_id="inf-1",
                model="gpt-test",
                provider_name="openai",
                resolved_payloads={
                    "request_payload": {
                        "instructions": "be brief",
                        "input": [_TOOL_CATALOGUE, _USER_INPUT],
                        "stream": True,
                        "reasoning": {"effort": "medium"},
                    },
                },
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-1",
                response_id="resp-1",
                resolved_payloads={
                    "response_payload": {
                        "output_items": [
                            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "look"}]},
                            _SHELL_CALL,
                        ],
                        "token_usage": {"input_tokens": 20, "cached_input_tokens": 5, "output_tokens": 3},
                    },
                },
            ),
            _notification("item/started", **_command()),
            _notification("item/completed", **_command(aggregated_output="a.py", status="completed", error=None)),
            _record(
                "inference_started",
                inference_call_id="inf-2",
                model="gpt-test",
                resolved_payloads={
                    "request_payload": {
                        "instructions": "be brief",
                        "input": [
                            _USER_INPUT,
                            _SHELL_CALL,
                            {"type": "function_call_output", "call_id": "cmd-1", "output": "a.py"},
                        ],
                    },
                },
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-2",
                response_id="resp-2",
                resolved_payloads={
                    "response_payload": {
                        "output_items": [
                            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a.py"}]},
                        ],
                    },
                },
            ),
            _notification("item/completed", **_item(id="msg-1", type="agentMessage", text="a.py").__dict__),
            _record("codex_turn_ended"),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w"))
    await harness.start(_context(host_capabilities=_OBSERVED))
    assert state.configs[0].kwargs["env"]["CODEX_ROLLOUT_TRACE_ROOT"] == "/tmp/openjiuwen-codex-rollout-test"

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)
    logger.info("observed codex events: {}", _kinds(events))

    assert _kinds(events) == [
        "turn:started",
        "request:inf-1",
        "tool:cmd-1:started",
        "tool:cmd-1:completed",
        "request:inf-2",
        "turn:finished",
    ]
    first, second = [event.event for event in events if isinstance(event.event, ModelRequestEvent)]
    assert first.input_observed and first.model == "gpt-test" and first.provider_name == "openai"
    assert [block.content for block in first.system_instructions] == ["be brief"]
    # The tool catalogue is a tool definition, not something the model said.
    assert first.tool_definitions == ({"name": "functions.shell", "description": "run", "parameters": {"type": "object"}},)
    assert first.request_parameters == {"stream": True, "reasoning_level": "medium"}
    assert first.response_id == "resp-1"
    assert [message.role for message in first.input_messages] == [MessageRole.USER]
    assert first.usage.input_tokens == 20 and first.usage.cached_input_tokens == 5
    assert [block.kind for block in first.output_message.content] == ["reasoning", "tool_call"]
    assert [message.role for message in second.input_messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    # The replayed tool call keeps the output item's id as its message id.
    assert second.input_messages[1].message_id == "fc-1"
    assert second.input_messages[0].message_id == first.input_messages[0].message_id
    tool_started = next(event for event in events if _kinds([event]) == ["tool:cmd-1:started"])
    assert "inf-1" in tool_started.causation_ids
    tool_completed = next(event for event in events if _kinds([event]) == ["tool:cmd-1:completed"])
    assert tool_completed.event.data["is_error"] is False

    await harness.stop()
    assert reader_type.instances[-1].closed


@pytest.mark.asyncio
async def test_chained_requests_and_code_cell_tools_resolve_through_rollout(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    _install_reader(monkeypatch)
    exec_call = {"type": "custom_tool_call", "id": "ctc-1", "call_id": "call-exec", "name": "exec", "input": "tools.exec_command()"}

    def exec_item(**fields: Any) -> dict[str, Any]:
        return _item(id="exec-1", type="commandExecution", command="ls", cwd="/w", **fields).__dict__

    state.scripts.append(
        [
            _record("codex_turn_started"),
            _record(
                "inference_started",
                inference_call_id="inf-1",
                resolved_payloads={"request_payload": {"input": [_USER_INPUT]}},
            ),
            _record("code_cell_started", runtime_cell_id="1", model_visible_call_id="call-exec"),
            _record(
                "tool_call_started",
                tool_call_id="exec-1",
                model_visible_call_id=None,
                requester={"type": "code_cell", "runtime_cell_id": "1"},
            ),
            _notification("item/started", **exec_item()),
            _record(
                "inference_completed",
                inference_call_id="inf-1",
                response_id="resp-1",
                resolved_payloads={"response_payload": {"output_items": [exec_call]}},
            ),
            _notification("item/completed", **exec_item(aggregated_output="a.py", status="completed", error=None)),
            _record(
                "inference_started",
                inference_call_id="inf-2",
                resolved_payloads={
                    "request_payload": {
                        "previous_response_id": "resp-1",
                        "input": [{"type": "custom_tool_call_output", "id": "ctco-1", "call_id": "call-exec", "output": "a.py"}],
                    },
                },
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-2",
                response_id="resp-2",
                resolved_payloads={"response_payload": {"output_items": []}},
            ),
            _record("codex_turn_ended"),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w"))
    await harness.start(_context(host_capabilities=_OBSERVED))

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == [
        "turn:started",
        "request:inf-1",
        "tool:exec-1:started",
        "tool:exec-1:completed",
        "request:inf-2",
        "turn:finished",
    ]
    tool_started = next(event for event in events if _kinds([event]) == ["tool:exec-1:started"])
    assert "inf-1" in tool_started.causation_ids
    second = [event.event for event in events if isinstance(event.event, ModelRequestEvent)][1]
    assert second.input_observed
    assert [message.message_id for message in second.input_messages][1:] == ["ctc-1", "ctco-1"]
    await harness.stop()


@pytest.mark.asyncio
async def test_silent_rollout_falls_back_to_raw_response_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    _install_reader(monkeypatch)
    state.scripts.append(
        [
            _notification("rawResponseItem/completed", item=_SHELL_CALL),
            _notification("rawResponse/completed", responseId="resp-1", usage={"input_tokens": 7, "output_tokens": 2}),
            _notification("item/started", **_command()),
            _notification("item/completed", **_command(aggregated_output="a.py", status="failed", error=None)),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w", request_observation_wait_s=0.05))
    await harness.start(_context(host_capabilities=_OBSERVED))

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == [
        "turn:started",
        "request:resp-1",
        "tool:cmd-1:started",
        "tool:cmd-1:completed",
        "turn:finished",
    ]
    request = next(event.event for event in events if isinstance(event.event, ModelRequestEvent))
    assert not request.input_observed
    assert request.usage.input_tokens == 7
    assert request.data["codex"]["observation"] == "raw_events"
    tool_started = next(event for event in events if _kinds([event]) == ["tool:cmd-1:started"])
    assert "resp-1" in tool_started.causation_ids
    tool_completed = next(event for event in events if _kinds([event]) == ["tool:cmd-1:completed"])
    assert tool_completed.event.data["is_error"] is True
    await harness.stop()


@pytest.mark.asyncio
async def test_hosts_without_model_request_observation_get_no_request_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    reader_type = _install_reader(monkeypatch)
    state.scripts.append(
        [
            _notification("rawResponse/completed", responseId="resp-1", usage=None),
            _notification("item/started", **_command()),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w"))
    await harness.start(_context())
    assert "CODEX_ROLLOUT_TRACE_ROOT" not in state.configs[0].kwargs["env"]
    assert not reader_type.instances

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events) == ["turn:started", "tool:cmd-1:started", "turn:finished"]
    await harness.stop()


def test_raw_param_reads_camel_and_snake_case() -> None:
    assert observation_module._raw_param({"responseId": "r"}, "responseId") == "r"
    assert observation_module._raw_param(SimpleNamespace(response_id="r"), "responseId") == "r"


def _telemetry(source_id: str, name: str, **attributes: Any) -> dict[str, Any]:
    """One CLI telemetry event as the loopback receiver decodes it."""
    return {
        "signal": "log",
        "name": "",
        "attributes": {"event.name": name, **attributes},
        "resource_attributes": {"env": source_id, "service.name": "codex-app-server"},
    }


def _deliver_telemetry(harness: CodexHarness, *events: dict[str, Any]) -> Callable[[Any], Any]:
    """Script step pushing telemetry at the observer the way the receiver does."""

    async def deliver(_handle: Any) -> None:
        observer = harness._request_observer
        assert observer is not None
        for event in events:
            observer._on_receiver_event(event)
        # The receiver hands events over through the loop, as a real one does.
        await asyncio.sleep(0)
        return None

    return deliver


@pytest.mark.asyncio
async def test_a_tool_call_the_app_server_never_announces_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex answers its own tool search, and a code cell that invokes nothing,
    # without reporting a thread item: the call lives only in one response and
    # its result in the next request.
    sdk, state = _install_fake_sdk(monkeypatch)
    _install_reader(monkeypatch)
    search_call = {
        "type": "tool_search_call",
        "id": "tsc-1",
        "call_id": "call-search",
        "name": "tool_search_call",
        "arguments": {"query": "send_message", "limit": 5},
    }
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w"))
    state.scripts.append(
        [
            _record("codex_turn_started"),
            _record(
                "inference_started",
                inference_call_id="inf-1",
                resolved_payloads={"request_payload": {"input": [_USER_INPUT]}},
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-1",
                response_id="resp-1",
                resolved_payloads={"response_payload": {"output_items": [search_call]}},
            ),
            lambda _handle: _deliver_telemetry(
                harness,
                _telemetry(
                    harness._request_observer._source_id,
                    "codex.tool_result",
                    call_id="call-search",
                    tool_name="tool_search",
                    tool_namespace="functions",
                    duration_ms=12,
                    success="true",
                    output_truncated=False,
                ),
                _telemetry(
                    harness._request_observer._source_id,
                    "codex.conversation_starts",
                    provider_name="OpenAI",
                    context_window=1000000,
                    approval_policy="never",
                ),
            )(_handle),
            _record(
                "inference_started",
                inference_call_id="inf-2",
                resolved_payloads={
                    "request_payload": {
                        "previous_response_id": "resp-1",
                        "input": [
                            {
                                "type": "tool_search_output",
                                "id": "tso-1",
                                "call_id": "call-search",
                                "status": "completed",
                                # A search answers with the catalogue it found,
                                # which is also the only statement of those
                                # tools' schemas.
                                "tools": [
                                    {
                                        "type": "namespace",
                                        "name": "mcp__openjiuwen_team",
                                        "tools": [
                                            {
                                                "type": "function",
                                                "name": "send_message",
                                                "description": "talk",
                                                "parameters": {"type": "object"},
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                },
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-2",
                response_id="resp-2",
                resolved_payloads={"response_payload": {"output_items": []}},
            ),
            _record("codex_turn_ended"),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    await harness.start(_context(host_capabilities=_OBSERVED))

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)
    logger.info("codex observation kinds: {}", _kinds(events))

    # The call is reported between the request that made it and the one that
    # read its result.
    assert _kinds(events) == [
        "turn:started",
        "request:inf-1",
        "tool:call-search:started",
        "tool:call-search:completed",
        "request:inf-2",
        "turn:finished",
    ]
    started, completed = [
        event.event for event in events if isinstance(event.event, ItemLifecycleEvent)
    ]
    assert "inf-1" in [event for event in events if event.event is started][0].causation_ids
    # The CLI's own report of the call wins over what the conversation implies.
    assert started.data["name"] == "tool_search"
    assert started.data["announced"] is False
    assert completed.data["duration_ms"] == 12
    assert completed.data["is_error"] is False
    # What the search returned is its result, not an empty one.
    assert "send_message" in json.dumps(json_value_to_builtin(completed.data["result"]), ensure_ascii=False)
    second = [event.event for event in events if isinstance(event.event, ModelRequestEvent)][1]
    # A deferred MCP tool is offered from the moment the search found it, named
    # the way the tool item names it so the two can be joined.
    definitions = json_value_to_builtin(second.tool_definitions) or []
    assert [definition["name"] for definition in definitions] == ["openjiuwen_team.send_message"]
    assert definitions[0]["parameters"] == {"type": "object"}
    # The model reads the search result as a tool message, not as its own words.
    tool_messages = [message for message in second.input_messages if message.role is MessageRole.TOOL]
    assert [block.data["call_id"] for message in tool_messages for block in message.content] == ["call-search"]
    # Session settings the CLI resolved to travel with the request.
    assert second.data["codex"]["context_window"] == 1000000
    assert second.data["codex"]["approval_policy"] == "never"
    await harness.stop()


@pytest.mark.asyncio
async def test_an_announced_tool_call_gets_no_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # The App Server named this one, so the observation must not double it.
    sdk, state = _install_fake_sdk(monkeypatch)
    _install_reader(monkeypatch)
    state.scripts.append(
        [
            _record("codex_turn_started"),
            _record(
                "inference_started",
                inference_call_id="inf-1",
                resolved_payloads={"request_payload": {"input": [_USER_INPUT]}},
            ),
            _notification("item/started", **_command()),
            _record(
                "inference_completed",
                inference_call_id="inf-1",
                response_id="resp-1",
                resolved_payloads={"response_payload": {"output_items": [_SHELL_CALL]}},
            ),
            _notification("item/completed", **_command(aggregated_output="a.py", status="completed", error=None)),
            _record(
                "inference_started",
                inference_call_id="inf-2",
                resolved_payloads={
                    "request_payload": {
                        "previous_response_id": "resp-1",
                        "input": [{"type": "function_call_output", "id": "fco-1", "call_id": "cmd-1", "output": "a.py"}],
                    },
                },
            ),
            _record(
                "inference_completed",
                inference_call_id="inf-2",
                response_id="resp-2",
                resolved_payloads={"response_payload": {"output_items": []}},
            ),
            _record("codex_turn_ended"),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False, cwd="/w"))
    await harness.start(_context(host_capabilities=_OBSERVED))

    receipt = await harness.send(HarnessInput(content="list files"))
    events = await _turn(harness, receipt.turn_id)

    assert _kinds(events).count("tool:cmd-1:started") == 1
    assert not [event for event in events if _kinds([event]) == ["tool:call-search:started"]]
    await harness.stop()


def test_telemetry_reads_only_what_the_agent_did() -> None:
    from openjiuwen.harness_providers.codex.observation import _telemetry_observation

    facts = _telemetry_observation(
        "codex.tool_result",
        {
            "call_id": "call-1",
            "tool_name": "exec",
            "arguments": '{"cmd":"ls"}',
            "duration_ms": "51",
            "success": "true",
            "user.email": "someone@example.com",
            "user.account_id": "acct-1",
        },
    )
    assert facts.arguments == {"cmd": "ls"}
    assert facts.duration_ms == 51 and facts.success is True
    # The signed-in account is not part of what the agent did.
    assert "someone@example.com" not in str(facts)
    assert _telemetry_observation("codex.tool_result", {"tool_name": "exec"}) is None
    assert _telemetry_observation("codex.startup_phase", {"model": "gpt-5"}) is None
