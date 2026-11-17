# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""State methods and automatic collection through real nested event chains."""

import asyncio
from dataclasses import replace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import (
    OPS_BUILD_EVENT_TYPE, Event, EventHandlerOpsBuilder,
    ExtensionEventBus, PackageMetadata, SessionContext,
)
from openjiuwen.core.events.builtins.event_types.state import StateGetInput, StateSetInput, match_pattern_func
from openjiuwen.core.events.builtins.event_types.agent_register import AgentRegisterInput, AgentRegisterOutput
from openjiuwen.core.events.builtins.event_types.tool_register import ToolRegisterOutput, ToolRegisterSpec
from openjiuwen.core.events.ops_impl.fs.models import FsReadInput, FsReadOutput, FsStatus
from openjiuwen.core.events.ops_impl.mcp.models import McpCallInput, McpCallOutput, McpStatus
from openjiuwen.core.events.ops_impl.state import configure_state, state_for
from openjiuwen.core.extension.data import JsonState, StateAddress, StateRef, StateSnapshot
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


async def _host(tmp_path):
    states = JsonState(tmp_path / "state")
    bus = ExtensionEventBus()
    configure_state(bus, states)

    async def core(event, ops, event_next):
        return None

    async def invoke(event, ops, event_next):
        try:
            return await event.event_input(ops)
        except BaseError as exc:
            return exc

    bus.register_event_type("test.state", lambda value, pattern: True, core)
    bus.register_event_handler("test.state", invoke, {}, package_metadata=PackageMetadata("demo"))
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    assert not states.directory.exists()
    return bus, states


@pytest.mark.asyncio
async def test_backend_state_scope_ownership_and_reads_without_ui_bindings(tmp_path):
    bus, states = await _host(tmp_path)
    seen = []

    async def observe(event, ops, event_next):
        seen.append((event.event_input.address.scope, ops.session_context))
        return await event_next(event)

    bus.register_event_handler("state.get", observe, {"ref.package": "demo", "ref.key": "count"})
    ref = StateRef("demo", "count")

    async def write(ops):
        return await ops.state.set(ref, 1, expected_version=0)

    assert (await bus.trigger(Event("test.state", write))).is_set
    output = await bus.trigger(Event("test.state", lambda ops: ops.state.get(ref)))
    assert output == StateSnapshot(True, 1, 1)
    assert seen[-1] == ("application", None)
    assert not (await bus.trigger(
        Event("test.state", lambda ops: ops.state.get(ref)), SessionContext("s1"),
    )).exists
    await states.set(StateAddress("other", "public", "application"), 10)
    assert (await bus.trigger(
        Event("test.state", lambda ops: ops.state.get(StateRef("other", "public"))),
    )).value == 10
    assert isinstance(await bus.trigger(
        Event("test.state", lambda ops: ops.state.set(StateRef("other", "public"), 20)),
    ), BaseError)
    assert isinstance(await bus.trigger(
        Event("test.state", lambda ops: ops.state.get(replace(ref, scope="session"))),
    ), BaseError)
    assert state_for(bus).dependencies.target_reads() == {}
    assert (await JsonState(states.directory).get(StateAddress("demo", "count", "application"))).value == 1


@pytest.mark.asyncio
async def test_build_reads_are_fixed_and_writes_are_rejected_even_when_triggered_directly(tmp_path):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    address = StateAddress("demo", "count", "application")
    await states.set(address, 1)
    async def read(ops):
        return await ops.state.get(StateRef("demo", "count"))

    with dependencies.collect("opaque-host-target") as render_id:
        assert (await bus.trigger(Event("test.state", read))).value == 1
        await states.set(address, 2)
        assert (await bus.trigger(Event("test.state", read))).value == 1
        assert dependencies.reads(render_id) == {address: 1}
        dependencies.commit(render_id, "opaque-host-target")
        assert dependencies.targets(address) == ("opaque-host-target",)
        assert await states.changed_since(dependencies.reads(render_id))
        with pytest.raises(BaseError):
            await bus.trigger(Event("state.set", StateSetInput("demo", address, 3)))
        assert isinstance(await bus.trigger(
            Event("test.state", lambda ops: ops.state.set(StateRef("demo", "count"), 3)),
        ), BaseError)
        dependencies.drop_target("opaque-host-target")
        with pytest.raises(BaseError):
            dependencies.commit(render_id, "opaque-host-target")
    assert dependencies.current_build is None
    assert (await states.get(address)).value == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["agent.register", "tool.register", "fs.read", "mcp.call"])
async def test_existing_nested_ops_preserve_state_build_dependencies(tmp_path, event_type):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    address = StateAddress("demo", "count", "session", "s1")
    await states.set(address, 7)
    seen = []

    async def tool() -> str:
        return "unused"

    cases = {
        "agent.register": (
            AgentRegisterInput(AgentCard(id="child", name="child"), prompt="unused"),
            AgentRegisterOutput(deny="test interception"),
        ),
        "tool.register": (ToolRegisterSpec(source=tool), ToolRegisterOutput(deny="test interception")),
        "fs.read": (FsReadInput("unused.txt"), FsReadOutput(FsStatus.OK, "unused.txt", "unused")),
        "mcp.call": (McpCallInput("unused", "tool"), McpCallOutput(McpStatus.OK, result={})),
    }
    request, output = cases[event_type]

    async def intercept(event, ops, event_next):
        snapshot = await ops.state.get(StateRef("demo", "count"))
        seen.append((dependencies.current_build, snapshot))
        return output

    async def invoke(ops):
        namespace, method = event_type.split(".")
        try:
            return await getattr(getattr(ops, namespace), method)(request)
        except RuntimeError as exc:
            assert str(exc) == "test interception"
            return None

    bus.register_event_handler(event_type, intercept, {}, package_metadata=PackageMetadata("demo"))
    with dependencies.collect("nested-target") as render_id:
        await bus.trigger(Event("test.state", invoke), SessionContext("s1"))
        assert seen == [(render_id, StateSnapshot(True, 7, 1))]
        assert dependencies.reads(render_id) == {address: 1}


@pytest.mark.asyncio
async def test_concurrent_collectors_isolate_panels_and_ignore_unrelated_backend_reads(tmp_path):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    addresses = [StateAddress("demo", key, "application") for key in ("left", "right")]
    for address, value in zip(addresses, (10, 20)):
        await states.set(address, value)
    entered, release = asyncio.Event(), asyncio.Event()
    builds = []

    async def build(target, address):
        with dependencies.collect(target) as render_id:
            ref = StateRef(address.package, address.key)
            first = await bus.trigger(Event("test.state", lambda ops: ops.state.get(ref)))
            builds.append(render_id)
            if len(builds) == 2:
                entered.set()
            await release.wait()
            second = await bus.trigger(Event("test.state", lambda ops: ops.state.get(ref)))
            assert second == first
            dependencies.commit(render_id, target)
            return first.value

    tasks = [asyncio.create_task(build(target, address))
             for target, address in zip(("left-panel", "right-panel"), addresses)]
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert dependencies.current_build is None
        await bus.trigger(Event("test.state", lambda ops: ops.state.get(StateRef("demo", "unrelated"))))
        await states.set(addresses[0], 11)
        release.set()
        assert await asyncio.gather(*tasks) == [10, 20]
        assert len(set(builds)) == 2
        assert dependencies.target_reads() == {"left-panel": {addresses[0]: 1}, "right-panel": {addresses[1]: 1}}
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_collector_does_not_bind_reads_from_an_independent_bus(tmp_path):
    bus, states = await _host(tmp_path / "first")
    other, other_states = await _host(tmp_path / "second")
    dependencies = state_for(bus).dependencies
    address = StateAddress("demo", "count", "application")
    await other_states.set(address, 2)
    with dependencies.collect("first-panel") as render_id:
        output = await other.trigger(Event("test.state", lambda ops: ops.state.get(StateRef("demo", "count"))))
        assert output.value == 2
        assert dependencies.reads(render_id) == {}
    assert dependencies.target_reads() == {}
    assert state_for(other).dependencies.target_reads() == {}
    assert not states.directory.exists()


@pytest.mark.asyncio
async def test_late_inherited_read_cannot_reuse_a_finished_collector(tmp_path):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    address = StateAddress("demo", "count", "application")
    await states.set(address, 1)
    release = asyncio.Event()

    async def late(ops):
        await release.wait()
        return await ops.state.get(StateRef("demo", "count"))

    with dependencies.collect("finished-panel"):
        task = asyncio.create_task(bus.trigger(Event("test.state", late)))
    release.set()
    assert isinstance(await asyncio.wait_for(task, 3), BaseError)
    assert dependencies.current_build is None
    assert dependencies.target_reads() == {}
    assert (await bus.trigger(Event("test.state", lambda ops: ops.state.get(StateRef("demo", "count"))))).value == 1


@pytest.mark.asyncio
async def test_cancelled_collection_releases_its_scope_and_keeps_backend_state_usable(tmp_path):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    entered = asyncio.Event()
    builds = []

    async def build():
        with dependencies.collect("cancelled-panel") as render_id:
            builds.append(render_id)
            await bus.trigger(Event("test.state", lambda ops: ops.state.get(StateRef("demo", "count"))))
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(build())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dependencies.current_build is None
    with pytest.raises(BaseError):
        dependencies.reads(builds[0])
    result = await bus.trigger(Event("test.state", lambda ops: ops.state.set(StateRef("demo", "count"), 5)))
    assert result.is_set
    assert dependencies.target_reads() == {}


@pytest.mark.asyncio
async def test_withdrawing_a_collector_during_io_rejects_the_late_snapshot(tmp_path, monkeypatch):
    bus, states = await _host(tmp_path)
    dependencies = state_for(bus).dependencies
    entered, release = asyncio.Event(), asyncio.Event()
    original = states.get

    async def delayed(address):
        entered.set()
        await release.wait()
        return await original(address)

    async def build():
        with dependencies.collect("withdrawn-panel"):
            return await bus.trigger(Event("test.state", lambda ops: ops.state.get(StateRef("demo", "count"))))

    monkeypatch.setattr(states, "get", delayed)
    task = asyncio.create_task(build())
    await asyncio.wait_for(entered.wait(), 3)
    dependencies.drop_target("withdrawn-panel")
    release.set()
    assert isinstance(await asyncio.wait_for(task, 3), BaseError)
    assert dependencies.target_reads() == {}


@pytest.mark.asyncio
async def test_interceptors_transform_values_and_short_circuit_without_commit(tmp_path):
    bus, states = await _host(tmp_path)

    async def transform(event, ops, event_next):
        request = event.event_input
        return await event_next(Event("state.set", replace(request, value=request.value + 1)))

    handler = bus.register_event_handler("state.set", transform, {"scope": "application"})
    result = await bus.trigger(Event("test.state", lambda ops: ops.state.set(StateRef("demo", "x"), 1)))
    assert result.is_set
    assert (await states.get(StateAddress("demo", "x", "application"))).value == 2
    bus.unregister_event_handler(handler)

    async def reject(event, ops, event_next):
        from openjiuwen.core.extension.data import StateSetResult
        return StateSetResult(False, False, 1, "rejected")

    bus.register_event_handler("state.set", reject, {})
    result = await bus.trigger(Event("test.state", lambda ops: ops.state.set(StateRef("demo", "x"), 99)))
    assert result.reason == "rejected"
    assert (await states.get(StateAddress("demo", "x", "application"))).version == 1


def test_matcher_boundaries():
    request = StateGetInput("caller", StateAddress("target", "x", "session", "s1"))
    assert match_pattern_func(request, {})
    assert match_pattern_func(request, {"ref.package": "target", "scope": "session", "session_id": "s1"})
    assert not match_pattern_func(request, {"ref.key": "other"})
    with pytest.raises(BaseError):
        match_pattern_func(request, {"unknown": "value"})


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    object(), StateGetInput("../bad", StateAddress("demo", "x", "application")),
    StateSetInput("demo", StateAddress("other", "x", "application"), 1),
    StateSetInput("demo", StateAddress("demo", "x", "application"), 1, True),
])
async def test_event_validator_rejects_malformed_requests_before_io(tmp_path, payload):
    bus, states = await _host(tmp_path)
    event_type = "state.get" if isinstance(payload, StateGetInput) else "state.set"
    with pytest.raises(BaseError):
        await bus.trigger(Event(event_type, payload))
    assert not states.directory.exists()
