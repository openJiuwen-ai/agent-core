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


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    for name in ("_shared_sandbox_ids", "_creation_locks", "_creation_failures", "_lifecycle_hooks"):
        monkeypatch.setattr(jb._JiuwenBoxProviderMixin, name, {})
    monkeypatch.delenv("JIUWENBOX_SANDBOX_ID", raising=False)


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
