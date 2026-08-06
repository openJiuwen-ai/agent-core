# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generic KV-cache identity, range, and session-action tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.foundation.kv_cache import (
    cancel_pending_session_kv_cache_signals,
    dispatch_session_kv_cache_signal,
    evict_session_kv_cache,
    message_range_kwargs,
    resolve_session_lineage,
    team_member_cache_identity,
    tools_range_kwargs,
)


def test_range_kwargs_always_return_complete_half_open_ranges():
    assert message_range_kwargs(2, 5) == {"msg_start": 2, "msg_end": 5}
    assert tools_range_kwargs(0, 1) == {"tools_start": 0, "tools_end": 1}


def test_session_lineage_ignores_non_string_affinity_overrides():
    session = SimpleNamespace(
        get_session_id=lambda: "session-id",
        get_parent_session_id=lambda: None,
        get_env=MagicMock(return_value=object()),
    )

    assert resolve_session_lineage(session) == ("session-id", "session-id")


def test_team_member_cache_identity_uses_card_id_scope():
    assert (
        team_member_cache_identity("team-sid", "team-a", "coder")
        == "team:team-sid:team:team-a:member:coder"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        SimpleNamespace(evict_kvc=AsyncMock(return_value=True)),
        SimpleNamespace(supports_kv_cache_affinity=False, evict_kvc=AsyncMock(return_value=True)),
        SimpleNamespace(
            supports_kv_cache_affinity=lambda: (_ for _ in ()).throw(RuntimeError("capability broken")),
            evict_kvc=AsyncMock(return_value=True),
        ),
    ],
)
async def test_session_action_capability_fail_closed(model):
    assert await evict_session_kv_cache(model, session_id="sid", parent_session_id="sid") is False
    model.evict_kvc.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_signals_are_background_ordered_and_evict_is_a_barrier():
    prefetch_release = asyncio.Event()
    calls: list[str] = []

    async def prefetch_kvc(**_kwargs):
        calls.append("prefetch-start")
        await prefetch_release.wait()
        calls.append("prefetch-end")
        return True

    async def offload_kvc(**_kwargs):
        calls.append("offload")
        return True

    async def evict_kvc(**_kwargs):
        calls.append("evict")
        return True

    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        prefetch_kvc=prefetch_kvc,
        offload_kvc=offload_kvc,
        evict_kvc=evict_kvc,
    )

    assert dispatch_session_kv_cache_signal(
        model,
        "prefetch",
        session_id="sid",
        parent_session_id="parent",
    )
    assert dispatch_session_kv_cache_signal(
        model,
        "offload",
        session_id="sid",
        parent_session_id="parent",
    )

    await asyncio.sleep(0)
    assert calls == ["prefetch-start"]

    prefetch_release.set()
    assert await evict_session_kv_cache(
        model,
        session_id="sid",
        parent_session_id="parent",
    )
    assert calls == ["prefetch-start", "prefetch-end", "offload", "evict"]


@pytest.mark.asyncio
async def test_affinity_disabled_evict_does_not_wait_for_pending_signal():
    signal_release = asyncio.Event()

    async def prefetch_kvc(**_kwargs):
        await signal_release.wait()
        return True

    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        prefetch_kvc=prefetch_kvc,
        evict_kvc=AsyncMock(return_value=True),
    )
    assert dispatch_session_kv_cache_signal(model, "prefetch", session_id="sid")
    await asyncio.sleep(0)

    assert await asyncio.wait_for(
        evict_session_kv_cache(model, session_id="sid", enabled=False),
        timeout=0.1,
    ) is False
    model.evict_kvc.assert_not_awaited()

    await cancel_pending_session_kv_cache_signals()


@pytest.mark.asyncio
async def test_signal_dispatched_during_evict_waits_for_evict_barrier():
    evict_started = asyncio.Event()
    evict_release = asyncio.Event()
    prefetch_done = asyncio.Event()
    calls: list[str] = []

    async def evict_kvc(**_kwargs):
        calls.append("evict-start")
        evict_started.set()
        await evict_release.wait()
        calls.append("evict-end")
        return True

    async def prefetch_kvc(**_kwargs):
        calls.append("prefetch")
        prefetch_done.set()
        return True

    model = SimpleNamespace(
        supports_kv_cache_affinity=lambda: True,
        evict_kvc=evict_kvc,
        prefetch_kvc=prefetch_kvc,
    )

    evict_task = asyncio.create_task(
        evict_session_kv_cache(model, session_id="sid")
    )
    await evict_started.wait()
    assert dispatch_session_kv_cache_signal(model, "prefetch", session_id="sid")
    await asyncio.sleep(0)
    assert calls == ["evict-start"]

    evict_release.set()
    assert await evict_task
    await asyncio.wait_for(prefetch_done.wait(), timeout=0.1)
    assert calls == ["evict-start", "evict-end", "prefetch"]
