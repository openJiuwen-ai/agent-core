# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Releasing cached memory index managers on a member's permanent teardown."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.core.memory.lite import manager as lite_manager


def _fake_manager(agent_id: str, root: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id,
        workspace=SimpleNamespace(root_path=root),
        close=AsyncMock(),
    )


@pytest.fixture
def index_cache(monkeypatch):
    cache: dict = {}
    monkeypatch.setattr(lite_manager, "INDEX_CACHE", cache)
    return cache


@pytest.mark.asyncio
async def test_closes_only_the_agents_managers_on_its_workspace(index_cache):
    own_memory = _fake_manager("member-a", "/ws/a")
    own_coding = _fake_manager("member-a", "/ws/a")
    other_member = _fake_manager("member-b", "/ws/b")
    same_id_elsewhere = _fake_manager("member-a", "/ws/other")
    index_cache.update(
        {
            "member-a:memory:/ws/a/memory": own_memory,
            "member-a:coding_memory:/ws/a/coding": own_coding,
            "member-b:memory:/ws/b/memory": other_member,
            "member-a:memory:/ws/other/memory": same_id_elsewhere,
        }
    )

    closed = await lite_manager.aclose_memory_managers_for(
        "member-a", SimpleNamespace(root_path="/ws/a")
    )

    assert closed == 2
    own_memory.close.assert_awaited_once()
    own_coding.close.assert_awaited_once()
    other_member.close.assert_not_awaited()
    same_id_elsewhere.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_close_does_not_stop_the_rest(index_cache):
    broken = _fake_manager("member-a", "/ws/a")
    broken.close.side_effect = RuntimeError("boom")
    healthy = _fake_manager("member-a", "/ws/a")
    index_cache.update({"k1": broken, "k2": healthy})

    assert await lite_manager.aclose_memory_managers_for("member-a", SimpleNamespace(root_path="/ws/a")) == 2
    healthy.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_dispose_hook_releases_the_members_managers(index_cache):
    own = _fake_manager("member-a", "/ws/a")
    index_cache["k"] = own
    harness = SimpleNamespace(card=SimpleNamespace(id="member-a"))

    await NativeHarness._close_memory_managers(
        harness, SimpleNamespace(workspace=SimpleNamespace(root_path="/ws/a"))
    )

    own.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_dispose_hook_without_workspace_is_a_no_op(index_cache):
    own = _fake_manager("member-a", "/ws/a")
    index_cache["k"] = own
    harness = SimpleNamespace(card=SimpleNamespace(id="member-a"))

    await NativeHarness._close_memory_managers(harness, None)
    await NativeHarness._close_memory_managers(harness, SimpleNamespace(workspace=None))

    own.close.assert_not_awaited()
