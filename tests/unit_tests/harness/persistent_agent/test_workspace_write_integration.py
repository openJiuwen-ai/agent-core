# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent-core integration coverage for the PA-16 write boundary."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.persistent_agent.persistent_agent.execution.emitter import EventEmitter
from openjiuwen.harness.persistent_agent.persistent_agent.execution.tool_intent_rail import PersistentToolIntentRail
from openjiuwen.harness.persistent_agent.persistent_agent.schema.build_context import ConstructionBinding
from openjiuwen.harness.persistent_agent.persistent_agent.schema.events import EventType
from openjiuwen.harness.persistent_agent.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.access import WorkspaceWriteScope
from openjiuwen.harness.persistent_agent.persistent_agent.workspace.file_access import (
    PersistentWorkspaceWriteRail,
    WorkspaceWriteAdapter,
)
from openjiuwen.harness.prompts.tools import build_tool_card

pytestmark = pytest.mark.level0


class ReadableConditionalFs:
    supports_conditional_write = True

    async def read_file(self, path, *, mode="bytes", **kwargs):
        del mode, kwargs
        return SimpleNamespace(
            code=StatusCode.SUCCESS.code,
            data=SimpleNamespace(content=await asyncio.to_thread(Path(path).read_bytes)),
        )


class ConditionalSysOperation:
    def __init__(self):
        self._fs = ReadableConditionalFs()

    def fs(self):
        return self._fs


class AtomicFileWriter:
    """Small host-like writer used to make adapter races deterministic."""

    def __init__(self, name="write_file"):
        self.card = build_tool_card(name, "AtomicFileWriter", "cn", agent_id="test")
        self.calls = []
        self._lock = asyncio.Lock()

    async def invoke(self, inputs, **kwargs):
        self.calls.append((dict(inputs), dict(kwargs)))
        condition = kwargs["_persistent_conditional_write"]
        target = Path(inputs["file_path"])
        async with self._lock:
            target_exists = await asyncio.to_thread(target.exists)
            current = await asyncio.to_thread(target.read_bytes) if target_exists else None
            expected = condition.get("expected_content_sha256")
            if expected is not None:
                import hashlib

                if current is None or hashlib.sha256(current).hexdigest() != expected:
                    return ToolOutput(
                        success=False,
                        data={"status": "version_conflict", "file_path": str(target)},
                        error=f"backend path {target}",
                    )
            elif condition.get("create_if_absent") and current is not None:
                return ToolOutput(
                    success=False,
                    data={"status": "version_conflict", "file_path": str(target)},
                    error=f"backend path {target}",
                )

            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_text, str(inputs["content"]), encoding="utf-8")
            return ToolOutput(success=True, data={"file_path": str(target)})


class RaisingWriter(AtomicFileWriter):
    async def invoke(self, inputs, **kwargs):
        self.calls.append((dict(inputs), dict(kwargs)))
        raise ConnectionError("host connection dropped after dispatch")


class RecordingCommit:
    def __init__(self, *, fail_intent=False):
        self.events = []
        self.fail_intent = fail_intent

    async def record(self, execution, event):
        del execution
        if self.fail_intent and event.event_type == EventType.TOOL_INTENT:
            raise RuntimeError("intent persistence failed")
        self.events.append(event)


class StaticWriteAccess:
    def __init__(self, scope):
        self.scope = scope

    async def resolve_write_scope(self, execution):
        del execution
        return self.scope


def execution_for(*, agent_id="agent-a", session_id="session-a"):
    return ExecutionContext(
        namespace_id="tenant",
        agent_id=agent_id,
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


def scope_for(
    root: Path,
    *,
    agent_id="agent-a",
    session_id="session-a",
    allowed_roots=None,
    current_session_root=None,
):
    return WorkspaceWriteScope(
        status="allowed",
        agent_id=agent_id,
        session_id=session_id,
        workspace_root=root,
        allowed_write_roots=tuple(allowed_roots or (root,)),
        private_sessions_root=root / "sessions",
        current_session_root=current_session_root or root / "sessions" / session_id,
        source_ref="workspace://agent-a",
        policy_revision="policy-1",
    )


def tool_context(tool_name, tool_args, *, call_id="call-1"):
    call = SimpleNamespace(id=call_id, name=tool_name, arguments=tool_args)
    return AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=ToolCallInputs(tool_call=call, tool_name=tool_name, tool_args=tool_args),
        extra={},
    )


def make_write_components(tmp_path, *, writer=None, scope=None, execution=None):
    execution = execution or execution_for()
    rail = PersistentWorkspaceWriteRail(
        execution,
        StaticWriteAccess(scope or scope_for(tmp_path)),
    )
    rail.set_sys_operation(ConditionalSysOperation())
    base = writer or AtomicFileWriter()
    adapter = WorkspaceWriteAdapter(base)
    return rail, adapter, base


@pytest.mark.asyncio
async def test_allowed_shared_and_current_session_writes_use_host_physical_path(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "sessions" / "session-a").mkdir(parents=True)
    rail, adapter, base = make_write_components(
        tmp_path,
        scope=scope_for(tmp_path, allowed_roots=(tmp_path / "shared",)),
    )

    for index, relative_path in enumerate(("shared/report.md", "sessions/session-a/draft.md")):
        ctx = tool_context("write_file", {"file_path": relative_path, "content": f"body-{index}"})
        await rail.before_tool_call(ctx)
        try:
            result = await adapter.invoke(ctx.inputs.tool_args)
        finally:
            await rail.after_tool_call(ctx)
        assert result.success is True
        assert base.calls[index][0]["file_path"] == str((tmp_path / relative_path).resolve())

    assert (tmp_path / "shared/report.md").read_text(encoding="utf-8") == "body-0"
    assert (tmp_path / "sessions/session-a/draft.md").read_text(encoding="utf-8") == "body-1"


@pytest.mark.asyncio
async def test_sibling_session_and_identity_mismatch_are_blocked_before_writer(tmp_path):
    (tmp_path / "sessions" / "session-b").mkdir(parents=True)
    writer = AtomicFileWriter()
    rail, _, _ = make_write_components(
        tmp_path,
        writer=writer,
        scope=scope_for(tmp_path, allowed_roots=(tmp_path / "shared",)),
    )

    sibling = tool_context(
        "write_file",
        {"file_path": "sessions/session-b/secret.md", "content": "secret"},
    )
    await rail.before_tool_call(sibling)
    sibling_result = sibling.inputs.tool_result
    await rail.after_tool_call(sibling)

    mismatched_rail, _, _ = make_write_components(
        tmp_path,
        writer=writer,
        execution=execution_for(agent_id="agent-a"),
        scope=scope_for(tmp_path, agent_id="another-agent", allowed_roots=(tmp_path / "shared",)),
    )
    mismatched = tool_context(
        "write_file",
        {"file_path": "shared/other.md", "content": "body"},
    )
    await mismatched_rail.before_tool_call(mismatched)
    mismatched_result = mismatched.inputs.tool_result
    await mismatched_rail.after_tool_call(mismatched)

    assert sibling_result.data["status"] == "forbidden"
    assert mismatched_result.data["status"] == "unknown"
    assert writer.calls == []
    assert not (tmp_path / "sessions/session-b/secret.md").exists()
    assert not (tmp_path / "shared/other.md").exists()


@pytest.mark.asyncio
async def test_competing_updates_through_persistent_adapters_have_one_winner(tmp_path):
    target = tmp_path / "report.md"
    target.write_text("base", encoding="utf-8")
    writer = AtomicFileWriter()
    rail, adapter, _ = make_write_components(tmp_path, writer=writer)
    contexts = [
        tool_context("write_file", {"file_path": "report.md", "content": "winner-a"}, call_id="call-a"),
        tool_context("write_file", {"file_path": "report.md", "content": "winner-b"}, call_id="call-b"),
    ]
    ready = asyncio.Event()
    release = asyncio.Event()
    counter = 0
    counter_lock = asyncio.Lock()

    async def run(ctx):
        nonlocal counter
        await rail.before_tool_call(ctx)
        async with counter_lock:
            counter += 1
            if counter == 2:
                ready.set()
        await release.wait()
        try:
            return await adapter.invoke(ctx.inputs.tool_args)
        finally:
            await rail.after_tool_call(ctx)

    tasks = [asyncio.create_task(run(ctx)) for ctx in contexts]
    await asyncio.wait_for(ready.wait(), timeout=2)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert sorted(result.success for result in results) == [False, True]
    assert sum(result.data.get("status") == "version_conflict" for result in results if not result.success) == 1
    assert target.read_text(encoding="utf-8") in {"winner-a", "winner-b"}
    assert all(str(tmp_path) not in str(result) for result in results)


@pytest.mark.asyncio
async def test_competing_creates_through_persistent_adapters_do_not_overwrite(tmp_path):
    writer = AtomicFileWriter()
    rail, adapter, _ = make_write_components(tmp_path, writer=writer)
    contexts = [
        tool_context("write_file", {"file_path": "new.md", "content": "create-a"}, call_id="call-a"),
        tool_context("write_file", {"file_path": "new.md", "content": "create-b"}, call_id="call-b"),
    ]
    ready = asyncio.Event()
    release = asyncio.Event()
    counter = 0
    counter_lock = asyncio.Lock()

    async def run(ctx):
        nonlocal counter
        await rail.before_tool_call(ctx)
        async with counter_lock:
            counter += 1
            if counter == 2:
                ready.set()
        await release.wait()
        try:
            return await adapter.invoke(ctx.inputs.tool_args)
        finally:
            await rail.after_tool_call(ctx)

    tasks = [asyncio.create_task(run(ctx)) for ctx in contexts]
    await asyncio.wait_for(ready.wait(), timeout=2)
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    target = tmp_path / "new.md"
    assert sorted(result.success for result in results) == [False, True]
    assert any(result.data.get("status") == "version_conflict" for result in results if not result.success)
    assert target.read_text(encoding="utf-8") in {"create-a", "create-b"}


async def _intent_rail_for(execution, commit):
    binding = ConstructionBinding(execution=execution, ports=SimpleNamespace(commit=commit))
    binding.control.emitter = EventEmitter(execution, commit, binding.control)
    return PersistentToolIntentRail(binding), binding


@pytest.mark.asyncio
async def test_intent_is_recorded_before_write_and_summary_is_logical(tmp_path):
    writer = AtomicFileWriter()
    execution = execution_for()
    commit = RecordingCommit()
    intent_rail, _ = await _intent_rail_for(execution, commit)
    write_rail, adapter, base = make_write_components(tmp_path, writer=writer, execution=execution)
    ctx = tool_context("write_file", {"file_path": "draft.md", "content": "private body"})

    await write_rail.before_tool_call(ctx)
    await intent_rail.before_tool_call(ctx)
    assert base.calls == []
    assert commit.events[0].event_type == EventType.TOOL_INTENT
    intent = commit.events[0].payload
    assert intent.target_ref == "workspace://agent-a/draft.md"
    assert "private body" not in intent.args_summary
    assert str(tmp_path) not in intent.args_summary

    result = await adapter.invoke(ctx.inputs.tool_args)
    ctx.inputs.tool_result = result
    await intent_rail.after_tool_call(ctx)
    await write_rail.after_tool_call(ctx)

    assert result.success is True
    assert [event.event_type for event in commit.events] == [EventType.TOOL_INTENT, EventType.TOOL_COMPLETED]
    assert commit.events[-1].payload.status == "succeeded"


@pytest.mark.asyncio
async def test_intent_persistence_failure_leaves_file_unchanged(tmp_path):
    writer = AtomicFileWriter()
    execution = execution_for()
    commit = RecordingCommit(fail_intent=True)
    intent_rail, _ = await _intent_rail_for(execution, commit)
    write_rail, adapter, base = make_write_components(tmp_path, writer=writer, execution=execution)
    ctx = tool_context("write_file", {"file_path": "draft.md", "content": "must not write"})

    await write_rail.before_tool_call(ctx)
    with pytest.raises(BaseError):
        await intent_rail.before_tool_call(ctx)
    await write_rail.after_tool_call(ctx)

    assert base.calls == []
    assert not (tmp_path / "draft.md").exists()


@pytest.mark.asyncio
async def test_dispatched_exception_is_completed_as_unknown_without_retry(tmp_path):
    writer = RaisingWriter()
    execution = execution_for()
    commit = RecordingCommit()
    intent_rail, _ = await _intent_rail_for(execution, commit)
    write_rail, adapter, base = make_write_components(tmp_path, writer=writer, execution=execution)
    ctx = tool_context("write_file", {"file_path": "draft.md", "content": "uncertain"})

    await write_rail.before_tool_call(ctx)
    await intent_rail.before_tool_call(ctx)
    with pytest.raises(ConnectionError) as exc_info:
        await adapter.invoke(ctx.inputs.tool_args)
    ctx.exception = exc_info.value
    await intent_rail.after_tool_call(ctx)
    await write_rail.after_tool_call(ctx)

    completed = commit.events[-1].payload
    assert completed.status == "unknown"
    assert completed.operation_id is not None
    assert base.calls
    assert not (tmp_path / "draft.md").exists()
