# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-16 write rail construction and callback-order tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent, ToolCallInputs
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.persistent_agent.persistent_agent.execution.tool_intent_rail import PersistentToolIntentRail
from openjiuwen.harness.persistent_agent.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.persistent_agent.schema.spec import PersistentAgentSpec
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.access import WorkspaceWriteScope
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.file_access import PersistentWorkspaceWriteRail

pytestmark = pytest.mark.level0


class UntouchedKVStore(BaseKVStore):
    def _touched(self, *args, **kwargs):
        raise AssertionError("rail construction must not access the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


class HostPorts:
    async def record(self, *args):
        raise AssertionError("rail construction must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("rail construction must not read logs")

    async def admit(self, *args):
        raise AssertionError("rail construction must not admit model calls")

    async def report(self, *args):
        raise AssertionError("rail construction must not report usage")


class StaticWorkspaceAccess:
    async def resolve_read_scope(self, execution):
        raise AssertionError("read scope should not be used by write rail tests")


class StaticWorkspaceWriteAccess:
    async def resolve_write_scope(self, execution):
        return WorkspaceWriteScope(
            status="allowed",
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            workspace_root=Path("workspace"),
            allowed_write_roots=(Path("workspace"),),
            private_sessions_root=Path("workspace/sessions"),
            current_session_root=Path("workspace/sessions") / execution.session_id,
            source_ref="workspace://agent-a",
            policy_revision=execution.policy_revision,
        )


def execution_for(spec):
    return ExecutionContext(
        namespace_id="tenant",
        agent_id=spec.card.id,
        session_id="session-a",
        request_id="request",
        execution_id="execution",
        config_version="v1",
        config_fingerprint=spec.fingerprint(),
        source_ref="source:message",
        principal_ref="principal",
        startup_scope=frozenset({"write"}),
        source_scope=frozenset({"write"}),
        policy_revision="policy-1",
        capability_snapshot=spec.enabled_features,
    )


def ports_for():
    return PersistentPorts(
        commit=HostPorts(),
        budget=HostPorts(),
        window_backend=UntouchedKVStore(),
        workspace_access=StaticWorkspaceAccess(),
        workspace_write_access=StaticWorkspaceWriteAccess(),
    )


class Agent:
    class PromptBuilder:
        language = "cn"

    def __init__(self):
        self.system_prompt_builder = self.PromptBuilder()
        self.card = AgentCard(id="agent-a", name="test")
        self.ability_manager = SimpleNamespace(added=[], removed=[])
        self.ability_manager.add_ability = lambda card, tool: self.ability_manager.added.append((card.name, tool))
        self.ability_manager.remove_ability = lambda name: self.ability_manager.removed.append(name)


class ConditionalFs:
    supports_conditional_write = True


class ConditionalSysOperation:
    def fs(self):
        return ConditionalFs()


def test_write_rail_is_after_security_but_before_intent_and_registers_existing_tool_names():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enabled_features=frozenset({"construction", "workspace_read", "workspace_write"}),
    )
    rail = PersistentWorkspaceWriteRail(execution_for(spec), StaticWorkspaceWriteAccess())

    assert rail.priority > PersistentToolIntentRail.priority
    assert rail.callback_priority(AgentCallbackEvent.BEFORE_TOOL_CALL) == rail.priority

    rail.set_sys_operation(ConditionalSysOperation())
    agent = Agent()
    rail.init(agent)

    assert {name for name, _ in agent.ability_manager.added} == {"write_file", "edit_file"}
    assert all(tool._base_tool.enable_conditional_writes is True for _, tool in agent.ability_manager.added)


def test_spec_mounts_write_rail_only_when_feature_is_enabled():
    read_spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=frozenset({"construction", "workspace_read"}),
    )
    read_context = PersistentBuildContext(
        execution=execution_for(read_spec),
        session=create_agent_session(session_id="session-a"),
        ports=ports_for(),
    )
    read_agent = read_spec.build(context=read_context)
    try:
        assert read_agent.find_rails_by_type((PersistentWorkspaceWriteRail,)) == []
    finally:
        read_agent.ability_manager.teardown_tools()

    write_spec = read_spec.model_copy(
        update={"enabled_features": frozenset({"construction", "workspace_read", "workspace_write"})}
    )
    write_context = PersistentBuildContext(
        execution=execution_for(write_spec),
        session=create_agent_session(session_id="session-a"),
        ports=ports_for(),
    )
    write_agent = write_spec.build(context=write_context)
    try:
        assert len(write_agent.find_rails_by_type((PersistentWorkspaceWriteRail,))) == 1
    finally:
        write_agent.ability_manager.teardown_tools()


@pytest.mark.asyncio
async def test_write_rail_records_preflight_summary_before_intent_context_is_consumed(tmp_path):
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enabled_features=frozenset({"construction", "workspace_read", "workspace_write"}),
    )
    execution = execution_for(spec)
    rail = PersistentWorkspaceWriteRail(execution, StaticWorkspaceWriteAccess())
    rail.set_sys_operation(ConditionalSysOperation())
    call = SimpleNamespace(
        id="call-1",
        name="write_file",
        arguments={"file_path": "draft.txt", "content": "private body"},
    )
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=ToolCallInputs(tool_call=call, tool_name="write_file", tool_args=call.arguments),
        extra={},
    )

    await rail.before_tool_call(ctx)

    assert ctx.extra["_persistent_tool_target_ref"] == "workspace://agent-a/draft.txt"
    assert "private body" not in ctx.extra["_persistent_tool_args_summary"]
    assert ctx.extra["_persistent_write_preflight"].relative_path == "draft.txt"
    await rail.after_tool_call(ctx)
    assert "_persistent_write_context_token" not in ctx.extra


def test_regular_deep_agent_does_not_mount_persistent_write_rail():
    agent = DeepAgent(AgentCard(id="regular-agent", name="regular"))

    assert agent.find_rails_by_type((PersistentWorkspaceWriteRail,)) == []
