# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lazy creation must not freeze the loop or retry once per skill."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as jb


class Probe(jb._JiuwenBoxProviderMixin):
    def __init__(self, client, scope="one"):
        self.endpoint = SimpleNamespace(base_url="http://localhost:8321", isolation_key=scope)
        self.config = None
        self._init_jiuwenbox(self.endpoint, None)
        self._client = client


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("ACL timeout"), RuntimeError("HTTP 500: cleanup failed")])
async def test_strict_delete_preserves_original_error(monkeypatch, failure):
    key = "http://localhost:8321|one"
    jb._JiuwenBoxProviderMixin.register_shared_sandbox_id(key, "sandbox")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.delete_sandbox.side_effect = failure
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match=str(failure)) as exc:
        await jb.delete_jiuwenbox_sandbox(shared_key=key, raise_on_error=True)
    assert exc.value.__cause__ is failure


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    for name in ("_shared_sandbox_ids", "_creation_locks", "_creation_failures", "_lifecycle_hooks",
                 "_pending_deletions", "_delete_tasks", "_idle_timeout_cache"):
        monkeypatch.setattr(jb._JiuwenBoxProviderMixin, name, {})
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


@pytest.mark.asyncio
async def test_confirmed_service_exit_retires_cached_and_pending_entries_only_for_its_url(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    old_url, other_url = "http://localhost:8321", "http://localhost:83210"
    cached_key, pending_key, other_key = old_url + "|cached", old_url + "|pending", other_url + "|other"
    cached_hook, pending_hook, other_hook = Mock(), Mock(), Mock()
    cls._shared_sandbox_ids.update({cached_key: "cached-id", other_key: "other-id"})
    cls._lifecycle_hooks.update({cached_key: cached_hook, other_key: other_hook})
    cls._pending_deletions[(pending_key, "pending-id")] = (True, pending_hook)
    cls._creation_locks[old_url + "|creating"] = threading.Lock()
    cls._creation_failures[old_url + "|failed"] = (123, "failure")
    cls._idle_timeout_cache.update({old_url: (10, 2), other_url: (10, 2)})
    client = Mock(side_effect=AssertionError("retired service must not receive HTTP"))
    monkeypatch.setattr(jb, "_JiuwenBoxClient", client)

    assert set(await jb.retire_jiuwenbox_service(old_url + "/")) == {"cached-id", "pending-id"}
    assert cls._shared_sandbox_ids == {other_key: "other-id"}
    assert cls._lifecycle_hooks == {other_key: other_hook}
    assert not cls._pending_deletions and not cls._delete_tasks
    assert not cls._creation_locks and not cls._creation_failures
    assert cls._idle_timeout_cache == {other_url: (10, 2)}
    assert [call.args[0] for call in cached_hook.call_args_list] == ["before_delete", "after_delete"]
    assert [call.args[0] for call in pending_hook.call_args_list] == ["after_delete"]
    other_hook.assert_not_called()
    assert await jb.retire_jiuwenbox_service(old_url) == []


@pytest.mark.asyncio
async def test_service_exit_recovers_failed_delete_without_retrying_dead_port(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    url, key = "http://localhost:8321", "http://localhost:8321|scope"
    cls.register_shared_sandbox_id(key, "old")
    hook = Mock()
    cls.register_lifecycle_hook(key, hook)
    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = httpx.ConnectError("old port is dead")
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match="old port is dead"):
        await jb.delete_jiuwenbox_sandbox(shared_key=key, raise_on_error=True)
    assert await jb.retire_jiuwenbox_service(url) == ["old"]
    client.delete_sandbox.assert_called_once_with("old")
    assert not cls._pending_deletions and not cls._shared_sandbox_ids
    assert [call.args[0] for call in hook.call_args_list] == ["before_delete", "after_delete"]


@pytest.mark.asyncio
async def test_service_retirement_preserves_strict_hook_failure_for_retry(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    key = "http://localhost:8321|scope"
    cls.register_shared_sandbox_id(key, "old")
    hook = Mock(side_effect=RuntimeError("cleanup refused"))
    cls.register_lifecycle_hook(key, hook)
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(side_effect=AssertionError("no HTTP")))
    with pytest.raises(RuntimeError, match="cleanup refused"):
        await jb.retire_jiuwenbox_service("http://localhost:8321")
    assert cls._shared_sandbox_ids[key] == "old"
    hook.side_effect = None
    assert await jb.retire_jiuwenbox_service("http://localhost:8321") == ["old"]


@pytest.mark.asyncio
async def test_service_retirement_joins_inflight_delete_before_finishing_hooks(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    key = "http://localhost:8321|scope"
    cls.register_shared_sandbox_id(key, "old")
    hook = Mock()
    cls.register_lifecycle_hook(key, hook)
    entered, release = threading.Event(), threading.Event()

    def delete(sid):
        entered.set()
        assert release.wait(2)
        raise httpx.ConnectError("service exited")

    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = delete
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    delete_task = asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=key))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        retire_task = asyncio.create_task(jb.retire_jiuwenbox_service("http://localhost:8321"))
        await asyncio.sleep(0)
        assert not retire_task.done()
    finally:
        release.set()
    assert await delete_task == []
    assert await retire_task == ["old"]
    assert [call.args[0] for call in hook.call_args_list] == ["before_delete", "after_delete"]


@pytest.mark.asyncio
async def test_service_retirement_drains_deletes_started_while_joining(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    url = "http://localhost:8321"
    keys = {sid: f"{url}|{sid}" for sid in ("first", "late")}
    cls._shared_sandbox_ids.update({key: sid for sid, key in keys.items()})
    hooks = {sid: Mock() for sid in keys}
    for sid, key in keys.items():
        cls.register_lifecycle_hook(key, hooks[sid])
    entered = {sid: threading.Event() for sid in keys}
    release = {sid: threading.Event() for sid in keys}
    requested = []

    def delete(sid):
        requested.append(sid)
        entered[sid].set()
        assert release[sid].wait(2)
        raise httpx.ConnectError("service exited")

    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = delete
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    first_task = asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=keys["first"]))
    try:
        assert await asyncio.to_thread(entered["first"].wait, 1)
        retire_task = asyncio.create_task(jb.retire_jiuwenbox_service(url))
        await asyncio.sleep(0)  # Retirement is now joining the first task.
        late_task = asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=keys["late"]))
        assert await asyncio.to_thread(entered["late"].wait, 1)
        release["first"].set()
        assert await first_task == []
        assert not retire_task.done()
    finally:
        for event in release.values():
            event.set()
    assert await late_task == []
    assert await retire_task == ["first", "late"]
    assert requested == ["first", "late"]
    assert not cls._shared_sandbox_ids and not cls._pending_deletions and not cls._delete_tasks
    for hook in hooks.values():
        assert [call.args[0] for call in hook.call_args_list] == ["before_delete", "after_delete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_failure", [False, True])
async def test_service_retirement_does_not_reuse_remote_task_started_during_local_cleanup(monkeypatch, remote_failure):
    cls = jb._JiuwenBoxProviderMixin
    url = "http://localhost:8321"
    first_key, late_key = url + "|first", url + "|late"
    cls._shared_sandbox_ids.update({first_key: "first", late_key: "late"})
    cls._idle_timeout_cache[url] = (10, 2)
    late_tasks = []
    first_events, late_hook = [], Mock()

    def first_hook(event, context):
        first_events.append(event)
        if event == "before_delete":
            # The local first-scope task schedules a normal per-agent teardown
            # before retirement resumes to process the next scope.
            late_tasks.append(asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=late_key)))

    cls.register_lifecycle_hook(first_key, first_hook)
    cls.register_lifecycle_hook(late_key, late_hook)
    entered, release = threading.Event(), threading.Event()

    def delete(sid):
        assert sid == "late"
        entered.set()
        assert release.wait(2)
        if remote_failure:
            raise httpx.ConnectError("late teardown reached dead port")

    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = delete
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    retire_task = asyncio.create_task(jb.retire_jiuwenbox_service(url))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not retire_task.done()
    finally:
        release.set()
    assert await late_tasks[0] == ([] if remote_failure else ["late"])
    assert await retire_task == ["first", "late"]
    client.delete_sandbox.assert_called_once_with("late")
    assert first_events == ["before_delete", "after_delete"]
    assert [call.args[0] for call in late_hook.call_args_list] == ["before_delete", "after_delete"]
    assert not cls._shared_sandbox_ids and not cls._pending_deletions and not cls._delete_tasks
    assert not cls._lifecycle_hooks and not cls._idle_timeout_cache


@pytest.mark.asyncio
async def test_bulk_delete_includes_pending_only_historical_urls_and_keeps_unreachable_entries(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    key = "http://localhost:8321|old"
    cls._pending_deletions[(key, "old")] = (True, None)
    cls._shared_sandbox_ids["http://localhost:9000|current"] = "current"
    urls = []

    def client_for(base_url, **kwargs):
        urls.append(base_url)
        client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
        client.__enter__.return_value = client
        if base_url.endswith(":8321"):
            client.delete_sandbox.side_effect = httpx.ConnectError("external service unavailable")
        return client

    monkeypatch.setattr(jb, "_JiuwenBoxClient", client_for)
    assert await jb.delete_jiuwenbox_sandbox(delete_all=True) == ["current"]
    assert set(urls) == {"http://localhost:8321", "http://localhost:9000"}
    assert (key, "old") in cls._pending_deletions
    with pytest.raises(RuntimeError, match="external service unavailable"):
        await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True)


@pytest.mark.asyncio
async def test_strict_bulk_delete_attempts_all_entries_then_raises_first_cause(monkeypatch):
    cls = jb._JiuwenBoxProviderMixin
    keys = {sid: f"http://localhost:8321|{sid}" for sid in ("first", "healthy", "last")}
    cls._shared_sandbox_ids.update({key: sid for sid, key in keys.items()})
    first_failure = httpx.ConnectError("first unavailable")
    last_failure = httpx.ReadTimeout("last timeout")
    attempted = []

    def delete(sid):
        attempted.append(sid)
        if sid == "first":
            raise first_failure
        if sid == "last":
            raise last_failure

    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = delete
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match="first unavailable") as caught:
        await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True)
    assert caught.value.__cause__ is first_failure
    assert "2 entries" in caught.value.__notes__[0]
    assert attempted == ["first", "healthy", "last"]
    assert cls._shared_sandbox_ids == {keys[sid]: sid for sid in ("first", "last")}
    assert set(cls._pending_deletions) == {(keys[sid], sid) for sid in ("first", "last")}
    client.delete_sandbox.side_effect = None
    assert await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=True) == ["first", "last"]
    assert not cls._shared_sandbox_ids and not cls._pending_deletions


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_bulk_hook_failure_is_strict_but_does_not_skip_later_entries(monkeypatch, strict):
    cls = jb._JiuwenBoxProviderMixin
    failed_key, healthy_key = "http://localhost:8321|failed", "http://localhost:8321|healthy"
    cls._shared_sandbox_ids.update({failed_key: "failed", healthy_key: "healthy"})
    hook_failure = RuntimeError("before hook refused")
    cls.register_lifecycle_hook(failed_key, Mock(side_effect=hook_failure))
    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match="before hook refused") as caught:
        await jb.delete_jiuwenbox_sandbox(delete_all=True, raise_on_error=strict)
    assert caught.value is hook_failure
    client.delete_sandbox.assert_called_once_with("healthy")
    assert cls._shared_sandbox_ids == {failed_key: "failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["fs", "exec"])
async def test_slow_initial_create_keeps_loop_responsive(pipeline):
    started, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()

    def create(**kwargs):
        assert threading.get_ident() != main_thread
        started.set()
        assert release.wait(2)
        return "sandbox"

    client = Mock(create_sandbox=Mock(side_effect=create))
    probe = Probe(client)
    if pipeline == "fs":
        operation = probe._execute_with_sandbox_retry(lambda sid: sid)
    else:
        operation = probe._run_exec_pipeline(
            sandbox_op=lambda sid: {"exit_code": 0, "stdout": sid, "stderr": ""},
            local_op=Mock(), fallback_on_failure=False,
        )
    task = asyncio.create_task(operation)
    try:
        assert await asyncio.to_thread(started.wait, 1)
        # A synchronous cache operation must also remain usable during HTTP.
        assert jb._JiuwenBoxProviderMixin.clear_shared_sandbox_for_key("other") is None
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    result = await asyncio.wait_for(task, 2)
    assert result == "sandbox" if pipeline == "fs" else result[0]["stdout"] == "sandbox"


@pytest.mark.asyncio
async def test_concurrent_providers_share_one_create():
    client = Mock(create_sandbox=Mock(return_value="sandbox"))
    first, second = Probe(client), Probe(client)
    results = await asyncio.gather(*[
        probe._execute_with_sandbox_retry(lambda sid: sid) for probe in (first, second)
    ])
    assert results == ["sandbox", "sandbox"]
    client.create_sandbox.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reset", ["clear", "expiry"])
async def test_failed_create_is_shared_and_can_recover(reset):
    client = Mock(create_sandbox=Mock(side_effect=httpx.ReadTimeout("ACL timeout")))
    first, second = Probe(client), Probe(client)
    with pytest.raises(httpx.ReadTimeout):
        await first._execute_with_sandbox_retry(lambda sid: sid)
    for _ in range(45):
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            await second._execute_with_sandbox_retry(lambda sid: sid)
    client.create_sandbox.assert_called_once()
    if reset == "clear":
        jb._JiuwenBoxProviderMixin.clear_shared_sandbox_for_key(first._shared_scope_key())
    else:
        jb._JiuwenBoxProviderMixin._creation_failures[first._shared_scope_key()] = (0.0, "expired")
    client.create_sandbox.side_effect = None
    client.create_sandbox.return_value = "recovered"
    assert await second._execute_with_sandbox_retry(lambda sid: sid) == "recovered"


@pytest.mark.asyncio
async def test_teardown_during_create_does_not_publish_orphan():
    started, release = threading.Event(), threading.Event()

    def create(**kwargs):
        started.set()
        assert release.wait(2)
        return "orphan"

    client = Mock(create_sandbox=Mock(side_effect=create))
    probe = Probe(client)
    task = asyncio.create_task(probe._execute_with_sandbox_retry(lambda sid: sid))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        jb._JiuwenBoxProviderMixin.clear_shared_sandbox_for_key(probe._shared_scope_key())
    finally:
        release.set()
    with pytest.raises(RuntimeError, match="invalidated"):
        await task
    assert not jb._JiuwenBoxProviderMixin._shared_sandbox_ids
    client.delete_sandbox.assert_called_once_with("orphan")


@pytest.mark.asyncio
async def test_failed_delete_blocks_reuse_and_recreation_until_retry(monkeypatch):
    probe = Probe(Mock())
    key = probe._shared_scope_key()
    jb._JiuwenBoxProviderMixin.register_shared_sandbox_id(key, "old")
    hook = Mock()
    jb._JiuwenBoxProviderMixin.register_lifecycle_hook(key, hook)
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.delete_sandbox.side_effect = [httpx.ReadTimeout("ACL timeout"), None]
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    assert await jb.delete_jiuwenbox_sandbox(shared_key=key) == []
    assert jb._JiuwenBoxProviderMixin._shared_sandbox_ids[key] == "old"
    assert jb._JiuwenBoxProviderMixin._lifecycle_hooks[key] is hook
    probe._sandbox_id = "old"
    with pytest.raises(RuntimeError, match="deletion is pending"):
        probe._get_sandbox_id()
    with pytest.raises(RuntimeError, match="deletion is pending"):
        await jb.force_recreate_jiuwenbox_sandbox("http://localhost:8321", shared_key=key)
    assert await jb.delete_jiuwenbox_sandbox(shared_key=key) == ["old"]
    assert [call.args[0] for call in hook.call_args_list] == ["before_delete", "after_delete"]
    assert key not in jb._JiuwenBoxProviderMixin._shared_sandbox_ids
    assert not jb._JiuwenBoxProviderMixin._pending_deletions
    client.create_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_pending_delete_retry_keeps_original_hook(monkeypatch):
    key = "http://localhost:8321|one"
    cls = jb._JiuwenBoxProviderMixin
    cls.register_shared_sandbox_id(key, "old")
    old_hook, new_hook = Mock(), Mock()
    cls.register_lifecycle_hook(key, old_hook)
    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = [httpx.ReadTimeout("ACL timeout"), None]
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    assert await jb.delete_jiuwenbox_sandbox(shared_key=key) == []
    cls._shared_sandbox_ids[key] = "new"
    cls.register_lifecycle_hook(key, new_hook)
    assert await jb.delete_jiuwenbox_sandbox(shared_key=key, sandbox_id="old") == ["old"]
    assert [call.args[0] for call in old_hook.call_args_list] == ["before_delete", "after_delete"]
    new_hook.assert_not_called()
    assert cls._shared_sandbox_ids[key] == "new"
    assert cls._lifecycle_hooks[key] is new_hook


@pytest.mark.asyncio
async def test_after_delete_hook_failure_does_not_restore_deleted_entry(monkeypatch):
    key = "http://localhost:8321|one"
    cls = jb._JiuwenBoxProviderMixin
    cls.register_shared_sandbox_id(key, "old")
    hook = Mock(side_effect=[None, RuntimeError("after hook failed")])
    cls.register_lifecycle_hook(key, hook)
    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    with pytest.raises(RuntimeError, match="after hook failed"):
        await jb.delete_jiuwenbox_sandbox(shared_key=key)
    assert key not in cls._shared_sandbox_ids
    assert not cls._pending_deletions


@pytest.mark.asyncio
async def test_concurrent_delete_runs_hooks_once_and_preserves_new_cache(monkeypatch):
    key = "http://localhost:8321|one"
    cls = jb._JiuwenBoxProviderMixin
    cls.register_shared_sandbox_id(key, "old")
    hook = Mock()
    cls.register_lifecycle_hook(key, hook)
    entered, release = threading.Event(), threading.Event()

    def delete(sid):
        entered.set()
        assert release.wait(2)

    client = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    client.__enter__.return_value = client
    client.delete_sandbox.side_effect = delete
    monkeypatch.setattr(jb, "_JiuwenBoxClient", Mock(return_value=client))
    first = asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=key, sandbox_id="old"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        cls.register_shared_sandbox_id(key, "new")
        second = asyncio.create_task(jb.delete_jiuwenbox_sandbox(shared_key=key, sandbox_id="old"))
        await asyncio.sleep(0)
        assert not second.done()
    finally:
        release.set()
    assert await asyncio.gather(first, second) == [["old"], ["old"]]
    assert cls._shared_sandbox_ids[key] == "new"
    client.delete_sandbox.assert_called_once_with("old")
    assert [call.args[0] for call in hook.call_args_list] == ["before_delete", "after_delete"]
