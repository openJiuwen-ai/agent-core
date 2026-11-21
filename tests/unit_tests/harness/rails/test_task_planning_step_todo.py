# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""todo.json snapshot per step and rollback before a step resume."""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.checkpointer.step_storage import StepResume
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.core.sys_operation.local.fs_operation import FsOperation
from openjiuwen.harness.rails.task_planning_rail import STEP_TODO_SNAPSHOT_KEY, TaskPlanningRail
from openjiuwen.harness.tools.todo import TodoListTool


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    await ReadWriteLockManager.stop()
    monkeypatch.setattr(ReadWriteLockManager, "_lock_dir", tmp_path / "locks")
    fs = FsOperation("fs", OperationMode.LOCAL, "test", LocalWorkConfig())
    operation = SimpleNamespace(fs=lambda: fs)
    tool = TodoListTool(operation, str(tmp_path))
    rail = TaskPlanningRail()
    rail.tools = [tool]
    session = Session("sess", card=AgentCard(id="todo-step", name="t"))
    ctx = SimpleNamespace(session=session, extra={}, inputs=None)
    yield rail, session, ctx, tmp_path / "sess" / "todo.json"
    await ReadWriteLockManager.stop()


def _todo_file(todo_path):
    todo_path.parent.mkdir(parents=True, exist_ok=True)
    return todo_path


@pytest.mark.asyncio
async def test_after_react_iteration_snapshots_todo_file(env):
    rail, session, ctx, todo_path = env
    _todo_file(todo_path).write_text('{"todos": [1]}', encoding="utf-8")

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": True, "content": '{"todos": [1]}'}


@pytest.mark.asyncio
async def test_after_react_iteration_records_missing_file(env):
    rail, session, ctx, _ = env

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": False, "content": ""}


@pytest.mark.asyncio
async def test_rollback_rewrites_ahead_file_on_step_resume(env):
    rail, session, ctx, todo_path = env
    session.update_state({STEP_TODO_SNAPSHOT_KEY: {"exists": True, "content": "OLD"}})
    _todo_file(todo_path).write_text("AHEAD", encoding="utf-8")
    session.peek_agent_step_resume = lambda: StepResume(iteration=2, query="q")

    await rail.before_invoke(ctx)

    assert todo_path.read_text(encoding="utf-8") == "OLD"


@pytest.mark.asyncio
async def test_rollback_restores_missing_file_on_step_resume(env):
    rail, session, ctx, todo_path = env
    session.update_state({STEP_TODO_SNAPSHOT_KEY: {"exists": True, "content": "OLD"}})
    session.peek_agent_step_resume = lambda: StepResume(iteration=2, query="q")

    await rail.before_invoke(ctx)

    assert todo_path.read_text(encoding="utf-8") == "OLD"


@pytest.mark.asyncio
async def test_no_rollback_without_pending_resume(env):
    rail, session, ctx, todo_path = env
    session.update_state({STEP_TODO_SNAPSHOT_KEY: {"exists": True, "content": "OLD"}})
    _todo_file(todo_path).write_text("AHEAD", encoding="utf-8")
    session.peek_agent_step_resume = lambda: None

    await rail.before_invoke(ctx)

    assert todo_path.read_text(encoding="utf-8") == "AHEAD"


@pytest.mark.asyncio
async def test_rollback_skips_when_snapshot_had_no_file(env):
    rail, session, ctx, todo_path = env
    session.update_state({STEP_TODO_SNAPSHOT_KEY: {"exists": False, "content": ""}})
    _todo_file(todo_path).write_text("AHEAD", encoding="utf-8")
    session.peek_agent_step_resume = lambda: StepResume(iteration=2, query="q")

    await rail.before_invoke(ctx)

    assert todo_path.read_text(encoding="utf-8") == "AHEAD"


class _ErrorResult:
    def __init__(self, code=199003, message="no such file", data=None):
        self.code = code
        self.message = message
        self.data = data


class _SandboxLikeFs:
    """Mimics sandbox providers: read options are ignored, a missing file errors.

    ``aio.py``/``yuanrong.py`` drop ``options`` entirely, so ``snapshot`` and
    ``missing_ok`` never reach them and a missing todo.json comes back as an
    error instead of ``exists=False``.
    """

    def __init__(self, real_fs):
        self._real = real_fs

    async def read_file(self, path, *, mode="text", **kwargs):
        import os

        if not os.path.exists(path):
            return _ErrorResult()
        return await self._real.read_file(path, mode=mode)

    async def list_files(self, path, **kwargs):
        return await self._real.list_files(path, **kwargs)


@pytest.mark.asyncio
async def test_after_react_iteration_records_missing_file_when_backend_ignores_options(env):
    rail, session, ctx, todo_path = env
    todo_path.parent.mkdir(parents=True)
    tool = rail.tools[0]
    tool.fs = _SandboxLikeFs(tool.fs)

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": False, "content": ""}


@pytest.mark.asyncio
async def test_after_react_iteration_reads_existing_file_when_backend_ignores_options(env):
    rail, session, ctx, todo_path = env
    _todo_file(todo_path).write_text('{"todos": [2]}', encoding="utf-8")
    tool = rail.tools[0]
    tool.fs = _SandboxLikeFs(tool.fs)

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": True, "content": '{"todos": [2]}'}


class _DataWithoutExists:
    def __init__(self, content):
        self.content = content


class _MetadataLessFs:
    """Successful read with no snapshot metadata, even for a missing file."""

    def __init__(self, real_fs, content):
        self._real = real_fs
        self._content = content

    async def read_file(self, path, *, mode="text", **kwargs):
        return SimpleNamespace(code=0, message="ok", data=_DataWithoutExists(self._content))

    async def list_files(self, path, **kwargs):
        return await self._real.list_files(path, **kwargs)


@pytest.mark.asyncio
async def test_empty_content_without_metadata_does_not_claim_file_exists(env):
    """A backend reporting "" for a missing file must not be snapshotted as existing."""
    rail, session, ctx, todo_path = env
    todo_path.parent.mkdir(parents=True)
    tool = rail.tools[0]
    tool.fs = _MetadataLessFs(tool.fs, "")

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": False, "content": ""}


@pytest.mark.asyncio
async def test_content_without_metadata_uses_probe_for_existence(env):
    rail, session, ctx, todo_path = env
    _todo_file(todo_path).write_text("REAL", encoding="utf-8")
    tool = rail.tools[0]
    tool.fs = _MetadataLessFs(tool.fs, "REAL")

    await rail.after_react_iteration(ctx)

    assert session.get_state(STEP_TODO_SNAPSHOT_KEY) == {"exists": True, "content": "REAL"}
