"""Todo consistency with actual local files (no model or live service)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.errors import FrameworkError
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.core.sys_operation.local.fs_operation import FsOperation
from openjiuwen.harness.schema.task import TodoStatus
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoListTool, TodoModifyTool
from openjiuwen.harness.tools.todo_store import _BINDING, bind_todo_batch, prepare_todo_batch


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    await ReadWriteLockManager.stop()
    monkeypatch.setattr(ReadWriteLockManager, "_lock_dir", tmp_path / "locks")
    fs = FsOperation("fs", OperationMode.LOCAL, "test", LocalWorkConfig())
    operation = SimpleNamespace(fs=lambda: fs)
    session = Session("session", card=AgentCard(id="todo-batch-test", name="test"))
    token = _BINDING.set(None)
    engines = [cls(operation, str(tmp_path)) for cls in (TodoCreateTool, TodoModifyTool, TodoListTool)]
    fresh = True

    async def lifecycle(_session_id, snapshot, *, committing=False):
        nonlocal fresh
        if not snapshot.exists and not fresh:
            raise ValueError("Todo file lost")
        if committing:
            fresh = False

    # This fixture explicitly creates a new object; production hosts persist
    # this permission in their existing session metadata before publication.
    for engine in engines:
        engine.snapshot_guard = lifecycle
    yield (*engines, session, tmp_path / "session/todo.json")
    _BINDING.reset(token)
    await ReadWriteLockManager.stop()


async def create(tool, session, **kwargs):
    return await tool.invoke(
        {"tasks": [{"id": "A", "content": "first"}, {"id": "B", "content": "second"}], **kwargs}, session=session
    )


async def update(tool, session, ident, **fields):
    return await tool.invoke({"action": "update", "todos": [{"id": ident, **fields}]}, session=session)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "modify"])
async def test_relative_workspace_provider_reuses_transaction(env, monkeypatch, action):
    from openjiuwen.harness.tools.todo_store import current_transaction, transact

    c, m, _, session, path = env
    if action == "modify":
        await create(c, session)
    monkeypatch.chdir(path.parent.parent)
    provider = SimpleNamespace(
        endpoint=SimpleNamespace(base_url="test://sandbox", sandbox_id="box", isolation_key="session"),
        read_file=c.fs.read_file,
        write_file=c.fs.write_file,
    )
    tool = c if action == "create" else m
    tool.workspace = "."
    tool.fs = provider

    async def invoke_nested():
        assert current_transaction(tool, "session") is not None
        if action == "create":
            return await create(tool, session)
        return await update(tool, session, "A", status="completed")

    result = await asyncio.wait_for(transact(tool, "session", invoke_nested), timeout=2)
    assert result.todo_snapshot == json.loads(path.read_text())
    assert result.todo_snapshot["revision"] == (1 if action == "create" else 2)
    if action == "modify":
        assert result.todo_snapshot["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_parallel_updates_and_separate_instances(env):
    c, m, _, session, path = env
    first = await create(c, session)
    assert "todo_snapshot" not in json.dumps(first)
    assert set(first) == {"message"}
    other = TodoModifyTool(SimpleNamespace(fs=lambda: m.fs), m.workspace)
    results = await asyncio.gather(
        update(m, session, "A", status="completed"), update(other, session, "B", content="changed")
    )
    saved = json.loads(path.read_text())
    assert saved["revision"] == first.todo_snapshot["revision"] + 2
    assert saved["todos"][0]["status"] == "completed"
    assert saved["todos"][1]["content"] == "changed"
    assert len({r.todo_snapshot["revision"] for r in results}) == 2


@pytest.mark.asyncio
async def test_deleted_file_cannot_be_recreated_by_new_or_rebuild(env):
    c, _, _, session, path = env
    await create(c, session)
    path.unlink()
    c.reset_invoke_marker("session")
    with pytest.raises(FrameworkError, match="file lost"):
        await create(c, session, plan_action="rebuild")
    assert not path.exists()


@pytest.mark.asyncio
async def test_create_boundary_and_batch_plan(env):
    c, m, _, session, path = env
    await create(c, session)
    c.reset_invoke_marker("session")
    with pytest.raises(FrameworkError, match="active plan"):
        await create(c, session)
    ctx = SimpleNamespace(extra={}, session=session)
    prepare_todo_batch(ctx)
    bind_todo_batch(ctx)
    await update(m, session, "A", status="completed")
    previous = json.loads(path.read_text())
    await create(c, session, plan_action="rebuild")
    with pytest.raises(FrameworkError, match="older plan"):
        await update(m, session, "B", status="completed")
    current = json.loads(path.read_text())
    assert current["plan_id"] != previous["plan_id"]
    assert current["revision"] == previous["revision"] + 1
    prepare_todo_batch(ctx)
    bind_todo_batch(ctx)
    await update(m, session, "A", status="completed")


@pytest.mark.asyncio
async def test_legacy_read_migration_empty_and_corruption(env):
    _, m, reader, session, path = env
    path.parent.mkdir()
    raw = '[{"id":"A","content":"old","extension":{"keep":true}}]'
    path.write_text(raw)
    listed = await reader.invoke({}, session=session)
    assert path.read_text() == raw and listed.todo_snapshot["revision"] == 0
    changed = await update(m, session, "A", status="completed")
    assert changed.todo_snapshot["todos"][0]["extension"] == {"keep": True}
    await m.invoke({"action": "delete", "ids": ["A"]}, session=session)
    assert json.loads(path.read_text())["todos"] == []
    path.write_text("{broken")
    with pytest.raises(FrameworkError):
        await update(m, session, "A", content="bad")
    assert path.read_text() == "{broken"


@pytest.mark.asyncio
async def test_delayed_empty_capture_reuses_confirmed_no_plan(env, monkeypatch):
    from openjiuwen.harness.tools.todo_store import capture_plan

    c, m, _, session, _ = env
    ctx = SimpleNamespace(extra={}, session=session)
    prepare_todo_batch(ctx)
    bind_todo_batch(ctx, m)
    read_started, release = asyncio.Event(), asyncio.Event()
    original = m.fs.read_file

    async def delayed(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        if not read_started.is_set():
            read_started.set()
            await release.wait()
        return snapshot

    monkeypatch.setattr(m.fs, "read_file", delayed)
    captured = asyncio.create_task(capture_plan(m, "session"))
    await read_started.wait()
    await create(c, session)
    release.set()
    # The batch confirmed no plan before creation. This old work cannot bind
    # the newly created plan, nor revalidate a superseded missing-file read.
    assert await captured is None


@pytest.mark.asyncio
async def test_noop_and_deleted_id_do_not_publish(env):
    c, m, _, session, path = env
    await create(c, session)
    previous = path.read_bytes()
    await update(m, session, "A", status="in_progress")
    assert path.read_bytes() == previous
    result = await update(m, session, "missing", content="bad")
    assert "failed" in result["message"] and path.read_bytes() == previous


@pytest.mark.asyncio
async def test_owner_expiring_during_snapshot_read_cannot_commit(env, monkeypatch):
    from openjiuwen.harness.tools.todo_store import transact

    c, m, _, session, path = env
    await create(c, session)
    before = path.read_bytes()
    active = True
    original = m.fs.read_file

    async def expire(*args, **kwargs):
        nonlocal active
        result = await original(*args, **kwargs)
        active = False
        return result

    def check_active():
        if not active:
            raise ValueError("request expired")

    monkeypatch.setattr(m.fs, "read_file", expire)
    with pytest.raises(ValueError, match="request expired"):
        await transact(m, "session", lambda: update(m, session, "A", status="completed"),
                       check_active=check_active)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_conflict_recomputes_and_unknown_not_replayed(env, monkeypatch):
    c, m, _, session, path = env
    await create(c, session)
    write = m.fs.write_file
    calls = []

    async def interfere(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            saved = json.loads(path.read_text())
            saved["todos"][1]["content"] = "other writer"
            saved["revision"] += 1
            path.write_text(json.dumps(saved))
        return await write(*args, **kwargs)

    monkeypatch.setattr(m.fs, "write_file", interfere)
    await update(m, session, "A", status="completed")
    assert len(calls) == 2 and json.loads(path.read_text())["todos"][1]["content"] == "other writer"

    async def unknown(*args, **kwargs):
        calls.append(1)
        await write(*args, **kwargs)
        raise ConnectionError("response lost")

    monkeypatch.setattr(m.fs, "write_file", unknown)
    with pytest.raises(FrameworkError, match="response lost"):
        await update(m, session, "B", status="completed")
    assert len(calls) == 3
    assert json.loads(path.read_text())["todos"][1]["status"] == "completed"


@pytest.mark.asyncio
async def test_conflict_revalidates_guard_and_reapplies_callback(env, monkeypatch):
    c, m, _, session, path = env
    await create(c, session)
    write = m.fs.write_file
    writes, registered, registrations = [], False, []
    candidates, guards = [], []

    async def guard(_session_id, snapshot, *, committing=False):
        nonlocal registered
        guards.append((snapshot.revision, committing))
        if committing and not registered:
            registered = True
            registrations.append(snapshot.plan_id)

    async def interfere(*args, **kwargs):
        writes.append(kwargs["options"]["expected_content_sha256"])
        if len(writes) == 1:
            saved = json.loads(path.read_text())
            saved["todos"][1]["content"] = "other writer"
            saved["revision"] += 1
            path.write_text(json.dumps(saved))
        return await write(*args, **kwargs)

    async def change(items):
        candidates.append(items[1].content)
        items[0].status = TodoStatus.COMPLETED

    m.snapshot_guard = guard
    monkeypatch.setattr(m.fs, "write_file", interfere)
    await m.update_todos("session", change)
    assert candidates == ["second", "other writer"]
    assert guards == [(1, False), (1, True), (2, False), (2, True)]
    assert len(registrations) == 1
    assert len(writes) == 2 and writes[0] != writes[1]
    saved = json.loads(path.read_text())
    assert saved["revision"] == 3
    assert saved["todos"][0]["status"] == "completed"
    assert saved["todos"][1]["content"] == "other writer"


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_at", ["retry_guard", "after_guard"])
async def test_retry_does_not_skip_owner_validation(env, monkeypatch, expire_at):
    c, m, _, session, path = env
    await create(c, session)
    original = path.read_bytes()
    guard_calls, write_calls, active = 0, 0, True

    async def guard(_session_id, _snapshot, *, committing=False):
        nonlocal guard_calls, active
        if committing:
            guard_calls += 1
            if expire_at == "retry_guard" and guard_calls == 2:
                raise ValueError("owner expired during retry")
            if expire_at == "after_guard":
                await asyncio.sleep(0)
                active = False

    async def conflict(*args, **kwargs):
        nonlocal write_calls
        write_calls += 1
        return SimpleNamespace(code=1, data=SimpleNamespace(conflict=True))

    def check_active():
        if not active:
            raise ValueError("owner expired during guard")

    async def change(items):
        items[0].status = TodoStatus.COMPLETED

    m.snapshot_guard = guard
    monkeypatch.setattr(m.fs, "write_file", conflict)
    with pytest.raises(ValueError, match="owner expired"):
        await m.update_todos("session", change, check_active=check_active)
    assert guard_calls == (2 if expire_at == "retry_guard" else 1)
    assert write_calls == (1 if expire_at == "retry_guard" else 0)
    assert path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["aio", "yuanrong"])
@pytest.mark.parametrize("tool_name", ["TodoCreateTool", "TodoListTool", "TodoGetTool", "TodoModifyTool"])
async def test_unsupported_provider_todo_reports_contract_before_write(env, monkeypatch, provider_name, tool_name):
    from unittest.mock import AsyncMock, Mock

    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
    from openjiuwen.extensions.sys_operation.sandbox.providers.aio import AIOFSProvider
    from openjiuwen.extensions.sys_operation.sandbox.providers.yuanrong import YuanrongFSProvider
    from openjiuwen.harness.tools import todo

    _, _, _, session, path = env
    endpoint = SandboxEndpoint(base_url="test://sandbox", sandbox_id="box", isolation_key="session")
    if provider_name == "aio":
        provider = AIOFSProvider(endpoint)
        client = SimpleNamespace(file=SimpleNamespace(read_file=Mock(
            return_value=SimpleNamespace(data=SimpleNamespace(content="[]")))))
        monkeypatch.setattr(provider, "_get_client", lambda: client)
    else:
        provider = YuanrongFSProvider(endpoint)
        monkeypatch.setattr(provider, "_fs_read_sync", lambda *args, **kwargs: "[]")
    write = AsyncMock()
    monkeypatch.setattr(provider, "write_file", write)
    tool = getattr(todo, tool_name)(SimpleNamespace(fs=lambda: provider), str(path.parent.parent))
    with pytest.raises(FrameworkError, match="does not support snapshot reads.*exists and content_sha256"):
        await tool.invoke({}, session=session)
    write.assert_not_awaited()
    assert not path.exists()


@pytest.mark.parametrize("revision", [True, False, 1.0, "1", -1, 2**53])
def test_snapshot_revision_rejects_non_safe_integer(revision):
    from openjiuwen.harness.tools.todo_store import decode_snapshot

    with pytest.raises(ValueError, match="Invalid Todo plan or revision"):
        decode_snapshot({"plan_id": "plan", "revision": revision, "todos": []})


def test_gateway_coordination_identity_resolves_session_without_launch(monkeypatch):
    from openjiuwen.core.sys_operation.sandbox import sandbox_mixin
    from openjiuwen.harness.tools.todo_store import object_key

    fs = sandbox_mixin.SandboxGatewayClientMixin()
    fs.mode = "sandbox"
    launcher = SimpleNamespace(gateway_url="test://gateway", base_url="test://box", sandbox_type="jiuwenbox")
    fs._init_client_context(SimpleNamespace(
        config=SimpleNamespace(launcher_config=launcher), isolation_key_template="todo-{session_id}"), "fs")
    monkeypatch.setattr(sandbox_mixin, "get_session_id", lambda: "first")
    first = object_key(fs, "/todo/session/todo.json")
    assert first[1][-1] == "todo-first"
    assert object_key(fs, "/todo/session/./todo.json") == first
    monkeypatch.setattr(sandbox_mixin, "get_session_id", lambda: "second")
    assert object_key(fs, "/todo/session/todo.json") != first
    assert not hasattr(fs, "_gateway_client")


@pytest.mark.asyncio
async def test_actual_ability_manager_keeps_batch_plan_without_extra_storage(env):
    from openjiuwen.core.foundation.llm import ToolCall
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent
    from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

    c, m, _, session, path = env
    await create(c, session)
    c.reset_invoke_marker("session")
    am = AbilityManager(owner_id="todo-batch-test")
    callbacks = AgentCallbackManager("todo-batch-test")
    rail = TaskPlanningRail()
    agent = SimpleNamespace(agent_callback_manager=callbacks)
    ctx = AgentCallbackContext(agent=agent, session=session)
    await Runner.start()
    try:
        await callbacks.register_callback(AgentCallbackEvent.BEFORE_TOOL_CALL, rail.before_tool_call)
        am.add_ability(c.card, c)
        am.add_ability(m.card, m)
        await rail.after_model_call(ctx)
        result = await am.execute(
            ctx,
            [
                ToolCall(
                    id="create",
                    type="function",
                    name="todo_create",
                    arguments=json.dumps({"tasks": [{"id": "A", "content": "new"}], "plan_action": "rebuild"}),
                ),
                ToolCall(
                    id="modify",
                    type="function",
                    name="todo_modify",
                    arguments=json.dumps({"action": "update", "todos": [{"id": "A", "status": "completed"}]}),
                ),
            ],
            session,
            parallel_tool_calls=False,
        )
        assert json.loads(path.read_text())["todos"][0]["status"] == "in_progress"
        assert "older plan" in str(result[1])
        await rail.after_model_call(ctx)
        result = await am.execute(
            ctx,
            ToolCall(
                id="next",
                type="function",
                name="todo_modify",
                arguments=json.dumps({"action": "update", "todos": [{"id": "A", "status": "completed"}]}),
            ),
            session,
        )
        assert json.loads(path.read_text())["todos"][0]["status"] == "completed"
    finally:
        am.remove_ability("todo_create")
        am.remove_ability("todo_modify")
        await callbacks.clear()
        await Runner.stop()


@pytest.mark.asyncio
async def test_revision_exhaustion_and_missing_id_do_not_commit(env):
    c, m, _, session, path = env
    await create(c, session)
    snapshot = json.loads(path.read_text())
    snapshot["revision"] = 2**53 - 1
    path.write_text(json.dumps(snapshot))
    before = path.read_bytes()
    with pytest.raises(FrameworkError, match="safe integer"):
        await update(m, session, "A", content="changed")
    assert path.read_bytes() == before
    with pytest.raises(FrameworkError, match="Missing required field"):
        await m.invoke({"action": "append", "todos": [{"content": "invalid", "status": "pending"}]}, session=session)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_independent_work_tool_does_not_wait_for_todo_read(env, monkeypatch):
    from openjiuwen.core.foundation.llm import ToolCall
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentCallbackEvent
    from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

    c, m, _, session, path = env
    await create(c, session)
    waiting, work_done = asyncio.Event(), asyncio.Event()
    original = m.fs.read_file
    reads = []

    async def delayed(*args, **kwargs):
        reads.append(1)
        waiting.set()
        await asyncio.wait_for(work_done.wait(), 2)
        return await original(*args, **kwargs)

    async def work():
        await asyncio.wait_for(waiting.wait(), 2)
        work_done.set()
        return "independent work"

    monkeypatch.setattr(m.fs, "read_file", delayed)
    callbacks = AgentCallbackManager("todo-independent-test")
    manager = AbilityManager(owner_id="todo-independent-test")
    rail = TaskPlanningRail()
    ctx = AgentCallbackContext(agent=SimpleNamespace(agent_callback_manager=callbacks, load_state=lambda _session: SimpleNamespace(task_plan=None)), session=session)
    standalone = LocalFunction(ToolCard(id="work", name="work", description="test"), work)
    await Runner.start()
    try:
        manager.add_ability(m.card, m)
        manager.add_ability(standalone.card, standalone)
        await callbacks.register_callback(AgentCallbackEvent.BEFORE_TOOL_CALL, rail.before_tool_call)
        rail.tools = [m]
        await callbacks.register_callback(AgentCallbackEvent.AFTER_TOOL_CALL, rail.after_tool_call)
        await rail.after_model_call(ctx)
        results = await manager.execute(
            ctx,
            [
                ToolCall(
                    id="modify",
                    type="function",
                    name="todo_modify",
                    arguments=json.dumps({"action": "update", "todos": [{"id": "A", "status": "completed"}]}),
                ),
                ToolCall(id="work", type="function", name="work", arguments="{}"),
            ],
            session,
            parallel_tool_calls=True,
        )
        assert work_done.is_set() and len(reads) == 1
        assert "independent work" in str(results[1])
        assert "plan_id" not in str(results[0][1].content)
        assert results[0][0].todo_snapshot["revision"] == 2
        assert json.loads(path.read_text())["todos"][0]["status"] == "completed"
    finally:
        work_done.set()
        manager.remove_ability("work")
        manager.remove_ability("todo_modify")
        await callbacks.clear()
        await Runner.stop()


@pytest.mark.asyncio
async def test_delayed_plan_capture_cannot_rebind_an_already_resolved_batch(env, monkeypatch):
    from openjiuwen.harness.tools.todo_store import capture_plan
    c, m, _, session, path = env
    created = await create(c, session)
    original_plan = created.todo_snapshot['plan_id']
    c.reset_invoke_marker('session')
    ctx = SimpleNamespace(extra={}, session=session)
    prepare_todo_batch(ctx)
    bind_todo_batch(ctx, m)
    started, release = asyncio.Event(), asyncio.Event()
    read = m.fs.read_file
    count = 0
    async def delayed(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            started.set()
            await release.wait()
        return await read(*args, **kwargs)
    monkeypatch.setattr(m.fs, 'read_file', delayed)
    captured = asyncio.create_task(capture_plan(m, 'session'))
    try:
        await started.wait()
        await update(m, session, 'A', status='completed')
        await create(c, session, plan_action='new')
        release.set()
        assert await captured == original_plan
        assert json.loads(path.read_text())['plan_id'] != original_plan
        with pytest.raises(FrameworkError, match='older plan'):
            await update(m, session, 'A', status='completed')
    finally:
        release.set()
        await asyncio.gather(captured, return_exceptions=True)
