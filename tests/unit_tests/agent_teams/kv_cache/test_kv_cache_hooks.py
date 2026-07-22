# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.kv_cache import kv_cache_hooks


class _RaisingRegistry:
    async def mark_ready_resident(self, member_id: str) -> None:
        raise RuntimeError(f"mark failed: {member_id}")

    async def evict_member(self, member_id: str, *, reason: str) -> bool:
        raise RuntimeError(f"evict member failed: {member_id}:{reason}")

    async def build_action_plan(self, action: str) -> None:
        raise RuntimeError(f"plan failed: {action}")


def _agent(registry: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        card=SimpleNamespace(id="coder"),
        member_name="Coder",
        resources=SimpleNamespace(team_kv_cache_registry=registry),
    )


@pytest.mark.asyncio
async def test_kv_cache_hooks_are_best_effort_when_registry_raises() -> None:
    agent = _agent(_RaisingRegistry())

    await kv_cache_hooks.mark_ready_resident(agent)
    await kv_cache_hooks.evict_member(agent, reason="member-shutdown")
    assert await kv_cache_hooks.build_action_plan(agent, "evict") is None
