from __future__ import annotations

import asyncio
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.context_engine.processor import forked
from openjiuwen.core.context_engine.processor.base import ContextEvent
from openjiuwen.core.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig,
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    BaseMessage,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.personal_context import agent_support
from openjiuwen.harness.personal_context.status_codes import StatusCode
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.rails import SecurityRail
from openjiuwen.harness.rails.context_engineer import ContextProcessorRail
from openjiuwen.harness.rails.tool_call_resilience_rail import ToolCallResilienceRail


@pytest.mark.parametrize(
    "command",
    [
        "$HOME",
        "$env:PATH",
        "%USERPROFILE%",
        "${OUTSIDE}",
        "~\\outside.txt",
        "~alice\\profile.txt",
        "Get-Content subdir/../../outside.txt",
        "Get-Content subdir\\..\\..\\outside.txt",
        "Get-Content \\\\server\\share\\file.txt",
        "Get-Content \\\\127.0.0.1\\share\\file.txt",
        "Get-Content //host/share/file.txt",
        "/proc/self/environ",
        "ls\nGet-Content secret.txt",
        "ls; Get-Content secret.txt",
        "ls && Get-Content secret.txt",
        "ls || Get-Content secret.txt",
        "ls | Select-String secret",
        "echo secret > outside.txt",
        "echo secret >> outside.txt",
        "Start-Process calc.exe",
        "reg add HKCU\\Software\\personal_context",
        "sc.exe stop service",
        "schtasks /create",
        "taskkill /f /im agent.exe",
        "rm -r -f outside",
        "rm -f --recursive outside",
        "Remove-Item outside -Force -Recurse",
        "rd /q /s outside",
        "del /s /f outside",
        "Get-Content https://example.invalid/remote.txt",
        "Get-Content ftp://example.invalid/remote.txt",
        "Get-Content file:///C:/Users/alice/secret.txt",
        "Get-Content file://server/share/secret.txt",
        "Get-Content C:/Windows/win.ini",
        "Get-Content /Windows/win.ini",
        "Get-Content ([Environment]::GetFolderPath('UserProfile'))",
        "Get-Content (Get-Item env:USERPROFILE).Value",
        "Get-Content variable:HOME",
        "Get-Content function:prompt",
        "Get-Content registry::HKEY_CURRENT_USER\\Software",
        "Get-Content HKLM:\\Software",
        "Get-Content HKCU:\\Software",
        "Get-Content HKCR:\\Software",
        "Get-Content wsman:\\localhost",
        "Get-Content cert:LocalMachine\\Root",
        "Get-Content alias:ls",
        "find . -exec Get-Content secret.txt \\;",
        "find . -execdir Get-Content secret.txt \\;",
        "find . -delete",
    ],
)
def test_personal_context_shell_patterns_reject_escape_and_process_commands(command: str) -> None:
    assert any(re.search(pattern, command) for pattern in agent_support._PERSONAL_CONTEXT_DANGEROUS_PATTERNS)


@pytest.mark.parametrize("command", ["head", "tail", "wc", "touch", "Test-Path", "Copy-Item", "Move-Item"])
def test_personal_context_shell_allowlist_contains_local_file_helpers(command: str) -> None:
    assert command in agent_support._PERSONAL_CONTEXT_SHELL_ALLOWLIST


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("authorization: Bearer TOP_SECRET", "authorization: [REDACTED]"),
        ('"authorization":"Bearer TOP_SECRET"', '"authorization":"[REDACTED]"'),
        ("Bearer TOP_SECRET", "Bearer [REDACTED]"),
        ("https://example.invalid/?access_token=TOP_SECRET", "https://example.invalid/"),
        ("https://example.invalid/path?foo=bar&x=1#frag", "https://example.invalid/path"),
        ("file://server/share/file.txt?token=TOP_SECRET#frag", "file://server/share/file.txt"),
        ("https://user:TOP_SECRET@example.invalid/path", "https://[REDACTED]@example.invalid/path"),
        (r"C:\Users\alice\foo.txt", "[PATH_REDACTED]"),
        ("/home/alice/foo.txt", "[PATH_REDACTED]"),
        ("/secret", "[PATH_REDACTED]"),
        ("/tmp", "[PATH_REDACTED]"),
        (r"\\server\share\foo.txt", "[PATH_REDACTED]"),
    ],
)
def test_validation_errors_redact_credentials_and_authorization(value: str, expected: str) -> None:
    result = agent_support._validation_errors([value])
    assert result and "TOP_SECRET" not in result[0]
    assert expected in result[0]


def _tool_message_group() -> list[object]:
    return [
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
        ),
        ToolMessage(content="ok", tool_call_id="call-1"),
    ]


def test_validate_message_continuity_accepts_complete_tool_group() -> None:
    messages = [UserMessage(content="read this"), *_tool_message_group()]
    agent_support.validate_personal_context_messages(messages)


@pytest.mark.parametrize(
    "messages",
    [
        [AssistantMessage(content="", tool_calls=[ToolCall(id="", type="function", name="read", arguments="{}")])],
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                    ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                ],
            ),
            ToolMessage(content="ok", tool_call_id="call-1"),
        ],
        [ToolMessage(content="orphan", tool_call_id="call-1")],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
            UserMessage(content="inserted"),
            ToolMessage(content="ok", tool_call_id="call-1"),
        ],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
            ),
        ],
    ],
)
def test_validate_personal_context_messages_rejects_broken_tool_group(messages: list[object]) -> None:
    with pytest.raises(Exception):
        agent_support.validate_personal_context_messages(messages)


def test_trim_personal_context_messages_keeps_tool_group_together() -> None:
    group = _tool_message_group()
    messages = [UserMessage(content="old"), *group, UserMessage(content="new")]
    kept = agent_support.trim_personal_context_messages(messages, budget=2)
    assert kept in ([UserMessage(content="new")], group, [*group, UserMessage(content="new")])
    assert not (
        any(getattr(item, "tool_calls", None) for item in kept)
        and not any(getattr(item, "tool_call_id", None) for item in kept)
    )


@pytest.mark.parametrize(
    ("model_name", "expected_budget", "expected_trigger", "expected_target"),
    [
        ("personal-context-unknown-model", 200_000, 90_000, 60_000),
        ("gpt-3.5-turbo", 16_385, 14_746, 9_830),
    ],
)
def test_context_processor_uses_core_round_compressor_and_model_context_window(
    model_name: str,
    expected_budget: int,
    expected_trigger: int,
    expected_target: int,
) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model=model_name, max_tokens=3)

    rail = agent_support._make_context_processor_rail(model_client, model_request)

    assert isinstance(rail, ContextProcessorRail)
    assert rail._preset is False
    assert len(rail._user_processors) == 1
    processor_name, config = rail._user_processors[0]
    assert processor_name == agent_support._PERSONAL_CONTEXT_ROUND_LEVEL_PROCESSOR_KEY
    assert type(config) is RoundLevelCompressorConfig
    assert config.trigger_context_ratio == pytest.approx(expected_trigger / expected_budget)
    assert config.target_total_tokens == expected_target
    assert config.keep_recent_messages == 6
    assert config.compression_call_max_tokens == 4_096
    assert config.model is model_request
    assert config.model_client is model_client
    assert config.target_total_tokens != model_request.max_tokens

    class ReactConfig:
        model_config_obj = model_request
        model_client_config = model_client
        context_processors: list[tuple[str, object]] = []
        context_engine_config = None

    class Agent:
        react_agent = type("ReactAgent", (), {"_config": ReactConfig()})()

    rail.init(cast(Any, Agent()))
    assert Agent.react_agent._config.context_processors == [(processor_name, config)]
    assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor


@pytest.mark.asyncio
async def test_context_processor_uses_core_compressor_after_forked_registry_pollution() -> None:
    forked.deactivate()
    assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is RoundLevelCompressor
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model", max_tokens=3)

    class ReactConfig:
        model_config_obj = model_request
        model_client_config = model_client
        context_processors: list[tuple[str, object]] = []
        context_engine_config = None

    class PollutingAgent:
        react_agent = type("ReactAgent", (), {"_config": ReactConfig()})()

    ContextProcessorRail(preset=True).init(cast(Any, PollutingAgent()))
    polluted_processor = ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"]
    assert polluted_processor is not RoundLevelCompressor
    try:
        rail = agent_support._make_context_processor_rail(model_client, model_request)
        processor_name, config = rail._user_processors[0]
        context = await ContextEngine().create_context(processors=[(processor_name, config)])
        processor = cast(Any, context)._processors[0]

        assert ContextEngine._PROCESSOR_MAP["RoundLevelCompressor"] is polluted_processor
        assert type(processor) is RoundLevelCompressor
        assert type(processor._config) is RoundLevelCompressorConfig
        assert processor._config.target_total_tokens == 60_000
        assert processor._config.compression_call_max_tokens == 4_096
    finally:
        forked.deactivate()


def test_context_processor_internal_registration_is_idempotent_under_concurrency() -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")

    with ThreadPoolExecutor(max_workers=8) as executor:
        rails = list(
            executor.map(
                lambda _index: agent_support._make_context_processor_rail(model_client, model_request),
                range(32),
            )
        )

    processor_key = agent_support._PERSONAL_CONTEXT_ROUND_LEVEL_PROCESSOR_KEY
    assert ContextEngine._PROCESSOR_MAP[processor_key] is RoundLevelCompressor
    assert all(rail._user_processors[0][0] == processor_key for rail in rails)


@pytest.mark.asyncio
async def test_real_factory_cleanup_unregisters_every_explicit_and_default_rail(tmp_path: Path) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)

    await cast(Any, agent).ensure_initialized()
    configured = cast(Any, agent).configured_rails()
    assert configured == rails
    assert sum(isinstance(rail, SecurityRail) for rail in rails) == 1
    assert sum(isinstance(rail, ToolCallResilienceRail) for rail in rails) == 1

    await agent_support._cleanup_runtime(agent, rails, None, [], None)
    assert cast(Any, agent).configured_rails() == []


@pytest.mark.asyncio
async def test_real_inner_callback_manager_registers_named_callbacks_and_cleans_exactly(tmp_path: Path) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)
    react_agent = cast(Any, agent).react_agent
    manager = react_agent.agent_callback_manager
    callbacks: list[tuple[AgentCallbackEvent, object]] = []
    state: dict[str, Any] | None = None
    registration_error: BaseException | None = None
    registered_callbacks: list[object] = []
    leaked_callbacks: list[object] = []

    try:
        try:
            callbacks, state = await agent_support._register_agent_callbacks(agent)
        except BaseException as exc:
            registration_error = exc
        if registration_error is None:
            for event, callback in callbacks:
                event_name = manager._get_agent_event(event)
                infos = Runner.callback_framework._callbacks[event_name]
                assert [info.callback for info in infos] == [callback]
                registered_callbacks.append(callback)
        await agent_support._cleanup_runtime(agent, rails, None, callbacks, state)
        for event in (
            AgentCallbackEvent.BEFORE_MODEL_CALL,
            AgentCallbackEvent.AFTER_MODEL_CALL,
            AgentCallbackEvent.AFTER_REACT_ITERATION,
        ):
            event_name = manager._get_agent_event(event)
            leaked_callbacks.extend(info.callback for info in Runner.callback_framework._callbacks[event_name])
    finally:
        # Keep the test process isolated when exercising the known broken
        # registration path that appends before reading callback.__name__.
        for event in AgentCallbackEvent:
            event_name = manager._get_agent_event(event)
            Runner.callback_framework._callbacks.pop(event_name, None)

    assert registration_error is None
    assert [callback.__name__ for callback in registered_callbacks] == [
        "personal_context_before_model_call_callback",
        "personal_context_after_model_call_callback",
        "personal_context_after_react_iteration_callback",
    ]
    assert all(asyncio.iscoroutinefunction(callback) for callback in registered_callbacks)
    assert leaked_callbacks == []


@pytest.mark.asyncio
async def test_real_inner_callback_manager_rolls_back_callback_written_before_registration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_client = ModelClientConfig(
        client_provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://example.invalid/v1",
    )
    model_request = ModelRequestConfig(model="personal-context-unknown-model")
    model = Model(model_client_config=model_client, model_config=model_request)
    context_rail = agent_support._make_context_processor_rail(model_client, model_request)
    agent, rails = agent_support._make_agent(model, tmp_path, context_rail)
    manager = cast(Any, agent).react_agent.agent_callback_manager
    original_info = Runner.callback_framework.logger.info
    failed_after_write = False

    def fail_after_second_callback_write(message: object, *args: object, **kwargs: object) -> None:
        nonlocal failed_after_write
        if "Registered callback" in str(message) and "personal_context_after_model_call_callback" in str(message):
            if not failed_after_write:
                failed_after_write = True
                raise RuntimeError("registration failed after write")
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(Runner.callback_framework.logger, "info", fail_after_second_callback_write)
    try:
        with pytest.raises(RuntimeError, match="registration failed after write"):
            await agent_support._register_agent_callbacks(agent)
        assert failed_after_write is True
        for event in (
            AgentCallbackEvent.BEFORE_MODEL_CALL,
            AgentCallbackEvent.AFTER_MODEL_CALL,
            AgentCallbackEvent.AFTER_REACT_ITERATION,
        ):
            assert manager.has_hooks(event) is False
    finally:
        await agent_support._cleanup_runtime(agent, rails, None, [], None)


@pytest.mark.parametrize("tool_count", [1, 2])
def test_round_compressor_never_splits_tool_group_at_compression_boundary(tool_count: int) -> None:
    calls = [ToolCall(id=f"call-{index}", type="function", name="read", arguments="{}") for index in range(tool_count)]
    group: list[BaseMessage] = [
        AssistantMessage(content="", tool_calls=calls),
        *[ToolMessage(content=f"result-{index}", tool_call_id=call.id) for index, call in enumerate(calls)],
    ]
    messages = [UserMessage(content="old request"), *group, UserMessage(content="recent")]
    compressor = RoundLevelCompressor(RoundLevelCompressorConfig())

    split_boundary = len(group) - 1
    split_targets = compressor._build_raw_targets(messages, compress_end=split_boundary)
    split_messages = [message for target in split_targets for message in target.messages]
    assert all(all(message is not grouped for message in split_messages) for grouped in group)

    complete_targets = compressor._build_raw_targets(messages, compress_end=len(group))
    complete_messages = [message for target in complete_targets for message in target.messages]
    assert all(any(message is grouped for message in complete_messages) for grouped in group)


@pytest.mark.asyncio
@pytest.mark.parametrize("compression_path", ["initial", "recursive", "aggressive", "hard_truncation"])
async def test_add_compression_is_disabled_until_multi_tool_group_closes(
    monkeypatch: pytest.MonkeyPatch,
    compression_path: str,
) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="old")],
    )
    processor = cast(Any, context)._processors[0]
    compression_calls: list[str] = []

    async def always_trigger(*_args: object, **_kwargs: object) -> bool:
        return True

    async def destructive_add_compression(
        model_context: object,
        _messages: object,
        **_kwargs: object,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        compression_calls.append(compression_path)
        cast(Any, model_context).set_messages([UserMessage(content=f"{compression_path} summary")])
        return ContextEvent(event_type="RoundLevelCompressor"), []

    monkeypatch.setattr(processor, "trigger_add_messages", always_trigger)
    monkeypatch.setattr(processor, "on_add_messages", destructive_add_compression)
    state: dict[str, Any] = {}
    callback_context = cast(Any, SimpleNamespace(context=context))
    after_model = getattr(agent_support, "_after_model_call_context_compression", None)
    if after_model is not None:
        await after_model(callback_context, state=state)

    assistant = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(id="call-1", type="function", name="read", arguments="{}"),
            ToolCall(id="call-2", type="function", name="grep", arguments="{}"),
        ],
    )
    first_result = ToolMessage(content="one", tool_call_id="call-1")
    second_result = ToolMessage(content="two", tool_call_id="call-2")
    await context.add_messages(assistant)
    await context.add_messages(first_result)

    # This is a real partial ReAct history: a later call-2 result must still be
    # able to close the group, so none of the ADD compression paths may run.
    assert compression_calls == []
    assert context.get_messages()[-2:] == [assistant, first_result]

    await context.add_messages(second_result)
    messages = context.get_messages()
    assert compression_calls == []
    agent_support.validate_personal_context_messages(messages)

    before_model = getattr(agent_support, "_before_model_call_context_compression", None)
    assert before_model is not None
    await before_model(callback_context, state=state)
    assert await processor.trigger_add_messages(context, [UserMessage(content="next")]) is True


@pytest.mark.asyncio
async def test_add_compression_stays_disabled_for_final_answer_and_first_in_place_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await ContextEngine().create_context(
        processors=[("RoundLevelCompressor", RoundLevelCompressorConfig())],
        history_messages=[UserMessage(content="request")],
    )
    processor = cast(Any, context)._processors[0]
    compression_calls: list[str] = []

    async def always_trigger(*_args: object, **_kwargs: object) -> bool:
        return True

    async def observe_add(
        _context: object,
        messages: list[BaseMessage],
        **_kwargs: object,
    ) -> tuple[ContextEvent, list[BaseMessage]]:
        compression_calls.append(type(messages[0]).__name__)
        return ContextEvent(event_type="RoundLevelCompressor"), messages

    monkeypatch.setattr(processor, "trigger_add_messages", always_trigger)
    monkeypatch.setattr(processor, "on_add_messages", observe_add)
    state: dict[str, Any] = {}
    callback_context = cast(Any, SimpleNamespace(context=context))
    after_model = getattr(agent_support, "_after_model_call_context_compression", None)
    if after_model is not None:
        await after_model(callback_context, state=state)

    await context.add_messages(AssistantMessage(content="invalid final answer"))
    await context.add_messages(UserMessage(content="repair in place"))
    assert compression_calls == []

    before_model = getattr(agent_support, "_before_model_call_context_compression", None)
    assert before_model is not None
    await before_model(callback_context, state=state)
    assert await processor.trigger_add_messages(context, [UserMessage(content="next")]) is True


@pytest.mark.asyncio
async def test_iteration_reminder_fires_after_complete_tool_group_at_20_40_60_80_only() -> None:
    pushed: list[str] = []

    class Context:
        def push_steering(self, message: str) -> None:
            pushed.append(message)

    state = {"turn_count": 0}
    for _ in range(100):
        await agent_support._after_react_iteration_reminder(cast(Any, Context()), state=state)

    assert [int(re.search(r"executed (\d+) ReAct", message).group(1)) for message in pushed] == [20, 40, 60, 80]
    assert all(message.startswith("[message from PersonalContext system]\n") for message in pushed)
    assert all("100 ReAct" not in message for message in pushed)

    history: list[object] = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="call-1", type="function", name="read", arguments="{}"),
                ToolCall(id="call-2", type="function", name="grep", arguments="{}"),
            ],
        ),
        ToolMessage(content="one", tool_call_id="call-1"),
        ToolMessage(content="two", tool_call_id="call-2"),
        UserMessage(content=pushed[0]),
    ]
    agent_support.validate_personal_context_messages(history)
    assert isinstance(history[3], UserMessage)


class _FakeModel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeReactAgent:
    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events
        self.agent_callback_manager = self
        self.registered_callbacks: list[tuple[AgentCallbackEvent, object]] = []
        self.unregistered_callbacks: list[tuple[AgentCallbackEvent, object]] = []

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: object,
        priority: int = 100,
    ) -> None:
        self.registered_callbacks.append((event, callback))
        self._events.append(("register_callback", (event, callback, priority)))

    async def unregister(self, event: AgentCallbackEvent, callback: object) -> None:
        self.unregistered_callbacks.append((event, callback))
        self._events.append(("unregister_callback", (event, callback)))

    async def clear_session(self, session_id: str) -> None:
        self._events.append(("clear_session", session_id))


class _FakeAgent:
    def __init__(self, events: list[tuple[str, Any]], outputs: list[object]) -> None:
        self.card = object()
        self.react_agent = _FakeReactAgent(events)
        self._events = events
        self._outputs = outputs
        self.invocations: list[tuple[object, object]] = []
        self.seeded_context: list[BaseMessage] | None = None
        self.context_history: list[BaseMessage] = []

    async def create_new_context_engine(self, *, session_id: str, messages: list[BaseMessage]) -> str:
        self.seeded_context = list(messages)
        self.context_history = list(messages)
        self._events.append(("seed_context", (session_id, list(messages))))
        return session_id

    def get_current_context(self, *, session_id: str) -> list[BaseMessage]:
        self._events.append(("get_context", session_id))
        return list(self.context_history)

    async def invoke(self, request: object, *, session: object) -> object:
        self.invocations.append((request, session))
        self._events.append(("invoke", request))
        output = self._outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        query = request.get("query") if isinstance(request, dict) else ""
        if query:
            self.context_history.append(UserMessage(content=str(query)))
        if isinstance(output, AssistantMessage):
            self.context_history.append(output)
        return output


class _FakeSession:
    def __init__(self, session_id: str, events: list[tuple[str, Any]]) -> None:
        self.session_id = session_id
        self._events = events

    def get_session_id(self) -> str:
        return self.session_id

    async def pre_run(self, **_kwargs: object) -> None:
        self._events.append(("pre_run", self.session_id))


def _patch_agent_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outputs_by_agent: list[list[object]],
    events: list[tuple[str, Any]],
) -> dict[str, Any]:
    created: dict[str, Any] = {
        "agents": [],
        "sessions": [],
        "rails": [],
        "processor_rails": [],
        "security_rails": [],
        "resilience_rails": [],
        "configs": [],
    }

    def fake_create_deep_agent(model: object, **kwargs: object) -> _FakeAgent:
        events.append(("create_agent", kwargs))
        agent = _FakeAgent(events, outputs_by_agent.pop(0))
        created["agents"].append(agent)
        return agent

    def fake_create_session(*, session_id: str, card: object, close_stream_on_post_run: bool) -> _FakeSession:
        events.append(("create_session", (session_id, card, close_stream_on_post_run)))
        session = _FakeSession(session_id, events)
        created["sessions"].append(session)
        return session

    def fake_rail(**kwargs: object) -> object:
        events.append(("rail", kwargs))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("rail_uninit", agent))

        rail = Rail()
        created["rails"].append(rail)
        return rail

    def fake_context_processor_rail(model_client: object, model_request: object) -> object:
        events.append(("context_processor_rail", (model_client, model_request)))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("context_processor_rail_uninit", agent))

        rail = Rail()
        created["processor_rails"].append(rail)
        return rail

    def fake_security_rail() -> object:
        events.append(("security_rail", None))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("security_rail_uninit", agent))

        rail = Rail()
        created["security_rails"].append(rail)
        return rail

    def fake_resilience_rail() -> object:
        events.append(("resilience_rail", None))

        class Rail:
            def uninit(self, agent: object) -> None:
                events.append(("resilience_rail_uninit", agent))

        rail = Rail()
        created["resilience_rails"].append(rail)
        return rail

    monkeypatch.setattr(agent_support, "Model", _FakeModel)
    monkeypatch.setattr(agent_support, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(agent_support, "create_agent_session", fake_create_session)
    monkeypatch.setattr(agent_support, "SysOperationRail", fake_rail)
    monkeypatch.setattr(agent_support, "SecurityRail", fake_security_rail)
    monkeypatch.setattr(agent_support, "ToolCallResilienceRail", fake_resilience_rail)
    monkeypatch.setattr(agent_support, "_make_context_processor_rail", fake_context_processor_rail)
    return created


@pytest.mark.asyncio
async def test_run_personal_context_agent_creates_unique_session_and_returns_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch, outputs_by_agent=[[AssistantMessage(content="  result  ")]], events=events
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=lambda _text, _path: [],
    )

    assert output == "result"
    assert len(created["agents"]) == 1
    assert len(created["sessions"]) == 1
    session = created["sessions"][0]
    assert session.session_id.startswith("personal-context-agent-")
    assert len(session.session_id) > len("personal-context-agent-")
    assert [event[0] for event in events].count("clear_session") == 1
    assert [event[0] for event in events].count("rail_uninit") == 1
    assert [event[0] for event in events].count("context_processor_rail_uninit") == 1
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1
    react_agent = created["agents"][0].react_agent
    assert [event for event, _ in react_agent.registered_callbacks] == [
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
        AgentCallbackEvent.AFTER_REACT_ITERATION,
    ]
    assert [callback.__name__ for _, callback in react_agent.registered_callbacks] == [
        "personal_context_before_model_call_callback",
        "personal_context_after_model_call_callback",
        "personal_context_after_react_iteration_callback",
    ]
    assert all(asyncio.iscoroutinefunction(callback) for _, callback in react_agent.registered_callbacks)
    assert react_agent.unregistered_callbacks == react_agent.registered_callbacks


@pytest.mark.asyncio
async def test_run_personal_context_agent_repairs_in_same_session_after_first_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "tmp").mkdir()
    original_invoke = _FakeAgent.invoke

    async def invoke_with_preserved_sandbox(self: _FakeAgent, request: object, *, session: object) -> object:
        scratch = sandbox / "tmp" / "repair-notes.md"
        if not self.invocations:
            scratch.write_text("keep for first repair", encoding="utf-8")
        else:
            assert scratch.read_text(encoding="utf-8") == "keep for first repair"
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_preserved_sandbox)
    validation_errors = iter([["invalid field token=secret"], []])
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(validation_errors),
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    agent = created["agents"][0]
    assert len(agent.invocations) == 2
    assert agent.invocations[0][1] is agent.invocations[1][1]
    repair_request = cast(dict[str, str], agent.invocations[1][0])
    assert "secret" not in repair_request["query"]
    assert "修正" in repair_request["query"]
    assert len(messages) == 2
    assert isinstance(messages[-1], UserMessage)
    react_agent = agent.react_agent
    assert len(react_agent.registered_callbacks) == 3
    assert react_agent.unregistered_callbacks == react_agent.registered_callbacks
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1


@pytest.mark.asyncio
async def test_run_personal_context_agent_repairs_in_same_session_after_invoke_error_with_closed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="fixed")]],
        events=events,
    )
    original_invoke = _FakeAgent.invoke

    async def invoke_with_closed_error(self: _FakeAgent, request: object, *, session: object) -> object:
        if not self.invocations:
            self.invocations.append((request, session))
            query = request.get("query") if isinstance(request, dict) else ""
            self.context_history.extend(
                [UserMessage(content=str(query)), AssistantMessage(content="closed model turn")]
            )
            raise RuntimeError("model failed with secret-token")
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_closed_error)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: [],
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    agent = created["agents"][0]
    assert len(agent.invocations) == 2
    assert agent.invocations[0][1] is agent.invocations[1][1]
    repair_query = cast(dict[str, str], agent.invocations[1][0])["query"]
    assert "修正" in repair_query
    assert "model failed" not in repair_query
    assert "secret-token" not in repair_query
    assert len(messages) == 2
    assert isinstance(messages[-1], UserMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        StatusCode.CONTEXT_PROACTIVE_PIPELINE_EXECUTION_ERROR,
        StatusCode.MODEL_CALL_FAILED,
        StatusCode.COMPONENT_LLM_INVOKE_CALL_FAILED,
        StatusCode.COMPONENT_LLM_EXECUTION_PROCESS_ERROR,
        StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
        StatusCode.AGENT_TOOL_EXECUTION_ERROR,
        StatusCode.TOOL_EXECUTION_ERROR,
        StatusCode.AGENT_CONTROLLER_INVOKE_CALL_FAILED,
        StatusCode.AGENT_CONTROLLER_EXECUTION_CALL_FAILED,
        StatusCode.AGENT_CONTROLLER_TOOL_EXECUTION_PROCESS_ERROR,
    ],
)
async def test_run_personal_context_agent_repairs_for_allowlisted_invoke_status_with_closed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: StatusCode
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="fixed")]],
        events=events,
    )
    original_invoke = _FakeAgent.invoke

    async def invoke_with_status_error(self: _FakeAgent, request: object, *, session: object) -> object:
        if not self.invocations:
            self.invocations.append((request, session))
            query = request.get("query") if isinstance(request, dict) else ""
            self.context_history.extend([UserMessage(content=str(query)), AssistantMessage(content="closed tool turn")])
            raise BaseError(status, msg="execution failed")
        return await original_invoke(self, request, session=session)

    monkeypatch.setattr(_FakeAgent, "invoke", invoke_with_status_error)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize logical-1")],
        validate_result=lambda _text, _path: [],
    )

    assert output == "fixed"
    assert len(created["agents"]) == 1
    assert len(created["agents"][0].invocations) == 2
    assert created["agents"][0].invocations[0][1] is created["agents"][0].invocations[1][1]


@pytest.mark.asyncio
async def test_run_personal_context_agent_clean_redo_after_invoke_error_without_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[RuntimeError("model failed")], [AssistantMessage(content="clean")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [UserMessage(content="summarize logical-1")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: [],
    )

    assert output == "clean"
    assert len(created["agents"]) == 2
    assert len(created["agents"][0].invocations) == 1
    assert len(created["agents"][1].invocations) == 1
    assert created["agents"][0].invocations[0][1] is created["sessions"][0]
    assert created["agents"][1].invocations[0][1] is created["sessions"][1]
    assert messages == [UserMessage(content="summarize logical-1")]


@pytest.mark.asyncio
async def test_run_personal_context_agent_seeds_closed_history_and_uses_only_current_user_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [
        UserMessage(content="old request"),
        AssistantMessage(content="old answer"),
        UserMessage(content="current logical-2"),
    ]
    validation_errors = iter([["invalid"], []])

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(validation_errors),
    )

    assert output == "fixed"
    agent = created["agents"][0]
    assert agent.seeded_context == messages[:2]
    first_query = cast(dict[str, str], agent.invocations[0][0])["query"]
    assert first_query == "current logical-2"
    second_query = cast(dict[str, str], agent.invocations[1][0])["query"]
    assert "old request" not in second_query
    assert "current logical-2" not in second_query
    assert "修正" in second_query
    assert [event[0] for event in events].count("get_context") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_output",
    ["", AssistantMessage(content="x" * (agent_support._MAX_AGENT_OUTPUT_CHARS + 1))],
)
async def test_run_personal_context_agent_repairs_bounded_output_validation_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_output: object,
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[first_output, AssistantMessage(content="fixed")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    validation_calls: list[str] = []

    def validate(text: str, _path: Path) -> list[str]:
        validation_calls.append(text)
        return []

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=validate,
    )

    assert output == "fixed"
    assert len(created["agents"][0].invocations) == 2
    assert validation_calls == ["fixed"]


@pytest.mark.asyncio
async def test_run_personal_context_agent_clean_redo_restores_baseline_after_second_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[
            [AssistantMessage(content="bad"), AssistantMessage(content="still bad")],
            [AssistantMessage(content="clean success")],
        ],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "baseline.txt").write_text("baseline", encoding="utf-8")
    read_only_tree = sandbox / "materialized-source"
    read_only_tree.mkdir()
    read_only_file = read_only_tree / "README.md"
    read_only_file.write_text("read-only baseline", encoding="utf-8")
    for path in (read_only_file, read_only_tree):
        path.chmod(path.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)

    async def fake_invoke(self: _FakeAgent, request: object, *, session: object) -> object:
        self.invocations.append((request, session))
        if len(created["agents"]) == 1:
            marker = "dirty-first.txt" if len(self.invocations) == 1 else "dirty-second.txt"
            (sandbox / marker).write_text("dirty", encoding="utf-8")
        else:
            assert not (sandbox / "dirty-first.txt").exists()
            assert not (sandbox / "dirty-second.txt").exists()
            (sandbox / "redo-dirty.txt").write_text("dirty", encoding="utf-8")
        return self._outputs.pop(0)

    monkeypatch.setattr(_FakeAgent, "invoke", fake_invoke)
    errors = iter([["first"], ["second token=secret"], []])
    messages = [UserMessage(content="summarize")]

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(errors),
    )

    assert output == "clean success"
    assert len(created["agents"]) == 2
    assert len(created["sessions"]) == 2
    assert created["agents"][0].invocations[1][1] is created["sessions"][0]
    assert created["agents"][1].invocations[0][1] is created["sessions"][1]
    redo_query = cast(dict[str, str], created["agents"][1].invocations[0][0])["query"]
    assert "second" in redo_query
    assert "secret" not in redo_query
    assert (sandbox / "baseline.txt").read_text(encoding="utf-8") == "baseline"
    assert read_only_file.read_text(encoding="utf-8") == "read-only baseline"
    assert not (sandbox / "dirty-first.txt").exists()
    assert not (sandbox / "dirty-second.txt").exists()
    assert list(tmp_path.glob(".personal-context-agent-baseline-*")) == []
    first_react = created["agents"][0].react_agent
    redo_react = created["agents"][1].react_agent
    assert first_react.unregistered_callbacks == first_react.registered_callbacks
    assert redo_react.unregistered_callbacks == redo_react.registered_callbacks
    assert all(
        first_callback is not redo_callback
        for (_, first_callback), (_, redo_callback) in zip(
            first_react.registered_callbacks,
            redo_react.registered_callbacks,
            strict=True,
        )
    )
    assert [callback.__name__ for _, callback in first_react.registered_callbacks] == [
        callback.__name__ for _, callback in redo_react.registered_callbacks
    ]

    first_reminders: list[str] = []
    redo_reminders: list[str] = []
    first_context = cast(Any, SimpleNamespace(push_steering=first_reminders.append))
    redo_context = cast(Any, SimpleNamespace(push_steering=redo_reminders.append))
    for _ in range(20):
        await first_react.registered_callbacks[2][1](first_context)
    await redo_react.registered_callbacks[2][1](redo_context)
    assert len(first_reminders) == 1
    assert redo_reminders == []
    assert [event[0] for event in events].count("context_processor_rail_uninit") == 2
    assert [event[0] for event in events].count("security_rail_uninit") == 2
    assert [event[0] for event in events].count("resilience_rail_uninit") == 2


@pytest.mark.asyncio
async def test_clean_redo_restore_failure_is_non_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, Any]] = []
    _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad"), AssistantMessage(content="bad")]],
        events=events,
    )
    monkeypatch.setattr(agent_support, "_restore_sandbox", lambda *_args: (_ for _ in ()).throw(OSError("denied")))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    errors = iter([["first"], ["second"]])

    with pytest.raises(BaseError) as raised:
        await agent_support.run_personal_context_agent(
            model_client=cast(Any, object()),
            model_request=cast(Any, object()),
            sandbox_path=sandbox,
            messages=[UserMessage(content="summarize")],
            validate_result=lambda _text, _path: next(errors),
        )

    assert raised.value.details == {"fallback_allowed": False}


@pytest.mark.asyncio
async def test_run_personal_context_agent_unclosed_tool_group_skips_in_place_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(
        monkeypatch,
        outputs_by_agent=[[AssistantMessage(content="bad")], [AssistantMessage(content="clean")]],
        events=events,
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    messages = [
        UserMessage(content="summarize"),
        AssistantMessage(
            content="",
            tool_calls=[ToolCall(id="call-1", type="function", name="read", arguments="{}")],
        ),
    ]
    errors = iter([["invalid"], []])

    output = await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=messages,
        validate_result=lambda _text, _path: next(errors),
    )

    assert output == "clean"
    assert len(created["agents"]) == 2
    assert [type(message) for message in messages] == [UserMessage, AssistantMessage]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("denied"),
        BaseError(StatusCode.DEEPAGENT_RUNTIME_ERROR, msg="runtime failed"),
        asyncio.CancelledError(),
    ],
)
async def test_run_personal_context_agent_always_cleans_session_and_rail_on_disk_or_cancelled_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(monkeypatch, outputs_by_agent=[[failure]], events=events)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else BaseError
    with pytest.raises(expected) as caught:
        await agent_support.run_personal_context_agent(
            model_client=cast(Any, object()),
            model_request=cast(Any, object()),
            sandbox_path=sandbox,
            messages=[UserMessage(content="summarize")],
            validate_result=lambda _text, _path: [],
        )

    assert "clear_session" in [event[0] for event in events]
    assert "rail_uninit" in [event[0] for event in events]
    assert [event[0] for event in events].count("security_rail_uninit") == 1
    assert [event[0] for event in events].count("resilience_rail_uninit") == 1
    assert len(created["agents"][0].invocations) == 1
    assert created["agents"][0].react_agent.unregistered_callbacks == (
        created["agents"][0].react_agent.registered_callbacks
    )
    if isinstance(failure, PermissionError):
        assert caught.value.details == {"fallback_allowed": False}
    if isinstance(failure, BaseError):
        assert caught.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR


@pytest.mark.asyncio
async def test_run_personal_context_agent_configures_explicit_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, Any]] = []
    created = _patch_agent_runtime(monkeypatch, outputs_by_agent=[[AssistantMessage(content="ok")]], events=events)
    monkeypatch.setattr(agent_support, "LocalWorkConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperationCard", lambda **kwargs: kwargs)
    monkeypatch.setattr(agent_support, "SysOperation", lambda card: ("sysop", card))
    monkeypatch.setattr(agent_support, "OperationMode", type("Mode", (), {"LOCAL": "local"}))
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    await agent_support.run_personal_context_agent(
        model_client=cast(Any, object()),
        model_request=cast(Any, object()),
        sandbox_path=sandbox,
        messages=[UserMessage(content="summarize")],
        validate_result=lambda _text, _path: [],
    )

    rail_kwargs = next(event[1] for event in events if event[0] == "rail")
    assert rail_kwargs["with_code_tool"] is False
    assert rail_kwargs["read_only"] is False
    factory_kwargs = next(event[1] for event in events if event[0] == "create_agent")
    assert factory_kwargs["rails"] == [
        created["rails"][0],
        created["processor_rails"][0],
        created["security_rails"][0],
        created["resilience_rails"][0],
    ]
    system_prompt = factory_kwargs["system_prompt"]
    assert "inputs" in system_prompt
    assert "tmp" in system_prompt
    assert "briefing" in system_prompt
    assert "small runs" in system_prompt
    assert "every bounded source preview" in system_prompt
    assert "large runs" in system_prompt
    assert "direct file tools" in system_prompt
    assert "validate.py" in system_prompt
    assert "one lightweight self-check" in system_prompt
    assert "personal_context_provenance_manifest.json" not in system_prompt
    assert "source-proofs" not in system_prompt
    assert "validate_manifest.ps1" not in system_prompt
    workspace = factory_kwargs["workspace"]
    assert workspace.root_path == str(sandbox.resolve())
    assert workspace.directories == []
    assert factory_kwargs["auto_create_workspace"] is False
    assert factory_kwargs["restrict_to_work_dir"] is True
    assert factory_kwargs["enable_task_loop"] is False
    assert factory_kwargs["add_general_purpose_agent"] is False
    assert factory_kwargs["parallel_tool_calls"] is False
    assert factory_kwargs["enable_read_image_multimodal"] is False
    assert factory_kwargs[agent_support._DEFAULT_RETRY_RAIL_FLAG] is False
    assert factory_kwargs["max_iterations"] == 100
    _, sys_card = factory_kwargs["sys_operation"]
    assert sys_card["mode"] == "local"
    assert sys_card["work_config"]["restrict_to_sandbox"] is True
    assert sys_card["work_config"]["sandbox_root"] == [str(sandbox.resolve())]
    assert sys_card["work_config"]["shell_allowlist"]
