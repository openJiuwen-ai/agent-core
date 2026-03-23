# -*- coding: utf-8 -*-
"""System tests for FileRolloutStore: real file I/O, save/query, train/val separation."""

import json

import pytest

from openjiuwen.dev_tools.agentrl.rollout_store.file_store import FileRolloutStore
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage, Rollout


def _make_rollout_msg(task_id="t1", origin_task_id="o1", reward=1.0):
    return RolloutMessage(
        task_id=task_id,
        origin_task_id=origin_task_id,
        rollout_id="r1",
        rollout_info=[
            Rollout(
                turn_id=0,
                input_prompt={"message": [{"role": "user", "content": "hi"}]},
                output_response={"role": "assistant", "content": "hello"},
            )
        ],
        reward_list=[reward],
        global_reward=reward,
        turn_count=1,
        round_num=0,
    )


@pytest.fixture
def store(tmp_path):
    return FileRolloutStore(save_path=str(tmp_path), flush_interval=100)


@pytest.mark.asyncio
async def test_file_store_e2e_save_and_query_train_val(store, tmp_path):
    """E2E: save rollout to train and val, then query both."""
    msg_train = _make_rollout_msg(task_id="train-1", reward=0.8)
    msg_val = _make_rollout_msg(task_id="val-1", reward=0.9)

    await store.save_rollout(step=0, task_id="train-1", rollout=msg_train, phase="train")
    await store.save_rollout(step=0, task_id="val-1", rollout=msg_val, phase="val")

    train_file = tmp_path / "train" / "rollouts" / "steps_000000_000099.jsonl"
    val_file = tmp_path / "val" / "rollouts" / "steps_000000_000099.jsonl"
    assert train_file.exists()
    assert val_file.exists()

    results = await store.query_rollouts({}, limit=10)
    task_ids = {r["task_id"] for r in results}
    assert "train-1" in task_ids
    assert "val-1" in task_ids


@pytest.mark.asyncio
async def test_file_store_e2e_step_summary(store, tmp_path):
    """E2E: save step summary and verify file contents."""
    await store.save_step_summary(step=150, metrics={"loss": 0.42, "reward_mean": 0.5})

    fpath = tmp_path / "step_summaries" / "steps_000100_000199.jsonl"
    assert fpath.exists()
    doc = json.loads(fpath.read_text(encoding="utf-8").strip())
    assert doc["step"] == 150
    assert doc["metrics"]["loss"] == 0.42
    assert doc["metrics"]["reward_mean"] == 0.5


@pytest.mark.asyncio
async def test_file_store_e2e_query_with_filter_and_limit(store):
    """E2E: save multiple rollouts, query with filter and limit."""
    for i in range(5):
        msg = _make_rollout_msg(task_id=f"t{i}")
        await store.save_rollout(step=0, task_id=f"t{i}", rollout=msg)

    results = await store.query_rollouts({"task_id": "t2"}, limit=10)
    assert len(results) == 1
    assert results[0]["task_id"] == "t2"

    results_limit = await store.query_rollouts({}, limit=3)
    assert len(results_limit) == 3


@pytest.mark.asyncio
async def test_file_store_e2e_close_no_error(store):
    """E2E: close does not raise."""
    await store.close()
