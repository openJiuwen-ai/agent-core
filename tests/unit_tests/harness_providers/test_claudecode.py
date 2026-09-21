# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Code provider tests driven by a fake ``claude_agent_sdk`` module."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from openjiuwen.harness_protocol import (
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessProtocol,
    HarnessState,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    ItemLifecycleEvent,
    ModelSelection,
    OutputEvent,
    OutputOperation,
    ProviderEvent,
    ProviderInteractionResponse,
    ResumePolicy,
    TurnEventKind,
    TurnLifecycleEvent,
    UserInputResponse,
    UsageUpdatedEvent,
)
from openjiuwen.harness_providers.base import ProviderStartupError
from openjiuwen.harness_providers.claudecode import (
    ClaudeCodeHarness,
    ClaudeCodeHarnessConfig,
    ClaudeCodeHarnessProvider,
    ClaudeModelConfig,
)
from openjiuwen.harness_providers.claudecode.failure_classifier import (
    classify_assistant_error,
    classify_result_message,
    merge_pending_error,
)
from openjiuwen.harness_providers.claudecode.harness import MODEL_CHANGED_EVENT
from openjiuwen.harness_providers.claudecode.lifecycle import SETTLED_SUBTYPE
from openjiuwen.harness_providers.claudecode.options import build_claude_options, build_claude_session_id
from tests.test_logger import logger


class _Options:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _Block:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeSdkState:
    def __init__(self) -> None:
        self.sdk: Any = None
        self.clients: list["_FakeClient"] = []
        self.transports: list["_FakeTransport"] = []
        self.scripts: list[list[Any]] = []
        self.connect_error: Exception | None = None
        self.server_info: dict[str, Any] | None = {"models": []}
        # ``False`` drops the private control channel, like an older SDK build.
        self.control_channel = True


class _FakeClient:
    def __init__(self, state: _FakeSdkState, options: Any, transport: Any) -> None:
        self.state = state
        self.options = options
        self.transport = transport
        self.queries: list[str] = []
        self.submitted: list[dict[str, Any]] = []
        self.interrupts = 0
        self.connected = False
        self.disconnected = False
        self.release = asyncio.Event()
        self.release.set()
        self.models_set: list[str | None] = []
        self.control_requests: list[dict[str, Any]] = []
        if state.control_channel:
            self._query = SimpleNamespace(_send_control_request=self._send_control_request)
        state.clients.append(self)

    async def _send_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.control_requests.append(request)
        return {}

    async def set_model(self, model: str | None = None) -> None:
        self.models_set.append(model)

    async def get_server_info(self) -> dict[str, Any] | None:
        return self.state.server_info

    async def connect(self) -> None:
        if self.state.connect_error is not None:
            raise self.state.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        _ = session_id
        if isinstance(prompt, str):
            self.queries.append(prompt)
            return
        async for frame in prompt:
            self.submitted.append(frame)
            self.queries.append(frame["message"]["content"])

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.release.set()

    async def receive_messages(self):
        script = self.state.scripts.pop(0)
        answered = False
        for message in script:
            if callable(message):
                message = await message(self)
                if message is None:
                    continue
            answered = answered or isinstance(message, self.state.sdk.ResultMessage)
            yield message
        if answered:
            # Stand in for the transport tap, which ends a turn once the CLI
            # has answered every message the turn handed it.
            yield self.state.sdk.SystemMessage(subtype=SETTLED_SUBTYPE, data={})


class _FakeTransport:
    """Stand-in for the SDK subprocess transport the provider now builds."""

    def __init__(self, state: _FakeSdkState, prompt: Any, options: Any) -> None:
        self.prompt = prompt
        self.options = options
        state.transports.append(self)

    async def connect(self) -> None:
        return None

    async def write(self, data: str) -> None:
        return None

    async def read_messages(self):
        return
        yield {}  # type: ignore[unreachable]

    async def close(self) -> None:
        return None

    async def end_input(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, _FakeSdkState]:
    state = _FakeSdkState()
    sdk = ModuleType("claude_agent_sdk")

    class ClaudeAgentOptions(_Options):
        pass

    class ClaudeSDKClient(_FakeClient):
        def __init__(self, options: Any = None, transport: Any = None) -> None:
            super().__init__(state, options, transport)

    class TextBlock(_Block):
        pass

    class ThinkingBlock(_Block):
        pass

    class ToolUseBlock(_Block):
        pass

    class ToolResultBlock(_Block):
        pass

    class AssistantMessage(_Block):
        pass

    class UserMessage(_Block):
        pass

    class SystemMessage(_Block):
        pass

    class StreamEvent(_Block):
        pass

    class ResultMessage(_Block):
        pass

    class PermissionResultAllow(_Block):
        pass

    class PermissionResultDeny(_Block):
        pass

    class CLINotFoundError(RuntimeError):
        pass

    class CLIConnectionError(RuntimeError):
        pass

    class ProcessError(RuntimeError):
        def __init__(self, message: str, *, exit_code: int = 1, stderr: str = "") -> None:
            super().__init__(message)
            self.exit_code = exit_code
            self.stderr = stderr

    for name, value in locals().items():
        if name not in {"state", "sdk"}:
            setattr(sdk, name, value)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    # The provider builds the transport itself now, so the fake SDK has to
    # offer one. A dotted name present in ``sys.modules`` is imported without
    # its parent packages, so only the leaf module is registered.
    transport_module = ModuleType("claude_agent_sdk._internal.transport.subprocess_cli")

    class SubprocessCLITransport(_FakeTransport):
        def __init__(self, *, prompt: Any, options: Any) -> None:
            super().__init__(state, prompt, options)

    transport_module.SubprocessCLITransport = SubprocessCLITransport  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport.subprocess_cli", transport_module)
    state.sdk = sdk
    return sdk, state


def _result(sdk: ModuleType, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 10,
        "duration_api_ms": 8,
        "is_error": False,
        "num_turns": 1,
        "session_id": "sess",
        "stop_reason": "end_turn",
        "total_cost_usd": 0.0025,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 2,
            "output_tokens_details": {"thinking_tokens": 3},
        },
        "result": "Hello world",
        "structured_output": None,
        "errors": None,
        "api_error_status": None,
    }
    values.update(overrides)
    return sdk.ResultMessage(**values)


def _context(**overrides: Any) -> HarnessContext:
    values: dict[str, Any] = {
        "agent_name": "coder",
        "agent_id": "team_coder",
        "host_session_id": "team-session",
        "system_prompt": "You are a coder.",
    }
    values.update(overrides)
    return HarnessContext(**values)


async def _turn(harness: HarnessProtocol, turn_id: str) -> list[HarnessEvent]:
    return [event async for event in harness.turn_events(turn_id)]


def _terminal(events: list[HarnessEvent]) -> TurnLifecycleEvent:
    payload = events[-1].event
    assert isinstance(payload, TurnLifecycleEvent)
    return payload


def test_config_validation_and_provider_card() -> None:
    config = ClaudeCodeHarnessConfig.from_mapping(
        {"cwd": "/tmp", "add_dirs": ["/a"], "model": {"model": "claude-x", "api_key": "secret"}, "max_turns": 3}
    )
    assert config.add_dirs == ("/a",)
    assert config.model is not None and config.model.model == "claude-x"
    assert "secret" not in repr(config)
    with pytest.raises(ValueError, match="unknown Claude Code configuration fields"):
        ClaudeCodeHarnessConfig.from_mapping({"nope": 1})
    with pytest.raises(ValueError, match="permission_mode"):
        ClaudeCodeHarnessConfig(permission_mode="yolo")
    with pytest.raises(ValueError):
        ClaudeModelConfig(model="")
    provider = ClaudeCodeHarnessProvider()
    assert provider.card.name == "claude-code"
    harness = provider.create({"cwd": "/tmp"})
    assert isinstance(harness, HarnessProtocol)
    assert harness.state is HarnessState.TERMINATED


@pytest.mark.asyncio
async def test_full_turn_maps_messages_to_protocol_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append(
        [
            sdk.StreamEvent(uuid="u1", session_id="s", event={"type": "message_start"}, parent_tool_use_id=None),
            sdk.StreamEvent(
                uuid="u2",
                session_id="s",
                event={"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hel"}},
                parent_tool_use_id=None,
            ),
            sdk.AssistantMessage(
                content=[
                    sdk.ThinkingBlock(thinking="plan"),
                    sdk.TextBlock(text="Hello"),
                    sdk.ToolUseBlock(id="tool-1", name="Bash", input={"command": "ls"}),
                ],
                model="claude-x",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-1",
                stop_reason="tool_use",
                session_id="s",
            ),
            sdk.UserMessage(
                content=[sdk.ToolResultBlock(tool_use_id="tool-1", content=[{"type": "text", "text": "a.py"}], is_error=False)],
                uuid="um-1",
                parent_tool_use_id=None,
                tool_use_result=None,
            ),
            sdk.SystemMessage(subtype="init", data={"tools": ["Bash"]}),
            _result(sdk),
        ]
    )
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False, cwd="/tmp"))
    await harness.start(_context())
    assert harness.state is HarnessState.IDLE
    assert harness.provider_session_id == build_claude_session_id(host_session_id="team-session", agent_name="coder")
    client = state.clients[0]
    assert client.options.system_prompt == {"type": "preset", "append": "You are a coder."}
    assert client.options.permission_mode == "bypassPermissions"
    assert client.options.can_use_tool is None

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    kinds = [type(event.event).__name__ for event in events]
    logger.info("claude turn event kinds: %s", kinds)
    outputs = [event.event for event in events if isinstance(event.event, OutputEvent)]
    assert [(output.operation, output.content) for output in outputs] == [
        (OutputOperation.DELTA, "Hel"),
        (OutputOperation.FINAL, "plan"),
        (OutputOperation.FINAL, "Hello"),
    ]
    assert outputs[0].output_id == outputs[2].output_id
    items = [(event.item_id, event.event.kind.value) for event in events if isinstance(event.event, ItemLifecycleEvent)]
    assert items == [("tool-1", "started"), ("tool-1", "completed")]
    assert any(isinstance(event.event, ProviderEvent) and event.event.event_type == "system/init" for event in events)
    assert any(isinstance(event.event, UsageUpdatedEvent) for event in events)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    result = terminal.result
    assert result.final_output == "Hello world"
    # GenAI states the whole prompt as input; the cache hit is a breakdown of it.
    assert result.usage.input_tokens == 12 and result.usage.cached_input_tokens == 2
    # Claude Code omits the thinking text, so the count is all a reader gets.
    assert result.usage.reasoning_output_tokens == 3
    assert result.cost.micros == 2500
    assert [message.role.value for message in result.messages] == ["assistant", "tool"]
    assert client.queries == ["hi"]
    checkpoint = await harness.export_checkpoint()
    assert checkpoint is not None and checkpoint.data["session_id"] == harness.provider_session_id
    await harness.stop()
    assert client.disconnected


@pytest.mark.asyncio
async def test_steer_and_abort_use_the_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _wait_release(client: _FakeClient) -> Any:
        client.release.clear()
        await client.release.wait()
        return _result(sdk, result="stopped")

    state.scripts.append([_wait_release])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="long task"))
    await asyncio.sleep(0.01)
    from openjiuwen.harness_protocol import DeliveryMode

    steer = await harness.send(HarnessInput(content="also this"), mode=DeliveryMode.STEER)
    assert steer.turn_id == receipt.turn_id
    await harness.abort()
    events = await _turn(harness, receipt.turn_id)
    client = state.clients[0]
    assert client.queries == ["long task", "also this"]
    assert client.interrupts == 1
    assert _terminal(events).kind is TurnEventKind.ABORTED
    await harness.stop()


@pytest.mark.asyncio
async def test_a_steer_answered_as_a_new_cycle_stays_in_the_same_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append(
        [
            sdk.AssistantMessage(
                content=[sdk.TextBlock(text="first")],
                model="claude-x",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-1",
                stop_reason="end_turn",
                session_id="s",
            ),
            _result(sdk, result="first"),
            sdk.AssistantMessage(
                content=[sdk.TextBlock(text="second")],
                model="claude-x",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-2",
                stop_reason="end_turn",
                session_id="s",
            ),
            _result(sdk, result="second", total_cost_usd=0.004, num_turns=1),
        ]
    )
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="do it"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    result = terminal.result
    # The cycle the CLI ran for the steered message belongs to this turn.
    assert result.final_output == "second"
    assert [message.message_id for message in result.messages] == ["msg-1", "msg-2"]
    # Usage is reported per cycle and summed; cost is reported per session.
    assert result.usage.input_tokens == 24 and result.usage.output_tokens == 10
    assert result.cost.micros == 4000
    assert result.provider_data["num_turns"] == 2
    await harness.stop()


@pytest.mark.asyncio
async def test_turn_cost_is_what_the_turn_added_to_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append([_result(sdk, total_cost_usd=0.0025)])
    state.scripts.append([_result(sdk, total_cost_usd=0.004)])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    first = await harness.send(HarnessInput(content="one"))
    assert _terminal(await _turn(harness, first.turn_id)).result.cost.micros == 2500
    second = await harness.send(HarnessInput(content="two"))
    terminal = _terminal(await _turn(harness, second.turn_id))
    # The CLI reports the session total; the turn reports its own share.
    assert terminal.result.cost.micros == 1500
    assert terminal.result.provider_data["session_cost_usd"] == 0.004
    await harness.stop()


@pytest.mark.asyncio
async def test_the_submitted_message_carries_the_receipt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append([_result(sdk)])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    await _turn(harness, receipt.turn_id)
    submitted = state.clients[0].submitted
    # The CLI echoes this id back as the ``command_uuid`` of its receipts.
    assert [frame["uuid"] for frame in submitted] == [receipt.message_id]
    assert submitted[0]["type"] == "user" and submitted[0]["parent_tool_use_id"] is None
    await harness.stop()


@pytest.mark.asyncio
async def test_failed_result_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append([_result(sdk, is_error=True, subtype="error", errors=["quota"], api_error_status=429)])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error.category == "rate_limited"
    assert terminal.result.error.provider_data["http_status"] == 429
    assert classify_result_message(_result(sdk, is_error=True, api_error_status=401)).category == "auth_required"
    # A failed ResultMessage ends the turn normally: the client stays usable.
    assert len(state.clients) == 1 and not state.clients[0].disconnected
    await harness.stop()


@pytest.mark.asyncio
async def test_turn_exception_drops_client_and_next_turn_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _explode(client: _FakeClient) -> Any:
        raise RuntimeError("JSON message exceeded maximum buffer size")

    state.scripts.append([_explode])
    state.scripts.append([_result(sdk, result="recovered")])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    first = state.clients[0]

    receipt = await harness.send(HarnessInput(content="read the big image"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FAILED
    # The SDK read task died with the exception; the client must be dropped so
    # the next turn does not reuse a message stream that only yields nothing.
    assert first.disconnected

    receipt = await harness.send(HarnessInput(content="retry"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FINISHED
    assert terminal.result.final_output == "recovered"
    # A fresh client served the retry, and it resumed the same CLI session.
    assert len(state.clients) == 2
    assert state.clients[1].queries == ["retry"]
    assert state.clients[1].options.resume == harness.provider_session_id
    await harness.stop()


@pytest.mark.asyncio
async def test_empty_stream_drops_client(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append([])  # stream ends without a ResultMessage
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error.code == "CLAUDE_MISSING_RESULT"
    assert state.clients[0].disconnected
    await harness.stop()


def test_max_buffer_size_config_flows_to_options(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    config = ClaudeCodeHarnessConfig.from_mapping({"max_buffer_size": 32 * 1024 * 1024})
    assert config.max_buffer_size == 32 * 1024 * 1024
    with pytest.raises(ValueError, match="max_buffer_size"):
        ClaudeCodeHarnessConfig(max_buffer_size=0)

    async def _run() -> None:
        harness = ClaudeCodeHarness(config)
        await harness.start(_context())
        await harness.stop()

    asyncio.run(_run())
    assert state.clients[0].options.max_buffer_size == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_startup_failure_is_a_provider_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.connect_error = sdk.CLINotFoundError("claude missing")
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    with pytest.raises(ProviderStartupError) as info:
        await harness.start(_context())
    assert info.value.error.category == "process_start_failed"
    assert harness.state is HarnessState.TERMINATED


@pytest.mark.asyncio
async def test_ask_user_question_routes_to_the_host_and_resume_uses_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    class _Handler:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def handle(self, request: Any) -> Any:
            self.requests.append(request)
            return UserInputResponse(
                request_id=request.request_id,
                status=InteractionResponseStatus.COMPLETED,
                content={"answers": {"Which color?": "teal"}},
            )

        async def cancel(self, request_id: str, *, reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW) -> None:
            _ = request_id, reason

    handler = _Handler()

    async def _ask(client: _FakeClient) -> Any:
        permission = await client.options.can_use_tool(
            "AskUserQuestion",
            {"questions": [{"question": "Which color?", "options": [{"label": "teal"}, {"label": "red"}]}]},
            _Block(tool_use_id="toolu-1", suggestions=[]),
        )
        assert isinstance(permission, sdk.PermissionResultAllow)
        assert permission.updated_input["answers"] == {"Which color?": "teal"}
        return _result(sdk, result="teal it is")

    state.scripts.append([_ask])
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context(interactions=handler, host_capabilities=frozenset({HostCapability.USER_INPUT})))
    client = state.clients[0]
    assert client.options.permission_mode == "default"
    # The CLI only routes permission prompts to ``can_use_tool`` when it was
    # launched with the stdio prompt tool, which the transport reads off its
    # own options; the client's own options must not carry it, since the SDK
    # rejects having both.
    assert state.transports[0].options.permission_prompt_tool_name == "stdio"
    assert getattr(client.options, "permission_prompt_tool_name", None) is None
    receipt = await harness.send(HarnessInput(content="pick"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.result.final_output == "teal it is"
    assert handler.requests[0].choices == ("teal", "red")
    checkpoint = await harness.export_checkpoint()
    await harness.stop()

    state.scripts.append([_result(sdk, result="resumed")])
    resumed = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await resumed.start(_context(resume_policy=ResumePolicy.REQUIRE_RESUME, checkpoint=checkpoint))
    resumed_client = state.clients[-1]
    assert resumed_client.options.resume == checkpoint.data["session_id"]
    assert resumed_client.options.session_id is None
    await resumed.stop()


@pytest.mark.asyncio
async def test_auth_failure_activates_fallback_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append(
        [
            sdk.SystemMessage(subtype="init", data={"model": "native-model"}),
            _result(sdk, is_error=True, subtype="error", api_error_status=401, errors=["auth"]),
        ]
    )
    state.scripts.append(
        [
            sdk.SystemMessage(subtype="init", data={"model": "fallback-model"}),
            _result(sdk, result="fallback ok"),
        ]
    )
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(
            inherit_process_env=False,
            fallback_model=ClaudeModelConfig(model="fallback-model", api_base="https://alt", api_key="k"),
        )
    )
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    assert terminal.result.final_output == "fallback ok"
    assert harness.fallback_activated
    fallback_client = state.clients[-1]
    assert fallback_client.options.model == "fallback-model"
    assert '"ANTHROPIC_BASE_URL": "https://alt"' in fallback_client.options.settings
    assert any(
        isinstance(event.event, ProviderEvent) and event.event.event_type == "auth_fallback_activated" for event in events
    )
    # Each CLI session reports the model it serves via ``system/init`` —
    # including members spawned without an explicit model — and the fallback
    # client's init carries the fallback model.
    init_models = [
        event.event.payload.get("model")
        for event in events
        if isinstance(event.event, ProviderEvent) and event.event.event_type == "system/init"
    ]
    assert init_models == ["native-model", "fallback-model"]
    await harness.stop()


@pytest.mark.asyncio
async def test_not_logged_in_text_still_activates_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    # The CLI reports a missing login as synthetic assistant text plus an
    # error result without ``errors``; the text must not count as consumed
    # output and block the one-shot auth fallback replay.
    state.scripts.append(
        [
            sdk.SystemMessage(subtype="init", data={"model": "native-model"}),
            sdk.AssistantMessage(
                content=[sdk.TextBlock(text="Not logged in · Please run /login")],
                model="native-model",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-1",
                stop_reason=None,
                session_id="s",
            ),
            _result(sdk, is_error=True, subtype="success", api_error_status=401),
        ]
    )
    state.scripts.append(
        [
            sdk.SystemMessage(subtype="init", data={"model": "fallback-model"}),
            _result(sdk, result="fallback ok"),
        ]
    )
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(
            inherit_process_env=False,
            fallback_model=ClaudeModelConfig(model="fallback-model", api_base="https://alt", api_key="k"),
        )
    )
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    assert terminal.result.final_output == "fallback ok"
    assert harness.fallback_activated
    # The diagnostic text survives as the first attempt's failure reason only
    # when the turn actually fails; here the fallback recovered the turn.
    await harness.stop()


@pytest.mark.asyncio
async def test_auth_failure_after_partial_output_still_activates_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    # Partial work before a mid-turn token expiry: the category is the gate,
    # not the emitted output — replaying on the fallback is preferred over
    # failing the turn (and no worse than the manual retry that would follow).
    state.scripts.append(
        [
            sdk.SystemMessage(subtype="init", data={"model": "native-model"}),
            sdk.AssistantMessage(
                content=[
                    sdk.TextBlock(text="let me check"),
                    sdk.ToolUseBlock(id="tool-1", name="Bash", input={"command": "ls"}),
                ],
                model="native-model",
                parent_tool_use_id=None,
                error=None,
                usage=None,
                message_id="msg-1",
                stop_reason="tool_use",
                session_id="s",
            ),
            _result(sdk, is_error=True, subtype="error", api_error_status=401, errors=["auth"]),
        ]
    )
    state.scripts.append([_result(sdk, result="fallback ok")])
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(
            inherit_process_env=False,
            fallback_model=ClaudeModelConfig(model="fallback-model", api_base="https://alt", api_key="k"),
        )
    )
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    assert terminal.result.final_output == "fallback ok"
    assert harness.fallback_activated
    assert len(state.clients) == 2  # native client replaced by the fallback
    await harness.stop()


@pytest.mark.asyncio
async def test_declined_fallback_ratification_restores_the_native_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append([_result(sdk, is_error=True, subtype="error", api_error_status=401, errors=["auth"])])
    requests: list[Any] = []

    class _Handler:
        async def handle(self, request: Any) -> Any:
            requests.append(request)
            return ProviderInteractionResponse(request_id=request.request_id, status=InteractionResponseStatus.DECLINED)

        async def cancel(self, request_id: str, *, reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW) -> None:
            _ = request_id, reason

    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(
            inherit_process_env=False,
            model=ClaudeModelConfig(model="native-model"),
            fallback_model=ClaudeModelConfig(model="fallback-model", api_base="https://alt", api_key="k"),
        )
    )
    await harness.start(
        _context(interactions=_Handler(), host_capabilities=frozenset({HostCapability.PROVIDER_INTERACTION}))
    )
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    logger.info("declined fallback terminal: %s", terminal)
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error.category == "auth_required"
    assert not harness.fallback_activated
    assert [request.request_type for request in requests] == ["auth_fallback"]
    assert requests[0].payload == {"model": "fallback-model", "api_base": "https://alt"}
    # native -> fallback -> native again; the fallback client was dropped.
    assert [client.options.model for client in state.clients] == ["native-model", "fallback-model", "native-model"]
    assert state.clients[1].disconnected
    assert not any(
        isinstance(event.event, ProviderEvent) and event.event.event_type == "auth_fallback_activated" for event in events
    )
    await harness.stop()


@pytest.mark.asyncio
async def test_failed_native_reconnect_recovers_on_later_input(monkeypatch):
    sdk, state = _install_fake_sdk(monkeypatch)
    from openjiuwen.harness_providers.claudecode import harness as module
    original = _FakeClient.connect
    attempts = 0

    async def connect(client):
        nonlocal attempts
        attempts += 1
        if attempts in {3, 4}:
            raise sdk.CLIConnectionError("native unavailable")
        await original(client)

    monkeypatch.setattr(_FakeClient, "connect", connect)

    class Decline:
        async def handle(self, request):
            return ProviderInteractionResponse(request_id=request.request_id, status=InteractionResponseStatus.DECLINED)
        async def cancel(self, request_id, *, reason):
            pass

    harness = module.ClaudeCodeHarness(ClaudeCodeHarnessConfig(
        inherit_process_env=False, model=ClaudeModelConfig(model="native"),
        fallback_model=ClaudeModelConfig(model="fallback"),
    ))
    state.scripts = [[_result(sdk, is_error=True, subtype="error", api_error_status=401)], [_result(sdk, result="recovered")]]
    await harness.start(_context(interactions=Decline(), host_capabilities=frozenset({HostCapability.PROVIDER_INTERACTION})))
    try:
        for expected in [TurnEventKind.FAILED, TurnEventKind.FAILED, TurnEventKind.FINISHED]:
            receipt = await harness.send(HarnessInput(content="hello"))
            terminal = _terminal(await _turn(harness, receipt.turn_id))
            assert terminal.kind is expected
        assert terminal.result.final_output == "recovered"
        assert attempts == 5
        assert state.clients[-1].options.model == "native"
        assert state.clients[-1].options.resume == state.clients[0].options.session_id
        assert not harness.fallback_activated
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_stop_closes_client_that_reconnects_after_stop(monkeypatch):
    sdk, state = _install_fake_sdk(monkeypatch)
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    await harness._close_session()
    original = harness._connect
    entered = asyncio.Event()
    release = asyncio.Event()

    async def connect(context, *, model, resume, session_id):
        entered.set()
        await release.wait()
        return await original(context, model=model, resume=resume, session_id=session_id)

    monkeypatch.setattr(harness, "_connect", connect)
    receipt = await harness.send(HarnessInput(content="new input"))
    consumer = asyncio.create_task(_turn(harness, receipt.turn_id))
    await asyncio.wait_for(entered.wait(), 2)
    stopping = asyncio.create_task(harness.stop())
    while not harness.active_turn.stop_requested:
        await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(stopping, 2)
    assert _terminal(await consumer).kind is TurnEventKind.ABORTED
    assert all(client.disconnected for client in state.clients)
    assert not any(client.queries for client in state.clients)


@pytest.mark.asyncio
async def test_bad_request_is_classified_as_request_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 400 and ``invalid_request`` both name a rejected request, not a generic SDK error."""
    sdk, _ = _install_fake_sdk(monkeypatch)
    assert classify_result_message(_result(sdk, is_error=True, api_error_status=400)).category == "request_rejected"
    assert classify_assistant_error("invalid_request").category == "request_rejected"
    logger.info("claude bad-request classification maps onto request_rejected")


@pytest.mark.asyncio
async def test_assistant_failure_detail_survives_into_the_turn_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The assistant text blocks carry the cause that ``error`` alone omits."""
    sdk, _ = _install_fake_sdk(monkeypatch)
    message = sdk.AssistantMessage(
        content=[sdk.TextBlock(text="model 'x' is not available on this endpoint")],
        error="invalid_request",
    )
    error = classify_assistant_error("invalid_request", message)
    assert error.message == "model 'x' is not available on this endpoint"
    assert error.code == "invalid_request"
    logger.info("claude assistant failure detail is preserved")


@pytest.mark.asyncio
async def test_uninformative_result_errors_fall_back_to_the_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """``errors=['unknown']`` says nothing; the status code is then the only fact."""
    sdk, _ = _install_fake_sdk(monkeypatch)
    error = classify_result_message(_result(sdk, is_error=True, errors=["unknown"], api_error_status=429))
    assert error.message == "Claude turn failed: HTTP 429"
    assert error.category == "rate_limited"
    logger.info("claude uninformative result errors fall back to the http status")


@pytest.mark.asyncio
async def test_pending_assistant_detail_is_merged_into_the_terminal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal result keeps the earlier assistant diagnostic instead of dropping it."""
    sdk, _ = _install_fake_sdk(monkeypatch)
    message = sdk.AssistantMessage(content=[sdk.TextBlock(text="endpoint rejected the tool schema")], error="invalid_request")
    pending = classify_assistant_error("invalid_request", message)
    terminal = classify_result_message(_result(sdk, is_error=True, errors=["unknown"], api_error_status=400))
    merged = merge_pending_error(pending, terminal)
    assert "endpoint rejected the tool schema" in merged.message
    assert "HTTP 400" in merged.message
    assert merged.category == "request_rejected"
    logger.info("claude pending assistant detail merges into the terminal error")


_CLAUDE_CATALOG = {
    "models": [
        {"value": "default", "displayName": "Default", "supportedEffortLevels": ["low", "high"], "pid": 1},
        {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5",
         "supportedEffortLevels": ["low", "medium", "high"]},
        {"value": "haiku", "displayName": "Haiku", "description": "Fastest"},
    ]
}


async def _next_provider_event(cursor: Any, event_type: str) -> ProviderEvent:
    while True:
        envelope = await asyncio.wait_for(anext(cursor), timeout=2)
        payload = envelope.event
        if isinstance(payload, ProviderEvent) and payload.event_type == event_type:
            return payload


def test_builtin_model_and_effort_flow_to_options(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, _ = _install_fake_sdk(monkeypatch)
    config = ClaudeCodeHarnessConfig.from_mapping({"model": {"model": "haiku", "effort": "low"}})
    options = build_claude_options(
        sdk=sdk,
        config=config,
        model=config.model,
        cwd=None,
        env={},
        system_prompt="",
        session_id=None,
        resume=None,
        mcp_servers={},
        can_use_tool=None,
        stderr=None,
    )
    assert options.model == "haiku"
    assert options.effort == "low"
    # A built-in model on the CLI login injects no endpoint settings.
    assert options.settings is None
    with pytest.raises(ValueError):
        ClaudeModelConfig(effort="")
    card = ClaudeCodeHarnessProvider().card
    assert card.supports(HarnessCapability.MODEL_SELECTION)
    assert card.supports(HarnessCapability.MODEL_DISCOVERY)


@pytest.mark.asyncio
async def test_list_models_reads_the_live_catalog_or_probes_without_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    _, state = _install_fake_sdk(monkeypatch)
    state.server_info = _CLAUDE_CATALOG
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))

    probed = await harness.list_models()
    assert [option.model_id for option in probed] == ["default", "sonnet", "haiku"]
    assert probed[0].is_default and not probed[1].is_default
    assert probed[1].efforts == ("low", "medium", "high")
    assert probed[1].extensions["resolvedModel"] == "claude-sonnet-5"
    assert "pid" not in probed[0].extensions
    assert probed[2].efforts == () and probed[2].description == "Fastest"
    # The probe used a throwaway client and closed it; nothing was queried.
    assert state.clients[0].disconnected and state.clients[0].queries == []

    await harness.start(_context())
    live = await harness.list_models()
    assert live == probed
    assert len(state.clients) == 2, "a started harness reads its own session"
    await harness.stop()


@pytest.mark.asyncio
async def test_set_model_switches_the_live_session_and_announces_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _, state = _install_fake_sdk(monkeypatch)
    harness = ClaudeCodeHarness(ClaudeCodeHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    cursor = harness.events()

    await harness.set_model(ModelSelection(model="haiku", effort="low"))

    client = state.clients[0]
    assert client.models_set == ["haiku"]
    assert client.control_requests == [{"subtype": "apply_flag_settings", "settings": {"effortLevel": "low"}}]
    event = await _next_provider_event(cursor, MODEL_CHANGED_EVENT)
    assert dict(event.payload) == {"model": "haiku", "effort": "low"}
    await cursor.aclose()
    await harness.stop()


@pytest.mark.asyncio
async def test_effort_without_control_channel_reconnects_with_the_new_options(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.control_channel = False
    state.scripts.append([_result(sdk)])
    harness = ClaudeCodeHarness(
        ClaudeCodeHarnessConfig(inherit_process_env=False, model=ClaudeModelConfig(model="sonnet"))
    )
    await harness.start(_context())

    await harness.set_model(ModelSelection(effort="high"))
    assert state.clients[0].disconnected, "an SDK without the control channel drops the client"

    receipt = await harness.send(HarnessInput(content="go"))
    assert _terminal(await _turn(harness, receipt.turn_id)).kind is TurnEventKind.FINISHED
    reconnected = state.clients[1]
    assert reconnected.options.model == "sonnet"
    assert reconnected.options.effort == "high"
    assert reconnected.options.resume == harness.provider_session_id
    await harness.stop()
