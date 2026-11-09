# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contended control and advisory locks must not leave partial runtime state."""

import asyncio
import contextlib
import threading
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.harness.personal_context import PersonalContext, agent_support
from openjiuwen.harness.personal_context import personal_context as runtime
from openjiuwen.harness.personal_context.fetch import feishu
from openjiuwen.harness.tools.base_tool import ToolOutput


def _config(tmp_path, *, interval=60):
    return PersonalContext.Config.from_dict({
        "collection_enabled": False, "agent_use_enabled": False, "strategy_profile": "rules",
        "fetch_services": [{
            "service_id": "notes", "provider": "local_files", "enabled": False,
            "interval_seconds": interval, "time_range": {"mode": "all"},
            "source": {"root_dir": str(tmp_path)},
        }],
    })


@pytest.mark.asyncio
async def test_busy_control_times_out_without_mutating_or_releasing_owner(tmp_path, monkeypatch):
    core = PersonalContext(home=tmp_path / "home")
    await core.set_configuration(_config(tmp_path))
    original = core._config
    monkeypatch.setattr(runtime, "_CONTROL_LOCK_TIMEOUT_SECONDS", 0.02)
    await core._state_lock.acquire()
    try:
        with pytest.raises(BaseError, match="control is busy"):
            await asyncio.wait_for(core.start_agent_use(), timeout=0.5)
        assert core._config is original
        assert core._state_lock.locked()
    finally:
        core._state_lock.release()
    try:
        await core.start_agent_use()
        assert core._config.agent_use_enabled
    finally:
        core.shutdown()


@pytest.mark.asyncio
async def test_cancelled_control_waiter_never_releases_owner(tmp_path):
    core = PersonalContext(home=tmp_path / "home")
    await core._state_lock.acquire()
    waiter = asyncio.create_task(core.start_agent_use())
    try:
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert core._state_lock.locked()
    finally:
        core._state_lock.release()
        core.shutdown()


@pytest.mark.asyncio
async def test_slow_configuration_reads_do_not_lock_or_overwrite_new_state(tmp_path, monkeypatch):
    core = PersonalContext(home=tmp_path / "home")
    await core.set_configuration(_config(tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()
    original_query = core._run_query

    async def slow_query(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_query(*args, **kwargs)

    monkeypatch.setattr(core, "_run_query", slow_query)
    update = asyncio.create_task(core.set_configuration(_config(tmp_path, interval=120)))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(core.start_agent_use(), timeout=0.2)
        assert (await core.snapshot()).agent_use_enabled
        release.set()
        with pytest.raises(BaseError, match="configuration changed"):
            await update
        assert core._config.fetch_services[0].interval_seconds == 60
        assert core._config.agent_use_enabled
    finally:
        release.set()
        with contextlib.suppress(BaseError):
            await update
        core.shutdown()


@pytest.mark.asyncio
async def test_slow_authorization_does_not_hold_controls_or_publish_after_cancel(tmp_path, monkeypatch):
    core = PersonalContext(home=tmp_path / "home")
    await core.set_configuration(_config(tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_status(_scopes):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return True, set(runtime.supported_read_scopes()), True

    monkeypatch.setattr(runtime, "_lark_cli_auth_status", slow_status)
    monkeypatch.setattr(runtime, "_CONTROL_LOCK_TIMEOUT_SECONDS", 0.02)
    request = asyncio.create_task(core.authorize_provider("feishu"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(core.start_agent_use(), timeout=0.2)
        assert (await core.snapshot()).agent_use_enabled
        await asyncio.wait_for(core._cancel_authorization(), timeout=0.2)
        assert core._authorization_start_task is None
        assert not core._authorization_lock.locked()
        assert not core._state_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert core._authorization_task is None
        assert core._authorization_challenge is None
        assert core._authorization_error is None
    finally:
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await request
        core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_lock", [True, False])
async def test_optional_directory_hints_timeout_preserves_tool_pair(tmp_path, monkeypatch, busy_lock):
    context = tmp_path / "context"
    context.mkdir()
    lock = asyncio.Lock()
    release = threading.Event()
    finished = threading.Event()
    call = ToolCall(id="read-1", type="function", name="list_files", arguments='{"path":"context"}')
    output = ToolOutput(success=True, data={"original": True})
    message = ToolMessage(content="original result", tool_call_id=call.id)
    inputs = ToolCallInputs(
        tool_call=call, tool_name="list_files", tool_args={"path": "context"},
        tool_result=output, tool_msg=message,
    )
    messages = [AssistantMessage(content="", tool_calls=[call]), message]
    state = {"snapshot_lock": lock, "seen_directories": set()}
    monkeypatch.setattr(agent_support, "_ADVISORY_LOCK_TIMEOUT_SECONDS", 0.02)

    def slow_snapshot(*args, **kwargs):
        try:
            assert release.wait(timeout=2)
            return {"late": True}
        finally:
            finished.set()

    if busy_lock:
        await lock.acquire()
    else:
        monkeypatch.setattr(agent_support, "_directory_snapshot", slow_snapshot)
    try:
        await asyncio.wait_for(
            agent_support._after_tool_call_directory_snapshot(
                SimpleNamespace(inputs=inputs), state=state, sandbox=tmp_path,
            ), timeout=0.5,
        )
        assert lock.locked() is busy_lock
        assert output.success and output.data == {"original": True}
        assert message.content == "original result"
        agent_support.validate_personal_context_messages(messages)
        release.set()
        if not busy_lock:
            assert await asyncio.to_thread(finished.wait, 1)
        assert message.content == "original result"
        assert state["seen_directories"] == set()
    finally:
        release.set()
        if busy_lock:
            lock.release()


@pytest.mark.asyncio
async def test_busy_install_guard_does_not_block_loop_or_mark_install_attempted(monkeypatch):
    guard = threading.Lock()
    guard.acquire()
    monkeypatch.setattr(feishu, "_lark_cli_install_guard", guard)
    monkeypatch.setattr(feishu, "_INSTALL_GUARD_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(feishu, "_lark_cli_install_attempted", False)
    monkeypatch.setattr(feishu.shutil, "which", lambda name: "installed")
    try:
        with pytest.raises(BaseError, match="install state is busy"):
            await asyncio.wait_for(feishu._ensure_lark_cli_installed(), timeout=0.5)
        assert not feishu._lark_cli_install_attempted
        assert guard.locked()
    finally:
        guard.release()
    await feishu._ensure_lark_cli_installed()
    assert feishu._lark_cli_install_attempted


def test_busy_processor_registration_keeps_owner_and_registry(monkeypatch):
    guard = threading.Lock()
    guard.acquire()
    registry = dict(agent_support.ContextEngine._PROCESSOR_MAP)
    monkeypatch.setattr(agent_support, "_PROCESSOR_REGISTRATION_LOCK", guard)
    monkeypatch.setattr(agent_support, "_ADVISORY_LOCK_TIMEOUT_SECONDS", 0.02)
    try:
        with pytest.raises(BaseError, match="processor registration is busy"):
            agent_support._make_context_processor_rail(None, None)
        assert guard.locked()
        assert agent_support.ContextEngine._PROCESSOR_MAP == registry
    finally:
        guard.release()
