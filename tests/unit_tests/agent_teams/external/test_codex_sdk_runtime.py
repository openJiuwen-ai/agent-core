# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the Codex Python SDK member runtime."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.external.cli_agent.codex.runtime import (
    CodexSdkRuntime,
    _json_arguments,
    _start_thread_with_raw_events,
    _tool_result,
)


def _notification(method: str, **payload):
    return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))


def _item_notification(method: str, item):
    return _notification(method, item=SimpleNamespace(root=item))


def _raw_notification(method: str, **params):
    return SimpleNamespace(
        method=method,
        payload=SimpleNamespace(params=params),
    )


class _FakeTurnHandle:
    def __init__(self, notifications):
        self.notifications = notifications
        self.steered: list[str] = []
        self.interrupt_count = 0

    async def stream(self):
        for notification in self.notifications:
            yield notification

    async def steer(self, content: str):
        self.steered.append(content)

    async def interrupt(self):
        self.interrupt_count += 1


class _FakeThread:
    def __init__(self, thread_id: str, turns):
        self.id = thread_id
        self._turns = list(turns)
        self.prompts: list[str] = []
        self.handles: list[_FakeTurnHandle] = []

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        handle = _FakeTurnHandle(self._turns.pop(0))
        self.handles.append(handle)
        return handle


class _BlockingTurnHandle(_FakeTurnHandle):
    def __init__(self):
        super().__init__([])
        self.streaming = asyncio.Event()
        self.block = asyncio.Event()

    async def stream(self):
        self.streaming.set()
        await self.block.wait()
        if False:  # pragma: no cover - make this an async generator
            yield None


class _BlockingThread(_FakeThread):
    def __init__(self, thread_id: str):
        super().__init__(thread_id, [])
        self.handle = _BlockingTurnHandle()

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        self.handles.append(self.handle)
        return self.handle


class _SdkPathString:
    def __init__(self, value: str):
        self.value = value

    def __str__(self) -> str:
        return self.value


def test_codex_json_arguments_stringifies_sdk_path_values():
    arguments = _json_arguments(
        {
            "path": _SdkPathString("codex-report.txt"),
            "nested": {"paths": [_SdkPathString(".team/workspace")]},
        }
    )

    assert json.loads(arguments) == {
        "path": "codex-report.txt",
        "nested": {"paths": [".team/workspace"]},
    }


class _HandleSequenceThread(_FakeThread):
    def __init__(self, thread_id: str, handles):
        super().__init__(thread_id, [])
        self._handles = list(handles)

    async def turn(self, prompt: str):
        self.prompts.append(prompt)
        handle = self._handles.pop(0)
        self.handles.append(handle)
        return handle


class _NotificationThenBlockingTurnHandle(_BlockingTurnHandle):
    async def stream(self):
        self.streaming.set()
        yield _notification("item/agentMessage/delta", delta="started")
        await self.block.wait()


class _FakeRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message


class _RejectingSteerHandle(_FakeTurnHandle):
    def __init__(self, error: Exception):
        super().__init__([])
        self.error = error

    async def steer(self, content: str):
        self.steered.append(content)
        raise self.error


class _FakeAsyncCodex:
    def __init__(self, *, config, thread, resume_error: Exception | None = None):
        self.config = config
        self.thread = thread
        self.resume_error = resume_error
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []
        self.close_count = 0

    async def thread_start(self, **options):
        self.start_calls.append(options)
        return self.thread

    async def thread_resume(self, thread_id: str, **options):
        self.resume_calls.append((thread_id, options))
        if self.resume_error is not None:
            raise self.resume_error
        return self.thread

    async def close(self):
        self.close_count += 1


class _FakeMemberSession:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.pre_run_count = 0
        self.commit_count = 0
        self.post_run_count = 0

    async def pre_run(self):
        self.pre_run_count += 1

    def get_state(self, key=None):
        return self.state if key is None else self.state.get(key)

    def update_state(self, data):
        self.state.update(data)

    async def commit(self):
        self.commit_count += 1

    async def post_run(self):
        self.post_run_count += 1
        await self.commit()


class _FakeTeamSession:
    def __init__(self, member_session):
        self.member_session = member_session
        self.created: list[tuple[str, bool]] = []

    def create_agent_session(self, *, agent_id, share_stream_writer):
        self.created.append((agent_id, share_stream_writer))
        return self.member_session


def _saved_state(thread_id: str):
    return {
        "external_runtime": {
            "backend": "codex",
            "external_session_id": thread_id,
        }
    }


def _runtime(
    *,
    thread,
    thread_id=None,
    resume_error=None,
    member_state=None,
    turn_idle_timeout_s=180.0,
    turn_idle_retries=1,
):
    client = _FakeAsyncCodex(config=None, thread=thread, resume_error=resume_error)
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: client)
    member_session = _FakeMemberSession(
        member_state if member_state is not None else (_saved_state(thread_id) if thread_id else None)
    )
    team_session = _FakeTeamSession(member_session)
    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        team_name="team",
        team_session_id="session",
        sdk=sdk,
        config=SimpleNamespace(name="config"),
        thread_options={
            "ephemeral": False,
            "config": {"model_reasoning_summary": "detailed"},
            "cwd": "/workspace",
            "developer_instructions": "role prompt",
        },
        resume_external_backend=thread_id is not None,
        turn_idle_timeout_s=turn_idle_timeout_s,
        turn_idle_retries=turn_idle_retries,
    )
    runtime._test_team_session = team_session
    runtime._test_member_session = member_session
    return runtime, client


async def _start(runtime):
    await runtime.start(team_session=runtime._test_team_session)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_reuses_thread_and_maps_stream_events():
    tool = SimpleNamespace(
        type="mcpToolCall",
        id="tool-1",
        server="openjiuwen-team",
        tool="send_message",
        arguments={"recipient": "leader"},
        result={"ok": True},
        error=None,
    )
    first_turn = [
        _notification("item/reasoning/textDelta", delta="thinking"),
        _item_notification("item/started", tool),
        _item_notification("item/completed", tool),
        _notification("item/agentMessage/delta", delta="done"),
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    second_turn = [
        _notification("item/agentMessage/delta", delta="continued"),
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    thread = _FakeThread("thread-developer", [first_turn, second_turn])
    runtime, client = _runtime(thread=thread)

    await _start(runtime)
    first = [chunk async for chunk in runtime._drive({"query": "first"})]
    second = [chunk async for chunk in runtime._drive({"query": "second"})]

    assert runtime.session_id == "thread-developer"
    assert client.start_calls == [
        {
            "experimental_raw_events": True,
            "ephemeral": False,
            "config": {"model_reasoning_summary": "detailed"},
            "cwd": "/workspace",
            "developer_instructions": "role prompt",
        }
    ]
    assert thread.prompts == ["first", "second"]
    assert [chunk.type for chunk in first] == [
        "llm_reasoning",
        "tool_call",
        "tool_result",
        "llm_output",
    ]
    assert first[1].payload == {
        "name": "openjiuwen-team.send_message",
        "arguments": '{"recipient": "leader"}',
        "tool_call_id": "tool-1",
    }
    assert first[2].payload == {
        "tool_name": "openjiuwen-team.send_message",
        "result": '{"ok": true}',
        "tool_call_id": "tool-1",
    }
    assert first[3].payload["content"] == "done"
    assert second[0].payload["content"] == "continued"

    await runtime.aclose()
    await runtime.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_waits_for_native_api_logs_before_finishing_turn():
    events: list[str] = []

    class _SpanBridge:
        def start_turn(self, **_):
            events.append("start")

        def append_output(self, _):
            pass

        def append_reasoning(self, _):
            pass

        def record_model_usage(self, **_):
            pass

        def append_raw_response_item(self, _):
            pass

        def complete_model_response(self, **_):
            pass

        def start_tool(self, **_):
            pass

        def finish_tool(self, **_):
            pass

        def record_error(self, _, **__):
            pass

        async def wait_for_native_observations(self):
            events.append("wait")

        def finish_turn(self, **_):
            events.append("finish")

    thread = _FakeThread(
        "thread-developer",
        [
            [
                _notification(
                    "turn/completed",
                    turn=SimpleNamespace(status="completed", error=None),
                ),
            ],
        ],
    )
    runtime, _ = _runtime(thread=thread)
    runtime._span_bridge = _SpanBridge()

    await _start(runtime)
    _ = [chunk async for chunk in runtime._drive({"query": "inspect task"})]

    assert events == ["start", "wait", "finish"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_emits_turn_and_tool_spans():
    exporter_module = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.extensions.observability.semconv import (
        GEN_AI_TOOL_INPUT,
        GEN_AI_TOOL_OUTPUT,
        LANGFUSE_OBSERVATION_INPUT,
        LANGFUSE_OBSERVATION_OUTPUT,
    )

    InMemorySpanExporter = exporter_module.InMemorySpanExporter
    exporter = InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="codex-runtime-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    try:
        from openjiuwen.agent_teams.observability.setup import get_tracer
        from openjiuwen.agent_teams.observability.span_context import (
            clear_team_span,
            get_or_create_team_span,
        )

        team_span = get_or_create_team_span(
            "team",
            get_tracer("codex-runtime-test"),
        )
        assert team_span is not None

        started_tool = SimpleNamespace(
            type="mcpToolCall",
            id="tool-1",
            server="openjiuwen-team",
            tool="claim_task",
            arguments={"task_id": "task-1", "status": "claimed"},
            result=None,
            error=None,
        )
        completed_tool = SimpleNamespace(
            type="mcpToolCall",
            id="tool-1",
            server="openjiuwen-team",
            tool="claim_task",
            arguments={"task_id": "task-1", "status": "claimed"},
            result={"status": "claimed"},
            error=None,
        )
        thread = _FakeThread(
            "thread-developer",
            [
                [
                    _item_notification("item/started", started_tool),
                    _item_notification("item/completed", completed_tool),
                    _notification("item/agentMessage/delta", delta="task claimed"),
                    _notification(
                        "turn/completed",
                        turn=SimpleNamespace(status="completed", error=None),
                    ),
                ]
            ],
        )
        runtime, _ = _runtime(thread=thread)

        await _start(runtime)
        clear_team_span()
        _ = [chunk async for chunk in runtime._drive({"query": "claim task-1"})]

        spans = list(exporter.get_finished_spans())
        turn_span = next(span for span in spans if span.name == "agent.developer.codex_turn.1")
        tool_span = next(span for span in spans if span.name == "tool.claim_task")

        assert turn_span.parent is not None
        assert turn_span.parent.span_id == team_span.context.span_id
        assert tool_span.parent is not None
        assert tool_span.parent.span_id == turn_span.context.span_id
        assert turn_span.attributes[LANGFUSE_OBSERVATION_INPUT] == "claim task-1"
        assert turn_span.attributes[LANGFUSE_OBSERVATION_OUTPUT] == "task claimed"
        assert '"task_id": "task-1"' in tool_span.attributes[GEN_AI_TOOL_INPUT]
        assert '"status": "claimed"' in tool_span.attributes[GEN_AI_TOOL_OUTPUT]
    finally:
        shutdown_observability()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_keeps_sdk_response_as_separate_summary():
    exporter_module = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.extensions.observability.semconv import (
        GEN_AI_USAGE_COMPLETION_TOKENS,
        GEN_AI_USAGE_PROMPT_TOKENS,
        GEN_AI_USAGE_TOTAL_TOKENS,
        LANGFUSE_OBSERVATION_OUTPUT,
    )

    exporter = exporter_module.InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="codex-raw-response-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    try:
        from openjiuwen.agent_teams.observability.setup import get_tracer
        from openjiuwen.agent_teams.observability.span_context import (
            clear_team_span,
            get_or_create_team_span,
        )

        assert get_or_create_team_span("team", get_tracer("codex-raw-response-test")) is not None
        tool = SimpleNamespace(
            type="mcpToolCall",
            id="tool-1",
            server="openjiuwen-team",
            tool="view_task",
            arguments={"task_id": "task-1"},
            result={"status": "pending"},
            error=None,
        )
        thread = _FakeThread(
            "thread-developer",
            [
                [
                    _notification("item/reasoning/textDelta", delta="inspect task"),
                    _raw_notification(
                        "rawResponseItem/completed",
                        item={"type": "function_call", "name": "view_task"},
                    ),
                    _raw_notification(
                        "rawResponse/completed",
                        responseId="response-1",
                        usage={
                            "inputTokens": 100,
                            "cachedInputTokens": 20,
                            "outputTokens": 10,
                            "reasoningOutputTokens": 4,
                            "totalTokens": 110,
                        },
                    ),
                    _item_notification("item/started", tool),
                    _item_notification("item/completed", tool),
                    _notification("item/reasoning/textDelta", delta="report result"),
                    _notification("item/agentMessage/delta", delta="task is pending"),
                    _raw_notification(
                        "rawResponseItem/completed",
                        item={"type": "message", "role": "assistant"},
                    ),
                    _raw_notification(
                        "rawResponse/completed",
                        responseId="response-2",
                        usage={
                            "inputTokens": 130,
                            "cachedInputTokens": 100,
                            "outputTokens": 15,
                            "reasoningOutputTokens": 5,
                            "totalTokens": 145,
                        },
                    ),
                    _notification(
                        "turn/completed",
                        turn=SimpleNamespace(status="completed", error=None),
                    ),
                ]
            ],
        )
        runtime, _ = _runtime(thread=thread)

        await _start(runtime)
        clear_team_span()
        _ = [chunk async for chunk in runtime._drive({"query": "inspect task-1"})]

        spans = list(exporter.get_finished_spans())
        assert not [span for span in spans if span.name == "llm.call"]
        summary = next(span for span in spans if span.name == "codex.sdk.summary")
        assert summary.attributes["codex.response.ids"] == (
            "response-1",
            "response-2",
        )
        assert summary.attributes[GEN_AI_USAGE_PROMPT_TOKENS] == 130
        assert summary.attributes[GEN_AI_USAGE_COMPLETION_TOKENS] == 15
        assert summary.attributes[GEN_AI_USAGE_TOTAL_TOKENS] == 145
        assert "task is pending" in summary.attributes[LANGFUSE_OBSERVATION_OUTPUT]
        reasoning_span = next(span for span in spans if span.name == "llm.reasoning")
        assert reasoning_span.parent.span_id == summary.context.span_id
        assert (
            next(span for span in spans if span.name == "tool.view_task").parent.span_id
            == next(span for span in spans if span.name == "agent.developer.codex_turn.1").context.span_id
        )
    finally:
        shutdown_observability()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_native_model_request_sets_exact_llm_span_timing(
):
    exporter_module = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge
    exporter = exporter_module.InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="codex-native-api-request-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    try:
        from openjiuwen.agent_teams.observability.setup import get_tracer
        from openjiuwen.agent_teams.observability.span_context import (
            clear_team_span,
            get_or_create_team_span,
        )

        assert (
            get_or_create_team_span(
                "team",
                get_tracer("codex-native-api-request-test"),
            )
            is not None
        )
        bridge = CodexSpanBridge(
            member_name="developer",
            member_agent_id="team_developer",
            team_name="team",
            session_id="session",
        )
        bridge.enable_native_model_spans()
        bridge.start_turn(
            prompt="inspect task-1",
            thread_id="thread-developer",
            developer_instructions="role prompt",
            model="gpt-test",
        )
        clear_team_span()

        end_ns = time.time_ns()
        async def deliver_native_span():
            await asyncio.sleep(0)
            bridge.record_native_model_span(
                {
                    "name": "run_sampling_request",
                    "start_time_ns": end_ns - 25_000_000,
                    "end_time_ns": end_ns,
                    "trace_id": "11" * 16,
                    "span_id": "22" * 8,
                    "parent_span_id": "33" * 8,
                    "status_code": 1,
                    "attributes": {
                        "turn_id": "turn-native",
                        "model": "gpt-native",
                    },
                },
            )

        delivery = asyncio.create_task(deliver_native_span())
        await bridge.wait_for_native_observations()
        await delivery
        bridge.finish_turn(status="completed")

        spans = list(exporter.get_finished_spans())
        llm_span = next(span for span in spans if span.name == "llm.call")
        assert llm_span.start_time == end_ns - 25_000_000
        assert llm_span.end_time == end_ns
        assert llm_span.attributes["codex.observation.granularity"] == "native_sampling_span"
        assert llm_span.attributes["codex.model.call.boundary"] == "run_sampling_request"
        assert llm_span.attributes["codex.model.call.boundary_exact"] is True
        assert llm_span.attributes["codex.model.call.start_observed"] is True
        assert llm_span.attributes["codex.model.call.paired"] is False
        assert llm_span.attributes["gen_ai.request.model"] == "gpt-native"
        assert llm_span.attributes["codex.turn.id"] == "turn-native"
        assert llm_span.attributes["codex.native.trace_id"] == "11" * 16
        assert llm_span.attributes["codex.native.span_id"] == "22" * 8
    finally:
        shutdown_observability()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_native_mode_preserves_exact_unpaired_span():
    exporter_module = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge

    exporter = exporter_module.InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="codex-unpaired-observation-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    try:
        from openjiuwen.agent_teams.observability.setup import get_tracer
        from openjiuwen.agent_teams.observability.span_context import (
            get_or_create_team_span,
        )

        assert get_or_create_team_span(
            "team",
            get_tracer("codex-unpaired-observation-test"),
        ) is not None
        bridge = CodexSpanBridge(
            member_name="developer",
            member_agent_id="team_developer",
            team_name="team",
            session_id="session",
        )
        bridge.enable_native_model_spans()
        bridge.start_turn(
            prompt="inspect task",
            thread_id="thread-developer",
            model="gpt-test",
        )
        end_ns = time.time_ns()
        bridge.record_native_model_span(
            {
                "name": "run_sampling_request",
                "start_time_ns": end_ns - 10_000_000,
                "end_time_ns": end_ns,
                "attributes": {
                    "turn_id": "turn-1",
                    "model": "gpt-test",
                },
            },
        )
        bridge.finish_turn(status="completed")

        llm_span = next(
            span for span in exporter.get_finished_spans() if span.name == "llm.call"
        )
        assert llm_span.attributes["codex.observation.granularity"] == "native_sampling_span"
        assert llm_span.attributes["codex.model.call.observed"] is True
        assert llm_span.attributes["codex.model.call.paired"] is False
        assert llm_span.start_time == end_ns - 10_000_000
        assert llm_span.end_time == end_ns
    finally:
        shutdown_observability()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_turn_does_not_infer_llm_call_when_native_export_is_missing():
    exporter_module = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from openjiuwen.agent_teams.observability import (
        ObservabilityConfig,
        init_observability,
        shutdown_observability,
    )
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge

    exporter = exporter_module.InMemorySpanExporter()
    init_observability(
        ObservabilityConfig(
            enabled=True,
            service_name="codex-missing-native-export-test",
            sample_rate=1.0,
        ),
        span_exporter_override=exporter,
    )
    try:
        from openjiuwen.agent_teams.observability.setup import get_tracer
        from openjiuwen.agent_teams.observability.span_context import (
            get_or_create_team_span,
        )

        assert get_or_create_team_span(
            "team",
            get_tracer("codex-missing-native-export-test"),
        ) is not None
        bridge = CodexSpanBridge(
            member_name="developer",
            member_agent_id="team_developer",
            team_name="team",
            session_id="session",
        )
        bridge.enable_native_model_spans()
        bridge.start_turn(
            prompt="inspect task",
            thread_id="thread-developer",
            model="gpt-test",
        )
        bridge.finish_turn(status="failed", error="transport closed")

        spans = list(exporter.get_finished_spans())
        assert not [span for span in spans if span.name == "llm.call"]
        turn_span = next(
            span for span in spans if span.name == "agent.developer.codex_turn.1"
        )
        assert turn_span.attributes["codex.native.model_span_count"] == 0
    finally:
        shutdown_observability()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_thread_start_uses_low_level_raw_event_compatibility():
    sdk_module = pytest.importorskip("openai_codex")
    requests: list[dict] = []

    class _LowLevelClient:
        async def thread_start(self, params):
            requests.append(params)
            return SimpleNamespace(thread=SimpleNamespace(id="thread-raw"))

    class _HighLevelClient:
        def __init__(self):
            self._client = _LowLevelClient()
            self.initialized = False

        async def _ensure_initialized(self):
            self.initialized = True

        async def thread_start(self, *, cwd=None, ephemeral=None):
            raise AssertionError("the public start method must not be used")

    client = _HighLevelClient()
    sdk = SimpleNamespace(
        ApprovalMode=sdk_module.ApprovalMode,
        AsyncThread=lambda owner, thread_id: SimpleNamespace(owner=owner, id=thread_id),
    )

    thread = await _start_thread_with_raw_events(
        client=client,
        sdk=sdk,
        options={"cwd": "/workspace", "ephemeral": False},
    )

    assert client.initialized is True
    assert thread.id == "thread-raw"
    assert requests[0]["experimentalRawEvents"] is True
    assert requests[0]["cwd"] == "/workspace"
    assert requests[0]["ephemeral"] is False
    assert requests[0]["approvalPolicy"] == "on-request"
    assert requests[0]["approvalsReviewer"] == "auto_review"


@pytest.mark.parametrize(
    "result",
    [
        pytest.param([], id="empty-list"),
        pytest.param({}, id="empty-dict"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-string"),
        pytest.param(False, id="false"),
    ],
)
@pytest.mark.level0
def test_codex_sdk_runtime_preserves_falsy_mcp_tool_results(result):
    item = SimpleNamespace(
        type="mcpToolCall",
        result=result,
        error={"message": "must not replace a valid falsy result"},
    )

    actual = _tool_result(item)

    assert actual == json.dumps(result, ensure_ascii=False)


@pytest.mark.level0
def test_codex_sdk_runtime_uses_mcp_error_when_result_is_none():
    error = {"message": "tool failed"}
    item = SimpleNamespace(type="mcpToolCall", result=None, error=error)

    assert _tool_result(item) == '{"message": "tool failed"}'


@pytest.mark.level0
def test_codex_sdk_runtime_returns_command_output_as_tool_result():
    item = SimpleNamespace(type="commandExecution", aggregated_output="codex reporter in\n", exit_code=0)

    assert _tool_result(item) == "codex reporter in\n"


@pytest.mark.level0
def test_codex_sdk_runtime_returns_exit_code_when_command_output_is_empty():
    item = SimpleNamespace(type="commandExecution", aggregated_output="", exit_code=0)

    assert _tool_result(item) == "exit_code=0"


@pytest.mark.level0
def test_codex_sdk_runtime_joins_text_block_tool_results():
    item = SimpleNamespace(
        type="dynamicToolCall",
        content_items=[
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ],
    )

    assert _tool_result(item) == "first\nsecond"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_resumes_saved_thread_id_without_ephemeral():
    thread = _FakeThread("thread-saved", [[]])
    runtime, client = _runtime(thread=thread, thread_id="thread-saved")

    await _start(runtime)

    assert client.start_calls == []
    assert client.resume_calls == [
        (
            "thread-saved",
            {
                "config": {"model_reasoning_summary": "detailed"},
                "cwd": "/workspace",
                "developer_instructions": "role prompt",
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_resume_failure_never_starts_replacement_thread():
    thread = _FakeThread("thread-saved", [[]])
    runtime, client = _runtime(
        thread=thread,
        thread_id="thread-saved",
        resume_error=RuntimeError("thread missing"),
    )

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)

    assert client.resume_calls == [
        (
            "thread-saved",
            {
                "config": {"model_reasoning_summary": "detailed"},
                "cwd": "/workspace",
                "developer_instructions": "role prompt",
            },
        )
    ]
    assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_rejects_unexpected_resumed_thread_id():
    thread = _FakeThread("thread-other", [[]])
    runtime, client = _runtime(thread=thread, thread_id="thread-saved")

    with pytest.raises(RuntimeError, match="resumed unexpected thread"):
        await _start(runtime)

    assert runtime._thread is None
    assert runtime.session_id == "thread-saved"
    assert client.start_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_checkpoints_new_thread_id_once():
    thread = _FakeThread("thread-new", [[], []])
    runtime, _ = _runtime(thread=thread)

    await _start(runtime)
    await _start(runtime)
    _ = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert runtime._test_member_session.state == _saved_state("thread-new")
    assert runtime._test_member_session.commit_count == 1
    assert runtime._test_team_session.created == [("team_developer", False)]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_rewrite_restored_thread_id():
    thread = _FakeThread("thread-saved", [[]])
    runtime, _ = _runtime(thread=thread, thread_id="thread-saved")

    await _start(runtime)

    assert runtime._test_member_session.commit_count == 0


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_strict_resume_rejects_missing_member_checkpoint():
    thread = _FakeThread("thread-new", [[]])
    runtime, client = _runtime(thread=thread)
    runtime._resume_external_backend = True

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)

    assert client.start_calls == []
    assert client.resume_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_ignores_other_backend_checkpoint_on_strict_resume():
    thread = _FakeThread("thread-new", [[]])
    runtime, _ = _runtime(
        thread=thread,
        member_state={
            "external_runtime": {
                "backend": "claude",
                "external_session_id": "claude-session",
            }
        },
    )
    runtime._resume_external_backend = True

    with pytest.raises(RuntimeError, match="strict resume forbids"):
        await _start(runtime)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_steers_and_interrupts_active_turn():
    thread = _FakeThread("thread-developer", [[]])
    runtime, client = _runtime(thread=thread)
    await _start(runtime)
    handle = _FakeTurnHandle([])
    runtime._active_turn = handle

    await runtime.steer("new priority")
    await runtime._abort_turn()

    assert handle.steered == ["new priority"]
    assert handle.interrupt_count == 1
    await runtime.aclose()
    assert client.close_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_queues_steer_when_server_turn_already_ended():
    thread = _FakeThread("thread-developer", [[]])
    runtime, _ = _runtime(thread=thread)
    handle = _RejectingSteerHandle(_FakeRpcError(-32600, "no active turn to steer"))
    runtime._active_turn = handle

    await runtime.steer("deliver on next turn")

    assert handle.steered == ["deliver on next turn"]
    assert runtime._active_turn is None
    assert runtime._pending == ["deliver on next turn"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_queue_unrelated_steer_errors():
    thread = _FakeThread("thread-developer", [[]])
    runtime, _ = _runtime(thread=thread)
    error = _FakeRpcError(-32600, "invalid steer input")
    handle = _RejectingSteerHandle(error)
    runtime._active_turn = handle

    with pytest.raises(_FakeRpcError, match="invalid steer input"):
        await runtime.steer("bad input")

    assert runtime._active_turn is handle
    assert runtime._pending == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_reports_non_retryable_sdk_errors():
    error = SimpleNamespace(message="boom")
    turn = [_notification("error", error=error, will_retry=False)]
    thread = _FakeThread("thread-developer", [turn])
    runtime, _ = _runtime(thread=thread)
    await _start(runtime)

    with pytest.raises(RuntimeError, match="codex SDK turn failed"):
        async for _ in runtime._drive({"query": "fail"}):
            pass


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_retries_silent_turn_on_same_thread():
    stalled = _BlockingTurnHandle()
    recovered = _FakeTurnHandle(
        [
            _notification("item/agentMessage/delta", delta="recovered"),
            _notification("turn/completed", turn=SimpleNamespace(status="completed")),
        ]
    )
    thread = _HandleSequenceThread("thread-developer", [stalled, recovered])
    runtime, client = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)

    chunks = await asyncio.wait_for(_collect_drive(runtime, "same prompt"), timeout=1.0)

    assert [chunk.payload["content"] for chunk in chunks] == ["recovered"]
    assert thread.prompts == ["same prompt", "same prompt"]
    assert stalled.interrupt_count == 1
    assert len(client.start_calls) == 1
    assert client.resume_calls == []


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_does_not_replay_turn_after_any_notification():
    stalled = _NotificationThenBlockingTurnHandle()
    unused = _FakeTurnHandle([])
    thread = _HandleSequenceThread("thread-developer", [stalled, unused])
    runtime, _ = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)
    chunks = []

    with pytest.raises(RuntimeError, match="produced no turn events"):
        async for chunk in runtime._drive({"query": "do not replay"}):
            chunks.append(chunk)

    assert [chunk.payload["content"] for chunk in chunks] == ["started"]
    assert thread.prompts == ["do not replay"]
    assert stalled.interrupt_count == 1


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_bounds_silent_turn_retries():
    first = _BlockingTurnHandle()
    second = _BlockingTurnHandle()
    thread = _HandleSequenceThread("thread-developer", [first, second])
    runtime, _ = _runtime(
        thread=thread,
        turn_idle_timeout_s=0.01,
        turn_idle_retries=1,
    )
    await _start(runtime)

    with pytest.raises(RuntimeError, match="produced no turn events"):
        async for _ in runtime._drive({"query": "bounded"}):
            pass

    assert thread.prompts == ["bounded", "bounded"]
    assert first.interrupt_count == 1
    assert second.interrupt_count == 1


async def _collect_drive(runtime, query):
    return [chunk async for chunk in runtime._drive({"query": query})]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_queues_follow_up_for_same_thread():
    thread = _FakeThread("thread-developer", [[], []])
    runtime, _ = _runtime(thread=thread)
    await _start(runtime)
    await runtime.follow_up("next")

    chunks = [chunk async for chunk in runtime._drive({"query": "first"})]

    assert chunks == []
    assert thread.prompts == ["first", "next"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_sdk_runtime_stop_does_not_wait_for_stuck_stream():
    thread = _BlockingThread("thread-developer")
    runtime, client = _runtime(thread=thread)
    await _start(runtime)
    await runtime.send("long task")
    await thread.handle.streaming.wait()

    await asyncio.wait_for(runtime.stop(), timeout=1.0)

    assert thread.handle.interrupt_count == 1
    assert client.close_count == 1
    assert runtime._test_member_session.post_run_count == 1

    await runtime.stop()
    assert runtime._test_member_session.post_run_count == 1
