# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration and construction contracts; no live model or delivery."""

from dataclasses import replace
from importlib import import_module

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec

pytestmark = pytest.mark.level0


@pytest.fixture
def api():
    return import_module("openjiuwen.harness.persistent_agent")


class Port:
    async def record(self, *args):
        raise AssertionError("construction must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("construction must not read execution logs")

    async def admit(self, *args):
        raise AssertionError("construction must not call a model")

    async def report(self, *args):
        raise AssertionError("construction must not report model usage")


class UntouchedKVStore(BaseKVStore):
    """Window backend whose every operation fails, proving construction does no storage I/O."""

    def _touched(self, *args, **kwargs):
        raise AssertionError("construction must not read or write the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


def ports_for(api, **overrides):
    port = Port()
    args = dict(commit=port, budget=port, window_backend=UntouchedKVStore())
    args.update(overrides)
    return api.PersistentPorts(**args)


def spec_for(api, **kwargs):
    return api.PersistentAgentSpec(
        card=AgentCard(id="business-agent", name="test"),
        enable_sys_operation=False,
        enable_task_loop=False,
        auto_create_workspace=False,
        **kwargs,
    )


def context_for(api, spec, session_id="session-a", **overrides):
    execution = api.ExecutionContext(
        namespace_id="tenant",
        agent_id=spec.card.id,
        session_id=session_id,
        request_id="request",
        execution_id="execution-" + session_id,
        config_version="v1",
        config_fingerprint=spec.fingerprint(),
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=spec.enabled_features,
    )
    return api.PersistentBuildContext(
        execution=execution,
        session=create_agent_session(session_id=session_id),
        ports=ports_for(api),
        **overrides,
    )


def test_flat_spec_roundtrip_and_inheritance(api):
    spec = spec_for(api, system_prompt="test")
    assert isinstance(spec, DeepAgentSpec)
    raw = spec.model_dump(mode="json")
    assert raw["system_prompt"] == "test"
    assert raw["schema_version"] == 1
    assert raw["enabled_features"] == ["construction"]
    assert "deep_agent" not in raw and "session" not in raw
    restored = api.PersistentAgentSpec.model_validate_json(spec.model_dump_json())
    assert restored.fingerprint() == spec.fingerprint()
    assert spec.model_copy(update={"system_prompt": "changed"}).fingerprint() != spec.fingerprint()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"enabled_features": {"windows"}},
        {"ports": {}},
    ],
)
def test_invalid_spec_rejected(api, change):
    with pytest.raises((ValidationError, BaseError)):
        spec_for(api, **change)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("participation_policy", "decide"),
        ("event_queue_capacity", 32),
        ("event_commit_timeout", 15.0),
        ("tool_shutdown_timeout", 5.0),
        ("supported_snapshot_versions", {1}),
    ],
)
def test_unimplemented_policy_fields_are_rejected(api, field, value):
    with pytest.raises(ValidationError):
        spec_for(api, **{field: value})


def test_factory_returns_native_deep_agent_without_side_effects(api):
    from openjiuwen.harness.deep_agent import DeepAgent

    spec = spec_for(api)
    before = spec.model_dump_json()
    ctx = context_for(api, spec, extras={"marker": "unchanged"})
    agent = api.create_persistent_agent(spec, context=ctx)
    assert type(agent) is DeepAgent
    assert agent.card.id == spec.card.id
    assert ctx.extras == {"marker": "unchanged"}
    assert spec.model_dump_json() == before
    ctx.validate_binding(agent)
    assert ctx.binding.execution == ctx.execution
    assert not hasattr(api, "PersistentAgent")
    assert not hasattr(api, "execute_persistent_request")
    agent.ability_manager.teardown_tools()


@pytest.mark.parametrize("bad", ["session", "card", "fingerprint", "capabilities", "ports"])
def test_mismatch_rejected_before_build(api, monkeypatch, bad):
    spec = spec_for(api)
    ctx = context_for(api, spec)
    if bad == "session":
        ctx.session = create_agent_session(session_id="wrong")
    elif bad == "card":
        ctx.execution = replace(ctx.execution, agent_id="wrong")
    elif bad == "fingerprint":
        ctx.execution = replace(ctx.execution, config_fingerprint="0" * 64)
    elif bad == "capabilities":
        ctx.execution = replace(ctx.execution, capability_snapshot=frozenset({"history"}))
    else:
        ctx.ports = ports_for(api, commit=object())

    def forbidden(*args, **kwargs):
        pytest.fail("invalid construction reached DeepAgent build")

    monkeypatch.setattr(DeepAgentSpec, "build", forbidden)
    with pytest.raises(BaseError):
        api.create_persistent_agent(spec, context=ctx)


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("commit", "missing async port commit.record"),
        ("budget", "missing async port budget.admit"),
        ("window_backend", "window_backend must be a BaseKVStore"),
    ],
)
def test_missing_required_dependency_fails_before_deep_agent_build(api, monkeypatch, missing, message):
    spec = spec_for(api)
    ctx = context_for(api, spec)
    ctx.ports = ports_for(api, **{missing: None})

    def forbidden(*args, **kwargs):
        pytest.fail("missing required host dependency reached DeepAgent build")

    monkeypatch.setattr(DeepAgentSpec, "build", forbidden)
    with pytest.raises(BaseError, match=message):
        api.create_persistent_agent(spec, context=ctx)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"window_backend": object()}, "window_backend must be a BaseKVStore"),
        ({"commit": type("SyncLog", (Port,), {"read_execution_log": lambda *a: ()})()}, "commit.read_execution_log"),
        ({"group_chat": object()}, "missing async port group_chat.recent_messages"),
        ({"knowledge": object()}, "missing async port knowledge.resolve"),
    ],
)
def test_injected_dependencies_must_satisfy_their_contract_before_build(api, monkeypatch, overrides, message):
    spec = spec_for(api)
    ctx = context_for(api, spec)
    ctx.ports = ports_for(api, **overrides)

    def forbidden(*args, **kwargs):
        pytest.fail("malformed host dependency reached DeepAgent build")

    monkeypatch.setattr(DeepAgentSpec, "build", forbidden)
    with pytest.raises(BaseError, match=message):
        api.create_persistent_agent(spec, context=ctx)


def test_optional_group_chat_and_knowledge_ports_are_accepted(api):
    class GroupChat:
        async def recent_messages(self, *args):
            raise AssertionError("construction must not read group messages")

        async def search_messages(self, *args):
            raise AssertionError("construction must not search group messages")

    class Knowledge:
        async def resolve(self, *args):
            raise AssertionError("construction must not resolve knowledge")

        async def read(self, *args):
            raise AssertionError("construction must not read knowledge")

    spec = spec_for(api)
    ctx = context_for(api, spec)
    ctx.ports = ports_for(api, group_chat=GroupChat(), knowledge=Knowledge())
    agent = api.create_persistent_agent(spec, context=ctx)
    try:
        ctx.validate_binding(agent)
    finally:
        agent.ability_manager.teardown_tools()


@pytest.mark.parametrize("removed", ["authority", "control", "context_store", "history", "group_material"])
def test_ports_removed_by_v2_are_rejected(api, removed):
    with pytest.raises(TypeError):
        ports_for(api, **{removed: Port()})


def test_one_context_cannot_be_reused_or_rebound(api):
    spec = spec_for(api)
    ctx = context_for(api, spec)
    agent = spec.build(context=ctx)
    try:
        with pytest.raises(BaseError):
            spec.build(context=ctx)
        with pytest.raises(BaseError):
            context_for(api, spec).validate_binding(agent)
        ctx.execution = replace(ctx.execution, execution_id="replacement")
        with pytest.raises(BaseError):
            ctx.validate_binding(agent)
    finally:
        agent.ability_manager.teardown_tools()


def test_derive_does_not_share_extras(api):
    spec = spec_for(api)
    ctx = context_for(api, spec, extras={"marker": "original"})
    derived = ctx.derive()
    derived.extras["marker"] = "changed"
    assert ctx.extras["marker"] == "original"
    assert derived.binding is ctx.binding


def test_reentrant_duplicate_does_not_cleanup_active_construction(api, monkeypatch):
    import openjiuwen.harness.factory as factory

    spec = spec_for(api)
    ctx = context_for(api, spec)
    real_apply = factory.apply_deep_agent_parts

    def duplicate_during_apply(agent, parts):
        with pytest.raises(BaseError):
            api.create_persistent_agent(spec, context=ctx)
        assert ctx.binding.parts is parts
        real_apply(agent, parts)

    monkeypatch.setattr(factory, "apply_deep_agent_parts", duplicate_during_apply)
    agent = spec.build(context=ctx)
    ctx.validate_binding(agent)
    agent.ability_manager.teardown_tools()


@pytest.mark.parametrize("missing", [True, False])
def test_workspace_is_not_created_or_populated(api, tmp_path, missing):
    from openjiuwen.harness.schema.deep_agent_spec import WorkspaceSpec

    root = tmp_path / "workspace"
    if not missing:
        root.mkdir()
    spec = spec_for(api, workspace=WorkspaceSpec(root_path=str(root)))
    ctx = context_for(api, spec)
    if missing:
        with pytest.raises(BaseError):
            spec.build(context=ctx)
        assert not root.exists()
    else:
        agent = spec.build(context=ctx)
        assert not list(root.iterdir())
        agent.ability_manager.teardown_tools()


@pytest.mark.parametrize("field,value", [("namespace_id", ""), ("recovery_ref", " "), ("startup_scope", set())])
def test_invalid_execution_identity(api, field, value):
    spec = spec_for(api)
    ctx = context_for(api, spec)
    with pytest.raises(BaseError):
        replace(ctx.execution, **{field: value})


def test_default_deep_spec_owner_unchanged():
    from openjiuwen.harness.schema.build_context import BuildContext

    spec = DeepAgentSpec(card=AgentCard(id="personal"), enable_sys_operation=False)
    assert spec.resolve_parts().config.tool_owner_id is None
    assert spec.resolve_parts(BuildContext(tool_owner_id="invocation")).config.tool_owner_id == "invocation"


def test_stateful_tool_card_reference_rejected(api):
    from openjiuwen.core.foundation.tool import ToolCard

    spec = spec_for(api, tools=[ToolCard(id="shared-stateful", name="shared-stateful")])
    ctx = context_for(api, spec)
    with pytest.raises(BaseError, match="stateful ToolCard"):
        spec.build(context=ctx)
    assert not ctx.binding.attempted


def test_stateless_tool_card_reference_remains_shared(api):
    from openjiuwen.core.foundation.tool import ToolCard

    card = ToolCard(id="shared-stateless", name="shared-stateless", stateless=True)
    spec = spec_for(api, tools=[card])
    one, two = context_for(api, spec), context_for(api, spec, "session-b")
    first = spec.build(context=one)
    second = spec.build(context=two)
    try:
        assert first.ability_manager.get("shared-stateless").id == card.id
        assert second.ability_manager.get("shared-stateless").id == card.id
        assert card.id == "shared-stateless"
    finally:
        first.ability_manager.teardown_tools()
        second.ability_manager.teardown_tools()


def test_same_card_tools_are_isolated_and_failure_cleanup_is_scoped(api, monkeypatch):
    from openjiuwen.core.foundation.tool import Tool, ToolCard
    from openjiuwen.core.runner import Runner
    from openjiuwen.harness.schema.deep_agent_spec import _TOOL_PROVIDER_REGISTRY, BuiltinToolSpec

    class Echo(Tool):
        async def invoke(self, inputs, **kwargs):
            return inputs

        async def stream(self, inputs, **kwargs):
            yield inputs

    def provider(params, context):
        return Echo(card=ToolCard(id="echo", name="echo", description="test"))

    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, "test.persistent.echo", provider)
    spec = spec_for(api, tools=[BuiltinToolSpec(type="test.persistent.echo")])
    one, two = context_for(api, spec), context_for(api, spec, "session-b")
    first = spec.build(context=one)
    second = spec.build(context=two)
    try:
        first_id = "echo_" + one.tool_owner_id
        second_id = "echo_" + two.tool_owner_id
        assert first_id != second_id
        assert Runner.resource_mgr.get_tool(first_id) is not Runner.resource_mgr.get_tool(second_id)
        assert first.card.id == second.card.id == "business-agent"

        import openjiuwen.harness.factory as factory

        real_apply = factory.apply_deep_agent_parts

        def fail_after_registration(agent, parts):
            real_apply(agent, parts)
            raise RuntimeError("synthetic construction failure")

        monkeypatch.setattr(factory, "apply_deep_agent_parts", fail_after_registration)
        failed = context_for(api, spec, "session-c")
        with pytest.raises(BaseError):
            spec.build(context=failed)
        assert Runner.resource_mgr.get_tool("echo_" + failed.tool_owner_id) is None
        assert Runner.resource_mgr.get_tool(first_id) is not None
        assert Runner.resource_mgr.get_tool(second_id) is not None
    finally:
        first.ability_manager.teardown_tools()
        second.ability_manager.teardown_tools()
