# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the Codex observability wiring around the protocol harness.

The provider-private ``notification_observer`` feeds raw SDK notifications
into the team ``CodexSpanBridge``; the spawn path starts the OTel receiver
and rollout reader and threads their config/env into ``CodexHarnessConfig``.
Both are driven here with fakes; no OTel exporter or Codex process is used.
"""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent import spawn as spawn_mod
from openjiuwen.agent_teams.external.cli_agent.codex.observer import build_codex_notification_observer
from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType
from tests.test_logger import logger


class _Status(Enum):
    failed = "failed"
    completed = "completed"


class _RecordingBridge:
    """Records every span-bridge call the observer makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, kwargs or args))

        return _record


def _notification(method: str, **payload: Any) -> Any:
    return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))


def _item(**fields: Any) -> Any:
    return SimpleNamespace(item=SimpleNamespace(root=SimpleNamespace(**fields)))


@pytest.mark.level1
def test_observer_routes_notifications_to_the_span_bridge() -> None:
    bridge = _RecordingBridge()
    observe = build_codex_notification_observer(bridge)

    observe(_notification("item/agentMessage/delta", delta="Hel", item_id="m", thread_id="t", turn_id="x"))
    observe(_notification("item/reasoning/textDelta", delta="think", item_id="r", thread_id="t", turn_id="x"))
    observe(
        _notification(
            "thread/tokenUsage/updated",
            thread_id="t",
            turn_id="x",
            token_usage=SimpleNamespace(
                last=SimpleNamespace(input_tokens=3, cached_input_tokens=1, output_tokens=2, reasoning_output_tokens=0, total_tokens=5),
                total=SimpleNamespace(total_tokens=9),
            ),
        )
    )
    observe(_notification("item/started", **_item(id="cmd-1", type="commandExecution", command="ls", cwd="/w").__dict__))
    observe(
        _notification(
            "item/completed",
            **_item(id="cmd-1", type="commandExecution", command="ls", cwd="/w", aggregated_output="ok", status=_Status.failed, error=None).__dict__,
        )
    )
    observe(_notification("item/started", **_item(id="mcp-1", type="mcpToolCall", server="team", tool="view_task", arguments={"a": 1}).__dict__))
    observe(_notification("item/started", **_item(id="msg-1", type="agentMessage", text="ignored").__dict__))
    observe(_notification("error", error=SimpleNamespace(message="boom"), will_retry=True, thread_id="t", turn_id="x"))
    observe(_notification("rawResponseItem/completed", params={"item": {"type": "message"}}))
    observe(_notification("rawResponse/completed", params={"responseId": "resp-1", "usage": {"total_tokens": 5}}))
    observe(_notification("turn/completed", turn=SimpleNamespace(id="x", status=_Status.completed)))

    names = [name for name, _ in bridge.calls]
    logger.info("observer bridge calls: %s", names)
    assert names == [
        "append_output",
        "append_reasoning",
        "record_model_usage",
        "start_tool",
        "finish_tool",
        "start_tool",
        "record_error",
        "append_raw_response_item",
        "complete_model_response",
    ]
    usage = dict(bridge.calls[2][1])
    assert usage == {
        "input_tokens": 3,
        "cached_input_tokens": 1,
        "output_tokens": 2,
        "reasoning_output_tokens": 0,
        "total_tokens": 5,
        "thread_total_tokens": 9,
    }
    finish_tool = dict(bridge.calls[4][1])
    assert finish_tool["call_id"] == "cmd-1" and finish_tool["tool_name"] == "shell"
    assert finish_tool["tool_result"] == "ok"
    assert finish_tool["error"] == {"status": "failed"}
    mcp_start = dict(bridge.calls[5][1])
    assert mcp_start["tool_name"] == "team.view_task" and mcp_start["server_name"] == "team"
    assert mcp_start["tool_args"] == {"a": 1}
    record_error = dict(bridge.calls[6][1])
    assert record_error["will_retry"] is True
    complete = dict(bridge.calls[8][1])
    assert complete == {"response_id": "resp-1", "usage": {"total_tokens": 5}}


@pytest.mark.level1
def test_observer_swallows_nothing_but_ignores_unknown_methods() -> None:
    bridge = _RecordingBridge()
    observe = build_codex_notification_observer(bridge)
    observe(_notification("thread/started", thread_id="t"))
    observe(_notification("item/started", **_item(id="r-1", type="reasoning").__dict__))
    assert bridge.calls == []


class _FakeReceiver:
    instances: list["_FakeReceiver"] = []

    def __init__(self) -> None:
        self.endpoint = "http://127.0.0.1:4318/v1/traces"
        self.closed = False
        _FakeReceiver.instances.append(self)

    @classmethod
    async def start(cls, callback: Any) -> "_FakeReceiver":
        _ = callback
        return cls()

    async def aclose(self) -> None:
        self.closed = True


class _FakeRolloutReader:
    instances: list["_FakeRolloutReader"] = []

    def __init__(self) -> None:
        self.root = "/tmp/rollout-root"
        self.closed = False
        _FakeRolloutReader.instances.append(self)

    @classmethod
    async def start(cls, callback: Any) -> "_FakeRolloutReader":
        _ = callback
        return cls()

    async def aclose(self) -> None:
        self.closed = True


class _FakeSpanBridge:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.rollout_enabled = False
        self.native_enabled = False

    def native_traceparent(self) -> str:
        return "00-trace-span-01"

    def enable_rollout_trace(self) -> None:
        self.rollout_enabled = True

    def enable_native_model_spans(self) -> None:
        self.native_enabled = True

    def record_rollout_event(self, event: Any) -> None:
        _ = event

    def record_native_model_span(self, event: Any) -> None:
        _ = event

    def start_turn(self, **kwargs: Any) -> None:
        _ = kwargs

    def finish_turn(self, *, status: str, error: Any | None = None) -> None:
        _ = status, error


def _install_fake_observability(monkeypatch: pytest.MonkeyPatch, *, initialized: bool) -> None:
    import openjiuwen.agent_teams.observability.codex as codex_observability
    import openjiuwen.agent_teams.observability.setup as setup_mod

    monkeypatch.setattr(setup_mod, "is_initialized", lambda: initialized)
    monkeypatch.setattr(codex_observability, "CodexOtelTraceReceiver", _FakeReceiver)
    monkeypatch.setattr(codex_observability, "CodexRolloutTraceReader", _FakeRolloutReader)
    monkeypatch.setattr(codex_observability, "CodexSpanBridge", _FakeSpanBridge)
    _FakeReceiver.instances.clear()
    _FakeRolloutReader.instances.clear()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_observability_is_inert_when_not_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_observability(monkeypatch, initialized=False)
    result = await spawn_mod._start_codex_observability(
        member_name="dev-1",
        member_agent_id="ext_team_dev-1",
        team_name="ext_team",
        session_id="sess-1",
        role="teammate",
    )
    assert result.span_bridge is None and result.observer is None
    assert result.config_overrides == () and result.env == {} and result.traceparent is None
    await result.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_observability_threads_receiver_and_reader_into_the_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_observability(monkeypatch, initialized=True)
    result = await spawn_mod._start_codex_observability(
        member_name="dev-1",
        member_agent_id="ext_team_dev-1",
        team_name="ext_team",
        session_id="sess-1",
        role="teammate",
    )
    bridge = result.span_bridge
    assert isinstance(bridge, _FakeSpanBridge)
    assert bridge.kwargs["member_name"] == "dev-1" and bridge.kwargs["role"] == "teammate"
    assert bridge.rollout_enabled and bridge.native_enabled
    assert result.traceparent == "00-trace-span-01"
    assert result.env["CODEX_ROLLOUT_TRACE_ROOT"] == "/tmp/rollout-root"
    assert result.env["OTEL_BSP_SCHEDULE_DELAY"] == "100"
    assert any('endpoint = "http://127.0.0.1:4318/v1/traces"' in item for item in result.config_overrides)
    assert "otel.exporter=none" in result.config_overrides
    assert result.observer is not None
    await result.aclose()
    assert _FakeReceiver.instances[0].closed and _FakeRolloutReader.instances[0].closed


def _ctx() -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.EXTERNAL_CLI,
        member_name="dev-1",
        cli_agent="codex",
        team_spec=TeamSpec(team_name="ext_team", display_name="Ext", language="en"),
        db_config=DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"),
        messager_config=MessagerTransportConfig(backend="inprocess", team_name="ext_team"),
    )


@pytest.mark.asyncio
@pytest.mark.level1
async def test_build_cli_runtime_binds_codex_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_observability(monkeypatch, initialized=True)
    token = set_session_id("sess-1")
    try:
        runtime = await spawn_mod.build_cli_runtime(_ctx(), cwd="/w", inject_mcp=False, member_agent_id="ext_team_dev-1")
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ExternalHarnessMemberRuntime)
    assert isinstance(runtime.span_bridge, _FakeSpanBridge)
    harness = runtime.harness
    assert harness._notification_observer is not None
    config = harness._config
    assert config.env["TRACEPARENT"] == "00-trace-span-01"
    assert config.env["CODEX_ROLLOUT_TRACE_ROOT"] == "/tmp/rollout-root"
    assert "otel.exporter=none" in config.config_overrides
    # The teardown hook closes the receiver and reader once the member stops.
    for hook in runtime._teardown_hooks:
        await hook()
    assert _FakeReceiver.instances[0].closed and _FakeRolloutReader.instances[0].closed
