# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real-file regressions for Todo commits; no model, Runner or service required."""

import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.sys_operation import LocalWorkConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.local import _atomic_file
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.harness.schema.task import TodoItem, TodoStatus
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoModifyTool
from openjiuwen.harness.tools.todo_store import TODO_CONTEXT_KEY, TODO_EXECUTION_KEY, TodoStore, decode_todo_snapshot


@pytest_asyncio.fixture
async def operation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_TODO_V2", "1")
    monkeypatch.setattr(ReadWriteLockManager, "_lock_dir", tmp_path / "locks")
    op = SysOperation(
        SysOperationCard(
            id="todo-consistency-test",
            work_config=LocalWorkConfig(sandbox_root=[str(tmp_path)], restrict_to_sandbox=True),
        )
    )
    yield op
    await ReadWriteLockManager.close_locks()


def session(sid="session", binding=None):
    return SimpleNamespace(get_session_id=lambda: sid, get_state=lambda _key: binding)


async def seed(tool, sid="session"):
    await tool.save_todos(
        sid, [TodoItem(id="A", content="A", status=TodoStatus.IN_PROGRESS), TodoItem(id="B", content="B")]
    )


def update(task, status="completed"):
    return {"action": "update", "todos": [{"id": task, "status": status}]}


@pytest.mark.asyncio
async def test_parallel_updates_from_distinct_tool_instances_survive(operation, tmp_path):
    first = TodoModifyTool(operation, str(tmp_path))
    second = TodoModifyTool(operation, str(tmp_path))
    await seed(first)
    outputs = await asyncio.gather(
        first.invoke(update("A"), session=session()), second.invoke(update("B"), session=session())
    )
    assert all("Successfully updated 1" in out["message"] for out in outputs)
    assert {item.id: item.status for item in await first.load_todos("session")} == {
        "A": TodoStatus.COMPLETED,
        "B": TodoStatus.COMPLETED,
    }
    versions = [int(out.todo_receipt.snapshot.metadata["revision"]) for out in outputs]
    assert len(set(versions)) == 2
    assert "todo_receipt" not in json.dumps(outputs)


@pytest.mark.asyncio
async def test_append_and_complete_preserve_other_fields(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    await asyncio.gather(
        tool.invoke(update("A"), session=session()),
        tool.invoke(
            {"action": "append", "todos": [{"id": "C", "content": "C", "status": "pending"}]}, session=session()
        ),
    )
    items = await tool.load_todos("session")
    assert [item.id for item in items] == ["A", "B", "C"]
    assert items[0].status == TodoStatus.COMPLETED
    assert items[1].to_dict()["content"] == "B"


@pytest.mark.asyncio
async def test_batch_partial_success_and_single_in_progress(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    result = await tool.invoke(
        {
            "action": "update",
            "todos": [
                {"id": "A", "status": "completed"},
                {"id": "B", "status": "in_progress"},
                {"id": "missing", "status": "completed"},
            ],
        },
        session=session(),
    )
    assert "Updated 2 task(s). 1 item(s) failed" in result["message"]
    assert [item.status for item in await tool.load_todos("session")] == [TodoStatus.COMPLETED, TodoStatus.IN_PROGRESS]


@pytest.mark.asyncio
async def test_deleted_item_is_not_recreated_and_empty_has_revision(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    await tool.invoke({"action": "delete", "ids": ["A", "B"]}, session=session())
    result = await tool.invoke(update("A"), session=session())
    assert "1 item(s) failed" in result["message"]
    snapshot = await tool.todo_store("session").read()
    assert snapshot.todos == []
    assert int(snapshot.metadata["revision"]) >= 2


@pytest.mark.asyncio
async def test_concurrent_create_is_once_and_explicit_new_plan_is_required(operation, tmp_path):
    creator = TodoCreateTool(operation, str(tmp_path))
    outputs = await asyncio.gather(
        *[creator.invoke({"tasks": [{"id": name, "content": name}]}, session=session()) for name in ("A", "B")],
        return_exceptions=True,
    )
    assert sum(isinstance(out, dict) for out in outputs) == 1
    before = await creator.todo_store("session").read()
    creator.reset_invoke_marker("session")
    with pytest.raises(Exception, match="active plan"):
        await creator.invoke({"tasks": [{"id": "C", "content": "C"}]}, session=session())
    await creator.invoke({"tasks": [{"id": "C", "content": "C"}], "plan_action": "new"}, session=session())
    after = await creator.todo_store("session").read()
    assert after.metadata["plan_id"] != before.metadata["plan_id"]
    from openjiuwen.harness.tools.todo_store import bind_todo_context

    bind_todo_context(creator.todo_store("session").key, before.binding())
    with pytest.raises(Exception, match="superseded"):
        await TodoModifyTool(operation, str(tmp_path)).invoke(update("C"), session=session(binding=before.binding()))


@pytest.mark.asyncio
async def test_stale_load_save_cannot_overwrite_a_commit(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    old = await tool.load_todos("session")
    await tool.invoke(update("A"), session=session())
    old[1].status = TodoStatus.COMPLETED
    with pytest.raises(Exception, match="snapshot changed"):
        await tool.save_todos("session", old)
    assert (await tool.load_todos("session"))[0].status == TodoStatus.COMPLETED


@pytest.mark.asyncio
async def test_legacy_migration_preserves_unknown_fields(operation, tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "todo.json").write_text(
        json.dumps(
            [
                {"id": "A", "content": "A", "status": "pending", "custom": {"keep": 1}},
                {"content": "legacy without id", "status": "pending"},
            ]
        )
    )
    tool = TodoModifyTool(operation, str(tmp_path))
    await tool.invoke(update("A"), session=session())
    raw = json.loads((directory / "todo.json").read_text())
    assert raw["schema_version"] == 2
    assert raw["todos"][0]["custom"] == {"keep": 1}
    first = await tool.load_todos("session")
    second = await tool.load_todos("session")
    assert first[1].id == second[1].id


@pytest.mark.asyncio
async def test_legacy_gate_off_still_serializes_updates(operation, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENJIUWEN_TODO_V2")
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    await asyncio.gather(*(tool.invoke(update(task), session=session()) for task in ("A", "B")))
    raw = json.loads((tmp_path / "session" / "todo.json").read_text())
    assert isinstance(raw, list)
    assert all(item["status"] == "completed" for item in raw)


@pytest.mark.asyncio
async def test_corrupt_file_is_never_overwritten(operation, tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    path = directory / "todo.json"
    path.write_text("{broken")
    with pytest.raises(Exception, match="Invalid Todo JSON"):
        await TodoCreateTool(operation, str(tmp_path)).invoke({"tasks": [{"content": "new"}]}, session=session())
    assert path.read_text() == "{broken"


@pytest.mark.asyncio
async def test_conditional_write_rejects_stale_hash_and_missing_race(operation, tmp_path):
    fs, path = operation.fs(), str(tmp_path / "state.json")
    empty = hashlib.sha256(b"").hexdigest()
    result = await fs.write_file(
        path,
        "first",
        prepend_newline=False,
        options={"atomic_replace": True, "expected_exists": False, "expected_content_sha256": empty},
    )
    assert result.code == 0 and result.data.atomic_replace
    conflict = await fs.write_file(path, "bad", options={"atomic_replace": True, "expected_content_sha256": empty})
    assert conflict.code != 0 and conflict.failure_kind == "conflict"
    conflict = await fs.write_file(path, "bad", options={"atomic_replace": True, "expected_exists": False})
    assert conflict.code != 0 and conflict.failure_kind == "conflict"
    assert (tmp_path / "state.json").read_text() == "first"


@pytest.mark.asyncio
async def test_atomic_replace_failure_preserves_old_file(operation, tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("old")

    def fail(*args, **kwargs):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(_atomic_file.os, "replace", fail)
    result = await operation.fs().write_file(str(path), "new", options={"atomic_replace": True})
    assert result.code != 0
    assert path.read_text() == "old"
    assert not list(tmp_path.glob(".state.json-*.tmp"))


@pytest.mark.asyncio
async def test_cancelled_atomic_worker_is_drained_before_unlock(operation, tmp_path, monkeypatch):
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = _atomic_file._replace_file

    def blocked(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(_atomic_file, "_replace_file", blocked)
    path = tmp_path / "state.json"
    task = asyncio.create_task(
        operation.fs().write_file(str(path), "committed", prepend_newline=False, options={"atomic_replace": True})
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.read_text() == "committed"
    assert (await operation.fs().write_file(str(path), "next", options={"atomic_replace": True})).code == 0


@pytest.mark.asyncio
async def test_unsupported_backend_fails_before_write(tmp_path):
    fs = SimpleNamespace(capabilities=AsyncMock(return_value={}), write_file=AsyncMock())
    store = TodoStore(fs, str(tmp_path / "todo.json"))
    with pytest.raises(Exception, match="requires backend"):
        await store.transact(AsyncMock(return_value={}))
    fs.write_file.assert_not_called()


def test_invalid_revision_and_unknown_format_are_rejected():
    with pytest.raises(Exception, match="Expected a Todo array"):
        decode_todo_snapshot({"todos": []})
    with pytest.raises(Exception, match="Invalid Todo revision"):
        decode_todo_snapshot({"schema_version": 2, "todos": [], "revision": "01"})


@pytest.mark.asyncio
async def test_old_task_keeps_frozen_execution_after_shared_session_changes(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    state = {TODO_EXECUTION_KEY: "first-execution"}
    shared = SimpleNamespace(get_session_id=lambda: "session", get_state=state.get, update_state=state.update)
    await tool.prepare_context(shared, new_execution=True)
    release = asyncio.Event()

    async def late():
        await release.wait()
        return await tool.invoke(update("A"), session=shared)

    old = asyncio.create_task(late())
    state[TODO_EXECUTION_KEY] = "second-execution"
    await tool.prepare_context(shared, new_execution=True)
    release.set()
    with pytest.raises(Exception, match="superseded"):
        await old
    assert (await tool.load_todos("session"))[0].status == TodoStatus.IN_PROGRESS
    assert state[TODO_CONTEXT_KEY]["writer_epoch"] == "second-execution"


@pytest.mark.asyncio
async def test_next_model_uses_committed_plan_without_an_extra_read(operation, tmp_path, monkeypatch):
    creator = TodoCreateTool(operation, str(tmp_path))
    state = {TODO_EXECUTION_KEY: "execution"}
    shared = SimpleNamespace(get_session_id=lambda: "session", get_state=state.get, update_state=state.update)
    await creator.prepare_context(shared, new_execution=True)
    old_plan = state[TODO_CONTEXT_KEY]["plan_id"]
    result = await creator.invoke({"tasks": [{"id": "A", "content": "A"}]}, session=shared)
    assert state[TODO_CONTEXT_KEY]["plan_id"] != old_plan
    reader = AsyncMock(side_effect=AssertionError("extra model-preparation read"))
    monkeypatch.setattr(creator.todo_store("session"), "read", reader)
    captured = await creator.prepare_context(shared)
    assert captured["plan_id"] == result.todo_receipt.snapshot.metadata["plan_id"]
    reader.assert_not_called()


@pytest.mark.asyncio
async def test_disabling_v2_does_not_downgrade_existing_file(operation, tmp_path, monkeypatch):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    before = (tmp_path / "session" / "todo.json").read_bytes()
    monkeypatch.delenv("OPENJIUWEN_TODO_V2")
    assert len(await tool.load_todos("session")) == 2
    with pytest.raises(Exception, match="writes are disabled"):
        await tool.invoke(update("A"), session=session())
    assert (tmp_path / "session" / "todo.json").read_bytes() == before


@pytest.mark.asyncio
async def test_busy_object_does_not_block_other_store_or_work(operation, tmp_path):
    first, second = TodoModifyTool(operation, str(tmp_path)), TodoModifyTool(operation, str(tmp_path / "other"))
    await seed(first)
    await seed(second)
    entered, release, work_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()
        return {}

    busy = asyncio.create_task(first.todo_store("session").transact(hold))
    await entered.wait()

    async def work():
        work_started.set()

    await asyncio.wait_for(asyncio.gather(second.invoke(update("A"), session=session()), work()), 2)
    assert work_started.is_set() and not busy.done()
    release.set()
    await busy


@pytest.mark.asyncio
async def test_conflict_retry_is_bounded_and_unknown_commit_is_never_replayed(operation, tmp_path, monkeypatch):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    store = tool.todo_store("session")
    original = store.fs.write_file
    conflict = SimpleNamespace(code=1, message="changed", failure_kind="conflict")
    writer = AsyncMock(return_value=conflict)
    monkeypatch.setattr(store.fs, "write_file", writer)
    with pytest.raises(Exception, match="changed repeatedly"):
        await tool.invoke(update("A"), session=session())
    assert writer.call_count == 3

    async def unknown(*args, **kwargs):
        result = await original(*args, **kwargs)
        result.data.atomic_replace = None
        return result

    writer = AsyncMock(side_effect=unknown)
    monkeypatch.setattr(store.fs, "write_file", writer)
    with pytest.raises(Exception, match="re-read before retrying"):
        await tool.invoke(update("A"), session=session())
    assert writer.call_count == 1
    assert (await tool.load_todos("session"))[0].status == TodoStatus.COMPLETED


@pytest.mark.asyncio
async def test_two_processes_compare_and_replace_then_retry_original_intent(operation, tmp_path):
    import os
    import sys

    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    program = r"""
import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
from openjiuwen.core.sys_operation import LocalWorkConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.harness.tools.todo import TodoModifyTool
async def run():
    root, item = sys.argv[1:]
    ReadWriteLockManager._lock_dir = Path(root) / 'locks'
    op = SysOperation(SysOperationCard(id='child', work_config=LocalWorkConfig(sandbox_root=[root], restrict_to_sandbox=True)))
    tool = TodoModifyTool(op, root)
    store = tool.todo_store('session')
    old = await store.read()
    print('READY', flush=True)
    await asyncio.to_thread(input)
    raw = {**old.metadata, 'todos': old.todos}
    raw['revision'] = str(int(raw['revision']) + 1)
    next(row for row in raw['todos'] if row['id'] == item)['status'] = 'completed'
    result = await op.fs().write_file(store.path, json.dumps(raw), prepend_newline=False,
        options={'atomic_replace': True, 'expected_exists': True, 'expected_content_sha256': old.content_sha256})
    if result.code != 0:
        assert result.failure_kind == 'conflict'
        await tool.invoke({'action': 'update', 'todos': [{'id': item, 'status': 'completed'}]},
            session=SimpleNamespace(get_session_id=lambda: 'session', get_state=lambda key: None))
    print('RESULT:' + str(result.code), flush=True)
    await ReadWriteLockManager.stop()
asyncio.run(run())
"""
    children = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-c",
            program,
            str(tmp_path),
            item,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        for item in ("A", "B")
    ]
    try:
        for child in children:
            while True:
                line = await asyncio.wait_for(child.stdout.readline(), 20)
                assert line, "child failed before barrier"
                if line.strip() == b"READY":
                    break
        for child in children:
            child.stdin.write(b"go\n")
            await child.stdin.drain()
        outputs = await asyncio.wait_for(asyncio.gather(*(child.communicate() for child in children)), 30)
        assert all(child.returncode == 0 for child in children)
        assert sum(b"RESULT:0" in out[0] for out in outputs) == 1
        assert all(item.status == TodoStatus.COMPLETED for item in await tool.load_todos("session"))
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()


@pytest.mark.asyncio
async def test_batch_reads_and_commits_once(operation, tmp_path, monkeypatch):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    store = tool.todo_store("session")
    reader, writer = AsyncMock(wraps=store.read), AsyncMock(wraps=store.fs.write_file)
    monkeypatch.setattr(store, "read", reader)
    monkeypatch.setattr(store.fs, "write_file", writer)
    await tool.invoke(
        {"action": "update", "todos": [{"id": "A", "status": "completed"}, {"id": "B", "status": "in_progress"}]},
        session=session(),
    )
    assert reader.call_count == writer.call_count == 1


@pytest.mark.asyncio
async def test_remote_contract_is_explicit_and_cached_without_version_rpc(operation, tmp_path):
    local = operation.fs()
    proxy = SimpleNamespace(
        capabilities=AsyncMock(return_value={"atomic_compare_replace": True}),
        read_file=local.read_file,
        write_file=local.write_file,
    )
    store = TodoStore(proxy, str(tmp_path / "remote.json"), namespace="sandbox:tenant-one")

    async def change():
        store.active().todos.append({"id": "A", "content": "A", "status": "pending"})
        return {}

    result = await store.transact(change)
    assert await store.capable() is True
    assert proxy.capabilities.call_count == 1
    assert result.todo_receipt.snapshot.todos[0]["id"] == "A"


@pytest.mark.asyncio
async def test_readers_never_observe_half_json_and_rollback_export_keeps_latest(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    path = tmp_path / "session" / "todo.json"
    observed = []

    async def reader():
        for _ in range(50):
            raw = json.loads(await asyncio.to_thread(path.read_text))
            observed.append((raw["revision"], raw["todos"]))
            await asyncio.sleep(0)

    async def writer():
        for i in range(12):
            await tool.invoke(
                {"action": "append", "todos": [{"id": f"extra-{i}", "content": "x" * 2048, "status": "pending"}]},
                session=session(),
            )

    await asyncio.gather(reader(), writer())
    assert all(len(items) == int(revision) + 1 for revision, items in observed)
    latest = await tool.todo_store("session").read()
    export = tmp_path / "rollback-export.json"
    export.write_text(json.dumps(latest.todos))
    assert decode_todo_snapshot(json.loads(export.read_text())).todos == latest.todos
    assert len(latest.todos) == 14


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False])
async def test_delete_update_commits_in_both_barrier_orders(operation, tmp_path, delete_first):
    tool, other = TodoModifyTool(operation, str(tmp_path)), TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delete = {"action": "delete", "ids": ["A"]}
    first_args, second_args = (delete, update("A")) if delete_first else (update("A"), delete)

    async def first():
        async def apply():
            entered.set()
            await release.wait()
            return await tool.invoke(first_args, session=session())

        return await tool.transact("session", apply, session=session())

    async def second():
        queued.set()
        return await other.invoke(second_args, session=session())

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(second())
    await queued.wait()
    assert not second_task.done()
    release.set()
    result1, result2 = await asyncio.gather(first_task, second_task)
    assert [item.id for item in await tool.load_todos("session")] == ["B"]
    if delete_first:
        assert "failed" in result2["message"]
        assert result2.todo_receipt.changed is False
    else:
        assert result1.todo_receipt.changed and result2.todo_receipt.changed
        assert int(result1.todo_receipt.snapshot.metadata["revision"]) < int(
            result2.todo_receipt.snapshot.metadata["revision"]
        )


@pytest.mark.asyncio
async def test_concurrent_different_fields_merge_and_same_field_uses_commit_order(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    results = await asyncio.gather(
        *(
            tool.invoke({"action": "update", "todos": [item]}, session=session())
            for item in ({"id": "A", "content": "renamed"}, {"id": "A", "status": "completed"})
        )
    )
    row = (await tool.load_todos("session"))[0]
    assert row.content == "renamed" and row.status == TodoStatus.COMPLETED
    results = await asyncio.gather(
        *(
            tool.invoke({"action": "update", "todos": [{"id": "A", "content": content}]}, session=session())
            for content in ("one", "two")
        )
    )
    newest = max(results, key=lambda result: int(result.todo_receipt.snapshot.metadata["revision"]))
    assert (await tool.load_todos("session"))[0].content == newest.todo_receipt.snapshot.todos[0]["content"]


@pytest.mark.asyncio
async def test_competing_migrations_share_one_identity_and_missing_id(operation, tmp_path):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            [
                {"id": "A", "content": "A", "status": "pending"},
                {"id": "B", "content": "B", "status": "pending"},
                {
                    "content": "legacy reminder",
                    "status": "pending",
                    "meta_data": {"persistent": True},
                    "custom": {"kept": 1},
                },
            ]
        )
    )
    tools = [TodoModifyTool(operation, str(tmp_path)) for _ in range(2)]
    results = await asyncio.gather(
        *(tool.invoke(update(item), session=session()) for tool, item in zip(tools, ("A", "B")))
    )
    snapshots = [result.todo_receipt.snapshot for result in results]
    assert len({snap.metadata["store_id"] for snap in snapshots}) == 1
    assert len({snap.metadata["plan_id"] for snap in snapshots}) == 1
    assert len({snap.todos[2]["id"] for snap in snapshots}) == 1
    assert snapshots[0].todos[2]["custom"] == snapshots[1].todos[2]["custom"] == {"kept": 1}
    assert (await tools[0].load_todos("session"))[2].meta_data["persistent"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_unlock_owner_and_next_write_progresses(operation, tmp_path):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()
        return {}

    owner = asyncio.create_task(tool.todo_store("session").transact(hold))
    await entered.wait()

    async def wait_to_update():
        queued.set()
        return await tool.invoke(update("A"), session=session())

    waiter = asyncio.create_task(wait_to_update())
    await queued.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not owner.done()
    release.set()
    await owner
    await asyncio.wait_for(tool.invoke(update("B"), session=session()), 2)
    rows = await tool.load_todos("session")
    assert rows[0].status == TodoStatus.IN_PROGRESS
    assert rows[1].status == TodoStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["write", "flush", "close"])
async def test_atomic_temp_fault_never_publishes_partial_file(operation, tmp_path, monkeypatch, fault):
    path = tmp_path / "atomic.json"
    path.write_text('["old"]')
    fdopen = _atomic_file.os.fdopen

    class FailingStream:
        def __init__(self, *args, **kwargs):
            self.inner = fdopen(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.inner.close()
            if fault == "close":
                raise OSError("injected close failure")

        def write(self, content):
            if fault == "write":
                raise OSError("injected write failure")
            return self.inner.write(content)

        def flush(self):
            if fault == "flush":
                raise OSError("injected flush failure")
            return self.inner.flush()

        def fileno(self):
            return self.inner.fileno()

    monkeypatch.setattr(_atomic_file.os, "fdopen", FailingStream)
    result = await operation.fs().write_file(
        str(path), '["new"]', prepend_newline=False, options={"atomic_replace": True}
    )
    assert result.code != 0
    assert path.read_text() == '["old"]'
    assert not list(tmp_path.glob(".atomic.json-*.tmp"))


@pytest.mark.asyncio
async def test_actual_ability_batch_starts_work_while_todo_commit_waits(operation, tmp_path, monkeypatch):
    import uuid

    from openjiuwen.core.foundation.llm import ToolCall
    from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

    manager = AbilityManager(owner_id=f"todo-batch-{uuid.uuid4().hex}")
    callbacks = AgentCallbackManager(manager._owner_id)
    tool = TodoCreateTool(operation, str(tmp_path))
    entered, release, work_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = tool.todo_store("session").read

    async def held_read(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(tool.todo_store("session"), "read", held_read)

    async def work(**kwargs):
        result = await operation.shell().execute_cmd(
            "echo todo-work-start", cwd=str(tmp_path), shell_type="sh", timeout=2
        )
        assert result.code == 0 and result.data.exit_code == 0
        work_started.set()
        return {"work": result.data.stdout.strip()}

    work_tool = LocalFunction(card=ToolCard(id="work", name="bash", description="test local work"), func=work)
    manager.add_ability(tool.card, tool)
    manager.add_ability(work_tool.card, work_tool)
    agent = SimpleNamespace(agent_callback_manager=callbacks, ability_manager=manager)
    calls = [
        ToolCall(
            id="create",
            type="function",
            name="todo_create",
            arguments=json.dumps({"tasks": [{"id": "A", "content": "A"}]}),
        ),
        ToolCall(id="work", type="function", name="bash", arguments="{}"),
    ]
    batch = asyncio.create_task(
        manager.execute(AgentCallbackContext(agent=agent), calls, session(), parallel_tool_calls=True)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(work_started.wait(), 2)
        assert not batch.done()
        release.set()
        results = await asyncio.wait_for(batch, 5)
        assert results[0][0].todo_receipt.changed
        assert results[1][0] == {"work": "todo-work-start"}
    finally:
        release.set()
        await asyncio.gather(batch, return_exceptions=True)
        manager.teardown_tools()
        await callbacks.clear()


@pytest.mark.asyncio
async def test_uninit_and_new_instance_wait_for_cancelled_atomic_worker(operation, tmp_path, monkeypatch):
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    entered, released = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = _atomic_file._replace_file

    def hold_once(*args):
        loop.call_soon_threadsafe(entered.set)
        assert released.wait(5)
        return original(*args)

    monkeypatch.setattr(_atomic_file, "_replace_file", hold_once)
    manager = AbilityManager()
    manager.add(tool.card)
    rail = TaskPlanningRail()
    rail.tools = [tool]
    old = asyncio.create_task(tool.invoke(update("A"), session=session()))
    await asyncio.wait_for(entered.wait(), 2)
    old.cancel()
    rail.uninit(SimpleNamespace(ability_manager=manager))
    tool.cleanup_session("session")
    new_tool = TodoModifyTool(operation, str(tmp_path))
    queued = asyncio.Event()

    async def following():
        queued.set()
        return await new_tool.invoke(update("B"), session=session())

    new = asyncio.create_task(following())
    await queued.wait()
    assert not new.done()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await old
    assert (await asyncio.wait_for(new, 3)).todo_receipt.changed
    assert all(row.status == TodoStatus.COMPLETED for row in await new_tool.load_todos("session"))


@pytest.mark.asyncio
async def test_alias_paths_share_coordination(operation, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    first, second = TodoModifyTool(operation, str(real)), TodoModifyTool(operation, str(alias))
    assert first.todo_store("session").key == second.todo_store("session").key
    await seed(first)
    await asyncio.gather(first.invoke(update("A"), session=session()), second.invoke(update("B"), session=session()))
    assert all(row.status == TodoStatus.COMPLETED for row in await first.load_todos("session"))


def test_coordination_does_not_reuse_an_async_lock_across_event_loops():
    from openjiuwen.harness.tools.todo_store import _object_lock

    async def take():
        lock = _object_lock("local:loop-test")
        assert lock is _object_lock("local:loop-test")
        return lock

    first, second = asyncio.run(take()), asyncio.run(take())
    assert first is not second


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["append", "insert_before", "insert_after"])
async def test_multi_item_insert_is_one_read_and_write(operation, tmp_path, monkeypatch, action):
    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    store = tool.todo_store("session")
    reader, writer = AsyncMock(wraps=store.read), AsyncMock(wraps=store.fs.write_file)
    monkeypatch.setattr(store, "read", reader)
    monkeypatch.setattr(store.fs, "write_file", writer)
    rows = [{"id": name, "content": name, "status": "pending"} for name in ("C", "D")]
    args = {
        "action": action,
        **({"todos": rows} if action == "append" else {"todo_data": {"target_id": "B", "items": rows}}),
    }
    result = await tool.invoke(args, session=session())
    assert result.todo_receipt.changed
    assert reader.call_count == writer.call_count == 1
    assert len(result.todo_receipt.snapshot.todos) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {
            "action": "append",
            "todos": [
                {"id": "C", "content": "c", "status": "pending"},
                {"id": " C ", "content": "duplicate", "status": "pending"},
            ],
        },
        {
            "action": "insert_after",
            "todo_data": {"target_id": "missing", "items": [{"id": "C", "content": "C", "status": "pending"}]},
        },
        {"action": "update", "todos": [{"id": "B", "status": "in_progress"}]},
    ],
)
async def test_invalid_batch_leaves_file_and_revision_unchanged(operation, tmp_path, args):
    from openjiuwen.core.common.exception.errors import BaseError

    tool = TodoModifyTool(operation, str(tmp_path))
    await seed(tool)
    before = (tmp_path / "session" / "todo.json").read_bytes()
    with pytest.raises(BaseError):
        await tool.invoke(args, session=session())
    assert (tmp_path / "session" / "todo.json").read_bytes() == before
    assert (await tool.invoke(update("A"), session=session())).todo_receipt.changed


@pytest.mark.asyncio
async def test_empty_bytes_are_corruption_and_not_a_missing_store(operation, tmp_path):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    path.write_bytes(b"")
    with pytest.raises(Exception, match="Invalid Todo JSON"):
        await TodoCreateTool(operation, str(tmp_path)).invoke(
            {"tasks": [{"id": "A", "content": "A"}]}, session=session()
        )
    assert path.read_bytes() == b""


@pytest.mark.asyncio
async def test_create_failure_does_not_consume_guard_and_two_instances_create_once(operation, tmp_path):
    state = {TODO_EXECUTION_KEY: "invoke-one"}
    shared = SimpleNamespace(get_session_id=lambda: "session", get_state=state.get, update_state=state.update)
    creator = TodoCreateTool(operation, str(tmp_path))
    await creator.prepare_context(shared, new_execution=True)
    before = (await creator.todo_store("session").read()).metadata["revision"]
    with pytest.raises(Exception, match="content"):
        await creator.invoke({"tasks": [{"id": "bad"}]}, session=shared)
    assert (await creator.todo_store("session").read()).metadata["revision"] == before
    other = TodoCreateTool(operation, str(tmp_path))
    results = await asyncio.gather(
        *(tool.invoke({"tasks": [{"id": "A", "content": "A"}]}, session=shared) for tool in (creator, other)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert int((await creator.todo_store("session").read()).metadata["revision"]) == int(before) + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "append", "insert_before", "insert_after", "cancel", "delete"])
async def test_each_incremental_action_migrates_legacy_without_changing_untouched_rows(operation, tmp_path, action):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    reminder = {
        "id": "R",
        "content": "remember",
        "status": "pending",
        "meta_data": {"persistent": True},
        "custom": {"nested": [1, 2]},
    }
    original = [
        {"id": "A", "content": "work", "status": "pending", "extra": "keep"},
        reminder,
        {"content": "missing id", "status": "pending", "old_field": 9},
    ]
    path.write_text(json.dumps(original))
    new_row = {"id": "C", "content": "inserted", "status": "pending"}
    args = {
        "update": update("A"),
        "append": {"action": "append", "todos": [new_row]},
        "insert_before": {"action": "insert_before", "todo_data": {"target_id": "A", "items": [new_row]}},
        "insert_after": {"action": "insert_after", "todo_data": {"target_id": "A", "items": [new_row]}},
        "cancel": {"action": "cancel", "ids": ["A"]},
        "delete": {"action": "delete", "ids": ["A"]},
    }[action]
    tool = TodoModifyTool(operation, str(tmp_path))
    result = await tool.invoke(args, session=session())
    rows = result.todo_receipt.snapshot.todos
    kept = next(row for row in rows if row["id"] == "R")
    assert {key: kept[key] for key in reminder} == reminder
    missing_id = next(row for row in rows if row.get("old_field") == 9)["id"]
    assert (
        next(row for row in (await tool.todo_store("session").read()).todos if row.get("old_field") == 9)["id"]
        == missing_id
    )
    ids = [row["id"] for row in rows]
    expected = {
        "update": ["A", "R", missing_id],
        "cancel": ["A", "R", missing_id],
        "delete": ["R", missing_id],
        "append": ["A", "R", missing_id, "C"],
        "insert_before": ["C", "A", "R", missing_id],
        "insert_after": ["A", "C", "R", missing_id],
    }
    assert ids == expected[action]
    if action != "delete":
        assert next(row for row in rows if row["id"] == "A")["extra"] == "keep"
    assert result.todo_receipt.snapshot.metadata["schema_version"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "A", "content": "A"}, {"id": " A ", "content": "duplicate"}],
        [{"id": "A", "content": "A", "status": "unknown"}],
    ],
)
async def test_invalid_legacy_entries_are_not_replaced_during_migration(operation, tmp_path, rows):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    raw = json.dumps(rows).encode()
    path.write_bytes(raw)
    with pytest.raises((BaseError, ValueError)):
        await TodoModifyTool(operation, str(tmp_path)).invoke(update("A"), session=session())
    assert path.read_bytes() == raw
