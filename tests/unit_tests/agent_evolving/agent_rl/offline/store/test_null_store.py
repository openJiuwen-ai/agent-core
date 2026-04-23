# -*- coding: utf-8 -*-
"""Unit tests for NullRolloutStore: all methods no-op, query_rollouts returns []."""

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutMessage


@pytest.fixture
def store():
    return NullRolloutStore()


@pytest.mark.asyncio
async def test_save_rollout_save_step_summary_close_no_error(store):
    msg = RolloutMessage(task_id="t1", origin_task_id="o1", rollout_id="r1")
    await store.save_rollout(step=0, task_id="t1", rollout=msg, phase="train")
    await store.save_rollout(step=0, task_id="t1", rollout=msg, phase="val")
    await store.save_step_summary(step=0, metrics={"loss": 0.1})
    await store.close()


@pytest.mark.asyncio
async def test_query_rollouts_returns_empty_list(store):
    out = await store.query_rollouts({}, limit=10)
    assert out == []
