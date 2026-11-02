# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for the ``mcp.register`` Ops method and CORE handler."""

from __future__ import annotations

from typing import Mapping

import pytest

from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventHandlerOps,
    EventHandlerOpsBuilder,
    EventNext,
    ExtensionEventBus,
    HandlerLevel,
    SessionContext,
)
from openjiuwen.core.events.builtins.event_types.mcp_register import (
    MCP_REGISTER,
    McpHttpSpec,
    McpRegisterInput,
    McpRegisterOutput,
    McpRegisterSpec,
    McpRegisterValue,
    McpStdioSpec,
)
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.resources_manager.base import Error, Ok
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.ability_manager import AddAbilityResult
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig

_BUILD = Event(OPS_BUILD_EVENT_TYPE, None)


def _matches(event_input: object, pattern: Mapping[str, object]) -> bool:
    del event_input
    return not pattern


async def _build_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    result = await bus.trigger(_BUILD)
    assert isinstance(result, EventHandlerOpsBuilder)
    return bus


def _make_agent(session_id: str) -> DeepAgent:
    agent = DeepAgent(
        AgentCard(id="mcp-parent-agent", name="mcp-parent-agent", description="parent"),
    ).configure(DeepAgentConfig(enable_task_loop=False))
    get_agent_runtime().bind(session_id, agent)
    return agent


def _stdio_spec(name: str = "notes") -> McpRegisterSpec:
    return McpRegisterSpec(
        name=name,
        transport="stdio",
        connection=McpStdioSpec(
            command="python",
            args=("server.py",),
            cwd="C:/servers",
            env={"MCP_MODE": "test"},
        ),
    )


def _patch_resource_manager(monkeypatch, captured: list[McpServerConfig]):
    async def add_mcp_server(config: McpServerConfig):
        captured.append(config)
        return Ok(config.server_id)

    removed: list[str] = []

    async def remove_mcp_server(*, server_id: str, **kwargs):
        del kwargs
        removed.append(server_id)
        return Ok(server_id)

    monkeypatch.setattr(Runner.resource_mgr, "add_mcp_server", add_mcp_server)
    monkeypatch.setattr(Runner.resource_mgr, "remove_mcp_server", remove_mcp_server)
    return removed


@pytest.mark.asyncio
async def test_mcp_register_connects_and_binds_native_config(monkeypatch) -> None:
    session_id = "mcp-register-session"
    agent = _make_agent(session_id)
    captured: list[McpServerConfig] = []
    _patch_resource_manager(monkeypatch, captured)
    bus = ExtensionEventBus()

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))

        result = await ops.mcp.register(_stdio_spec())

        assert result == McpRegisterValue(server="notes")
        assert len(captured) == 1
        config = captured[0]
        assert config.server_name == "notes"
        assert config.client_type == "stdio"
        assert config.server_path == ""
        assert config.params == {
            "command": "python",
            "args": ["server.py"],
            "cwd": "C:/servers",
            "env": {"MCP_MODE": "test"},
        }
        assert agent.ability_manager.get("notes") is config
    finally:
        agent.ability_manager.remove("notes")
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_maps_streamable_http_without_runtime_input_instance(monkeypatch) -> None:
    session_id = "mcp-register-http-session"
    agent = _make_agent(session_id)
    captured_configs: list[McpServerConfig] = []
    _patch_resource_manager(monkeypatch, captured_configs)
    bus = ExtensionEventBus()
    captured_inputs: list[McpRegisterInput] = []

    async def capture_input(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        captured_inputs.append(event.event_input)
        return await event_next(event)

    bus.register_event_handler(MCP_REGISTER, capture_input, {}, HandlerLevel.USER)

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))
        result = await ops.mcp.register(
            McpRegisterSpec(
                name="remote_notes",
                transport="streamable_http",
                connection=McpHttpSpec(
                    url="https://example.com/mcp",
                    headers={"X-Test": "enabled"},
                ),
                include_image_content=True,
            )
        )

        assert result.server == "remote_notes"
        assert len(captured_inputs) == 1
        assert isinstance(captured_inputs[0], McpRegisterInput)
        assert not isinstance(captured_inputs[0], McpServerConfig)
        assert isinstance(captured_inputs[0].connection, McpHttpSpec)
        assert captured_inputs[0].connection.url == "https://example.com/mcp"
        assert captured_configs[0].client_type == "streamable-http"
        assert captured_configs[0].server_path == "https://example.com/mcp"
        assert captured_configs[0].auth_headers == {"X-Test": "enabled"}
        assert captured_configs[0].include_image_content is True
        assert agent.ability_manager.get("remote_notes") is captured_configs[0]
    finally:
        agent.ability_manager.remove("remote_notes")
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_rejects_duplicate_ability_before_connect(monkeypatch) -> None:
    session_id = "mcp-register-duplicate-session"
    agent = _make_agent(session_id)
    existing = McpServerConfig(server_name="notes", server_path="", client_type="stdio")
    agent.ability_manager.add(existing)
    add_calls: list[McpServerConfig] = []
    _patch_resource_manager(monkeypatch, add_calls)
    bus = ExtensionEventBus()

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))

        with pytest.raises(RuntimeError, match="already registered"):
            await ops.mcp.register(_stdio_spec())

        assert add_calls == []
        assert agent.ability_manager.get("notes") is existing
    finally:
        agent.ability_manager.remove("notes")
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_cleans_up_resource_when_ability_binding_fails(monkeypatch) -> None:
    session_id = "mcp-register-cleanup-session"
    agent = _make_agent(session_id)
    captured: list[McpServerConfig] = []
    removed = _patch_resource_manager(monkeypatch, captured)
    monkeypatch.setattr(
        agent.ability_manager,
        "add",
        lambda config: AddAbilityResult(name=config.server_name, added=False, reason="duplicate_mcp_server"),
    )
    bus = ExtensionEventBus()

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))

        with pytest.raises(RuntimeError, match="ability registration failed"):
            await ops.mcp.register(_stdio_spec())

        assert len(captured) == 1
        assert removed == [captured[0].server_id]
        assert agent.ability_manager.get("notes") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_surfaces_resource_failure_without_binding(monkeypatch) -> None:
    session_id = "mcp-register-resource-failure-session"
    agent = _make_agent(session_id)
    captured: list[McpServerConfig] = []
    removed = _patch_resource_manager(monkeypatch, captured)
    failure = RuntimeError("server unavailable")

    async def add_mcp_server(config: McpServerConfig):
        captured.append(config)
        return Error(failure)

    monkeypatch.setattr(Runner.resource_mgr, "add_mcp_server", add_mcp_server)
    bus = ExtensionEventBus()

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))

        with pytest.raises(RuntimeError, match="MCP server registration failed"):
            await ops.mcp.register(_stdio_spec())

        assert len(captured) == 1
        assert removed == [captured[0].server_id]
        assert agent.ability_manager.get("notes") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_user_short_circuit_skips_core(monkeypatch) -> None:
    session_id = "mcp-register-short-circuit-session"
    agent = _make_agent(session_id)
    captured: list[McpServerConfig] = []
    _patch_resource_manager(monkeypatch, captured)
    bus = ExtensionEventBus()

    async def short_circuit(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> McpRegisterOutput:
        del event, ops, event_next
        return McpRegisterOutput(value=McpRegisterValue(server="short_circuit"))

    bus.register_event_handler(MCP_REGISTER, short_circuit, {}, HandlerLevel.USER)

    try:
        ops_builder = await bus.trigger(_BUILD)
        assert isinstance(ops_builder, EventHandlerOpsBuilder)
        ops = ops_builder.build(session_context=SessionContext(session_id))

        result = await ops.mcp.register(_stdio_spec())

        assert result.server == "short_circuit"
        assert captured == []
        assert agent.ability_manager.get("notes") is None
    finally:
        get_agent_runtime().unbind(session_id, agent)


@pytest.mark.asyncio
async def test_mcp_register_requires_bound_runtime_agent() -> None:
    bus = await _build_bus()
    request = McpRegisterInput(
        name="notes",
        transport="stdio",
        connection=McpStdioSpec(command="python"),
    )

    with pytest.raises(RuntimeError, match="not bound"):
        await bus.trigger(Event(MCP_REGISTER, request), SessionContext("missing-mcp-agent"))


def test_mcp_register_output_rejects_empty_deny() -> None:
    with pytest.raises(ValueError, match="deny must not be empty"):
        McpRegisterOutput(deny=" ")
