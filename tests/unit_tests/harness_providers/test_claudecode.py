# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Code provider tests driven by a fake ``claude_agent_sdk`` module."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import pytest

from openjiuwen.harness_protocol import (
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessProtocol,
    HarnessState,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    ItemLifecycleEvent,
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
from openjiuwen.harness_providers.claudecode.options import build_claude_session_id
from tests.test_logger import logger


class _Options:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _Block:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeSdkState:
    def __init__(self) -> None:
        self.clients: list["_FakeClient"] = []
        self.scripts: list[list[Any]] = []
        self.connect_error: Exception | None = None


class _FakeClient:
    def __init__(self, state: _FakeSdkState, options: Any, transport: Any) -> None:
        self.state = state
        self.options = options
        self.transport = transport
        self.queries: list[str] = []
        self.interrupts = 0
        self.connected = False
        self.disconnected = False
        self.release = asyncio.Event()
        self.release.set()
        state.clients.append(self)

    async def connect(self) -> None:
        if self.state.connect_error is not None:
            raise self.state.connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def query(self, prompt: str, session_id: str = "default") -> None:
        _ = session_id
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.release.set()

    async def receive_response(self):
        script = self.state.scripts.pop(0)
        for message in script:
            if callable(message):
                message = await message(self)
                if message is None:
                    continue
            yield message


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
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2},
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
    assert result.usage.input_tokens == 10 and result.usage.cached_input_tokens == 2
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
    await harness.stop()


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
    state.scripts.append([_result(sdk, is_error=True, subtype="error", api_error_status=401, errors=["auth"])])
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
    fallback_client = state.clients[-1]
    assert fallback_client.options.model == "fallback-model"
    assert '"ANTHROPIC_BASE_URL": "https://alt"' in fallback_client.options.settings
    assert any(
        isinstance(event.event, ProviderEvent) and event.event.event_type == "auth_fallback_activated" for event in events
    )
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
