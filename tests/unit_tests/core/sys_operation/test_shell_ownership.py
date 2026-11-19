# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Ownership and cancellation tests without creating operating system processes."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.core.sys_operation import shell_process_registry as registry_module
from openjiuwen.core.sys_operation._process_owner import ProcessOwner
from openjiuwen.core.sys_operation.local import shell_operation as shell_module


@pytest.fixture
def registry(monkeypatch):
    instance = registry_module.ShellProcessRegistry()
    instance._reaper = Mock()  # Drive reaping explicitly, without a background thread.
    monkeypatch.setattr(registry_module, "SHELL_PROCESS_REGISTRY", instance)
    return instance


def _process(returncode=0):
    process = Mock(spec=asyncio.subprocess.Process)
    process.pid = 12345
    process.returncode = returncode
    process.wait = AsyncMock(return_value=0)
    return process


def _owner():
    owner = Mock(spec=ProcessOwner)
    owner.is_empty.return_value = False
    owner.spawn_options.return_value = {}
    return owner


def _adopt(registry, sid="session", process=None, owner=None):
    process = process or _process()
    owner = owner or _owner()
    registry.adopt(registry.reserve(sid), process, owner)
    return process, owner


def test_completed_parent_retains_live_descendants_until_explicit_stop(registry):
    process, owner = _adopt(registry)
    registry.unregister("session", process)
    registry.reap_completed()
    owner.close.assert_not_called()
    assert registry.kill_session("session") == 1
    owner.close.assert_called_once()
    assert not registry._owners
    assert not registry._processes
    assert registry.kill_session("session") == 0


def test_empty_execution_is_reaped_without_waiting_for_session_stop(registry):
    _, owner = _adopt(registry)
    owner.is_empty.return_value = True
    registry.reap_completed()
    owner.close.assert_called_once()
    assert not registry._owners
    assert not registry._processes


def test_inspection_failure_preserves_preview_but_does_not_prevent_stop(registry):
    _, owner = _adopt(registry)
    owner.is_empty.side_effect = OSError("enumeration unavailable")
    registry.reap_completed()
    owner.close.assert_not_called()
    assert registry.kill_session("session") == 1
    owner.close.assert_called_once()
    assert not registry._owners


def test_failed_close_keeps_ownership_for_retry(registry):
    _, owner = _adopt(registry)
    owner.close.side_effect = [OSError("close failed"), None]
    assert registry.kill_session("session") == 0
    assert registry._owners
    assert registry.kill_session("session") == 1
    assert not registry._owners


def test_stop_marks_only_matching_pending_spawns_and_allows_future_turn(registry):
    pending = registry.reserve("session_sub_browser")
    other = registry.reserve("session2")
    assert registry.kill_session_tree("session") == 0
    assert pending.cancelled
    assert not other.cancelled
    process, owner = _process(), _owner()
    with pytest.raises(asyncio.CancelledError):
        registry.adopt(pending, process, owner)
    owner.start.assert_not_called()
    assert not registry._owners
    registry.finish_spawn(pending)
    registry.finish_spawn(other)
    _adopt(registry, "session_sub_browser")
    assert registry.kill_session_tree("session") == 1
    assert registry.consume_cancelled("session")
    assert not registry.consume_cancelled("session")


def test_exact_stop_and_tree_stop_have_distinct_session_boundaries(registry):
    _, parent = _adopt(registry)
    _, child = _adopt(registry, "session_sub_browser")
    _, other = _adopt(registry, "session2")
    assert registry.kill_session("session") == 1
    parent.close.assert_called_once()
    child.close.assert_not_called()
    other.close.assert_not_called()
    assert registry.kill_session_tree("session") == 1
    child.close.assert_called_once()
    other.close.assert_not_called()


def test_shutdown_includes_anonymous_executions_and_pending_spawns(registry):
    _adopt(registry)
    _, anonymous = _adopt(registry, None)
    pending = registry.reserve(None)
    registry.close()
    anonymous.close.assert_called_once()
    assert pending.cancelled
    assert not registry._owners
    assert not registry._processes


def test_same_pid_does_not_confuse_distinct_os_owners(registry):
    first, first_owner = _adopt(registry)
    _, second_owner = _adopt(registry, "another_session")
    assert registry.terminate(first)
    first_owner.close.assert_called_once()
    second_owner.close.assert_not_called()


@pytest.fixture
def spawn_runtime(registry, monkeypatch):
    owner, process = _owner(), _process(returncode=None)
    shell = shell_module.ShellOperation(
        "shell", OperationMode.LOCAL, "spawn test", LocalWorkConfig(shell_allowlist=None),
    )
    monkeypatch.setattr(shell, "_resolve_execution_plan", Mock(return_value=(["unused"], False, None)))
    monkeypatch.setattr(shell_module, "create_process_owner", Mock(return_value=owner))

    def terminate_unowned(child):
        child.kill()
        return True

    monkeypatch.setattr(registry_module, "_terminate_unowned_process", terminate_unowned)
    token = registry_module.set_shell_session_id("session")
    try:
        yield shell, process, owner
    finally:
        registry_module.reset_shell_session_id(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_task", [False, True])
async def test_stop_during_process_creation_reclaims_new_child(
    registry, spawn_runtime, monkeypatch, cancel_task,
):
    shell, process, owner = spawn_runtime
    creating, complete = asyncio.Event(), asyncio.Event()

    async def create(*args, **kwargs):
        creating.set()
        await complete.wait()
        return process

    monkeypatch.setattr(shell_module.asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(shell._create_subprocess("unused", ".", {}))
    await creating.wait()
    if cancel_task:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated cancellation must not lose the creation task.
    else:
        registry.kill_session_tree("session")
    complete.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    owner.start.assert_not_called()
    owner.close.assert_called_once()
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
    assert not registry._pending
    assert not registry._owners


def test_unowned_process_does_not_signal_the_hosts_shared_group(monkeypatch):
    process = _process(returncode=None)
    get_group, kill_group = Mock(return_value=999), Mock()
    monkeypatch.setattr(registry_module.os, "getpgid", get_group, raising=False)
    monkeypatch.setattr(registry_module.os, "killpg", kill_group, raising=False)
    with pytest.MonkeyPatch.context() as local:
        local.setattr(registry_module.os, "name", "posix")
        assert registry_module._terminate_unowned_process(process)
    process.kill.assert_called_once()
    kill_group.assert_not_called()


def test_completed_unowned_process_is_never_signalled_by_reused_pid(monkeypatch):
    process = _process(returncode=0)
    kill_group = Mock()
    monkeypatch.setattr(registry_module.os, "killpg", kill_group, raising=False)
    assert not registry_module._terminate_unowned_process(process)
    kill_group.assert_not_called()
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_assignment_failure_kills_suspended_unassigned_child(spawn_runtime, monkeypatch):
    shell, process, owner = spawn_runtime
    owner.attach.side_effect = OSError("job assignment failed")
    monkeypatch.setattr(shell_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(OSError, match="job assignment failed"):
        await shell._create_subprocess("unused", ".", {})
    owner.close.assert_called_once()
    owner.start.assert_not_called()
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_creation_failure_releases_empty_owner(registry, spawn_runtime, monkeypatch):
    shell, _, owner = spawn_runtime
    monkeypatch.setattr(
        shell_module.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")),
    )
    with pytest.raises(OSError, match="spawn failed"):
        await shell._create_subprocess("unused", ".", {})
    owner.close.assert_called_once()
    assert not registry._pending


@pytest.mark.asyncio
async def test_assignment_precedes_resume_and_registration_is_retained(registry, spawn_runtime, monkeypatch):
    shell, process, owner = spawn_runtime
    calls = Mock()
    calls.attach_mock(owner.attach, "attach")
    calls.attach_mock(owner.start, "start")
    monkeypatch.setattr(shell_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert await shell._create_subprocess("unused", ".", {}) is process
    assert [call[0] for call in calls.mock_calls] == ["attach", "start"]
    assert registry._owners[process] is owner
    assert not registry._pending
