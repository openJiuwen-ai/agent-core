# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-16 guarded write-tool adapter tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceWriteScope
from openjiuwen.harness.persistent_agent.workspace.file_access import (
    PersistentWorkspaceWriteRail,
    WorkspaceWriteAdapter,
)
from openjiuwen.harness.prompts.tools import build_tool_card

pytestmark = pytest.mark.level0


class StaticWorkspaceWriteAccess:
    def __init__(self, scope):
        self.scope = scope
        self.calls = 0

    async def resolve_write_scope(self, execution):
        self.calls += 1
        return self.scope


class RecordingTool:
    def __init__(self, name, *, data=None, success=True, error=None):
        self.card = build_tool_card(name, "RecordingTool", "cn", agent_id="test")
        self.calls = []
        self.data = data
        self.success = success
        self.error = error

    async def invoke(self, inputs, **kwargs):
        self.calls.append((inputs, kwargs))
        return ToolOutput(success=self.success, data=self.data, error=self.error)


class ConditionalFs:
    supports_conditional_write = True

    async def read_file(self, path, *, mode="bytes", **kwargs):
        del mode, kwargs
        return SimpleNamespace(
            code=StatusCode.SUCCESS.code,
            data=SimpleNamespace(content=await asyncio.to_thread(Path(path).read_bytes)),
        )


class ConditionalSysOperation:
    def __init__(self):
        self._fs = ConditionalFs()

    def fs(self):
        return self._fs


class UnsupportedSysOperation:
    class Fs:
        supports_conditional_write = False

    def fs(self):
        return self.Fs()


def execution_for(session_id="session-a"):
    return ExecutionContext(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id=session_id,
        request_id="request",
        execution_id="execution",
        config_version="v1",
        config_fingerprint="0" * 64,
        source_ref="source:message",
        principal_ref="principal",
        startup_scope=frozenset({"write"}),
        source_scope=frozenset({"write"}),
        policy_revision="policy-1",
        capability_snapshot=frozenset({"construction", "workspace_read", "workspace_write"}),
    )


def scope_for(root: Path, *, status="allowed"):
    return WorkspaceWriteScope(
        status=status,
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=root,
        allowed_write_roots=(root,),
        private_sessions_root=root / "sessions",
        current_session_root=root / "sessions/session-a",
        source_ref="workspace://agent-a",
        policy_revision="policy-1",
    )


def tool_context(tool_name, tool_args):
    call = SimpleNamespace(id="call-1", name=tool_name, arguments=tool_args)
    return AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=ToolCallInputs(tool_call=call, tool_name=tool_name, tool_args=tool_args),
        extra={},
    )


@pytest.mark.asyncio
async def test_write_adapter_delegates_physical_path_but_returns_logical_data(tmp_path):
    target = tmp_path / "report.md"
    base = RecordingTool(
        "write_file",
        data={
            "file_path": str(target),
            "bytes_written": 12,
            "created": True,
            "original_file": "secret old body",
        },
    )
    execution = execution_for()
    rail = PersistentWorkspaceWriteRail(execution, StaticWorkspaceWriteAccess(scope_for(tmp_path)))
    rail.set_sys_operation(ConditionalSysOperation())
    ctx = tool_context("write_file", {"file_path": "report.md", "content": "secret new body"})
    adapter = WorkspaceWriteAdapter(base)

    await rail.before_tool_call(ctx)
    target_ref = ctx.extra["_persistent_tool_target_ref"]
    args_summary = ctx.extra["_persistent_tool_args_summary"]
    try:
        result = await adapter.invoke(ctx.inputs.tool_args)
    finally:
        await rail.after_tool_call(ctx)

    assert result.success is True
    assert base.calls[0][0]["file_path"] == str(target.resolve())
    assert result.data["file_path"] == "report.md"
    assert "original_file" not in result.data
    assert str(tmp_path) not in str(result.data)
    assert base.calls[0][1]["_persistent_conditional_write"] == {"create_if_absent": True}
    assert target_ref == "workspace://agent-a/report.md"
    assert "secret new body" not in args_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["forbidden", "unknown"])
async def test_write_rail_blocks_before_backend_for_denied_or_unknown_scope(tmp_path, status):
    base = RecordingTool("write_file", data={"file_path": "should-not-write"})
    execution = execution_for()
    rail = PersistentWorkspaceWriteRail(execution, StaticWorkspaceWriteAccess(scope_for(tmp_path, status=status)))
    rail.set_sys_operation(ConditionalSysOperation())
    ctx = tool_context("write_file", {"file_path": "report.md", "content": "body"})
    adapter = WorkspaceWriteAdapter(base)

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True
    assert base.calls == []
    result = await adapter.invoke(ctx.inputs.tool_args)
    assert result.success is False
    assert result.data["status"] in {"forbidden", "unknown"}
    assert str(tmp_path) not in str(result.data)


@pytest.mark.asyncio
async def test_write_adapter_exposes_typed_version_conflict_without_backend_path(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("old", encoding="utf-8")
    base = RecordingTool(
        "edit_file",
        success=False,
        data={"status": "version_conflict", "file_path": str(target)},
        error=f"backend leaked {target}",
    )
    execution = execution_for()
    rail = PersistentWorkspaceWriteRail(execution, StaticWorkspaceWriteAccess(scope_for(tmp_path)))
    rail.set_sys_operation(ConditionalSysOperation())
    ctx = tool_context(
        "edit_file",
        {"file_path": "report.md", "old_string": "old", "new_string": "new"},
    )
    adapter = WorkspaceWriteAdapter(base)

    await rail.before_tool_call(ctx)
    try:
        result = await adapter.invoke(ctx.inputs.tool_args)
    finally:
        await rail.after_tool_call(ctx)

    assert result.success is False
    assert result.data == {"status": "version_conflict"}
    assert result.error == "workspace file version conflict; re-read it before retrying"
    assert str(tmp_path) not in str(result)
    assert base.calls[0][1]["_persistent_conditional_write"]["expected_content_sha256"]


@pytest.mark.asyncio
async def test_write_adapter_fails_closed_without_preflight(tmp_path):
    base = RecordingTool("write_file", data={"file_path": str(tmp_path / "body.txt")})
    adapter = WorkspaceWriteAdapter(base)

    result = await adapter.invoke({"file_path": "body.txt", "content": "body"})

    assert result.success is False
    assert result.data == {"status": "unknown"}
    assert base.calls == []


@pytest.mark.asyncio
async def test_write_rail_fails_closed_when_backend_lacks_conditional_write(tmp_path):
    base = RecordingTool("write_file", data={"file_path": "should-not-write"})
    execution = execution_for()
    rail = PersistentWorkspaceWriteRail(execution, StaticWorkspaceWriteAccess(scope_for(tmp_path)))
    rail.set_sys_operation(UnsupportedSysOperation())
    ctx = tool_context("write_file", {"file_path": "draft.txt", "content": "body"})

    await rail.before_tool_call(ctx)

    assert ctx.inputs.tool_result.data == {"status": "unavailable"}
    assert base.calls == []
