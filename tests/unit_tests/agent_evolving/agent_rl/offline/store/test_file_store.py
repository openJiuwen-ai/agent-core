# -*- coding: utf-8 -*-
"""Unit tests for FileRolloutStore: train/val sub-dirs, step-based file splitting, query."""

import json

import pytest

from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutMessage, Rollout


@pytest.fixture
def store(tmp_path):
    return FileRolloutStore(save_path=str(tmp_path), flush_interval=100)


@pytest.fixture
def small_store(tmp_path):
    return FileRolloutStore(save_path=str(tmp_path), flush_interval=3)


def _make_rollout_msg(task_id="t1", origin_task_id="o1", reward=1.0):
    return RolloutMessage(
        task_id=task_id,
        origin_task_id=origin_task_id,
        rollout_id="r1",
        rollout_info=[Rollout(turn_id=0, input_prompt={"message": [{"role": "user", "content": "hi"}]})],
        reward_list=[reward],
        global_reward=reward,
        turn_count=1,
    )


@pytest.mark.asyncio
async def test_save_train_rollout_creates_jsonl(store, tmp_path):
    msg = _make_rollout_msg()
    await store.save_rollout(step=0, task_id="t1", rollout=msg, phase="train")

    fpath = tmp_path / "train" / "rollouts" / "steps_000000_000099.jsonl"
    assert fpath.exists()
    lines = fpath.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["task_id"] == "t1"
    assert doc["global_reward"] == 1.0


@pytest.mark.asyncio
async def test_default_phase_is_train(store, tmp_path):
    await store.save_rollout(step=0, task_id="t1", rollout=_make_rollout_msg())
    assert (tmp_path / "train" / "rollouts" / "steps_000000_000099.jsonl").exists()
    assert not (tmp_path / "val" / "rollouts" / "steps_000000_000099.jsonl").exists()


@pytest.mark.asyncio
async def test_save_val_rollout_creates_jsonl_in_val_dir(store, tmp_path):
    msg = _make_rollout_msg(task_id="v1")
    await store.save_rollout(step=5, task_id="v1", rollout=msg, phase="val")

    fpath = tmp_path / "val" / "rollouts" / "steps_000000_000099.jsonl"
    assert fpath.exists()
    doc = json.loads(fpath.read_text(encoding="utf-8").strip())
    assert doc["task_id"] == "v1"


@pytest.mark.asyncio
async def test_train_and_val_in_separate_dirs(store, tmp_path):
    await store.save_rollout(step=0, task_id="train1", rollout=_make_rollout_msg(task_id="train1"), phase="train")
    await store.save_rollout(step=0, task_id="val1", rollout=_make_rollout_msg(task_id="val1"), phase="val")

    train_file = tmp_path / "train" / "rollouts" / "steps_000000_000099.jsonl"
    val_file = tmp_path / "val" / "rollouts" / "steps_000000_000099.jsonl"
    assert train_file.exists()
    assert val_file.exists()

    train_doc = json.loads(train_file.read_text(encoding="utf-8").strip())
    val_doc = json.loads(val_file.read_text(encoding="utf-8").strip())
    assert train_doc["task_id"] == "train1"
    assert val_doc["task_id"] == "val1"


@pytest.mark.asyncio
async def test_save_step_summary_creates_jsonl(store, tmp_path):
    await store.save_step_summary(step=150, metrics={"loss": 0.42})

    fpath = tmp_path / "step_summaries" / "steps_000100_000199.jsonl"
    assert fpath.exists()
    doc = json.loads(fpath.read_text(encoding="utf-8").strip())
    assert doc["step"] == 150
    assert doc["metrics"]["loss"] == 0.42


@pytest.mark.asyncio
async def test_file_splitting_by_flush_interval(small_store, tmp_path):
    for step in range(6):
        await small_store.save_rollout(
            step=step, task_id=f"t{step}", rollout=_make_rollout_msg(task_id=f"t{step}")
        )

    file1 = tmp_path / "train" / "rollouts" / "steps_000000_000002.jsonl"
    file2 = tmp_path / "train" / "rollouts" / "steps_000003_000005.jsonl"
    assert file1.exists()
    assert file2.exists()
    assert len(file1.read_text(encoding="utf-8").strip().split("\n")) == 3
    assert len(file2.read_text(encoding="utf-8").strip().split("\n")) == 3


@pytest.mark.asyncio
async def test_query_rollouts_searches_both_train_and_val(store):
    await store.save_rollout(step=0, task_id="t1", rollout=_make_rollout_msg(task_id="t1"), phase="train")
    await store.save_rollout(step=0, task_id="v1", rollout=_make_rollout_msg(task_id="v1"), phase="val")

    results = await store.query_rollouts({}, limit=10)
    task_ids = {r["task_id"] for r in results}
    assert "t1" in task_ids
    assert "v1" in task_ids


@pytest.mark.asyncio
async def test_query_rollouts_with_filter(store):
    await store.save_rollout(step=0, task_id="t1", rollout=_make_rollout_msg(task_id="t1"))
    await store.save_rollout(step=0, task_id="t2", rollout=_make_rollout_msg(task_id="t2"))

    results = await store.query_rollouts({"task_id": "t1"}, limit=10)
    assert len(results) == 1
    assert results[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_query_rollouts_with_limit(store):
    for i in range(5):
        await store.save_rollout(step=0, task_id=f"t{i}", rollout=_make_rollout_msg(task_id=f"t{i}"))
    results = await store.query_rollouts({}, limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_query_rollouts_empty(store):
    assert await store.query_rollouts({}) == []


@pytest.mark.asyncio
async def test_close_no_error(store):
    await store.close()


@pytest.mark.asyncio
async def test_multiple_saves_append_to_same_file(store, tmp_path):
    for i in range(3):
        await store.save_rollout(step=10, task_id=f"t{i}", rollout=_make_rollout_msg(task_id=f"t{i}"))

    fpath = tmp_path / "train" / "rollouts" / "steps_000000_000099.jsonl"
    lines = fpath.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
