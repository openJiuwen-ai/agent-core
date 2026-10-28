# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-16 write-scope contract and construction gates."""

from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

import openjiuwen.harness.persistent_agent as persistent_agent_api
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.spec import PersistentAgentSpec

pytestmark = pytest.mark.level0


class UntouchedKVStore(BaseKVStore):
    def _touched(self, *args, **kwargs):
        raise AssertionError("contract validation must not access the window backend")

    set = exclusive_set = get = exists = delete = get_by_prefix = _touched
    delete_by_prefix = mget = batch_delete = pipeline = _touched


class HostPorts:
    async def record(self, *args):
        raise AssertionError("contract validation must not record events")

    async def read_execution_log(self, *args):
        raise AssertionError("contract validation must not read logs")

    async def admit(self, *args):
        raise AssertionError("contract validation must not admit model calls")

    async def report(self, *args):
        raise AssertionError("contract validation must not report usage")


class StaticReadAccess:
    async def resolve_read_scope(self, execution):
        raise AssertionError("construction must not resolve read authorization")


class StaticWriteAccess:
    async def resolve_write_scope(self, execution):
        raise AssertionError("construction must not resolve write authorization")


def make_spec(features=frozenset({"construction"}), *, enable_sys_operation=True):
    return PersistentAgentSpec(
        card=AgentCard(id="agent-a", name="test"),
        enable_sys_operation=enable_sys_operation,
        enable_task_loop=False,
        auto_create_workspace=False,
        enabled_features=features,
    )


def make_context(spec, *, read_access=None, write_access=None):
    session_id = "session-a"
    execution = ExecutionContext(
        namespace_id="tenant",
        agent_id=spec.card.id,
        session_id=session_id,
        request_id="request",
        execution_id="execution",
        config_version="v1",
        config_fingerprint=spec.fingerprint(),
        source_ref="source:message",
        principal_ref="principal",
        startup_scope=frozenset({"read", "write"}),
        source_scope=frozenset({"read", "write"}),
        policy_revision="policy-1",
        capability_snapshot=spec.enabled_features,
    )
    port_args = {
        "commit": HostPorts(),
        "budget": HostPorts(),
        "window_backend": UntouchedKVStore(),
    }
    if read_access is not None:
        port_args["workspace_access"] = read_access
    if write_access is not None:
        port_names = {field.name for field in fields(PersistentPorts)}
        assert "workspace_write_access" in port_names, "PersistentPorts lacks the PA-16 host port"
        port_args["workspace_write_access"] = write_access
    return PersistentBuildContext(
        execution=execution,
        session=create_agent_session(session_id=session_id),
        ports=PersistentPorts(**port_args),
    )


def test_workspace_write_feature_is_supported_but_disabled_by_default():
    default_spec = make_spec()
    assert "workspace_write" not in default_spec.enabled_features

    try:
        spec = make_spec(frozenset({"construction", "workspace_read", "workspace_write"}))
    except Exception as exc:
        pytest.fail(f"PersistentAgentSpec rejected the PA-16 capability: {exc}")
    assert "workspace_write" in spec.enabled_features


def test_existing_workspace_read_exports_still_resolve_from_root_package():
    assert getattr(persistent_agent_api, "WorkspaceAccessPort", None) is not None
    assert getattr(persistent_agent_api, "WorkspaceReadScope", None) is not None


def test_write_scope_is_public_immutable_and_bound_to_execution_identity():
    scope_type = getattr(persistent_agent_api, "WorkspaceWriteScope", None)
    assert scope_type is not None, "WorkspaceWriteScope is not exported by the Persistent Agent API"
    validate_scope = getattr(
        __import__("openjiuwen.harness.persistent_agent.workspace", fromlist=["validate_write_scope"]),
        "validate_write_scope",
        None,
    )
    assert callable(validate_scope), "write scope validation is not exported by the workspace package"

    spec = make_spec(frozenset({"construction", "workspace_read", "workspace_write"}))
    context = make_context(spec)
    scope = scope_type(
        status="allowed",
        agent_id=spec.card.id,
        session_id=context.execution.session_id,
        principal_ref=context.execution.principal_ref,
        policy_revision=context.execution.policy_revision,
        workspace_root=Path("/workspace/agent-a"),
        allowed_write_roots=(Path("/workspace/agent-a/shared"),),
        private_sessions_root=Path("/workspace/agent-a/sessions"),
        current_session_root=Path("/workspace/agent-a/sessions/session-a"),
        source_ref="workspace:agent-a",
    )

    assert validate_scope(scope, context.execution) is scope
    assert scope.allowed_write_roots == (Path("/workspace/agent-a/shared"),)
    with pytest.raises(FrozenInstanceError):
        scope.status = "forbidden"

    wrong_execution = replace(context.execution, session_id="session-b")
    with pytest.raises(BaseError, match="workspace write scope mismatch"):
        validate_scope(scope, wrong_execution)


def test_persistent_ports_validate_the_async_write_access_port():
    port_names = {field.name for field in fields(PersistentPorts)}
    assert "workspace_write_access" in port_names, "PersistentPorts lacks the PA-16 host port"

    context = make_context(
        make_spec(frozenset({"construction", "workspace_read", "workspace_write"})),
        write_access=StaticWriteAccess(),
    )
    port_type = getattr(persistent_agent_api, "WorkspaceWriteAccessPort", None)
    assert port_type is not None, "WorkspaceWriteAccessPort is not exported by the Persistent Agent API"
    assert isinstance(context.ports.workspace_write_access, port_type)
    context.ports.validate()


def test_workspace_write_requires_read_capability_and_host_write_port():
    write_without_read = make_spec(frozenset({"construction", "workspace_write"}))
    with pytest.raises(BaseError, match="workspace_write requires workspace_read"):
        make_context(write_without_read).validate(write_without_read)

    write_without_port = make_spec(frozenset({"construction", "workspace_read", "workspace_write"}))
    with pytest.raises(BaseError, match="workspace_write_access"):
        make_context(write_without_port, read_access=StaticReadAccess()).validate(write_without_port)


def test_workspace_write_requires_sys_operation():
    spec = make_spec(
        frozenset({"construction", "workspace_read", "workspace_write"}),
        enable_sys_operation=False,
    )
    with pytest.raises(BaseError, match="workspace_write requires enable_sys_operation"):
        make_context(spec, read_access=StaticReadAccess(), write_access=StaticWriteAccess()).validate(spec)
