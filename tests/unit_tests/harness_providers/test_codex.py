# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Codex provider tests driven by a fake ``openai_codex`` module."""

from __future__ import annotations

import asyncio
import sys
from enum import Enum
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from openjiuwen.harness_protocol import (
    DeliveryMode,
    DiagnosticEvent,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessProtocol,
    HarnessProtocolError,
    HarnessState,
    HostCapability,
    InteractionResponseStatus,
    ItemLifecycleEvent,
    McpServerConfig,
    McpTransport,
    OutputEvent,
    OutputOperation,
    ProviderEvent,
    ProviderInteractionResponse,
    ResumePolicy,
    ToolApprovalDecision,
    ToolApprovalResponse,
    TurnEventKind,
    TurnLifecycleEvent,
    UsageUpdatedEvent,
)
from openjiuwen.harness_providers.codex import CodexHarness, CodexHarnessConfig, CodexHarnessProvider, CodexModelConfig
from openjiuwen.harness_providers.codex.options import codex_mcp_config_overrides, codex_model_config_overrides
from tests.test_logger import logger


class _Status(Enum):
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class _FakeSdkState:
    def __init__(self) -> None:
        self.configs: list[Any] = []
        self.clients: list["_FakeCodex"] = []
        self.thread_calls: list[tuple[str, dict[str, Any]]] = []
        self.scripts: list[list[Any]] = []
        self.next_thread_id = "thread-1"
        self.handles: list["_FakeHandle"] = []
        # ``thread.turn()`` blocks until released so tests can steer before the
        # SDK handle exists (the STARTED-to-turn/start window).
        self.turn_gate = asyncio.Event()
        self.turn_gate.set()


class _FakeHandle:
    def __init__(self, state: _FakeSdkState, thread_id: str, prompt: str) -> None:
        self.state = state
        self.thread_id = thread_id
        self.id = f"turn-{prompt}"
        self.steers: list[str] = []
        self.interrupts = 0
        self.release = asyncio.Event()
        self.release.set()

    async def steer(self, text: str) -> None:
        self.steers.append(text)

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.release.set()

    async def stream(self):
        script = self.state.scripts.pop(0)
        for item in script:
            if callable(item):
                item = await item(self)
                if item is None:
                    continue
            yield item


class _FakeThread:
    def __init__(self, state: _FakeSdkState, thread_id: str) -> None:
        self.state = state
        self.id = thread_id
        self.handles: list[_FakeHandle] = []

    async def turn(self, prompt: str) -> _FakeHandle:
        await self.state.turn_gate.wait()
        handle = _FakeHandle(self.state, self.id, prompt)
        self.handles.append(handle)
        self.state.handles.append(handle)
        return handle


class _FakeCodex:
    def __init__(self, state: _FakeSdkState, config: Any) -> None:
        self.state = state
        self.config = config
        self.closed = False
        self._client = SimpleNamespace(_sync=SimpleNamespace(_approval_handler=None))
        state.clients.append(self)

    async def thread_start(self, **options: Any) -> _FakeThread:
        self.state.thread_calls.append(("start", options))
        return _FakeThread(self.state, self.state.next_thread_id)

    async def thread_resume(self, thread_id: str, **options: Any) -> _FakeThread:
        self.state.thread_calls.append(("resume", options))
        return _FakeThread(self.state, thread_id)

    async def close(self) -> None:
        self.closed = True


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, _FakeSdkState]:
    state = _FakeSdkState()
    sdk = ModuleType("openai_codex")

    class CodexConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            state.configs.append(self)

    class AsyncCodex(_FakeCodex):
        def __init__(self, config: Any = None) -> None:
            super().__init__(state, config)

    sdk.CodexConfig = CodexConfig
    sdk.AsyncCodex = AsyncCodex
    sdk.ApprovalMode = SimpleNamespace(deny_all="deny_all", auto_review="auto_review")
    sdk.Sandbox = SimpleNamespace(full_access="full_access")
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    monkeypatch.setattr("openjiuwen.harness_providers.codex.options.load_codex_sdk", lambda: sdk)
    monkeypatch.setattr("openjiuwen.harness_providers.codex.harness.load_codex_sdk", lambda: sdk)
    return sdk, state


def _notification(method: str, **payload: Any) -> Any:
    return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))


def _item(**fields: Any) -> Any:
    return SimpleNamespace(item=SimpleNamespace(root=SimpleNamespace(**fields)))


def _turn_completed(turn_id: str, status: _Status, error: Any = None) -> Any:
    return SimpleNamespace(
        method="turn/completed",
        payload=SimpleNamespace(turn=SimpleNamespace(id=turn_id, status=status, error=error)),
    )


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


def test_config_validation_and_option_rendering() -> None:
    config = CodexHarnessConfig.from_mapping(
        {"cwd": "/w", "mcp_env_passthrough": ["A"], "model": {"model": "m", "provider": "deepseek", "api_key": "k"}}
    )
    assert config.mcp_env_passthrough == ("A",)
    assert "api_key" not in repr(config.model)
    with pytest.raises(ValueError, match="unknown Codex configuration fields"):
        CodexHarnessConfig.from_mapping({"bogus": 1})
    with pytest.raises(ValueError, match="turn_idle_timeout_s"):
        CodexHarnessConfig(turn_idle_timeout_s=0)
    assert codex_model_config_overrides(CodexModelConfig(provider="deep-seek", api_base="https://x", api_key="k")) == (
        'model_provider="deep-seek"',
        'model_providers.deep-seek.name="deep-seek"',
        'model_providers.deep-seek.base_url="https://x"',
        'model_providers.deep-seek.env_key="OPENJIUWEN_CODEX_API_KEY"',
    )
    overrides = codex_mcp_config_overrides(
        McpServerConfig(name="openjiuwen-team", transport=McpTransport.STDIO, command=("mcp", "--flag"), env={"A": "1"}),
        env_passthrough=("JOIN",),
        startup_timeout_s=120,
        required=True,
        default_tools_approval_mode="approve",
    )
    assert overrides[0] == 'mcp_servers.openjiuwen_team.command="mcp"'
    assert 'mcp_servers.openjiuwen_team.args=["--flag"]' in overrides
    assert 'mcp_servers.openjiuwen_team.env={ A = "1" }' in overrides
    assert 'mcp_servers.openjiuwen_team.env_vars=["JOIN"]' in overrides
    assert "mcp_servers.openjiuwen_team.required=true" in overrides
    assert 'mcp_servers.openjiuwen_team.default_tools_approval_mode="approve"' in overrides
    http = codex_mcp_config_overrides(
        McpServerConfig(name="remote", transport=McpTransport.HTTP, url="https://mcp", headers={"X": "y"}),
        env_passthrough=(),
        startup_timeout_s=5,
        required=False,
        default_tools_approval_mode=None,
    )
    assert 'mcp_servers.remote.url="https://mcp"' in http
    assert 'mcp_servers.remote.http_headers={ X = "y" }' in http
    assert CodexHarnessProvider().card.name == "codex"


@pytest.mark.asyncio
async def test_full_turn_maps_notifications_to_protocol_events(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    state.scripts.append(
        [
            _notification("turn/started", turn_id="turn-hi"),
            _notification("item/agentMessage/delta", delta="Hel", item_id="msg-1", thread_id="t", turn_id="turn-hi"),
            _notification("item/reasoning/summaryTextDelta", delta="think", item_id="r-1", thread_id="t", turn_id="turn-hi"),
            _notification(
                "item/started",
                **_item(id="cmd-1", type="commandExecution", command="ls", cwd="/w").__dict__,
            ),
            _notification(
                "item/completed",
                **_item(id="cmd-1", type="commandExecution", command="ls", cwd="/w", aggregated_output="a.py", status="completed", error=None).__dict__,
            ),
            _notification("item/completed", **_item(id="msg-1", type="agentMessage", text="Hello").__dict__),
            _notification(
                "thread/tokenUsage/updated",
                thread_id="t",
                turn_id="turn-hi",
                token_usage=SimpleNamespace(
                    last=SimpleNamespace(input_tokens=10, output_tokens=4, cached_input_tokens=1, reasoning_output_tokens=2, total_tokens=17),
                    total=SimpleNamespace(total_tokens=17),
                ),
            ),
            _turn_completed("turn-hi", _Status.completed),
        ]
    )
    config = CodexHarnessConfig(inherit_process_env=False, cwd="/w", model=CodexModelConfig(model="m", provider="p", api_key="k"))
    harness = CodexHarness(config)
    await harness.start(
        _context(mcp_servers=(McpServerConfig(name="team", transport=McpTransport.STDIO, command=("mcp",)),))
    )
    assert harness.provider_session_id == "thread-1"
    kind, options = state.thread_calls[0]
    assert kind == "start"
    assert options["developer_instructions"] == "You are a coder."
    assert options["approval_mode"] == "deny_all"
    codex_config = state.configs[0].kwargs
    assert codex_config["env"]["OPENJIUWEN_CODEX_API_KEY"] == "k"
    assert 'model_provider="p"' in codex_config["config_overrides"]
    assert 'mcp_servers.team.command="mcp"' in codex_config["config_overrides"]

    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    logger.info("codex turn events: %s", [type(event.event).__name__ for event in events])
    outputs = [event.event for event in events if isinstance(event.event, OutputEvent)]
    assert [(output.channel.value, output.operation, output.content) for output in outputs] == [
        ("answer", OutputOperation.DELTA, "Hel"),
        ("reasoning", OutputOperation.DELTA, "think"),
        ("answer", OutputOperation.FINAL, "Hello"),
    ]
    items = [(event.item_id, event.event.kind.value) for event in events if isinstance(event.event, ItemLifecycleEvent)]
    assert items == [("cmd-1", "started"), ("cmd-1", "completed")]
    tool_started = next(event.event for event in events if isinstance(event.event, ItemLifecycleEvent))
    assert tool_started.data["name"] == "shell"
    assert any(isinstance(event.event, UsageUpdatedEvent) for event in events)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FINISHED
    assert terminal.result.final_output == "Hello"
    assert terminal.result.usage.total_tokens == 17
    checkpoint = await harness.export_checkpoint()
    assert checkpoint is not None and checkpoint.data["thread_id"] == "thread-1"
    await harness.stop()
    assert state.clients[0].closed


@pytest.mark.asyncio
async def test_steer_abort_and_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _wait(handle: _FakeHandle) -> Any:
        handle.release.clear()
        await handle.release.wait()
        return _turn_completed(handle.id, _Status.interrupted)

    state.scripts.append([_wait])
    state.scripts.append(
        [
            _notification("error", error=SimpleNamespace(message="overloaded", codex_error_info=None), will_retry=True, thread_id="t", turn_id="x"),
            _turn_completed("turn-fail", _Status.failed, error=SimpleNamespace(message="bad", codex_error_info=None)),
        ]
    )
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    receipt = await harness.send(HarnessInput(content="long"))
    await asyncio.sleep(0.01)
    steer = await harness.send(HarnessInput(content="also"), mode=DeliveryMode.STEER)
    assert steer.turn_id == receipt.turn_id
    await harness.abort()
    events = await _turn(harness, receipt.turn_id)
    assert _terminal(events).kind is TurnEventKind.ABORTED
    handle = state.clients[0]  # keep for closure inspection
    _ = handle

    failing = await harness.send(HarnessInput(content="fail"))
    events = await _turn(harness, failing.turn_id)
    diagnostics = [event.event for event in events if isinstance(event.event, DiagnosticEvent)]
    assert diagnostics and diagnostics[0].data["kind"] == "retrying"
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error.message == "bad"
    await harness.stop()


@pytest.mark.asyncio
async def test_resume_requires_checkpoint_and_uses_thread_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False))
    with pytest.raises(HarnessProtocolError):
        await harness.start(_context(resume_policy=ResumePolicy.REQUIRE_RESUME))
    await harness.start(_context())
    checkpoint = await harness.export_checkpoint()
    await harness.stop()

    resumed = CodexHarness(CodexHarnessConfig(inherit_process_env=False))
    await resumed.start(_context(resume_policy=ResumePolicy.REQUIRE_RESUME, checkpoint=checkpoint))
    assert state.thread_calls[-1][0] == "resume"
    assert "ephemeral" not in state.thread_calls[-1][1]
    assert resumed.provider_session_id == "thread-1"
    await resumed.stop()


@pytest.mark.asyncio
async def test_approval_requests_route_to_the_host_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)
    decisions: list[Any] = []

    class _Handler:
        async def handle(self, request: Any) -> Any:
            decisions.append(request)
            return ToolApprovalResponse(request_id=request.request_id, decision=ToolApprovalDecision.DENY, reason="no")

        async def cancel(self, request_id: str, *, reason: Any = None) -> None:
            _ = request_id, reason

    async def _ask_approval(handle: _FakeHandle) -> Any:
        client = state.clients[0]
        approval_handler = client._client._sync._approval_handler
        assert approval_handler is not None
        loop = asyncio.get_running_loop()
        decision = await loop.run_in_executor(
            None,
            approval_handler,
            "item/commandExecution/requestApproval",
            {"itemId": "cmd-9", "command": "rm -rf /", "threadId": "t", "turnId": handle.id},
        )
        assert decision == {"decision": "decline"}
        return _turn_completed(handle.id, _Status.completed)

    state.scripts.append([_ask_approval])
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False))
    await harness.start(_context(interactions=_Handler(), host_capabilities=frozenset({HostCapability.TOOL_APPROVAL})))
    receipt = await harness.send(HarnessInput(content="danger"))
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FINISHED
    assert decisions[0].tool_name == "shell" and decisions[0].call_id == "cmd-9"
    assert decisions[0].arguments["command"] == "rm -rf /"
    await harness.stop()


@pytest.mark.asyncio
async def test_steer_before_the_sdk_handle_exists_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _complete(handle: _FakeHandle) -> Any:
        return _turn_completed(handle.id, _Status.completed)

    state.scripts.append([_complete])
    harness = CodexHarness(CodexHarnessConfig(inherit_process_env=False))
    await harness.start(_context())
    state.turn_gate.clear()
    receipt = await harness.send(HarnessInput(content="long"))
    await asyncio.sleep(0.01)
    assert harness.state is HarnessState.RUNNING and not state.handles
    steer = await harness.send(HarnessInput(content="early"), mode=DeliveryMode.STEER)
    assert steer.turn_id == receipt.turn_id
    state.turn_gate.set()
    terminal = _terminal(await _turn(harness, receipt.turn_id))
    assert terminal.kind is TurnEventKind.FINISHED
    assert state.handles[0].steers == ["early"]
    await harness.stop()


def _auth_failure(turn_id: str) -> Any:
    error = SimpleNamespace(message="unauthorized", codex_error_info="unauthorized")
    return _turn_completed(turn_id, _Status.failed, error=error)


def _fallback_config() -> CodexHarnessConfig:
    return CodexHarnessConfig(
        inherit_process_env=False,
        model=CodexModelConfig(model="native-model"),
        fallback_model=CodexModelConfig(model="fallback-model", provider="alt", api_base="https://alt", api_key="k"),
    )


class _RatificationHandler:
    def __init__(self, status: InteractionResponseStatus) -> None:
        self.status = status
        self.requests: list[Any] = []

    async def handle(self, request: Any) -> Any:
        self.requests.append(request)
        return ProviderInteractionResponse(request_id=request.request_id, status=self.status)

    async def cancel(self, request_id: str, *, reason: Any = None) -> None:
        _ = request_id, reason


@pytest.mark.asyncio
async def test_auth_failure_activates_fallback_after_host_ratification(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _fail(handle: _FakeHandle) -> Any:
        return _auth_failure(handle.id)

    async def _complete(handle: _FakeHandle) -> Any:
        return _turn_completed(handle.id, _Status.completed)

    state.scripts.append([_fail])
    state.scripts.append([_complete])
    handler = _RatificationHandler(InteractionResponseStatus.COMPLETED)
    harness = CodexHarness(_fallback_config())
    await harness.start(_context(interactions=handler, host_capabilities=frozenset({HostCapability.PROVIDER_INTERACTION})))
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    assert _terminal(events).kind is TurnEventKind.FINISHED
    assert harness.fallback_activated
    assert [request.request_type for request in handler.requests] == ["auth_fallback"]
    assert handler.requests[0].payload["model"] == "fallback-model"
    assert [call[0] for call in state.thread_calls] == ["start", "resume"]
    assert any(
        isinstance(event.event, ProviderEvent) and event.event.event_type == "auth_fallback_activated" for event in events
    )
    await harness.stop()


@pytest.mark.asyncio
async def test_declined_fallback_ratification_restores_the_native_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk, state = _install_fake_sdk(monkeypatch)

    async def _fail(handle: _FakeHandle) -> Any:
        return _auth_failure(handle.id)

    state.scripts.append([_fail])
    handler = _RatificationHandler(InteractionResponseStatus.DECLINED)
    harness = CodexHarness(_fallback_config())
    await harness.start(_context(interactions=handler, host_capabilities=frozenset({HostCapability.PROVIDER_INTERACTION})))
    receipt = await harness.send(HarnessInput(content="hi"))
    events = await _turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    logger.info("declined codex fallback terminal: %s", terminal)
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error.category == "auth_required"
    assert not harness.fallback_activated
    assert len(handler.requests) == 1
    # native start -> fallback resume -> native resume; the fallback client is closed.
    assert [call[0] for call in state.thread_calls] == ["start", "resume", "resume"]
    assert len(state.clients) == 3
    assert state.clients[1].closed
    assert not state.clients[2].closed
    assert harness.provider_session_id == "thread-1"
    assert not any(
        isinstance(event.event, ProviderEvent) and event.event.event_type == "auth_fallback_activated" for event in events
    )
    await harness.stop()
