# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 workspace access contract tests."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.spec import PersistentAgentSpec
from openjiuwen.harness.persistent_agent.workspace.access import (
    WorkspaceAccessPort,
    WorkspaceReadScope,
    WorkspaceWriteScope,
    validate_read_scope,
    validate_write_scope,
)

pytestmark = pytest.mark.level0


class UntouchedKVStore(BaseKVStore):
    def _touched(self, *args, **kwargs):
        raise AssertionError("contract validation must not access the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


class HostWorkspaceAccess:
    async def resolve_read_scope(self, execution):
        return WorkspaceReadScope(
            status="unknown",
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            workspace_root=Path("workspace"),
            allowed_roots=(Path("workspace"),),
            private_sessions_root=Path("workspace/sessions"),
            current_session_root=Path("workspace/sessions") / execution.session_id,
            source_ref="workspace:test",
            policy_revision=execution.policy_revision,
        )


class HostPorts:
    async def record(self, *args):
        raise AssertionError("contract validation must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("contract validation must not read execution logs")

    async def admit(self, *args):
        raise AssertionError("contract validation must not admit model calls")

    async def report(self, *args):
        raise AssertionError("contract validation must not report model usage")


def execution_for(spec, *, session_id="session-a"):
    return ExecutionContext(
        namespace_id="tenant",
        agent_id=spec.card.id,
        session_id=session_id,
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


def context_for(spec, *, workspace_access=None):
    execution = execution_for(spec)
    return PersistentBuildContext(
        execution=execution,
        session=create_agent_session(session_id=execution.session_id),
        ports=PersistentPorts(
            commit=HostPorts(),
            budget=HostPorts(),
            window_backend=UntouchedKVStore(),
            workspace_access=workspace_access,
        ),
    )


def test_workspace_read_scope_is_immutable_and_contains_authorized_roots():
    scope = WorkspaceReadScope(
        status="allowed",
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=Path("workspace"),
        allowed_roots=(Path("workspace/work"),),
        private_sessions_root=Path("workspace/sessions"),
        current_session_root=Path("workspace/sessions/session-a"),
        source_ref="workspace:agent-a",
        policy_revision="policy-1",
    )

    assert scope.status == "allowed"
    assert scope.allowed_roots == (Path("workspace/work"),)
    with pytest.raises(FrozenInstanceError):
        scope.status = "forbidden"


def test_workspace_access_port_is_async_and_validated_by_persistent_ports():
    ports = PersistentPorts(
        commit=HostPorts(),
        budget=HostPorts(),
        window_backend=UntouchedKVStore(),
        workspace_access=HostWorkspaceAccess(),
    )

    assert isinstance(ports.workspace_access, WorkspaceAccessPort)
    ports.validate()


def test_workspace_read_feature_requires_workspace_access_before_build():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_sys_operation=True,
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=frozenset({"construction", "workspace_read"}),
    )

    with pytest.raises(BaseError, match="workspace_access"):
        context_for(spec).validate(spec)


def test_unknown_workspace_scope_is_preserved_without_filesystem_access():
    spec = PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_sys_operation=True,
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=frozenset({"construction", "workspace_read"}),
    )
    context = context_for(spec, workspace_access=HostWorkspaceAccess())

    context.validate(spec)
    scope = __import__("asyncio").run(context.ports.workspace_access.resolve_read_scope(context.execution))

    assert scope.status == "unknown"


def test_scope_identity_mismatch_is_rejected():
    scope = WorkspaceReadScope(
        status="allowed",
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=Path("workspace"),
        allowed_roots=(Path("workspace"),),
        private_sessions_root=Path("workspace/sessions"),
        current_session_root=Path("workspace/sessions/session-a"),
        source_ref="workspace:agent-a",
        policy_revision="policy-1",
    )
    spec = PersistentAgentSpec(card=AgentCard(id="agent-b", name="test"))

    with pytest.raises(BaseError, match="workspace access scope mismatch"):
        validate_read_scope(scope, execution_for(spec))


def test_host_wide_read_scope_is_bound_to_full_access_profile():
    spec = PersistentAgentSpec(card=AgentCard(id="agent-a", name="test"), permission_profile="full_access")
    scope = WorkspaceReadScope(
        status="allowed",
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=Path("workspace"),
        allowed_roots=(),
        private_sessions_root=Path("workspace/sessions"),
        current_session_root=Path("workspace/sessions/session-a"),
        source_ref="workspace:agent-a",
        policy_revision="policy-1",
        host_wide=True,
    )

    assert validate_read_scope(scope, execution_for(spec), permission_profile="full_access") is scope
    with pytest.raises(BaseError, match="workspace access profile mismatch"):
        validate_read_scope(scope, execution_for(spec), permission_profile="default")


def test_host_wide_write_scope_is_bound_to_full_access_profile():
    spec = PersistentAgentSpec(card=AgentCard(id="agent-a", name="test"), permission_profile="full_access")
    scope = WorkspaceWriteScope(
        status="allowed",
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=Path("workspace"),
        allowed_write_roots=(),
        private_sessions_root=Path("workspace/sessions"),
        current_session_root=Path("workspace/sessions/session-a"),
        source_ref="workspace:agent-a",
        policy_revision="policy-1",
        host_wide=True,
    )

    assert validate_write_scope(scope, execution_for(spec), permission_profile="full_access") is scope
    with pytest.raises(BaseError, match="workspace write profile mismatch"):
        validate_write_scope(scope, execution_for(spec), permission_profile="automatic")
