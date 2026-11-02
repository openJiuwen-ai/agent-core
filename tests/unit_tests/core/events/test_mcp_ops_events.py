# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``mcp.call`` / ``mcp.connect`` events and their ``ops.mcp`` capabilities."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.ops_impl.mcp.models import (
    McpCallInput,
    McpCallOutput,
    McpConnectInput,
    McpConnectOutput,
    McpStatus,
    McpToolDescriptor,
)
from openjiuwen.core.events.ops_impl.mcp.runtime import require_resource_mgr, set_resource_mgr_provider
from openjiuwen.core.events.ops_impl.mcp.service import _select_tool
from openjiuwen.core.events.ops_impl.runtime import build_context
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventOutput,
    PackageMetadata,
    SessionContext,
)
from openjiuwen.core.extension.schema.mcp import declarations, parse_mcp_config
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpToolCard, McpToolResult
from openjiuwen.core.runner.resources_manager.base import Error, Ok
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime

pytestmark = pytest.mark.asyncio

_SESSION = SessionContext("session-mcp")
_SERVER = McpServerConfig(
    server_id="srv-1",
    server_name="audit",
    server_path="http://localhost:9000",
    client_type="sse",
)

_BOUND: list[tuple[str, object]] = []


class _DeepConfig:
    def __init__(self, sys_operation: object | None) -> None:
        self.sys_operation = sys_operation
        self.cwd = None
        self.project_root = None
        self.workspace = None


class _Agent:
    def __init__(self, ability_manager: object | None, sys_operation: object | None) -> None:
        self.ability_manager = ability_manager
        self.deep_config = _DeepConfig(sys_operation)


def _bind_agent(session: SessionContext, *, ability_manager: object | None = None) -> None:
    agent = _Agent(ability_manager, None)
    get_agent_runtime().bind(session.session_id, agent)
    _BOUND.append((session.session_id, agent))


def _use_resource_mgr(manager: object) -> None:
    set_resource_mgr_provider(lambda: manager)


@pytest.fixture(autouse=True)
def _reset_state():
    """Keep the agent runtime and resource provider isolated between tests."""

    _BOUND.clear()
    declarations.clear()
    previous = set_resource_mgr_provider(lambda: None)
    yield
    runtime = get_agent_runtime()
    for session_id, agent in _BOUND:
        runtime.unbind(session_id, agent)
    _BOUND.clear()
    declarations.clear()
    set_resource_mgr_provider(previous)


@dataclass
class _Card:
    id: str
    name: str
    description: str
    input_params: dict = field(default_factory=dict)

    def tool_info(self) -> "_Info":
        return _Info(self.name, self.description, self.input_params)


@dataclass
class _Info:
    name: str
    description: str
    parameters: dict


class _Tool:
    def __init__(self, card: _Card, delay: float = 0.0, error: Exception | None = None) -> None:
        self.card = card
        self.delay = delay
        self.error = error
        self.calls: list[tuple[dict, dict]] = []
        self.result: McpToolResult | None = None

    async def invoke(self, inputs, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((inputs, kwargs))
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return {"result": {"content": "ok"}}


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, float | None]] = []

    async def call_tool(self, tool_name: str, arguments: dict, **kwargs) -> dict:
        self.calls.append((tool_name, arguments, kwargs.get("timeout")))
        return {"content": "ok"}


class _FakeResourceMgr:
    def __init__(self) -> None:
        self.configs: dict[str, McpServerConfig] = {}
        self.tools: dict[str, object] = {}
        self.server_tools: dict[str, list[str]] = {}
        self.tool_info_error: Exception | None = None
        self.tool_info_delay = 0.0
        self.add_calls: list[tuple[McpServerConfig, object, float | None]] = []
        self.add_error: Exception | None = None
        self.removed: list[str] = []

    def get_mcp_server_config(self, server_id: str):
        return self.configs.get(server_id)

    def get_mcp_tool_ids(self, server_id: str):
        return list(self.server_tools.get(server_id, []))

    async def get_tool_infos(self, tool_id=None, **kwargs):
        if self.tool_info_delay:
            await asyncio.sleep(self.tool_info_delay)
        if self.tool_info_error is not None:
            raise self.tool_info_error
        ids = [] if tool_id is None else ([tool_id] if isinstance(tool_id, str) else list(tool_id))
        return [self.tools[item].card.tool_info() for item in ids if item in self.tools]

    async def get_mcp_tool(self, name=None, server_id=None, **kwargs):
        return [self.tools[item] for item in self.server_tools.get(server_id, []) if self.tools[item].card.name == name]

    async def add_mcp_server(self, config, *, tag=None, timeout=None, **kwargs):  # noqa: ASYNC109
        self.add_calls.append((config, tag, timeout))
        if self.add_error is not None:
            return Error(self.add_error)
        card = _Card(id=f"{config.server_id}.record_change", name="record_change", description="declared")
        self.configs[config.server_id] = config
        self.tools[card.id] = _Tool(card)
        self.server_tools[config.server_id] = [card.id]
        return Ok(config.server_id)

    async def remove_mcp_server(self, server_id=None, **kwargs):
        self.removed.append(str(server_id))
        self.configs.pop(str(server_id), None)
        for tool_id in self.server_tools.pop(str(server_id), []):
            self.tools.pop(tool_id, None)
        return Ok(str(server_id))


class _FakeAbilityManager:
    def __init__(self) -> None:
        self.servers: dict[str, McpServerConfig] = {}
        self.add_calls = 0
        self.remove_calls = 0

    def add(self, ability):
        self.add_calls += 1
        self.servers.setdefault(ability.server_name, ability)

    def remove(self, name):
        self.remove_calls += 1
        return self.servers.pop(name, None)

    def get(self, name):
        return self.servers.get(name)

    def list(self):
        return list(self.servers.values())


class _RaisingAbilityManager:
    def add(self, ability):
        raise RuntimeError("publish failed")

    def list(self):
        return []


async def _ready_ops(bus: ExtensionEventBus, session: SessionContext = _SESSION) -> EventHandlerOps:
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    return builder.build(None, session)


def _connected_mgr(*, ability: object | None = None) -> _FakeResourceMgr:
    mgr = _FakeResourceMgr()
    mgr.configs[_SERVER.server_id] = _SERVER
    card = _Card(id="srv-1.record_change", name="record_change", description="record a change")
    mgr.tools[card.id] = _Tool(card)
    mgr.server_tools[_SERVER.server_id] = [card.id]
    _use_resource_mgr(mgr)
    _bind_agent(_SESSION, ability_manager=ability)
    return mgr


async def test_ops_build_exposes_mcp_namespace() -> None:
    bus = ExtensionEventBus()
    ops = await _ready_ops(bus)

    assert callable(ops.mcp.call)
    assert callable(ops.mcp.connect)


async def test_ops_require_an_installed_resource_mgr() -> None:
    bus = ExtensionEventBus()
    _bind_agent(_SESSION)
    ops = await _ready_ops(bus)

    with pytest.raises(RuntimeError):
        await ops.mcp.connect(McpConnectInput(server_id="srv-1"))


async def test_connect_describes_registered_tools() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.status is McpStatus.OK
    assert result.connected is True
    assert result.server_name == "audit"
    assert result.tool_count == 1
    assert result.tools[0].name == "record_change"


async def test_connect_publishes_server_to_agent() -> None:
    bus = ExtensionEventBus()
    ability = _FakeAbilityManager()
    _connected_mgr(ability=ability)
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.agent_visible is True
    assert ability.get("audit") is not None
    assert ability.add_calls == 1


async def test_connect_does_not_publish_twice() -> None:
    bus = ExtensionEventBus()
    ability = _FakeAbilityManager()
    _connected_mgr(ability=ability)
    ops = await _ready_ops(bus)

    await ops.mcp.connect(McpConnectInput(server_id="srv-1"))
    second = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert second.agent_visible is True
    assert ability.add_calls == 1


async def test_connect_without_agent_reports_not_visible() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.status is McpStatus.OK
    assert result.agent_visible is False


async def test_connect_keeps_result_when_publish_raises() -> None:
    bus = ExtensionEventBus()
    _connected_mgr(ability=_RaisingAbilityManager())
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.status is McpStatus.OK
    assert result.connected is True
    assert result.agent_visible is False


async def test_connect_reports_unknown_server() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="missing"))

    assert result.status is McpStatus.SERVER_NOT_FOUND


async def test_connect_reports_tool_list_failure() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    mgr.tool_info_error = RuntimeError("tool lookup failed")
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.status is McpStatus.ERROR
    assert result.error is not None and result.error.kind == "tool_list_failed"


async def test_connect_passes_timeout_to_transport() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    mgr = _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(
        McpConnectInput(package_id=spec.package_id, server_name=spec.name, timeout_ms=1_500)
    )

    assert result.status is McpStatus.OK
    assert mgr.add_calls[0][2] == 1.5


async def test_connect_does_not_report_another_server_with_same_name() -> None:
    bus = ExtensionEventBus()
    ability = _FakeAbilityManager()
    ability.servers["audit"] = _SERVER.model_copy(update={"server_id": "srv-old"})
    _connected_mgr(ability=ability)
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id="srv-1"))

    assert result.status is McpStatus.OK
    assert result.agent_visible is False


async def test_call_executes_registered_tool() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(
        McpCallInput(server_id="srv-1", tool="record_change", arguments={"path": "a.txt"}, timeout_ms=1_000),
    )

    assert result.status is McpStatus.OK
    assert result.is_error is False
    assert result.result == {"content": "ok"}
    assert mgr.tools["srv-1.record_change"].calls[0][0] == {"path": "a.txt"}
    assert mgr.tools["srv-1.record_change"].calls[0][1]["timeout"] == 1.0


async def test_call_uses_default_timeout() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    ops = await _ready_ops(bus)

    await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert mgr.tools["srv-1.record_change"].calls[0][1]["timeout"] == 120.0


async def test_call_requires_a_registered_server() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    del mgr.configs[_SERVER.server_id]
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert result.status is McpStatus.SERVER_NOT_FOUND


async def test_call_reports_unknown_tool() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="missing"))

    assert result.status is McpStatus.TOOL_NOT_FOUND


async def test_call_times_out() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    mgr.tools["srv-1.record_change"].delay = 0.3
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change", timeout_ms=50))

    assert result.status is McpStatus.TIMEOUT


async def test_call_maps_wrapped_timeout_to_timeout() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    wrapped = RuntimeError("MCP call failed")
    wrapped.__cause__ = TimeoutError("transport timed out")
    mgr.tools["srv-1.record_change"].error = wrapped
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert result.status is McpStatus.TIMEOUT
    assert result.error is not None and result.error.kind == "timeout"


async def test_call_reports_tool_error() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    mgr.tools["srv-1.record_change"].error = RuntimeError("tool exploded")
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert result.status is McpStatus.ERROR
    assert result.error is not None and result.error.kind == "call_failed"


def _tool_card_mgr() -> tuple[_FakeResourceMgr, _RecordingClient]:
    mgr = _FakeResourceMgr()
    mgr.configs[_SERVER.server_id] = _SERVER
    client = _RecordingClient()
    card = McpToolCard(
        id="srv-1.record_change",
        name="record_change",
        server_name="audit",
        input_params={
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    )
    mgr.tools[card.id] = MCPTool(client, card)
    mgr.server_tools[_SERVER.server_id] = [card.id]
    _use_resource_mgr(mgr)
    _bind_agent(_SESSION)
    return mgr, client


async def test_call_formats_arguments_from_tool_card() -> None:
    bus = ExtensionEventBus()
    _, client = _tool_card_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(
        McpCallInput(server_id="srv-1", tool="record_change", arguments={"path": "a.txt"}),
    )

    assert result.status is McpStatus.OK
    assert client.calls == [("record_change", {"path": "a.txt"}, 120.0)]


async def test_call_rejects_arguments_that_break_tool_schema() -> None:
    bus = ExtensionEventBus()
    _, client = _tool_card_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(
        McpCallInput(server_id="srv-1", tool="record_change", arguments={}),
    )

    assert result.status is McpStatus.INVALID_INPUT
    assert result.error is not None and result.error.kind == "invalid_arguments"
    assert client.calls == []


async def test_call_normalizes_tool_result_error() -> None:
    bus = ExtensionEventBus()
    mgr = _connected_mgr()
    mgr.tools["srv-1.record_change"].result = McpToolResult(success=False, data={}, error="denied")
    ops = await _ready_ops(bus)

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert result.status is McpStatus.OK
    assert result.is_error is True
    assert result.error is not None and result.error.kind == "mcp_tool_error"


async def test_nested_same_event_is_denied() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    async def nested(event: Event, handler_ops: EventHandlerOps | None, event_next) -> EventOutput:
        del event, event_next
        assert handler_ops is not None
        return await handler_ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    bus.register_event_handler("mcp.call", nested, {})

    result = await ops.mcp.call(McpCallInput(server_id="srv-1", tool="record_change"))

    assert result.status is McpStatus.DENIED
    assert result.error is not None and result.error.kind == "reentrant_call"


async def test_direct_trigger_uses_event_default_budgets(monkeypatch) -> None:
    from openjiuwen.core.events.builtins.event_types import mcp_events

    bus = ExtensionEventBus()

    budgets: list[float] = []
    real_fail_after = mcp_events.anyio.fail_after

    def record(delay: float):
        budgets.append(delay)
        return real_fail_after(delay)

    monkeypatch.setattr(mcp_events.anyio, "fail_after", record)
    spec = _declare()
    mgr = _connected_mgr()
    await _ready_ops(bus)

    await bus.trigger(Event("mcp.call", McpCallInput(server_id="srv-1", tool="record_change")), _SESSION)
    await bus.trigger(
        Event("mcp.connect", McpConnectInput(package_id=spec.package_id, server_name=spec.name)), _SESSION
    )

    assert budgets == [120.0]
    assert mgr.add_calls[0][2] == 60.0


async def test_direct_trigger_rejects_malformed_payload() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    await _ready_ops(bus)

    with pytest.raises(ValidationError) as invalid:
        await bus.trigger(
            Event("mcp.call", McpCallInput(server_id="srv-1", tool="record_change", timeout_ms=0)),
            _SESSION,
        )

    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


async def test_outputs_are_json_serializable() -> None:
    connect = McpConnectOutput(
        status=McpStatus.OK,
        server_id="srv-1",
        tools=(McpToolDescriptor(name="record_change", description="", input_schema={"type": "object"}),),
        tool_count=1,
    )
    call = McpCallOutput(status=McpStatus.OK, result={"content": "ok"})

    assert json.loads(json.dumps(asdict(connect)))["tools"][0]["input_schema"] == {"type": "object"}
    assert json.loads(json.dumps(asdict(call)))["result"] == {"content": "ok"}


async def test_context_fills_cwd_from_current_task() -> None:
    from openjiuwen.core.sys_operation.cwd import get_cwd

    builder = EventHandlerOpsBuilder()
    builder.add_namespace("mcp")
    ops = builder.build(None, SessionContext("session-mcp", cwd=None))

    assert build_context(ops).cwd == get_cwd()


async def test_context_keeps_explicit_cwd() -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("mcp")
    ops = builder.build(None, SessionContext("session-mcp", cwd="/explicit"))

    assert build_context(ops).cwd == "/explicit"


async def test_select_tool_picks_matching_card() -> None:
    matching = _Tool(_Card(id="srv-1.record_change", name="record_change", description=""))
    other = _Tool(_Card(id="srv-1.other", name="other", description=""))

    assert _select_tool([other, matching], "record_change") is matching
    assert _select_tool([other], "record_change") is None
    assert _select_tool(matching, "record_change") is matching
    assert _select_tool(None, "record_change") is None

_DECLARED = {
    "mcpServers": {
        "audit": {
            "client_type": "stdio",
            "params": {"command": "audit-mcp", "args": []},
        }
    }
}


def _declare(package_id: str = "audit.pkg"):
    specs = parse_mcp_config(_DECLARED, package_id)
    declarations.declare(package_id, specs)
    return specs[0]


async def test_connect_opens_a_package_declaration() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(package_id=spec.package_id, server_name=spec.name))

    assert result.status is McpStatus.OK
    assert result.server_id == spec.server_id
    assert result.connected is True
    assert result.tool_count == 1


async def test_connect_opens_declaration_by_server_id() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    mgr = _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(server_id=spec.server_id))

    assert result.status is McpStatus.OK
    assert mgr.add_calls[0][0].server_id == spec.server_id


async def test_connect_opens_declaration_once() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    mgr = _connected_mgr()
    ops = await _ready_ops(bus)

    await ops.mcp.connect(McpConnectInput(package_id=spec.package_id, server_name=spec.name))
    second = await ops.mcp.connect(McpConnectInput(package_id=spec.package_id, server_name=spec.name))

    assert second.connected is True
    assert len(mgr.add_calls) == 1


async def test_connect_uses_facade_package_identity() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    _connected_mgr()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    ops = builder.build(PackageMetadata(package_id=spec.package_id), _SESSION)

    result = await ops.mcp.connect(McpConnectInput(server_name=spec.name))

    assert result.status is McpStatus.OK
    assert result.server_id == spec.server_id


async def test_connect_reports_unknown_declaration() -> None:
    bus = ExtensionEventBus()
    _connected_mgr()
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(package_id="audit.pkg", server_name="missing"))

    assert result.status is McpStatus.SERVER_NOT_FOUND
    assert result.error is not None and "audit.pkg/missing" in result.error.message


async def test_connect_reports_registration_failure() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    mgr = _connected_mgr()
    mgr.add_error = RuntimeError("connect refused")
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(package_id=spec.package_id, server_name=spec.name))

    assert result.status is McpStatus.ERROR
    assert result.error is not None and result.error.kind == "connect_failed"


async def test_connect_replaces_changed_mount_on_agent() -> None:
    bus = ExtensionEventBus()
    spec = _declare()
    ability = _FakeAbilityManager()
    stale = McpServerConfig(server_id=spec.server_id, server_name=spec.server_name, server_path="stdio://old")
    ability.servers[spec.server_name] = stale
    _connected_mgr(ability=ability)
    ops = await _ready_ops(bus)

    result = await ops.mcp.connect(McpConnectInput(package_id=spec.package_id, server_name=spec.name))

    assert result.agent_visible is True
    assert ability.remove_calls == 1
    assert ability.get(spec.server_name) is not None
    assert ability.get(spec.server_name).params["command"] == "audit-mcp"


async def test_resource_mgr_defaults_to_runner(monkeypatch) -> None:
    from openjiuwen.core.runner import Runner

    previous = set_resource_mgr_provider(None)
    manager = object()
    monkeypatch.setattr(Runner, "resource_mgr", manager)

    try:
        assert require_resource_mgr() is manager
    finally:
        set_resource_mgr_provider(previous)
