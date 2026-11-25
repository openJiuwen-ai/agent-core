# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ops builder behavior, and member identity through the event boundary and ops verbs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, ValidationError
from openjiuwen.core.events.builtins.event_types.agent_spawn import AgentSpawnInput
from openjiuwen.core.events.builtins.event_types.tool_check import TOOL_CHECK
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, PackageMetadata, SessionContext
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import with_session
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.subagent_runtime.models import SpawnResult, SubagentStatus
from openjiuwen.harness.tools.subagent._control_registry import get_subagent_control


async def _return_context(
    ops: EventHandlerOps,
    value: str,
    *,
    suffix: str,
) -> tuple[EventHandlerOps, PackageMetadata | None, SessionContext | None, str]:
    return ops, ops.package_metadata, ops.session_context, f"{value}{suffix}"


async def _noop(ops: EventHandlerOps) -> None:
    del ops


def _sync_method(ops: EventHandlerOps) -> None:
    del ops


class _BusinessFailure(Exception):
    """Representative extension business failure."""


def _member(name: str) -> DeepAgent:
    agent = DeepAgent(AgentCard(id=f"member-ops-{name}")).configure(DeepAgentConfig(enable_task_loop=False))

    async def identify() -> str:
        return name

    tool = LocalFunction(
        card=ToolCard(
            id=f"member-ops-{name}-echo",
            name="member_echo",
            description="Identify this member",
            input_params={"type": "object", "properties": {}},
        ),
        func=identify,
    )
    agent.ability_manager.add_ability(tool.card, tool)
    return agent


@pytest.mark.asyncio
async def test_ops_happy_path_and_context_isolation() -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "call", _return_context)

    package_a = PackageMetadata("package-a", "1.0")
    package_b = PackageMetadata("package-b", "2.0")
    session_a = SessionContext("session-a", "/workspace/a")
    session_b = SessionContext("session-b", "/workspace/b")
    ops_a = builder.build(package_a, session_a)
    ops_b = builder.build(package_b, session_b)

    result_a, result_b = await asyncio.gather(
        ops_a.tool.call("A", suffix="-done"),
        ops_b.tool.call("B", suffix="-done"),
    )

    assert ops_a is not ops_b
    assert object.__getattribute__(ops_a, "_catalog") is object.__getattribute__(ops_b, "_catalog")
    assert result_a == (ops_a, package_a, session_a, "A-done")
    assert result_b == (ops_b, package_b, session_b, "B-done")
    assert ops_a.package_metadata is package_a
    assert ops_a.session_context is session_a
    assert ops_b.package_metadata is package_b
    assert ops_b.session_context is session_b


@pytest.mark.parametrize(
    ("operation", "name"),
    [
        ("namespace", 1),
        ("namespace", "not-valid"),
        ("namespace", "class"),
        ("namespace", "_private"),
        ("namespace", "package_metadata"),
        ("namespace", "session_context"),
        ("method", 1),
        ("method", "not-valid"),
        ("method", "class"),
        ("method", "_private"),
        ("duplicate_method", "call"),
        ("missing_namespace", "call"),
        ("sync_method", "call"),
    ],
)
def test_builder_rejects_invalid_registration(operation: str, name: object) -> None:
    builder = EventHandlerOpsBuilder()
    if operation != "namespace":
        builder.add_namespace("tool")
    if operation == "duplicate_method":
        builder.add_method("tool", "call", _noop)

    actions: dict[str, Callable[[], None]] = {
        "namespace": lambda: builder.add_namespace(name),  # type: ignore[arg-type]
        "method": lambda: builder.add_method("tool", name, _noop),  # type: ignore[arg-type]
        "duplicate_method": lambda: builder.add_method("tool", "call", _noop),
        "missing_namespace": lambda: builder.add_method("missing", "call", _noop),
        "sync_method": lambda: builder.add_method("tool", "call", _sync_method),  # type: ignore[arg-type]
    }

    with pytest.raises(ValidationError) as invalid:
        actions[operation]()
    assert invalid.value.status is StatusCode.EVENTS_OPS_INVALID


def test_shared_namespace_keeps_contributed_methods_unique() -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "call", _noop)
    builder.add_namespace("tool")
    builder.add_method("tool", "list", _noop)

    with pytest.raises(ValidationError) as invalid:
        builder.add_method("tool", "call", _noop)
    assert invalid.value.status is StatusCode.EVENTS_OPS_INVALID


@pytest.mark.parametrize("context", ["package_metadata", "session_context"])
def test_builder_rejects_invalid_build_context(context: str) -> None:
    builder = EventHandlerOpsBuilder()

    with pytest.raises(ValidationError) as invalid:
        builder.build(**{context: object()})  # type: ignore[arg-type]
    assert invalid.value.status is StatusCode.EVENTS_PAYLOAD_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["namespace", "method"])
async def test_unknown_capability_is_rejected(capability: str) -> None:
    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    ops = builder.build()

    with pytest.raises(ValidationError) as unknown:
        if capability == "namespace":
            _ = ops.missing
        else:
            await ops.tool.missing()
    assert unknown.value.status is StatusCode.EVENTS_OPS_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [
        pytest.param(_BusinessFailure("business failure"), id="business-failure"),
        pytest.param(asyncio.CancelledError("cancelled"), id="cancelled"),
    ],
)
async def test_ops_method_exceptions_propagate(exception: BaseException) -> None:
    async def fail(ops: EventHandlerOps, exception: BaseException) -> None:
        del ops
        raise exception

    builder = EventHandlerOpsBuilder()
    builder.add_namespace("tool")
    builder.add_method("tool", "fail", fail)
    ops = builder.build()

    with pytest.raises(type(exception)) as raised:
        await ops.tool.fail(exception)
    assert raised.value is exception


@pytest.mark.asyncio
@pytest.mark.parametrize("context_only", [False, True])
async def test_shared_session_events_and_nested_ops_keep_member_identity(context_only: bool) -> None:
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    async def read_identity(event, ops, event_next):
        del event, event_next
        await asyncio.sleep(0)
        return ops.session_context.agent_id

    async def nested(event, ops, event_next):
        del event, event_next
        return await bus.trigger(Event("test.member_inner", None), ops.session_context)

    bus.register_event_type("test.member_inner", lambda _event, pattern: not pattern, read_identity)
    bus.register_event_type("test.member_outer", lambda _event, pattern: not pattern, nested)
    contexts = []
    for member in ("leader", "teammate"):
        if context_only:
            context = SessionContext("shared", "/workspace", {"key": "value"}, agent_id=member)
        else:
            context = create_agent_session(session_id="shared", card=AgentCard(id=member))
        contexts.append(context)

    assert await asyncio.gather(
        *(bus.trigger(Event("test.member_outer", None), context) for context in contexts)
    ) == ["leader", "teammate"]
    assert builder.build(session_context=SessionContext("legacy")).session_context.agent_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", ["", 42])
async def test_event_boundary_rejects_invalid_member_identity(agent_id) -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    async def terminal(event, ops, event_next):
        del event, ops, event_next

    bus.register_event_type("test.invalid_member", lambda _event, pattern: not pattern, terminal)
    with pytest.raises(BaseError):
        await bus.trigger(Event("test.invalid_member", None), SessionContext("shared", agent_id=agent_id))


@pytest.mark.asyncio
async def test_cardless_session_keeps_the_legacy_session_only_context() -> None:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    async def read_context(event, ops, event_next):
        del event, event_next
        return ops.session_context

    bus.register_event_type("test.cardless", lambda _event, pattern: not pattern, read_context)
    result = await bus.trigger(Event("test.cardless", None), create_agent_session(session_id="legacy"))
    assert result.session_id == "legacy"
    assert result.agent_id is None


@pytest.mark.parametrize("with_card", [False, True])
def test_session_agent_metadata_getters_handle_optional_card(with_card: bool) -> None:
    card = AgentCard(id="member", name="Member", description="Team member") if with_card else None
    session = create_agent_session(session_id="metadata", card=card)

    assert session.get_agent_id() == ("member" if with_card else None)
    assert session.get_agent_name() == ("Member" if with_card else None)
    assert session.get_agent_description() == ("Team member" if with_card else None)


@pytest.mark.asyncio
async def test_tool_ops_select_each_members_tool_and_permission_rail() -> None:
    await Runner.start()
    agents = [_member("leader"), _member("teammate")]
    registry = get_agent_runtime()
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    seen = []

    async def observe(event, ops, event_next):
        seen.append((ops.session_context.agent_id, event.event_input.agent_id))
        return await event_next(event)

    bus.register_event_handler(TOOL_CHECK, observe, {})

    async def execute(agent: DeepAgent, decision: str):
        agent.add_rail(PermissionInterruptRail(config={"enabled": True, "tools": {"member_echo": decision}}))
        session = create_agent_session(session_id="member-ops-shared", card=agent.card)
        registry.bind(session.get_session_id(), agent)
        ops = builder.build(session_context=SessionContext(session.get_session_id(), agent_id=agent.card.id))

        @with_session(session)
        async def invoke():
            await asyncio.sleep(0)
            check = await ops.tool.check("member_echo", {})
            call = await ops.tool.call("member_echo", {})
            return check.decision, call.result

        return await invoke()

    try:
        outputs = await asyncio.gather(execute(agents[0], "deny"), execute(agents[1], "allow"))
        assert outputs == [("deny", "leader"), ("allow", "teammate")]
        assert sorted(seen) == [
            ("member-ops-leader", "member-ops-leader"),
            ("member-ops-teammate", "member-ops-teammate"),
        ]
    finally:
        for agent in agents:
            registry.unbind("member-ops-shared", agent)
            await agent.aclose()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["call", "spawn"])
async def test_ops_reject_another_members_live_session_before_side_effect(method: str) -> None:
    agent = _member(f"mismatch-{method}")
    target_session = create_agent_session(session_id="member-ops-mismatch", card=agent.card)
    other_session = create_agent_session(session_id="member-ops-mismatch", card=AgentCard(id="other-member"))
    registry = get_agent_runtime()
    registry.bind(target_session.get_session_id(), agent)
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    ops = builder.build(session_context=SessionContext(target_session.get_session_id(), agent_id=agent.card.id))
    control = get_subagent_control(agent, other_session)
    control.spawn = AsyncMock(return_value=SpawnResult(
        subagent_id="should-not-exist", task_id="should-not-run", status=SubagentStatus.pending_init(),
    ))
    control.emit_status_update = AsyncMock()

    @with_session(other_session)
    async def invoke():
        if method == "call":
            return await ops.tool.call("member_echo", {})
        return await ops.agent.spawn(AgentSpawnInput(
            subagent_type="explore", task_description="inspect", display_name="Explorer", role="researcher",
        ))

    try:
        with pytest.raises(BaseError, match="session does not match"):
            await invoke()
        control.spawn.assert_not_awaited()
    finally:
        registry.unbind(target_session.get_session_id(), agent)
        await agent.aclose()


@pytest.mark.asyncio
async def test_spawn_uses_the_calling_members_control_and_preserves_nested_identity() -> None:
    agents = [_member("spawn-leader"), _member("spawn-teammate")]
    registry = get_agent_runtime()
    bus = ExtensionEventBus()
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    seen = []

    async def observe(event, ops, event_next):
        seen.append(ops.session_context.agent_id)
        return await event_next(event)

    bus.register_event_handler("agent.spawn", observe, {})
    sessions = [create_agent_session(session_id="member-spawn-shared", card=agent.card) for agent in agents]
    for agent, session in zip(agents, sessions):
        registry.bind(session.get_session_id(), agent)
        control = get_subagent_control(agent, session)
        control.spawn = AsyncMock(return_value=SpawnResult(
            subagent_id=f"child-of-{agent.card.id}", task_id="task", status=SubagentStatus.pending_init(),
        ))
        control.emit_status_update = AsyncMock()

    async def execute(agent: DeepAgent, session):
        ops = builder.build(session_context=SessionContext(session.get_session_id(), agent_id=agent.card.id))

        @with_session(session)
        async def invoke():
            return await ops.agent.spawn(AgentSpawnInput(
                subagent_type="explore", task_description="inspect", display_name="Explorer", role="researcher",
            ))

        return await invoke()

    try:
        outputs = await asyncio.gather(*(execute(agent, session) for agent, session in zip(agents, sessions)))
        assert [output.subagent_id for output in outputs] == [
            "child-of-member-ops-spawn-leader", "child-of-member-ops-spawn-teammate",
        ]
        assert sorted(seen) == ["member-ops-spawn-leader", "member-ops-spawn-teammate"]
    finally:
        for agent in agents:
            registry.unbind("member-spawn-shared", agent)
            await agent.aclose()
