"""Todo consistency with actual local files (no model or live service)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.errors import BaseError, FrameworkError
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation.local._rw_lock_manager import ReadWriteLockManager
from openjiuwen.core.sys_operation.local.fs_operation import FsOperation
from openjiuwen.harness.schema.task import TaskPlan, TodoItem, TodoStatus
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoGetTool, TodoListTool, TodoModifyTool, TodoTool
from openjiuwen.harness.tools.todo_store import _BINDING, bind_todo_batch, prepare_todo_batch, transact


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
@pytest.mark.parametrize("inputs,reason", [
    ({}, "is required"),
    ({"tasks": "[]"}, "must be a JSON array"),
    ({"tasks": []}, "non-empty"),
    ({"tasks": ["bad"]}, "JSON object"),
    ({"tasks": [{"content": ""}]}, "nonempty content"),
])
async def test_create_input_errors_preserve_domain_code_without_io(env, monkeypatch, inputs, reason):
    from unittest.mock import AsyncMock

    creator, _, _, session, path = env
    read = AsyncMock(side_effect=AssertionError("Invalid input must not read storage"))
    monkeypatch.setattr(creator.fs, "read_file", read)
    with pytest.raises(BaseError, match=reason) as caught:
        await creator.invoke(inputs, session=session)
    assert caught.value.code == 182503
    assert caught.value.details["field"] == "tasks"
    assert str(caught.value).count("[182503]") == 1
    read.assert_not_awaited()
    assert not path.exists()


@pytest.mark.asyncio
async def test_todo_outcomes_are_internal_and_preserve_partial_update_semantics(env):
    creator, modifier, listing, session, path = env
    created = await create(creator, session)
    assert set(created) == {"message"}
    assert created.tool_execution_status == "success"
    assert created.tool_item_counts["applied_count"] == 2
    partial = await modifier.invoke({"action": "update", "todos": [
        {"id": "A", "status": "completed"}, {"id": "missing", "status": "completed"},
    ]}, session=session)
    assert partial.tool_execution_status == "partial"
    assert partial.tool_item_counts == {"requested_count": 2, "accepted_count": 1,
                                        "failed_count": 1, "unmatched_count": 0, "applied_count": 1}
    before = path.read_bytes()
    failed = await modifier.invoke({"action": "update", "todos": [{"id": "missing"}]}, session=session)
    assert failed.tool_execution_status == "failure"
    assert failed.tool_commit_state == "not_needed"
    noop = await update(modifier, session, "A", status="completed")
    assert noop.tool_execution_status == "noop"
    assert path.read_bytes() == before
    read = await listing.invoke({}, session=session)
    assert set(read) == {"tasks"}
    assert read.tool_execution_status == "success"
    assert "tool_execution_status" not in json.dumps(read)


def test_old_result_attributes_remain_readable_after_upgrade():
    from openjiuwen.harness.tools.todo_store import TodoResult, decode_snapshot, snapshot_from_result

    result = TodoResult({"message": "old"}, decode_snapshot({"plan_id": "P", "revision": 4, "todos": []}))
    del result.todo_snapshot_versioned
    del result.todo_read_authoritative
    restored = snapshot_from_result(result)
    assert restored.plan_id == "P" and restored.revision == 4
    assert restored.read_authoritative is True


@pytest.mark.asyncio
@pytest.mark.parametrize("read_result", [
    SimpleNamespace(code=1, message="backend read failed", data=None),
    SimpleNamespace(code=0, data=SimpleNamespace(content="broken", exists=True, content_sha256="0" * 64)),
])
async def test_snapshot_failures_keep_the_load_error_code(env, monkeypatch, read_result):
    from unittest.mock import AsyncMock

    _, modifier, listing, session, path = env
    monkeypatch.setattr(listing.fs, "read_file", AsyncMock(return_value=read_result))
    for operation in (lambda: listing.invoke({}, session=session), lambda: modifier.load_todos("session")):
        with pytest.raises(BaseError) as caught:
            await operation()
        assert caught.value.code == 182500
        assert str(caught.value).count("[182500]") == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_backend_digest_representation_is_preserved_for_conditional_writes(env, monkeypatch):
    creator, modifier, listing, session, path = env
    read_file, write_file = creator.fs.read_file, creator.fs.write_file
    expected_digests = []

    async def read_with_prefix(*args, **kwargs):
        result = await read_file(*args, **kwargs)
        result.data.content_sha256 = "sha256:" + result.data.content_sha256
        return result

    async def compare_with_prefix(*args, **kwargs):
        options = dict(kwargs["options"])
        digest = options["expected_content_sha256"]
        expected_digests.append(digest)
        assert digest.startswith("sha256:")
        options["expected_content_sha256"] = digest.removeprefix("sha256:")
        return await write_file(*args, **{**kwargs, "options": options})

    monkeypatch.setattr(creator.fs, "read_file", read_with_prefix)
    monkeypatch.setattr(creator.fs, "write_file", compare_with_prefix)
    await create(creator, session)
    await update(modifier, session, "A", status="completed")
    listed = await listing.invoke({}, session=session)
    assert len(expected_digests) == 2
    assert listed.todo_snapshot == json.loads(path.read_text())
    assert listed.todo_snapshot["revision"] == 2
    assert listed.todo_snapshot["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    {"exists": "true", "content_sha256": "0" * 64},
    {"exists": True, "content_sha256": 42},
    {"exists": True, "content_sha256": ""},
    {"exists": True, "content_sha256": "   "},
])
async def test_invalid_snapshot_metadata_is_not_a_read_only_fallback(env, monkeypatch, metadata):
    from unittest.mock import AsyncMock
    from openjiuwen.harness.tools.todo_store import read_snapshot

    _, modifier, listing, session, path = env
    result = SimpleNamespace(code=0, data=SimpleNamespace(content="[]", **metadata))
    monkeypatch.setattr(listing.fs, "read_file", AsyncMock(return_value=result))
    for operation in (
        lambda: listing.invoke({}, session=session),
        lambda: modifier.load_todos("session"),
        lambda: read_snapshot(listing.fs, str(path), allow_read_only=True),
    ):
        with pytest.raises(BaseError) as caught:
            await operation()
        assert caught.value.code == 182500
        assert caught.value.details["reason"] == "invalid_snapshot_metadata"
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
async def test_serialization_failure_does_not_publish_or_replace_global_json(env, monkeypatch, error_type):
    from unittest.mock import AsyncMock

    from openjiuwen.harness.tools import todo_store

    creator, modifier, _, session, path = env
    await create(creator, session)
    before = path.read_bytes()
    write = AsyncMock()

    def fail_dump(*_args, **_kwargs):
        raise error_type("serialization failed")

    monkeypatch.setattr(todo_store, "json", SimpleNamespace(loads=json.loads, dumps=fail_dump))
    monkeypatch.setattr(modifier.fs, "write_file", write)
    assert json.loads(json.dumps({"unrelated": True})) == {"unrelated": True}
    with pytest.raises(BaseError if error_type is RuntimeError else KeyboardInterrupt, match="serialization failed"):
        await update(modifier, session, "A", content="changed")
    write.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_inflight_publication_failure_does_not_claim_zero_writes(env, monkeypatch, cancel):
    from unittest.mock import patch
    from openjiuwen.core.foundation.tool import execution_logging

    creator, modifier, _, session, path = env
    await create(creator, session)
    published = asyncio.Event()
    calls = 0

    async def accept_then_lose_response(_path, content, **_kwargs):
        nonlocal calls
        calls += 1
        path.write_text(content)
        published.set()
        if cancel:
            await asyncio.Future()
        raise OSError("accepted, but response lost")

    monkeypatch.setattr(modifier.fs, "write_file", accept_then_lose_response)
    with patch.object(execution_logging.logger, "warning") as warning:
        task = asyncio.create_task(modifier.invoke({"action": "update", "todos": [
            {"id": "A", "status": "completed"},
        ]}, session=session, tool_call_id="inflight"))
        await published.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else BaseError) as caught:
            await task
        if not cancel:
            assert "outcome is unknown" in str(caught.value)
            assert "Read the current Todo" in str(caught.value)
            assert "Do not blindly retry" in str(caught.value)
    assert calls == 1
    assert json.loads(path.read_text())["todos"][0]["status"] == "completed"
    records = [call.kwargs["metadata"] for call in warning.call_args_list
               if call.kwargs.get("metadata", {}).get("event_role") == "terminal"]
    assert len(records) == 1
    assert records[0]["commit_state"] == "unknown"
    assert records[0]["item_counts"]["applied_count"] is None


@pytest.mark.asyncio
async def test_domain_publication_error_keeps_code_and_explains_unknown_outcome(env, monkeypatch):
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    creator, modifier, _, session, path = env
    await create(creator, session)
    error = build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason="response lost")
    calls = 0

    async def lose_response(_path, content, **_kwargs):
        nonlocal calls
        calls += 1
        path.write_text(content)
        raise error

    monkeypatch.setattr(modifier.fs, "write_file", lose_response)
    with pytest.raises(BaseError) as caught:
        await update(modifier, session, "A", status="completed")
    assert caught.value is error
    assert caught.value.code == 182501
    assert "outcome is unknown" in str(caught.value)
    assert "Read the current Todo" in str(caught.value)
    assert "Do not blindly retry" in str(caught.value)
    assert error.tool_commit_state == "unknown"
    assert error.tool_item_counts["applied_count"] is None
    assert calls == 1 and json.loads(path.read_text())["todos"][0]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["error", "missing", "unconfirmed"])
async def test_unconfirmed_publication_never_suggests_safe_replay(env, monkeypatch, response_kind):
    creator, modifier, _, session, path = env
    await create(creator, session)
    calls = 0

    async def unconfirmed(_path, content, **_kwargs):
        nonlocal calls
        calls += 1
        path.write_text(content)
        return SimpleNamespace(code=1 if response_kind == "error" else 0, message="not confirmed",
                               data=None if response_kind == "missing" else SimpleNamespace(
                                   conflict=False, atomic_replace=False))

    monkeypatch.setattr(modifier.fs, "write_file", unconfirmed)
    with pytest.raises(BaseError) as caught:
        await update(modifier, session, "A", status="completed")
    assert caught.value.code == 182501
    assert "Read the current Todo" in str(caught.value)
    assert "Do not blindly retry" in str(caught.value)
    assert caught.value.details["commit_state"] == "unknown"
    assert caught.value.tool_item_counts["applied_count"] is None
    assert calls == 1


@pytest.mark.asyncio
async def test_unknown_publication_guidance_reaches_actual_tool_message(env, monkeypatch):
    from openjiuwen.core.foundation.llm import ToolCall
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

    creator, modifier, _, session, path = env
    await create(creator, session)
    calls = 0

    async def lose_response(_path, content, **_kwargs):
        nonlocal calls
        calls += 1
        path.write_text(content)
        raise OSError("response lost")

    monkeypatch.setattr(modifier.fs, "write_file", lose_response)
    callbacks = AgentCallbackManager("todo-batch-test")
    manager = AbilityManager(owner_id="todo-batch-test")
    context = AgentCallbackContext(agent=SimpleNamespace(agent_callback_manager=callbacks), session=session)
    await Runner.start()
    try:
        manager.add_ability(modifier.card, modifier)
        result = await manager.execute(context, ToolCall(
            id="unknown-publication", type="function", name="todo_modify", arguments=json.dumps({
                "action": "update", "todos": [{"id": "A", "status": "completed"}],
            })), session)
        message = result[0][1].content
        assert "outcome is unknown" in message
        assert "Read the current Todo" in message
        assert "Do not blindly retry" in message
        assert calls == 1 and json.loads(path.read_text())["todos"][0]["status"] == "completed"
    finally:
        manager.remove_ability("todo_modify")
        await callbacks.clear()
        await Runner.stop()


@pytest.mark.asyncio
async def test_legacy_migration_validates_the_published_plan(env):
    _, modifier, _, session, path = env
    path.parent.mkdir()
    path.write_text(json.dumps([
        {"id": "old", "content": "old", "status": "in_progress", "generation_token": "old"},
    ]))
    session.update_state({"todo_generation_token": "new"})
    before = path.read_bytes()
    with pytest.raises(BaseError, match="More than one") as caught:
        await modifier.invoke({"action": "append", "todos": [
            {"id": "new", "content": "new", "status": "in_progress"},
        ]}, session=session)
    assert caught.value.code == 182503
    assert caught.value.details["reason"] == "migration_conflict"
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_legal_legacy_migration_allows_followup_and_preserves_extensions(env):
    _, modifier, _, session, path = env
    path.parent.mkdir()
    path.write_text(json.dumps([
        {"id": "old", "content": "old", "status": "completed", "extension": {"keep": True}},
    ]))
    await modifier.invoke({"action": "append", "todos": [
        {"id": "new", "content": "new", "status": "in_progress"},
    ]}, session=session)
    await update(modifier, session, "new", content="changed")
    saved = json.loads(path.read_text())
    assert saved["revision"] == 2
    assert saved["todos"][0]["extension"] == {"keep": True}
    assert saved["todos"][1]["content"] == "changed"


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
async def test_plan_sync_and_append_preserve_both_changes_without_legacy_cache(env, monkeypatch):
    from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

    creator, modifier, _, session, path = env
    before = (await create(creator, session)).todo_snapshot
    plan = TaskPlan(tasks=[
        TodoItem(id="A", status=TodoStatus.COMPLETED),
        TodoItem(id="B", status=TodoStatus.PENDING),
    ])
    rail = TaskPlanningRail()
    rail.tools = [modifier]
    ctx = SimpleNamespace(session=session, extra={}, agent=SimpleNamespace(
        load_state=lambda _session: SimpleNamespace(task_plan=plan),
    ))

    async def legacy_cache(*args, **kwargs):
        raise AssertionError("Plan sync must not read or write the legacy cache")

    monkeypatch.setattr(TodoTool, "load_todos", legacy_cache)
    monkeypatch.setattr(TodoTool, "save_todos", legacy_cache)
    await asyncio.gather(
        rail.after_task_iteration(ctx),
        modifier.invoke({"action": "append", "todos": [
            {"id": "C", "content": "third", "status": "pending"},
        ]}, session=session),
    )
    saved = json.loads(path.read_text())
    assert saved["revision"] == before["revision"] + 2
    assert [(item["id"], item["status"]) for item in saved["todos"]] == [
        ("A", "completed"), ("B", "pending"), ("C", "pending"),
    ]
    assert saved["todos"][1] == before["todos"][1]
    unchanged = path.read_bytes()
    await rail.after_task_iteration(ctx)
    assert path.read_bytes() == unchanged


@pytest.mark.asyncio
async def test_transactional_host_io_bypasses_legacy_cache_and_stages_updates(env, monkeypatch):
    creator, modifier, _, session, path = env
    initial = (await create(creator, session)).todo_snapshot

    async def legacy_cache(*args, **kwargs):
        raise AssertionError("Transaction I/O must not use the legacy cache")

    monkeypatch.setattr(TodoTool, "load_todos", legacy_cache)
    monkeypatch.setattr(TodoTool, "save_todos", legacy_cache)

    async def change():
        items = await modifier.read_todos("session")
        items[0].status = TodoStatus.COMPLETED
        await modifier.write_todos("session", items)
        assert (await modifier.read_todos("session"))[0].status == TodoStatus.COMPLETED
        assert json.loads(path.read_text()) == initial

    await transact(modifier, "session", change)
    saved = json.loads(path.read_text())
    assert saved["revision"] == initial["revision"] + 1
    assert saved["todos"][0]["status"] == "completed"
    assert saved["todos"][1] == initial["todos"][1]


@pytest.mark.asyncio
async def test_native_transactions_are_independent_of_legacy_io_decorators(env, monkeypatch):
    c, m, listing, session, path = env

    async def legacy_decorator(*args, **kwargs):
        raise AssertionError("A transaction must not read or stage through a legacy I/O decorator")

    monkeypatch.setattr(TodoTool, "load_todos", legacy_decorator)
    monkeypatch.setattr(TodoTool, "save_todos", legacy_decorator)
    first = await create(c, session)
    await asyncio.gather(update(m, session, "A", status="completed"),
                         update(m, session, "B", content="changed"))
    fetched = await TodoGetTool(SimpleNamespace(fs=lambda: m.fs), m.workspace).invoke({"id": "B"}, session=session)
    listed = await listing.invoke({}, session=session)
    disk = json.loads(path.read_text())
    assert disk["revision"] == first.todo_snapshot["revision"] + 2
    assert [(row["id"], row["status"]) for row in disk["todos"]] == [("A", "completed"), ("B", "pending")]
    assert fetched.todo_snapshot == listed.todo_snapshot == disk
    assert disk["todos"][1]["content"] == "changed"


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
    c.require_explicit_plan_action = True
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
async def test_active_plan_creation_policy_is_scoped_to_the_tool_instance(env):
    creator, modifier, _, session, path = env
    await create(creator, session)
    before = json.loads(path.read_text())

    guarded = TodoCreateTool(SimpleNamespace(fs=lambda: modifier.fs), modifier.workspace)
    guarded.snapshot_guard = creator.snapshot_guard
    guarded.require_explicit_plan_action = True
    with pytest.raises(FrameworkError, match="active plan"):
        await create(guarded, session)
    assert json.loads(path.read_text()) == before

    creator.reset_invoke_marker("session")
    result = await create(creator, session)
    assert result.todo_snapshot["plan_id"] != before["plan_id"]
    assert result.todo_snapshot["revision"] == before["revision"] + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("versioned", [False, True])
async def test_empty_plan_sync_preserves_legacy_behavior(env, versioned):
    from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

    _, _, reader, session, path = env
    path.parent.mkdir()
    path.write_text(json.dumps({"plan_id": "P", "revision": 1, "todos": []} if versioned else []))
    result = await reader.invoke({}, session=session)
    state = SimpleNamespace(task_plan=TaskPlan(tasks=[TodoItem(id="old", content="old")]))
    agent = SimpleNamespace(load_state=lambda _session: state, save_state=lambda *_args: None)
    rail = TaskPlanningRail()
    rail.tools = [reader]
    ctx = SimpleNamespace(session=session, agent=agent, inputs=SimpleNamespace(tool_result=result))
    await rail._sync_plan_from_todos(ctx)
    assert (state.task_plan is None) is versioned


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

    async def lifecycle(*_args, **_kwargs):
        pass

    tool.snapshot_guard = lifecycle
    inputs = {
        "TodoCreateTool": {"tasks": [{"id": "A", "content": "valid"}]},
        "TodoGetTool": {"id": "A"},
        "TodoModifyTool": {"action": "update", "todos": [{"id": "A"}]},
        "TodoListTool": {},
    }[tool_name]
    with pytest.raises(FrameworkError, match="does not support snapshot reads.*exists and content_sha256"):
        await tool.invoke(inputs, session=session)
    write.assert_not_awaited()
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["aio", "yuanrong"])
@pytest.mark.parametrize("versioned", [False, True])
async def test_ordinary_reads_on_legacy_provider_do_not_enable_unprotected_writes(
    env, monkeypatch, provider_name, versioned
):
    from unittest.mock import AsyncMock, Mock

    from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxEndpoint
    from openjiuwen.extensions.sys_operation.sandbox.providers.aio import AIOFSProvider
    from openjiuwen.extensions.sys_operation.sandbox.providers.yuanrong import YuanrongFSProvider

    _, _, _, session, path = env
    rows = [{"id": "A", "content": "existing", "status": "pending"}]
    content = json.dumps({"plan_id": "P", "revision": 1, "todos": rows} if versioned else rows)
    endpoint = SandboxEndpoint(base_url="test://sandbox", sandbox_id="box", isolation_key="session")
    if provider_name == "aio":
        provider = AIOFSProvider(endpoint)
        client = SimpleNamespace(file=SimpleNamespace(read_file=Mock(
            return_value=SimpleNamespace(data=SimpleNamespace(content=content)))))
        monkeypatch.setattr(provider, "_get_client", lambda: client)
    else:
        provider = YuanrongFSProvider(endpoint)
        monkeypatch.setattr(provider, "_fs_read_sync", lambda *args, **kwargs: content)
    write = AsyncMock()
    monkeypatch.setattr(provider, "write_file", write)
    operation = SimpleNamespace(fs=lambda: provider)
    reader = TodoListTool(operation, str(path.parent.parent))
    getter = TodoGetTool(operation, str(path.parent.parent))
    modifier = TodoModifyTool(operation, str(path.parent.parent))

    listed = await reader.invoke({}, session=session)
    assert listed["tasks"][0]["id"] == "A"
    assert not hasattr(listed, "todo_snapshot")
    assert (await getter.invoke({"id": "A"}, session=session))["todo"]["id"] == "A"
    items = await modifier.load_todos("session")
    assert items[0].id == "A"
    with pytest.raises(FrameworkError, match="does not support snapshot reads"):
        await modifier.save_todos("session", items)
    write.assert_not_awaited()


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
    with pytest.raises(BaseError, match="Missing required field"):
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
