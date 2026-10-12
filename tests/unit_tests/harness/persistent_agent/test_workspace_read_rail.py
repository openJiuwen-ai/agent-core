# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 read-only Rail tests."""

from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.persistent_agent.persistent_agent.execution.tool_intent_rail import PersistentToolIntentRail
from openjiuwen.harness.persistent_agent.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.persistent_agent.schema.spec import PersistentAgentSpec
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.file_access import PersistentWorkspaceReadRail

pytestmark = pytest.mark.level0


class UntouchedKVStore(BaseKVStore):
    def _touched(self, *args, **kwargs):
        raise AssertionError("Rail validation must not access the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


class HostPorts:
    async def record(self, *args):
        raise AssertionError("Rail validation must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("Rail validation must not read logs")

    async def admit(self, *args):
        raise AssertionError("Rail validation must not admit model calls")

    async def report(self, *args):
        raise AssertionError("Rail validation must not report usage")


class StaticWorkspaceAccess:
    async def resolve_read_scope(self, execution):
        return WorkspaceReadScope(
            status="unknown",
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            workspace_root=Path("workspace"),
            allowed_roots=(Path("workspace"),),
            private_sessions_root=Path("workspace/sessions"),
            current_session_root=Path("workspace/sessions") / execution.session_id,
            source_ref="workspace://agent-a",
            policy_revision=execution.policy_revision,
        )


class AbilityManager:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_ability(self, card, tool):
        self.added.append((card.name, tool))

    def remove_ability(self, name):
        self.removed.append(name)


class Agent:
    class PromptBuilder:
        language = "cn"

    def __init__(self):
        self.system_prompt_builder = self.PromptBuilder()
        self.card = AgentCard(id="agent-a", name="test")
        self.ability_manager = AbilityManager()


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
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="policy-1",
        capability_snapshot=spec.enabled_features,
    )


def ports_for(access):
    return PersistentPorts(
        commit=HostPorts(),
        budget=HostPorts(),
        window_backend=UntouchedKVStore(),
        workspace_access=access,
    )


def test_rail_registers_only_guarded_read_tools():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enabled_features=frozenset({"construction", "workspace_read"}),
    )
    rail = PersistentWorkspaceReadRail(execution_for(spec), StaticWorkspaceAccess())
    rail.set_sys_operation(object())
    agent = Agent()

    rail.init(agent)

    assert {name for name, _ in agent.ability_manager.added} == {"read_file", "list_files"}
    assert not {
        "write_file",
        "edit_file",
        "bash",
        "powershell",
        "code",
    }.intersection({name for name, _ in agent.ability_manager.added})


def test_workspace_read_requires_sys_operation():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_sys_operation=False,
        enabled_features=frozenset({"construction", "workspace_read"}),
    )
    execution = execution_for(spec)
    context = PersistentBuildContext(
        execution=execution,
        session=create_agent_session(session_id=execution.session_id),
        ports=ports_for(StaticWorkspaceAccess()),
    )

    with pytest.raises(BaseError, match="enable_sys_operation"):
        context.validate(spec)


def test_spec_mounts_workspace_read_rail_only_when_feature_is_enabled():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=frozenset({"construction", "workspace_read"}),
    )
    context = PersistentBuildContext(
        execution=execution_for(spec),
        session=create_agent_session(session_id="session-a"),
        ports=ports_for(StaticWorkspaceAccess()),
    )

    agent = spec.build(context=context)
    try:
        assert len(agent.find_rails_by_type((PersistentWorkspaceReadRail,))) == 1
        assert len(agent.find_rails_by_type((PersistentToolIntentRail,))) == 1
    finally:
        agent.ability_manager.teardown_tools()


def test_spec_mounts_tool_intent_rail_without_workspace_read():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_task_loop=False,
        auto_create_workspace=False,
    )
    execution = execution_for(spec)
    context = PersistentBuildContext(
        execution=execution,
        session=create_agent_session(session_id=execution.session_id),
        ports=ports_for(StaticWorkspaceAccess()),
    )

    agent = spec.build(context=context)
    try:
        assert len(agent.find_rails_by_type((PersistentToolIntentRail,))) == 1
    finally:
        agent.ability_manager.teardown_tools()


def test_regular_deep_agent_does_not_mount_persistent_tool_intent_rail():
    agent = DeepAgent(AgentCard(id="regular-agent", name="regular"))

    assert agent.find_rails_by_type((PersistentToolIntentRail,)) == []
