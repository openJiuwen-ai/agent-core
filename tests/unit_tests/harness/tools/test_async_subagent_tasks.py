"""Lifecycle edge cases for task groups without model or network dependencies."""

import asyncio
from dataclasses import replace

import pytest

from openjiuwen.core.runner import Runner  # noqa: F401 -- canonical SDK bootstrap
from openjiuwen.harness.tools.subagent.task_manager import AsyncSubagentTaskManager, TaskRequest


def submit(manager, scope, execute, cleanup, call_id="dispatch"):
    return manager.submit_batch(
        scope,
        call_id,
        [TaskRequest("general-purpose", "task")],
        existing_ids=[],
        executor_factory=lambda execution: (execute, cleanup),
    ).wait_ids[0]


@pytest.mark.asyncio
async def test_forged_owner_cannot_close_another_scope():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")

    async def execute(query):
        return {"result_type": "answer", "output": "done"}

    async def cleanup():
        pass

    task_id = submit(manager, scope, execute, cleanup)
    try:
        await manager.wait_any(scope, [task_id], 2)
        with pytest.raises(ValueError, match="task_not_found"):
            await manager.close_run(replace(scope, owner_id="foreign"), "foreign")
        result = await manager.wait_any(scope, [task_id], 2)
        assert result.ready[0]["output"] == "done"
    finally:
        await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_unique_scope_can_resume_without_explicit_session_id():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")
    try:
        assert manager.scope_if_active() == scope
        run_id, token = manager.enter_run(None, resume=True)
        assert run_id == scope.run_id
        manager.leave_run(run_id, token)
    finally:
        await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_two_simultaneous_close_requests_share_one_cleanup():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")
    running, cleanup_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = []

    async def execute(query):
        running.set()
        await asyncio.Event().wait()

    async def cleanup():
        cleanup_started.set()
        await release.wait()
        cleaned.append(True)

    submit(manager, scope, execute, cleanup)
    await asyncio.wait_for(running.wait(), 2)
    first = asyncio.create_task(manager.close_run(scope, "first"))
    await asyncio.wait_for(cleanup_started.wait(), 2)
    second = asyncio.create_task(manager.close_run(scope, "second"))
    release.set()
    reports = await asyncio.gather(first, second)
    assert all(report["tasks"][0]["status"] == "cancelled" for report in reports)
    assert cleaned == [True] and not manager._runs


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_cancel_execution():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(query):
        started.set()
        await release.wait()
        return {"result_type": "answer", "output": "kept"}

    async def cleanup():
        pass

    task_id = submit(manager, scope, execute, cleanup)
    waiter = asyncio.create_task(manager.wait_any(scope, [task_id], 10))
    try:
        await asyncio.wait_for(started.wait(), 2)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        release.set()
        result = await manager.wait_any(scope, [task_id], 2)
        assert result.ready[0]["output"] == "kept"
    finally:
        release.set()
        await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_cleanup_survives_repeated_cancellation_and_runs_once():
    manager = AsyncSubagentTaskManager("owner", drain_timeout_s=0.01)
    scope = manager.scope_for_session("session")
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    count = []

    async def execute(query):
        started.set()
        await asyncio.Event().wait()

    async def cleanup():
        cleaning.set()
        await release.wait()
        count.append(True)

    task_id = submit(manager, scope, execute, cleanup)
    await asyncio.wait_for(started.wait(), 2)
    first = asyncio.create_task(manager.cancel(scope, [task_id]))
    await asyncio.wait_for(cleaning.wait(), 2)
    await manager.cancel(scope, [task_id])
    assert not count
    release.set()
    await first
    result = await manager.wait_any(scope, [task_id], 2)
    assert result.ready[0]["status"] == "cancelled" and count == [True]
    await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_failed_result_preserves_partial_output_and_releases_resources():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")
    cleaned = []

    async def execute(query):
        return {"result_type": "error", "output": "artifact.txt exists; remaining work blocked"}

    async def cleanup():
        cleaned.append(True)

    task_id = submit(manager, scope, execute, cleanup)
    result = await manager.wait_any(scope, [task_id], 2)
    assert result.ready[0]["status"] == "failed"
    assert result.ready[0]["output"].startswith("artifact.txt") and result.ready[0]["error"]
    assert cleaned == [True]
    await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_closing_scope_rejects_new_dispatch_before_drain_finishes():
    manager = AsyncSubagentTaskManager("owner")
    scope = manager.scope_for_session("session")
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(query):
        started.set()
        await asyncio.Event().wait()

    async def cleanup():
        cleaning.set()
        await release.wait()

    submit(manager, scope, execute, cleanup)
    await asyncio.wait_for(started.wait(), 2)
    closing = asyncio.create_task(manager.close_run(scope, "close"))
    try:
        await asyncio.wait_for(cleaning.wait(), 2)
        with pytest.raises(ValueError, match="task_not_found|closing"):
            submit(manager, scope, execute, cleanup, "new-dispatch")
    finally:
        release.set()
        await closing


@pytest.mark.asyncio
async def test_reused_tool_id_in_new_run_gets_distinct_child_owner():
    manager = AsyncSubagentTaskManager("owner")
    child_ids = []

    async def execute(query):
        return {"output": "done", "result_type": "answer"}

    async def cleanup():
        pass

    def factory(execution):
        child_ids.append(execution.child_session_id)
        return execute, cleanup

    for _ in range(2):
        scope = manager.scope_for_session("same-session")
        receipt = manager.submit_batch(
            scope, "same-call", [TaskRequest("general-purpose", "task")], existing_ids=[], executor_factory=factory
        )
        await manager.wait_any(scope, receipt.wait_ids, 2)
        await manager.close_run(scope, "finished")
    assert len(set(child_ids)) == 2


@pytest.mark.asyncio
async def test_capacity_rejection_is_atomic_before_creating_any_runtime():
    manager = AsyncSubagentTaskManager("owner", max_tasks=128)
    scope = manager.scope_for_session("session")
    created = []

    def factory(execution):
        created.append(execution)
        raise AssertionError("capacity must be checked before building executors")

    try:
        with pytest.raises(ValueError, match="task_capacity_exceeded"):
            manager.submit_batch(
                scope,
                "oversized",
                [TaskRequest("general-purpose", "x")] * 129,
                existing_ids=[],
                executor_factory=factory,
            )
        assert not created and not manager._runs[scope.run_id].records
    finally:
        await manager.close_run(scope, "test")


@pytest.mark.asyncio
async def test_approval_pause_does_not_consume_or_reset_execution_budget(monkeypatch):
    from openjiuwen.harness.tools.subagent import task_manager as module

    clock = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    manager = AsyncSubagentTaskManager("owner", task_timeout_s=1)
    scope = manager.scope_for_session("session")
    cleaned = []

    async def execute(reply):
        clock[0] += 0.2
        if reply is None:
            return {"result_type": "interrupt", "interrupt_ids": ["approval"], "state": []}
        return {"result_type": "answer", "output": "resumed"}

    async def cleanup():
        cleaned.append(True)

    task_id = submit(manager, scope, execute, cleanup)
    try:
        result = await manager.wait_any(scope, [task_id], 2)
        assert result.interruptions
        record = manager._runs[scope.run_id].records[task_id]
        assert record.remaining_s == pytest.approx(0.8)
        clock[0] += 3600  # Simulate a user leaving the approval dialog open.
        await manager.resume(scope, task_id, object())
        result = await manager.wait_any(scope, [task_id], 2)
        assert result.ready[0]["output"] == "resumed"
        assert record.remaining_s == pytest.approx(0.6) and cleaned == [True]
    finally:
        await manager.close_run(scope, "test")
