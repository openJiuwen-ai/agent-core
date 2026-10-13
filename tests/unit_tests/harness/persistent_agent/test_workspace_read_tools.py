# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-14 guarded read/list tool tests."""

from pathlib import Path

import pytest

from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.spec import PersistentAgentSpec
from openjiuwen.harness.persistent_agent.tools.workspace_read import (
    PersistentWorkspaceListTool,
    PersistentWorkspaceReadTool,
)
from openjiuwen.harness.persistent_agent.workspace.access import WorkspaceReadScope
from openjiuwen.harness.prompts.tools import build_tool_card

pytestmark = pytest.mark.level0


class StaticWorkspaceAccess:
    def __init__(self, scope):
        self.scope = scope
        self.calls = 0

    async def resolve_read_scope(self, execution):
        self.calls += 1
        return self.scope


class RecordingTool:
    def __init__(self, name, *, success=True, data=None, error=None):
        self.card = build_tool_card(name, "RecordingTool", "cn", agent_id="test")
        self.calls = []
        self.success = success
        self.data = data
        self.error = error

    async def invoke(self, inputs, **kwargs):
        self.calls.append((inputs, kwargs))
        return ToolOutput(success=self.success, data=self.data, error=self.error)

    def render_for_llm(self, output):
        return str(output.data)


def execution_for(session_id="session-a"):
    spec = PersistentAgentSpec(card=AgentCard(id="agent-a", name="test"))
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


def scope_for(root: Path, *, status="allowed"):
    return WorkspaceReadScope(
        status=status,
        agent_id="agent-a",
        session_id="session-a",
        workspace_root=root,
        allowed_roots=(root,),
        private_sessions_root=root / "sessions",
        current_session_root=root / "sessions/session-a",
        source_ref="workspace://agent-a",
        policy_revision="policy-1",
    )


@pytest.mark.asyncio
async def test_read_tool_checks_scope_before_delegating_and_returns_metadata(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("hello", encoding="utf-8")
    base = RecordingTool(
        "read_file",
        data={"content": "hello", "file_path": str(target), "line_count": 1},
    )
    tool = PersistentWorkspaceReadTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path)),
    )

    result = await tool.invoke({"file_path": "report.md"})

    assert result.success is True
    assert len(base.calls) == 1
    assert base.calls[0][0]["file_path"] == str(target.resolve())
    assert result.data["file_path"] == "report.md"
    assert result.data["source_refs"] == ["workspace://agent-a/report.md"]
    assert result.data["version"]
    assert result.data["status"] == "ok"
    assert str(tmp_path) not in str(result.data)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_status", ["forbidden", "unknown"])
async def test_read_tool_does_not_call_backend_for_denied_or_unknown_scope(tmp_path, scope_status):
    base = RecordingTool("read_file", data={"content": "should not be returned"})
    tool = PersistentWorkspaceReadTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path, status=scope_status)),
    )

    result = await tool.invoke({"file_path": "report.md"})

    assert result.success is False
    assert base.calls == []
    assert result.data["status"] == scope_status
    assert str(tmp_path) not in str(result.data)


@pytest.mark.asyncio
async def test_read_tool_sanitizes_missing_file_error(tmp_path):
    missing = tmp_path / "missing.md"
    base = RecordingTool(
        "read_file",
        success=False,
        error=f"backend leaked {missing}",
    )
    tool = PersistentWorkspaceReadTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path)),
    )

    result = await tool.invoke({"file_path": "missing.md"})

    assert result.success is False
    assert result.data["status"] == "not_found"
    assert str(tmp_path) not in (result.error or "")


@pytest.mark.asyncio
async def test_list_tool_returns_logical_path_and_source(tmp_path):
    base = RecordingTool(
        "list_files",
        data={"files": ["report.md"], "dirs": ["subdir"]},
    )
    tool = PersistentWorkspaceListTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path)),
    )

    result = await tool.invoke({"path": "."})

    assert result.success is True
    assert base.calls[0][0]["path"] == str(tmp_path.resolve())
    assert result.data["path"] == "."
    assert result.data["source_refs"] == ["workspace://agent-a"]
    assert result.data["status"] == "ok"
    assert result.data["files"] == ["report.md"]


@pytest.mark.asyncio
async def test_read_tool_marks_explicit_range_as_partial(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("hello", encoding="utf-8")
    base = RecordingTool("read_file", data={"content": "hello", "file_path": str(target)})
    tool = PersistentWorkspaceReadTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path)),
    )

    result = await tool.invoke({"file_path": "report.md", "limit": 1})

    assert result.success is True
    assert result.data["status"] == "partial"
    assert result.data["completeness"] == "partial"
    assert result.data["next_cursor"] is None


@pytest.mark.asyncio
async def test_read_tool_does_not_expose_backend_path_in_content_metadata(tmp_path):
    target = tmp_path / "image.png"
    target.write_bytes(b"not-an-image")
    base = RecordingTool(
        "read_file",
        data={
            "content": f"Image file read: {target}",
            "file_path": str(target),
            "multimodal": [{"source_path": str(target)}],
        },
    )
    tool = PersistentWorkspaceReadTool(
        base_tool=base,
        execution=execution_for(),
        workspace_access=StaticWorkspaceAccess(scope_for(tmp_path)),
    )

    result = await tool.invoke({"file_path": "image.png"})

    assert result.success is True
    assert str(target) not in result.data["content"]
    assert result.data["file_path"] == "image.png"
    assert result.data["multimodal"][0]["source_path"] == "image.png"
