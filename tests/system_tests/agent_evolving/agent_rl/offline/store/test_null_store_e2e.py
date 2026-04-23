# -*- coding: utf-8 -*-
"""System tests for NullRolloutStore: save/query no side effects, close no-op."""

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutMessage, Rollout


@pytest.fixture
def store():
    return NullRolloutStore()


@pytest.mark.asyncio
async def test_null_store_e2e_save_rollout_and_summary_no_error(store):
    msg = RolloutMessage(
        task_id="t1",
        origin_task_id="o1",
        rollout_id="r1",
        rollout_info=[Rollout(turn_id=0, input_prompt={}, output_response={})],
        reward_list=[0.5],
        global_reward=0.5,
        turn_count=1,
    )
    await store.save_rollout(step=0, task_id="t1", rollout=msg, phase="train")
    await store.save_rollout(step=0, task_id="t1", rollout=msg, phase="val")
    await store.save_step_summary(step=0, metrics={"loss": 0.1})


@pytest.mark.asyncio
async def test_null_store_e2e_query_returns_empty(store):
    result = await store.query_rollouts({}, limit=100)
    assert result == []


@pytest.mark.asyncio
async def test_null_store_e2e_close_no_error(store):
    await store.close()
