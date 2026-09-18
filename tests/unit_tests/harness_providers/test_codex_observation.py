# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Codex model-request observation tests driven by a fake SDK and rollout reader."""

from __future__ import annotations

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
                        "input": [_USER_INPUT],
                        "tools": [{"type": "function", "name": "shell"}],
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
